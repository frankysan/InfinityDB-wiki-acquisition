"""P2: rebuild verified Wiki history snapshots from an archived parent.

Current pages/assets are copied byte-for-byte. An existing history parent's
oldid content is reused only after matching newly enumerated revision identity.
No original ZIP, manifest or inventory is modified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file, write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)


class SnapshotUpgradeResult(NamedTuple):
    archive: Path
    manifest: Path
    inventory: Path
    parent_archive: Path
    reused_members: int
    revision_count: int
    reused_revisions: int = 0


class SnapshotUpgradeIncomplete(ValueError):
    """History acquisition was incomplete; work is retained for inspection."""

    def __init__(self, work: Path, failures: tuple[Any, ...]) -> None:
        self.work = work
        self.failures = failures
        super().__init__(
            f"History upgrade incomplete: {len(failures)} revision request(s) "
            f"failed; preserved work -> {work}"
        )


def _reusable_parent_pages(document: dict[str, Any]) -> tuple[Any, ...]:
    """Require explicit, unambiguous page identities; never classify by name."""
    from infinity_wiki_acquisition.cli import WikiPage

    pages: list[Any] = []
    page_urls: set[str] = set()
    page_paths: set[str] = set()
    for member in document["members"]:
        kind = member["kind"]
        path = member["path"]
        if kind == "current-page":
            page = member.get("page")
            identity = member.get("sourceIdentity")
            if not isinstance(page, dict) or not isinstance(identity, dict):
                raise ValueError(f"Missing page identity in source inventory: {path}")
            url, title = page.get("url"), page.get("title")
            if not isinstance(url, str) or not url.strip() or not isinstance(title, str) or not title.strip():
                raise ValueError(f"Missing page URL/title in source inventory: {path}")
            if identity.get("requestUrl") != url or identity.get("title") != title:
                raise ValueError(f"Conflicting source page identity: {path}")
            if url in page_urls or path in page_paths:
                raise ValueError(f"Duplicate source page identity: {path}")
            page_urls.add(url)
            page_paths.add(path)
            pages.append(WikiPage(url, path, title))
        elif kind == "asset":
            continue
        elif document["history"] and kind in {"revision", "generated-index"}:
            # History is reconstructed from a fresh API enumeration; the old
            # index cannot be copied because titles, aliases or coverage may
            # have changed.
            continue
        else:
            # An unknown member could be an unidentified HTML page whose
            # revisions would otherwise be skipped; refuse incomplete coverage.
            raise ValueError(
                f"Cannot establish history coverage for {path} ({kind}); "
                "review the source identity before upgrading"
            )
    if not pages:
        raise ValueError("Source inventory contains no identified current wiki pages")
    return tuple(sorted(pages, key=lambda page: page.path))


def _parent_revision_records(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Recover verified oldids with their original page associations."""
    if not document["history"]:
        return {}
    revisions: dict[int, dict[str, Any]] = {}
    for member in document["members"]:
        if member["kind"] != "revision":
            continue
        oldid = member["revision"]["oldid"]
        if oldid in revisions:
            raise ValueError(f"Duplicate parent oldid: {oldid}")
        revisions[oldid] = member
    if not revisions:
        raise ValueError("History parent contains no indexed revisions")
    return revisions


