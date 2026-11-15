"""Conservative P2.3 archive-derived refresh of current Wiki snapshots.

A verified parent is used as a byte-level comparison baseline, never as
unverified evidence that remote content has remained unchanged. Every source
member in the new output is freshly crawled and checksummed. Identical bytes
are copied from the parent ZIP to establish exact reuse. This first stage
does not claim to avoid HTTP downloads; source-version based skipping is
blocked until authoritative page/asset version baselines exist.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, read_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import sha256_file, write_snapshot_manifest
from infinity_wiki_acquisition.snapshot_upgrade import _publish_verified_inventory
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot


class SnapshotUpdateIncomplete(ValueError):
    """A required source failed; work remains available for inspection."""

    def __init__(self, work: Path, failures: tuple[Any, ...]) -> None:
        self.work = work
        self.failures = failures
        super().__init__(
            f"Wiki update incomplete: {len(failures)} failed source URL(s); "
            f"preserved work -> {work}"
        )


class SnapshotUpdateResult(NamedTuple):
    archive: Path
    manifest: Path
    inventory: Path
    reused_members: int
    changed_members: int
    new_members: int
    removed_members: int
    moved_pages: int


def _parent_sources(parent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Require a classified source set; avoid interpreting unknown members."""
    if parent["history"]:
        raise ValueError(
            "--update-from currently accepts non-history parents only; "
            "history-preserving updates need explicit reconciliation"
        )
    if parent["captureMetadata"].get("legacyIndexed") is True:
        raise ValueError(
            "--update-from needs original source URL evidence; "
            "a legacy-reconstructed inventory is insufficient"
        )
    known: dict[str, dict[str, Any]] = {}
    for member in parent["members"]:
        if member["kind"] not in {"current-page", "asset"}:
            raise ValueError(
                f"Unclassified member cannot be updated safely: {member['path']}"
            )
        for url in member["sourceUrls"]:
            previous = known.setdefault(url, member)
            if previous is not member:
                raise ValueError(f"Parent URL maps to different members: {url}")
    if not any(member["kind"] == "current-page" for member in parent["members"]):
        raise ValueError("Parent inventory does not identify any current pages")
    return known


