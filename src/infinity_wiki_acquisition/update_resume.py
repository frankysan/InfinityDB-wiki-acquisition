"""Private P2.5b evidence for restartable archive-derived current refreshes.

The parent-bound run receipt identifies this work directory. This module
persists the already completed RecentChanges scan and one version decision
per asset. Only a checkpointed saved member with matching bytes may recover
its decision; stale/uncommitted records cannot claim successful downloads.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RECENT_FILE = ".wiki-update-recentchanges.json"
ASSET_DIRECTORY = ".wiki-update-asset-decisions"
FORMAT = "InfinityDB wiki current update evidence"
VERSION = 1


def _write(path: Path, doc: dict[str, Any]) -> None:
    if path.is_symlink():
        raise ValueError("Unsafe update checkpoint path")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe update evidence: {path.name}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"Unreadable update evidence: {path.name}") from exc
    if not isinstance(doc, dict):
        raise ValueError("Invalid update evidence object")
    return doc


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Update evidence lacks source time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid update source time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Update source time is not timezone aware")
    return parsed


def write_recent_changes(
    work: Path, changes: dict[str, Any],
    *, parent_at: datetime, acquired_at: datetime,
) -> None:
    from datetime import timezone

    if (changes.get("retentionCoverage") != "unverified"
            or not isinstance(changes.get("events"), list)
            or changes.get("observedCount") != len(changes["events"])):
        raise ValueError("Cannot checkpoint incomplete RecentChanges scan")
    _write(work / RECENT_FILE, {
        "format": FORMAT, "version": VERSION,
        "kind": "recent-changes",
        "parentAt": parent_at.isoformat(),
        "acquiredAt": acquired_at.isoformat(),
        "changes": changes,
    })


def load_recent_changes(
    work: Path, *, parent_at: datetime, acquired_at: datetime,
) -> dict[str, Any]:
    doc = _read(work / RECENT_FILE)
    if (set(doc) != {"format", "version", "kind", "parentAt", "acquiredAt", "changes"}
            or doc["format"] != FORMAT or doc["version"] != VERSION
            or doc["kind"] != "recent-changes"
            or _time(doc["parentAt"]) != parent_at
            or _time(doc["acquiredAt"]) != acquired_at):
        raise ValueError("RecentChanges checkpoint does not match verified update time")
    changes = doc["changes"]
    if (not isinstance(changes, dict)
            or set(changes) != {
                "requestedFrom", "requestedThrough", "retentionCoverage",
                "observedCount", "events",
            }
            or changes["retentionCoverage"] != "unverified"
            or not isinstance(changes["events"], list)
            or type(changes["observedCount"]) is not int
            or changes["observedCount"] != len(changes["events"])
            or _time(changes["requestedFrom"]) != parent_at
            or _time(changes["requestedThrough"]) != acquired_at):
        raise ValueError("Incomplete or inconsistent RecentChanges checkpoint")
    seen = set()
    for event in changes["events"]:
        if (not isinstance(event, dict)
                or type(event.get("rcid")) is not int or event["rcid"] <= 0
                or event["rcid"] in seen
                or not isinstance(event.get("title"), str)
                or not event["title"]
                or event.get("type") not in {
                    "edit", "new", "log", "categorize", "external",
                }
                or not parent_at <= _time(event.get("timestamp")) <= acquired_at):
            raise ValueError("Invalid recorded RecentChanges event")
        seen.add(event["rcid"])
    return changes


def _asset_path(work: Path, url: str) -> Path:
    return work / ASSET_DIRECTORY / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def write_asset_decision(
    work: Path, url: str, path: str, payload: bytes,
    *, upload: dict[str, Any] | None, conditional_reused: bool,
) -> None:
    _write(_asset_path(work, url), {
        "format": FORMAT, "version": VERSION, "kind": "asset",
        "sourceUrl": url, "memberPath": path,
        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
        "uploadVersion": upload,
        "conditionalReused": conditional_reused,
    })


def validate_saved_current_pages(
    work: Path, state: dict[str, Any], *, require_source_replay: bool,
) -> None:
    """Check saved mirror bytes and optional raw source before crawler resume.

    The P0 checkpoint checks saved sizes; this adds source-body provenance
    hashes to detect same-size corruption without refetching remote pages.
    """
    from infinity_wiki_acquisition.page_evidence import read_page_evidence
    from infinity_wiki_acquisition.source_replay import source_body_path

    for page in state["pages"]:
        url, relative = page["url"], page["path"]
        evidence = read_page_evidence(work, url, path=relative)
        response = (
            evidence.get("renderedResponseVersion")
            if isinstance(evidence, dict) else None
        )
        path = work.joinpath(*relative.split("/"))
        if (not isinstance(response, dict)
                or path.is_symlink()
                or not path.is_file()
                or response.get("rewrittenMemberSha256")
                != hashlib.sha256(path.read_bytes()).hexdigest()
                or response.get("originalResponseSha256")
                != evidence["originalSha256"]):
            raise ValueError(f"Saved current HTML lacks matching source evidence: {url}")
        if require_source_replay:
            original = source_body_path(work, url)
            if (original.is_symlink() or not original.is_file()
                    or hashlib.sha256(original.read_bytes()).hexdigest()
                    != evidence["originalSha256"]):
                raise ValueError(f"Saved original HTML replay input missing or altered: {url}")


def validate_postcrawl_observations(
    work: Path, pages: Any, page_ids: dict[str, int],
    *, site: str, language: str,
) -> tuple[bool, bool]:
    """Return completed revision/relationship phases after exact page binding.

    Existing complete observations are not silently overwritten on resume;
    an incorrect page set, altered identity or source HTML timestamp fails.
    """
    from infinity_wiki_acquisition.current_revisions import (
        load_current_revision_evidence,
    )
    from infinity_wiki_acquisition.page_relationships import (
        load_page_relationship_evidence,
    )
    from infinity_wiki_acquisition.page_evidence import read_page_evidence

    revisions = load_current_revision_evidence(work, site=site, language=language)
    relationships = load_page_relationship_evidence(
        work, site=site, language=language,
    )
    expected = {page.url: page for page in pages}
    for records in (revisions, relationships):
        if records is None:
            continue
        if set(records) != set(expected):
            raise ValueError("Resumed page observations differ from completed crawl")
        for url, page in expected.items():
            row = records[url]
            source = read_page_evidence(work, url, path=page.path)
            html_at = source.get("fetchedAt") if source else None
            if (row["memberPath"] != page.path
                    or row["pageId"] != page_ids.get(url)
                    or row["htmlFetchedAt"] != html_at):
                raise ValueError("Resumed page observations have stale source identity")
    return revisions is not None, relationships is not None


def load_saved_asset_decisions(
    work: Path, saved: dict[str, Any],
    discovered: dict[str, bool],
    *, parent_by_url: dict[str, dict[str, Any]],
    site: Any,
) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
    """Validate EVERY saved asset against its body and decision before use."""
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.asset_evidence import read_asset_evidence

    uploads: dict[str, dict[str, Any]] = {}
    conditional: set[str] = set()
    reused_uploads: set[str] = set()
    for url, (relative, size) in saved.items():
        if discovered[url] is not True:
            continue
        doc = _read(_asset_path(work, url))
        target = work.joinpath(*relative.split("/"))
        if (set(doc) != {
                "format", "version", "kind", "sourceUrl", "memberPath",
                "sha256", "size", "uploadVersion", "conditionalReused",
            }
                or doc["format"] != FORMAT or doc["version"] != VERSION
                or doc["kind"] != "asset" or doc["sourceUrl"] != url
                or doc["memberPath"] != relative
                or type(doc["size"]) is not int or doc["size"] != size
                or type(doc["conditionalReused"]) is not bool
                or target.is_symlink() or not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != doc["sha256"]):
            raise ValueError(f"Recorded update asset differs from saved bytes: {url}")
        evidence = read_asset_evidence(work, url, relative, doc["sha256"])
        if doc["conditionalReused"]:
            previous = parent_by_url.get(url)
            version = previous.get("sourceVersion") if previous else None
            if (not isinstance(version, dict)
                    or version.get("sourceUrl") != url
                    or version.get("effectiveUrl") != url
                    or version.get("sha256") != doc["sha256"]
                    or not cli._is_strong_etag(version.get("strongEtag"))
                    or evidence is None
                    or evidence.get("responseStatus") != 304
                    or evidence.get("strongEtag") != version["strongEtag"]
                    or evidence.get("conditionalRevalidated") is not True):
                raise ValueError(f"Unverified resumed conditional asset: {url}")
            conditional.add(url)
        upload = doc["uploadVersion"]
        if upload is not None:
            if (not isinstance(upload, dict)
                    or upload.get("sourceUrl") != url
                    or upload.get("size") != size
                    or not isinstance(upload.get("sha1Hex"), str)
                    or hashlib.sha1(target.read_bytes()).hexdigest() != upload["sha1Hex"]
                    or type(upload.get("usedForReuse")) is not bool):
                raise ValueError(f"Invalid resumed MediaWiki upload identity: {url}")
            if upload["usedForReuse"]:
                previous = parent_by_url.get(url)
                if (previous is None or previous["kind"] != "asset"
                        or previous["sourceUrls"] != [url]
                        or previous["path"] != relative
                        or previous["sha256"] != doc["sha256"]):
                    raise ValueError(f"Unverified resumed upload reuse: {url}")
                reused_uploads.add(url)
            uploads[url] = upload
        if upload is not None and doc["conditionalReused"]:
            raise ValueError("An asset cannot reuse upload and conditional proof together")
    return uploads, conditional, reused_uploads
