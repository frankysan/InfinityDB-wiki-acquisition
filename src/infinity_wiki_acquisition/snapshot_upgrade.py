"""P2.1: upgrade a verified non-history Wiki snapshot with revision history.

This operation uses the *current content bytes* and reviewed identities from
the parent inventory; it does not crawl current pages again. It acquires all
revisions advertised during the new history enumeration (no date cutoff).
Only the newly created artifacts can be rolled back.
"""

from __future__ import annotations

import copy
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

    if document["history"]:
        raise ValueError("P2.1 accepts only non-history source snapshots (history reuse is P2.2)")

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
        elif kind != "asset":
            # An unknown member could be an unidentified HTML page whose
            # revisions would otherwise be skipped; refuse incomplete coverage.
            raise ValueError(
                f"Cannot establish history coverage for {path} ({kind}); "
                "review the source identity before upgrading"
            )
    if not pages:
        raise ValueError("Source inventory contains no identified current wiki pages")
    return tuple(sorted(pages, key=lambda page: page.path))


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
        # Verification has already established portable, unique, regular
        # members. Copy each member from the verified container, never extract
        # directories or trust a filesystem path originating in the archive.
        members: list[Path] = []
        with zipfile.ZipFile(source_archive) as source:
            for member in parent["members"]:
                relative = member["path"]
                target = work.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(relative) as contents, target.open("xb") as output:
                    shutil.copyfileobj(contents, output)
                members.append(target)

        if progress is None:
            progress = cli.ConsoleHistoryProgressReporter()
        try:
            history = cli.download_wiki_history(
                pages, work, site=site, language=language,
                checkpoint_file=work / HISTORY_CHECKPOINT_FILENAME,
                progress=progress,
            )
        finally:
            if hasattr(progress, "finish"):
                progress.finish()
        if history.failures:
            raise SnapshotUpgradeIncomplete(work, history.failures)

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
        inherited = {item["path"]: item for item in parent["members"]}
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
                # Carry forward enumerated page IDs when already known.
                for item in member["sourceIdentity"]["pageIds"]:
                    item["pageId"] = page_ids_by_url.get(item["url"])

        inventory_document["ignoredUrls"] = copy.deepcopy(parent["ignoredUrls"])
        inventory_document["captureMetadata"] = {
            **copy.deepcopy(parent["captureMetadata"]),
            "currentContentAcquiredAt": parent["snapshot"]["acquiredAt"],
            "historyRetrievedAt": history_retrieved_at.isoformat(timespec="seconds"),
            "historyCutoff": "all-observed",
        }
        inventory_document["parent"] = {
            "archiveName": source_archive.name,
            "archiveSha256": parent["snapshot"]["archiveSha256"],
            "contentSha256": parent["snapshot"]["contentSha256"],
            "manifestSha256": sha256_file(source_manifest),
            "inventorySha256": sha256_file(source_inventory),
            "acquiredAt": parent["snapshot"]["acquiredAt"],
            "reusedMemberCount": len(inherited),
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
        source_archive, len(inherited), history.revision_count,
    )
