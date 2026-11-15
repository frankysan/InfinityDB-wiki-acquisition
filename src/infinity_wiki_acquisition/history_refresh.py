"""P2.3c: reconcile a verified history parent with freshly crawled pages.

Historical oldid bytes remain immutable. A page move is established by the
same numeric MediaWiki page ID in the parent and fresh allpages results;
matching old URLs/titles alone never prove the link. A missing old page ID
cannot be represented as a current-page member and blocks publication.

Rebuild the history index after full revision enumeration. Do not carry
forward stale canonical titles or silently drop archived oldid associations.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_upgrade import (
    _parent_revision_records, SnapshotUpgradeIncomplete,
)


def refresh_history_from_parent(
    parent: dict[str, Any],
    source_archive: Path,
    pages: tuple[Any, ...],
    page_ids: dict[str, int],
    work: Path,
    *,
    site: Any,
    language: str,
    progress: Any = None,
) -> tuple[Any, set[int], list[dict[str, Any]]]:
    """Return new complete history, oldid reuse identities, URL remapping.

    A current page absent from fresh main-namespace discovery might have
    moved, been deleted or become invisible. If its page ID cannot be
    re-established, refuse an apparently complete refreshed history archive.
    The separate historical-only/tombstone representation remains future work.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.history_checkpoint import HISTORY_CHECKPOINT_FILENAME

    if not parent["history"]:
        raise ValueError("A history parent is required for historical refresh")
    old_records = _parent_revision_records(parent)
    current_pages = [member for member in parent["members"]
                     if member["kind"] == "current-page"]
    fresh_by_url = {page.url: page for page in pages}
    fresh_by_id: dict[int, list[Any]] = {}
    for page in pages:
        page_id = page_ids.get(page.url)
        if type(page_id) is int and page_id > 0:
            fresh_by_id.setdefault(page_id, []).append(page)

    source_to_fresh: dict[str, str] = {}
    mappings: list[dict[str, Any]] = []
    for old in current_pages:
        old_url = old["page"]["url"]
        old_id = old["sourceIdentity"].get("pageId")
        if type(old_id) is not int or old_id <= 0:
            raise ValueError(
                "Cannot preserve historical page association without a "
                f"verified prior MediaWiki page ID: {old_url}"
            )
        matches = fresh_by_id.get(old_id, [])
        stable_url = fresh_by_url.get(old_url)
        if stable_url in matches:
            chosen = stable_url
        elif len(matches) == 1:
            chosen = matches[0]
        elif not matches:
            raise ValueError(
                f"Historical page ID {old_id} ({old_url}) is missing from "
                "fresh discovery; a deleted, moved or inaccessible page "
                "cannot be silently omitted from history"
            )
        else:
            raise ValueError(
                f"Historical page ID {old_id} ({old_url}) maps to multiple "
                "fresh aliases; explicit reconciliation is required"
            )
        source_to_fresh[old_url] = chosen.url
        mappings.append({
            "pageId": old_id,
            "previousUrl": old_url,
            "currentUrl": chosen.url,
            "identityBasis": "verified-allpages-page-id",
            "changedUrl": old_url != chosen.url,
        })
    mappings.sort(key=lambda entry: entry["previousUrl"])

    # Historical associations must already have a traceable parent page ID.
    allowed: dict[int, set[str]] = {}
    for oldid, record in old_records.items():
        for prior in record["revision"]["pages"]:
            old_url = prior["url"]
            new_url = source_to_fresh.get(old_url)
            if new_url is None:
                raise ValueError(
                    f"Unmapped oldid {oldid} historical association: {old_url}"
                )
            expected_id = next(
                row["pageId"] for row in mappings
                if row["previousUrl"] == old_url
            )
            recorded_id = next(
                (row["pageId"]
                 for row in record["sourceIdentity"]["pageIds"]
                 if row["url"] == old_url),
                None,
            )
            if recorded_id not in (None, expected_id):
                raise ValueError(
                    f"Parent oldid {oldid} disagrees about page ID for {old_url}"
                )
            allowed.setdefault(oldid, set()).add(new_url)

    reused_oldids: set[int] = set()
    with zipfile.ZipFile(source_archive) as old_zip:
        def reuse_oldid(
            page: Any, title: str, oldid: int, parentid: int,
            timestamp: str,
        ) -> bytes | None:
            old = old_records.get(oldid)
            if old is None:
                return None
            prior = old["revision"]
            if (prior["parentid"], prior["timestamp"]) != (parentid, timestamp):
                raise ValueError(
                    f"Conflicting oldid {oldid} identity in new history"
                )
            if page.url not in allowed.get(oldid, set()):
                # A new page association does not by itself prove this
                # oldid belongs to a previously known page.
                return None
            content = old_zip.read(old["path"])
            if (len(content) != old["size"]
                    or hashlib.sha256(content).hexdigest() != old["sha256"]):
                raise ValueError(f"Reused oldid {oldid} content differs")
            reused_oldids.add(oldid)
            return content

        if progress is None:
            progress = cli.ConsoleHistoryProgressReporter()
        try:
            history = cli.download_wiki_history(
                pages, work, language=language, site=site,
                checkpoint_file=work / HISTORY_CHECKPOINT_FILENAME,
                progress=progress, reuse_oldid=reuse_oldid,
            )
        finally:
            if hasattr(progress, "finish"):
                progress.finish()

    if history.failures:
        raise SnapshotUpgradeIncomplete(work, history.failures)
    index = json.loads(
        (work / "_history" / "index.json").read_text(encoding="utf-8"),
    )
    fresh_records: dict[int, tuple[int, str]] = {}
    fresh_links: set[tuple[str, int]] = set()
    for entry in index["pages"]:
        for record in entry["revisions"]:
            oldid = record["oldid"]
            ident = (record["parentid"], record["timestamp"])
            existing = fresh_records.setdefault(oldid, ident)
            if existing != ident:
                raise ValueError(
                    f"Conflicting oldid {oldid} across freshly enumerated pages"
                )
            fresh_links.add((entry["currentUrl"], oldid))
    missing = set(old_records) - set(fresh_records)
    if missing:
        raise ValueError(
            f"Previously archived oldids absent from fresh history: "
            f"{sorted(missing)[:10]}"
        )
    for oldid, old in old_records.items():
        prior = old["revision"]
        if fresh_records[oldid] != (prior["parentid"], prior["timestamp"]):
            raise ValueError(
                f"Oldid {oldid} changed parent/timestamp in fresh history"
            )
        for page in prior["pages"]:
            new_url = source_to_fresh[page["url"]]
            if (new_url, oldid) not in fresh_links:
                raise ValueError(
                    "Historical page/oldid association lost: "
                    f"{page['url']} -> {new_url}, oldid {oldid}"
                )
    return history, reused_oldids, mappings