def scan_recent_changes(
    site: Any, language: str, *, since: datetime, until: datetime,
    max_records: int = 100000,
) -> dict[str, Any]:
    """Enumerate RecentChanges as evidence, not a proof of retention coverage.

    Even an exhausted continuation is not proof that the source retains all
    changes back to the requested start. Never use this log to skip content
    fetching or to declare a page deleted without independent discovery.
    """
    from infinity_wiki_acquisition import cli

    if (since.tzinfo is None or until.tzinfo is None
            or since.utcoffset() is None or until.utcoffset() is None
            or since > until):
        raise ValueError("RecentChanges window must have ordered aware timestamps")
    start = until.astimezone(timezone.utc).isoformat(timespec="seconds")
    end = since.astimezone(timezone.utc).isoformat(timespec="seconds")
    token: str | None = None
    seen_tokens: set[str] = set()
    events: list[dict[str, Any]] = []
    ids: set[int] = set()
    while True:
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "list": "recentchanges", "rcprop": "title|ids|timestamp|loginfo|flags",
            "rcstart": start, "rcend": end,
            "rcdir": "older", "rclimit": "max",
        }
        if token is not None:
            params["rccontinue"] = token
        url = f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"
        payload = cli.fetch_site_bytes(url, language=language, site=site)
        try:
            response = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid MediaWiki RecentChanges JSON") from exc
        if not isinstance(response, dict) or "errors" in response:
            raise ValueError("MediaWiki RecentChanges request failed")
        query = response.get("query")
        batch = query.get("recentchanges") if isinstance(query, dict) else None
        if not isinstance(batch, list):
            raise ValueError("MediaWiki RecentChanges response is incomplete")
        for change in batch:
            if not isinstance(change, dict):
                raise ValueError("Invalid MediaWiki RecentChanges record")
            rcid = change.get("rcid")
            title = change.get("title")
            changed_at = change.get("timestamp")
            kind = change.get("type")
            if (type(rcid) is not int or rcid <= 0 or rcid in ids
                    or not isinstance(title, str)
                    or not title or not isinstance(changed_at, str)
                    or kind not in {"edit", "new", "log", "categorize", "external"}):
                raise ValueError("Invalid or duplicate RecentChanges identity")
            try:
                event_time = datetime.fromisoformat(changed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Invalid RecentChanges event timestamp") from exc
            if (event_time.tzinfo is None or event_time.utcoffset() is None
                    or not since <= event_time <= until):
                raise ValueError("RecentChanges record is outside the requested window")
            ids.add(rcid)
            event: dict[str, Any] = {
                "rcid": rcid, "type": kind,
                "title": title, "timestamp": changed_at,
            }
            for key in ("pageid", "revid", "old_revid"):
                value = change.get(key)
                if value is not None:
                    if type(value) is not int or value < 0:
                        raise ValueError(f"Invalid RecentChanges {key}")
                    event[key] = value
            if kind == "log":
                logtype = change.get("logtype")
                if not isinstance(logtype, str) or not logtype:
                    raise ValueError("Invalid RecentChanges log type")
                event["logtype"] = logtype
                action = change.get("logaction")
                if isinstance(action, str):
                    event["logaction"] = action
                parameters = change.get("logparams")
                if isinstance(parameters, dict):
                    target_title = parameters.get("target_title")
                    if isinstance(target_title, str) and target_title:
                        event["targetTitle"] = target_title
            events.append(event)
            if len(events) > max_records:
                raise ValueError(
                    f"RecentChanges exceeds {max_records} records; "
                    "cannot retain a bounded audit trail"
                )
        cont = response.get("continue")
        if cont is None:
            break
        next_token = cont.get("rccontinue") if isinstance(cont, dict) else None
        if (not isinstance(next_token, str) or not next_token
                or next_token in seen_tokens):
            raise ValueError("Invalid MediaWiki RecentChanges continuation")
        seen_tokens.add(next_token)
        token = next_token
    return {
        "requestedFrom": end,
        "requestedThrough": start,
        "retentionCoverage": "unverified",
        "observedCount": len(events),
        "events": events,
    }


def _page_changes(
    parent: dict[str, Any], updated: dict[str, Any],
    recent_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare allpages IDs; annotate moves only when a log corroborates them."""
    def id_map(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
        ids: dict[int, dict[str, Any]] = {}
        for member in document["members"]:
            if member["kind"] != "current-page":
                continue
            page_id = member["sourceIdentity"].get("pageId")
            if page_id is None:
                continue
            if page_id in ids and ids[page_id]["page"]["url"] != member["page"]["url"]:
                raise ValueError(f"Multiple current pages claim MediaWiki ID {page_id}")
            ids[page_id] = member
        return ids

    before = id_map(parent)
    after = id_map(updated)
    moved = []
    disappeared = []
    for page_id, member in sorted(before.items()):
        current = after.get(page_id)
        if current is None:
            disappeared.append({
                "pageId": page_id,
                "oldUrl": member["page"]["url"],
                "oldPath": member["path"],
                "status": "absent-from-current-enumeration",
            })
        elif (current["page"]["url"], current["path"]) != (
            member["page"]["url"], member["path"],
        ):
            def normalize(title: str) -> str:
                return title.replace("_", " ").casefold()

            corroborated = any(
                event.get("type") == "log"
                and event.get("logtype") == "move"
                and event.get("pageid") == page_id
                and normalize(event.get("title", "")) == normalize(member["page"]["title"])
                and normalize(event.get("targetTitle", "")) == normalize(current["page"]["title"])
                for event in recent_events
            )
            moved.append({
                "pageId": page_id,
                "oldUrl": member["page"]["url"],
                "newUrl": current["page"]["url"],
                "oldPath": member["path"],
                "newPath": current["path"],
                "interpretation": (
                    "move-corroborated-by-recentchanges"
                    if corroborated else "unresolved-url-association"
                ),
            })
    return moved, disappeared


def update_snapshot(
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
) -> SnapshotUpdateResult:
    """Reacquire current content and publish a new independently verified snapshot.

    This is a conservative baseline. It performs full current-page and asset
    fetches rather than silently trusting absent revision/upload metadata.
    Allpages enumeration is forced to discover current main-namespace titles
    and compare recorded numeric page identities.
    """
    from infinity_wiki_acquisition import cli

    parent = verify_snapshot(source_archive, source_manifest, source_inventory)
    previous_by_url = _parent_sources(parent)
    site = cli.WIKI_SITES[parent["site"]]
    language = parent["language"]
    for member in parent["members"]:
        if (member["kind"] == "current-page"
                and not cli.wiki_page_matches_language(
                    member["page"]["url"], language, site=site,
                )):
            raise ValueError(
                f"Parent source page is outside the selected site/language: {member['path']}"
            )
    if expected_site is not None and expected_site != site.key:
        raise ValueError(f"Source snapshot site mismatch: {expected_site}")
    if expected_language is not None and expected_language != language:
        raise ValueError(f"Source snapshot language mismatch: {expected_language}")
    acquired_at = now or datetime.now().astimezone()
    parent_at = datetime.fromisoformat(parent["snapshot"]["acquiredAt"])
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None or acquired_at < parent_at:
        raise ValueError("Updated snapshot time must be aware and not precede its parent")

    changes = scan_recent_changes(
        site, language, since=parent_at, until=acquired_at,
    )
    work = cli.create_wiki_work_directory(
        work_root, site=site, language=language, now=acquired_at,
        include_history=False,
    )
    archive: Path | None = None
    manifest: Path | None = None
    inventory: Path | None = None
    try:
        if progress is None:
            progress = cli.ConsoleProgressReporter()
        try:
            fresh = cli.download_wiki(
                site.root_urls[language], work, site=site, language=language,
                checkpoint_file=work / CHECKPOINT_FILENAME,
                progress=progress, force_allpages=True,
            )
        finally:
            if hasattr(progress, "finish"):
                progress.finish()
        if fresh.failures:
            raise SnapshotUpdateIncomplete(work, fresh.failures)
        if not fresh.files or not fresh.pages:
            raise ValueError("Current source discovery did not produce a complete page set")

        state = read_crawl_checkpoint(
            work / CHECKPOINT_FILENAME, work=work, site=site.key,
            language=language, root_url=cli.canonical_crawl_url(
                site.root_urls[language], site=site,
            ),
        )
        if state["failed"]:
            raise ValueError("Updated crawl contains unresolved failures")
        if (any(
            item["kind"] == "current-page"
            and item["sourceIdentity"].get("pageId") is not None
            for item in parent["members"]
        ) and not state["pageIds"]):
            raise ValueError(
                "MediaWiki allpages did not provide page IDs for an identity-based update"
            )
        # Whole-state fresh fetch has independently verified bytes for each
        # URL. Reuse exact parent bytes *only after* hashing fresh content.
        reused_paths: set[str] = set()
        with zipfile.ZipFile(source_archive) as previous_zip:
            for url, saved in state["saved"].items():
                old = previous_by_url.get(url)
                if old is None or old["path"] != saved[0]:
                    continue
                target = work.joinpath(*saved[0].split("/"))
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != old["sha256"] or target.stat().st_size != old["size"]:
                    continue
                if saved[0] not in reused_paths:
                    # This copies from the immutable parent rather than
                    # relying on a filename or cached filesystem artifact.
                    target.write_bytes(previous_zip.read(old["path"]))
                    reused_paths.add(saved[0])

        files = list(fresh.files)
        archive = cli.archive_wiki(
            files, archive_directory, root=work, site=site,
            language=language, now=acquired_at,
        )
        manifest = write_snapshot_manifest(
            archive, manifest_directory, snapshot_type="wiki",
            acquired_at=acquired_at, source_url=site.root_urls[language],
            document_count=len(files), project_root=Path.cwd(),
            language=language, input_artifact=source_archive, require_new=True,
        )
        updated = build_snapshot_inventory(
            archive, manifest, work=work, site=site.key,
            language=language, root_url=site.root_urls[language],
            include_history=False, pages=fresh.pages,
            crawl_checkpoint=work / CHECKPOINT_FILENAME,
        )
        old_members_by_path = {
            member["path"]: member for member in parent["members"]
        }
        old_paths = set(old_members_by_path)
        new_paths = {item["path"] for item in updated["members"]}
        matched = reused_paths & new_paths
        changed = (new_paths & old_paths) - matched
        added = new_paths - old_paths
        removed = old_paths - new_paths
        moved, absent = _page_changes(parent, updated, changes["events"])
        updated["captureMetadata"] = {
            **updated["captureMetadata"],
            "updateMode": "verified-full-source-refresh",
            "sourceVersionSkipping": False,
            "pageRevisionComparison": "unavailable-without-authoritative-parent-baseline",
            "assetVersionComparison": "content-hash-after-download",
            "matchedParentMemberCount": len(matched),
            "changedMemberCount": len(changed),
            "newMemberCount": len(added),
            "removedMemberCount": len(removed),
            "pageMovesById": moved,
            "parentPageIdsAbsentFromEnumeration": absent,
            "recentChanges": changes,
        }
        updated["parent"] = {
            "archiveName": source_archive.name,
            "archiveSha256": parent["snapshot"]["archiveSha256"],
            "contentSha256": parent["snapshot"]["contentSha256"],
            "manifestSha256": sha256_file(source_manifest),
            "inventorySha256": sha256_file(source_inventory),
            "acquiredAt": parent["snapshot"]["acquiredAt"],
            "reusedMemberCount": len(matched),
        }
        inventory = _publish_verified_inventory(
            updated, archive, manifest, manifest_directory / "inventories",
        )
    except BaseException:
        if inventory is not None:
            inventory.unlink(missing_ok=True)
        if manifest is not None:
            manifest.unlink(missing_ok=True)
        if archive is not None:
            archive.unlink(missing_ok=True)
        raise

    try:
        shutil.rmtree(work)
    except OSError:
        pass
    return SnapshotUpdateResult(
        archive, manifest, inventory, len(matched), len(changed),
        len(added), len(removed), len(moved),
    )
