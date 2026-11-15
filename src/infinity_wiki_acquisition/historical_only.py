"""Versioned historical-only page records for source snapshots.

A missing page in a fresh allpages enumeration is *not* proof of deletion.
Version 2 archives retain previous current-page bytes and oldid associations
under _history/retired/, while keeping those pages out of the current index.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

HISTORY_INDEX_V2 = 2
HISTORICAL_COVERAGE = "current-all-observed-former-last-archived"
FORMER_STATUS = "absent-from-current-enumeration"
FORMER_REVISION_SCOPE = "last-archived-only"


def historical_page_path(page_id: int, url: str) -> str:
    """Stable, portable archival location for previous current HTML."""
    if type(page_id) is not int or page_id <= 0 or not url:
        raise ValueError("Historical-only source requires a positive page ID and URL")
    token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return f"_history/retired/{page_id}/{token}.html"


def read_history_index(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        document = json.loads(archive.read("_history/index.json"))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise ValueError("Missing or invalid archived history index") from exc
    if (
        not isinstance(document, dict)
        or document.get("format") != "InfinityDB wiki revision history"
        or document.get("formatVersion") not in (1, HISTORY_INDEX_V2)
        or not isinstance(document.get("pages"), list)
    ):
        raise ValueError("Unsupported archived history index")
    if document["formatVersion"] == HISTORY_INDEX_V2:
        if (
            document.get("historyCoverage") != HISTORICAL_COVERAGE
            or not isinstance(document.get("historicalOnlyPages"), list)
            or not document["historicalOnlyPages"]
        ):
            raise ValueError("Incomplete historical-only index v2")
    return document


def oldid_records_for_current_url(
    index: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for page in index["pages"]:
        url = page.get("currentUrl")
        if not isinstance(url, str) or url in result:
            raise ValueError("Duplicate current history URL")
        result[url] = page
    return result


def build_historical_only_index(
    current: dict[str, Any],
    former_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use v2 only when needed; no stale current page is inserted in pages."""
    if not former_pages:
        return current
    if current.get("formatVersion") != 1:
        raise ValueError("Cannot upgrade an unexpected current history index")
    existing = {item["currentUrl"] for item in current["pages"]}
    paths: set[str] = set()
    urls: set[str] = set()
    oldids: set[int] = {
        entry["oldid"]
        for page in current["pages"] for entry in page["revisions"]
    }
    for page in former_pages:
        url = page["formerUrl"]
        path = page["archivedPath"]
        if url in urls or url in existing or path in paths:
            raise ValueError("Historical-only page conflicts with current identity")
        urls.add(url)
        paths.add(path)
        oldids.update(entry["oldid"] for entry in page["revisions"])
    return {
        **current,
        "formatVersion": HISTORY_INDEX_V2,
        "historyCoverage": HISTORICAL_COVERAGE,
        "historicalOnlyPages": sorted(
            former_pages, key=lambda entry: entry["formerUrl"],
        ),
        "revisionCount": len(oldids),
    }


def archived_former_pages(
    parent: dict[str, Any],
    source_archive: Path,
    missing: list[dict[str, Any]],
    work: Path,
) -> tuple[list[dict[str, Any]], list[Path], set[int]]:
    """Copy missing current HTML plus any inherited former-page bytes.

    Revisions are retained separately by the history merger. The original
    source page title/URL and acquisition time are not inferred from paths.
    """
    former: list[dict[str, Any]] = []
    files: list[Path] = []
    oldids: set[int] = set()
    with zipfile.ZipFile(source_archive) as source:
        parent_index = read_history_index(source)
        by_current_url = oldid_records_for_current_url(parent_index)
        prior_former = parent_index.get("historicalOnlyPages", [])
        for entry in prior_former:
            old_member = next(
                (
                    item for item in parent["members"]
                    if item["path"] == entry["archivedPath"]
                    and item["kind"] == "historical-page"
                ),
                None,
            )
            if old_member is None:
                raise ValueError("Prior retired page lacks verified archived bytes")
            contents = source.read(entry["archivedPath"])
            if (len(contents) != old_member["size"]
                    or hashlib.sha256(contents).hexdigest() != old_member["sha256"]):
                raise ValueError("Prior historical-only bytes changed")
            destination = work.joinpath(*entry["archivedPath"].split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                if (destination.is_symlink() or not destination.is_file()
                        or destination.read_bytes() != contents):
                    raise ValueError("Saved historical-only page differs from verified parent")
            else:
                destination.write_bytes(contents)
            files.append(destination)
            former.append(entry)
            oldids.update(revision["oldid"] for revision in entry["revisions"])

        for member in missing:
            url = member["page"]["url"]
            page_id = member["sourceIdentity"]["pageId"]
            if type(page_id) is not int or page_id <= 0:
                raise ValueError(
                    f"Cannot archive a former page without original page ID: {url}"
                )
            existing_page = by_current_url.get(url)
            if existing_page is None or not existing_page.get("revisions"):
                raise ValueError(
                    f"No verified current history association for retired {url}"
                )
            relative = historical_page_path(page_id, url)
            contents = source.read(member["path"])
            if (len(contents) != member["size"]
                    or hashlib.sha256(contents).hexdigest() != member["sha256"]):
                raise ValueError("Parent current-page bytes changed")
            destination = work.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                if (destination.is_symlink() or not destination.is_file()
                        or destination.read_bytes() != contents):
                    raise ValueError("Saved retired current page differs from verified parent")
            else:
                destination.write_bytes(contents)
            files.append(destination)
            observed_at = parent["captureMetadata"].get(
                "currentContentAcquiredAt", parent["snapshot"]["acquiredAt"],
            )
            former.append({
                "pageId": page_id,
                "formerUrl": url,
                "formerTitle": existing_page["title"],
                "lastKnownCurrentTitle": member["page"]["title"],
                "originalPath": member["path"],
                "archivedPath": relative,
                "originalSha256": member["sha256"],
                "lastKnownCurrentAcquiredAt": observed_at,
                "status": FORMER_STATUS,
                "historyScope": FORMER_REVISION_SCOPE,
                "revisions": existing_page["revisions"],
            })
            oldids.update(
                revision["oldid"] for revision in existing_page["revisions"]
            )
    return former, files, oldids