def _publish_verified_inventory(
    document: dict[str, Any], archive: Path, manifest: Path, directory: Path,
) -> Path:
    """Validate the entire triple before claiming a new immutable sidecar."""
    target = directory / f"{archive.stem}.inventory.json"
    directory.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError(f"Snapshot inventory already exists: {target}")
    temporary = target.with_name(f"{target.name}.tmp")
    payload = (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(payload)
        verify_snapshot(archive, manifest, temporary)
        os.link(temporary, target)
    except FileExistsError as exc:
        raise ValueError(f"Snapshot inventory already exists or is staging: {target}") from exc
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return target


def upgrade_snapshot_with_history(
    source_archive: Path,
    source_manifest: Path,
    source_inventory: Path,
    *,
    archive_directory: Path,
    manifest_directory: Path,
    work_root: Path,
    now: datetime | None = None,
    expected_site: str | None = None,
    expected_language: str | None = None,
    progress: Any = None,
) -> SnapshotUpgradeResult:
    """Create a separate, fully verified history snapshot from a checked parent.

    Current-page and asset bytes/metadata are kept unchanged. New revision
    history is enumerated from the live site (callers/test fixtures supply the
    ordinary downloader). No revision-date cutoff is supported: the snapshot
    reflects all revisions observed during the new history enumeration.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.history_checkpoint import HISTORY_CHECKPOINT_FILENAME

    parent = verify_snapshot(source_archive, source_manifest, source_inventory)
    site_key = parent["site"]
    language = parent["language"]
    if expected_site is not None and site_key != expected_site:
        raise ValueError(f"Source snapshot site mismatch: expected {expected_site}, got {site_key}")
    if expected_language is not None and language != expected_language:
        raise ValueError(
            f"Source snapshot language mismatch: expected {expected_language}, got {language}"
        )
    site = cli.WIKI_SITES[site_key]
    pages = _reusable_parent_pages(parent)
    for page in pages:
        if not cli.wiki_page_matches_language(page.url, language, site=site):
            raise ValueError(
                f"Source page URL does not match snapshot site/language: {page.url}"
            )
    root_url = site.root_urls[language]

    started = now or datetime.now().astimezone()
    work = cli.create_wiki_work_directory(
        work_root, site=site, language=language, now=started,
        include_history=True,
    )
    new_archive: Path | None = None
    new_manifest: Path | None = None
    new_inventory: Path | None = None
    try:
        # Copy only the parent current pages/assets. Oldids are validated
        # against the newly enumerated API identity *before* each reuse.
        members: list[Path] = []
        parent_revisions = _parent_revision_records(parent)
        reused_oldids: set[int] = set()
        with zipfile.ZipFile(source_archive) as source:
            for member in parent["members"]:
                if member["kind"] not in {"current-page", "asset"}:
                    continue
                relative = member["path"]
                target = work.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(relative) as contents, target.open("xb") as output:
                    shutil.copyfileobj(contents, output)
                members.append(target)

            def reuse_oldid(page: Any, title: str, oldid: int,
                            parentid: int, timestamp: str) -> bytes | None:
                record = parent_revisions.get(oldid)
                if record is None:
                    return None
                old_revision = record["revision"]
                if (old_revision["parentid"], old_revision["timestamp"]) != (
                    parentid, timestamp,
                ):
                    raise ValueError(f"Contradictory historical identity for oldid {oldid}")
                # A renamed canonical title can still identify the same oldid
                # when the requested page URL remains an archived association.
                # Different URLs are NOT automatically treated as aliases.
                if page.url not in {entry["url"] for entry in old_revision["pages"]}:
                    return None
                payload = source.read(record["path"])
                if (len(payload) != record["size"] or
                        hashlib.sha256(payload).hexdigest() != record["sha256"]):
                    raise ValueError(f"Source oldid bytes changed: {oldid}")
                reused_oldids.add(oldid)
                return payload

            if progress is None:
                progress = cli.ConsoleHistoryProgressReporter()
            try:
                history = cli.download_wiki_history(
                    pages, work, site=site, language=language,
                    checkpoint_file=work / HISTORY_CHECKPOINT_FILENAME,
                    progress=progress, reuse_oldid=reuse_oldid,
                )
            finally:
                if hasattr(progress, "finish"):
                    progress.finish()
        if history.failures:
            raise SnapshotUpgradeIncomplete(work, history.failures)
        if parent_revisions:
            present_oldids = {
                int(path.stem)
                for path in history.files
                if path.parent.name == "oldid"
                and path.parent.parent.name == "_history"
            }
            missing = set(parent_revisions) - present_oldids
            if missing:
                raise ValueError(
                    "Previously archived oldids absent from newly enumerated "
                    f"history: {sorted(missing)[:10]}; review redirects/moves "
                    "or revision visibility before publishing"
                )
            # Revision IDs are globally unique on one Wiki, but sharing an
            # oldid is not proof that an earlier *page association* survived.
            # Check every parent (requested page URL, oldid) pair explicitly.
            new_index = json.loads((work / "_history/index.json").read_text(encoding="utf-8"))
            fresh_associations = {
                (page["currentUrl"], revision["oldid"])
                for page in new_index["pages"]
                for revision in page["revisions"]
            }
            parent_associations = {
                (page["url"], oldid)
                for oldid, record in parent_revisions.items()
                for page in record["revision"]["pages"]
            }
            vanished = parent_associations - fresh_associations
            if vanished:
                raise ValueError(
                    "Previously archived page/oldid associations disappeared: "
                    f"{sorted(vanished)[:5]}; review aliases and moves before publishing"
                )

        history_retrieved_at = now or datetime.now().astimezone()
        members.extend(history.files)
        # `acquiredAt` in the new manifest is the history snapshot's build
        # time, not a claim the inherited current HTML was freshly fetched.
        acquired_at = now or datetime.now().astimezone()
        new_archive = cli.archive_wiki(
            members, archive_directory, root=work, site=site,
            language=language, include_history=True, now=acquired_at,
        )
        new_manifest = write_snapshot_manifest(
            new_archive, manifest_directory, snapshot_type="wiki",
            acquired_at=acquired_at, source_url=root_url,
            document_count=len(members), project_root=Path.cwd(),
            language=language, input_artifact=source_archive, require_new=True,
        )
        inventory_document = build_snapshot_inventory(
            new_archive, new_manifest, work=work, site=site_key,
            language=language, root_url=root_url, include_history=True,
            pages=pages, crawl_checkpoint=None,
        )
        inherited = {
            item["path"]: item for item in parent["members"]
            if item["kind"] in {"current-page", "asset"}
        }
        parent_reused_oldids = {
            item["path"]: item for item in parent["members"]
            if item["kind"] == "revision"
            and item["revision"]["oldid"] in reused_oldids
        }
        page_ids_by_url = {
            item["page"]["url"]: item["sourceIdentity"].get("pageId")
            for item in parent["members"] if item["kind"] == "current-page"
        }
        for index, member in enumerate(inventory_document["members"]):
            if member["path"] in inherited:
                previous = inherited[member["path"]]
                if (member["sha256"] != previous["sha256"]
                        or member["size"] != previous["size"]):
                    raise ValueError(f"Reused source content changed: {member['path']}")
                inventory_document["members"][index] = copy.deepcopy(previous)
            elif member["kind"] == "revision":
                if member["path"] in parent_reused_oldids:
                    old = parent_reused_oldids[member["path"]]
                    if member["sha256"] != old["sha256"] or member["size"] != old["size"]:
                        raise ValueError(f"Reused revision content changed: {member['path']}")
                # Metadata is deliberately rebuilt from the refreshed history
                # index, not copied from old aliases or canonical titles.
                # Carry forward known page IDs for the stable request URLs.
                for item in member["sourceIdentity"]["pageIds"]:
                    item["pageId"] = page_ids_by_url.get(item["url"])

        inventory_document["ignoredUrls"] = copy.deepcopy(parent["ignoredUrls"])
        parent_capture = copy.deepcopy(parent["captureMetadata"])
        if "updateMode" in parent_capture:
            # The already verified parent update's change/reuse accounting
            # describes *that* acquisition, not this new history enumeration.
            # Retain it as provenance, not as a new update-mode assertion.
            inherited_capture = {
                key: parent_capture[key]
                for key in (
                    "httpHeadersAvailable", "mediaWikiPageIdsAvailable",
                    "currentRevisionEvidence", "pageRelationshipEvidence",
                )
                if key in parent_capture
            }
            inherited_capture["inheritedCurrentUpdateEvidence"] = parent_capture
        else:
            inherited_capture = parent_capture
        inventory_document["captureMetadata"] = {
            **inherited_capture,
            "currentContentAcquiredAt": parent["captureMetadata"].get(
                "currentContentAcquiredAt", parent["snapshot"]["acquiredAt"],
            ),
            "historyRetrievedAt": history_retrieved_at.isoformat(timespec="seconds"),
            "historyCutoff": "all-observed",
            "parentHistory": parent["history"],
            "reusedRevisionCount": len(reused_oldids),
        }
        inventory_document["parent"] = {
            "archiveName": source_archive.name,
            "archiveSha256": parent["snapshot"]["archiveSha256"],
            "contentSha256": parent["snapshot"]["contentSha256"],
            "manifestSha256": sha256_file(source_manifest),
            "inventorySha256": sha256_file(source_inventory),
            "acquiredAt": parent["snapshot"]["acquiredAt"],
            "reusedMemberCount": len(inherited) + len(reused_oldids),
        }
        new_inventory = _publish_verified_inventory(
            inventory_document, new_archive, new_manifest,
            manifest_directory / "inventories",
        )
    except BaseException:
        if new_inventory is not None:
            new_inventory.unlink(missing_ok=True)
        if new_manifest is not None:
            new_manifest.unlink(missing_ok=True)
        if new_archive is not None:
            new_archive.unlink(missing_ok=True)
        # A failed run retains its work and history checkpoint. Independent
        # archive-derived resume/reuse is planned for P2.5.
        raise

    try:
        shutil.rmtree(work)
    except OSError:
        pass  # The published output is complete; leftover work is inspectable.
    return SnapshotUpgradeResult(
        new_archive, new_manifest, new_inventory,
        source_archive, len(inherited) + len(reused_oldids),
        history.revision_count, len(reused_oldids),
    )
