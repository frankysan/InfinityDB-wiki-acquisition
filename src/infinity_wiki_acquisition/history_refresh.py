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
from infinity_wiki_acquisition.historical_only import (
    archived_former_pages, build_historical_only_index, read_history_index,
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
    allow_historical_only: bool = False,
) -> tuple[Any, set[int], list[dict[str, Any]], list[Path], list[dict[str, Any]]]:
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

    source_to_fresh: dict[str, str | None] = {}
    mappings: list[dict[str, Any]] = []
    retired_current: list[dict[str, Any]] = []
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
            if not allow_historical_only:
                raise ValueError(
                    f"Historical page ID {old_id} ({old_url}) is missing from "
                    "fresh discovery; use --allow-historical-only to preserve "
                    "the unverified absence with a versioned archival index"
                )
            retired_current.append(old)
            source_to_fresh[old_url] = None
            mappings.append({
                "pageId": old_id, "previousUrl": old_url, "currentUrl": None,
                "identityBasis": "verified-parent-id-absent-from-allpages",
                "changedUrl": True,
            })
            continue
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
            if old_url not in source_to_fresh:
                # Prior historical-only associations live in the v2 index
                # and are preserved without presenting them as current pages.
                continue
            if new_url is None:
                continue
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

    from infinity_wiki_acquisition.archive_run import (
        record_parent_oldid_reuse, restored_parent_oldid_reuse,
    )

    reused_oldids: set[int] = restored_parent_oldid_reuse(work, old_records)
    former: list[dict[str, Any]] = []
    former_files: list[Path] = []
    with zipfile.ZipFile(source_archive) as old_zip:
        parent_index = read_history_index(old_zip)
        if parent_index["formatVersion"] == 2:
            if not allow_historical_only:
                raise ValueError(
                    "Version 2 history parents require --allow-historical-only"
                )
            existing_retired_ids = {
                entry["pageId"] for entry in parent_index["historicalOnlyPages"]
            }
            if any(page_id in fresh_by_id for page_id in existing_retired_ids):
                raise ValueError(
                    "Previously retired page ID reappeared in current discovery; "
                    "reinstatement requires explicit source identity review"
                )
        if allow_historical_only:
            former, former_files, retained_oldids = archived_former_pages(
                parent, source_archive, retired_current, work,
            )
        else:
            retained_oldids = set()
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
            record_parent_oldid_reuse(work, oldid, old["sha256"])
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
    if missing - retained_oldids:
        raise ValueError(
            f"Previously archived oldids absent from fresh history: "
            f"{sorted(missing - retained_oldids)[:10]}"
        )
    retired_links = {
        (page["formerUrl"], revision["oldid"])
        for page in former for revision in page["revisions"]
    }
    for oldid, old in old_records.items():
        prior = old["revision"]
        if oldid in fresh_records and fresh_records[oldid] != (
            prior["parentid"], prior["timestamp"],
        ):
            raise ValueError(
                f"Oldid {oldid} changed parent/timestamp in fresh history"
            )
        for page in prior["pages"]:
            old_url = page["url"]
            mapped = source_to_fresh.get(old_url)
            if (mapped, oldid) not in fresh_links and (
                old_url, oldid
            ) not in retired_links:
                raise ValueError(
                    "Historical page/oldid association lost: "
                    f"{old_url} -> {mapped}, oldid {oldid}"
                )
    if former:
        # Missing oldids belong only to archived former pages, and their
        # previously verified bytes are restored before publication.
        with zipfile.ZipFile(source_archive) as old_zip:
            for oldid in sorted(missing):
                prior = old_records[oldid]
                relative = prior["path"]
                target = work.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                contents = old_zip.read(relative)
                if len(contents) != prior["size"] or hashlib.sha256(
                    contents,
                ).hexdigest() != prior["sha256"]:
                    raise ValueError(f"Historical oldid {oldid} bytes changed")
                if target.exists() or target.is_symlink():
                    if (target.is_symlink() or not target.is_file()
                            or target.read_bytes() != contents):
                        raise ValueError(
                            "Saved historical-only oldid differs from verified parent"
                        )
                else:
                    target.write_bytes(contents)
                reused_oldids.add(oldid)
        index = build_historical_only_index(index, former)
        target_index = work / "_history/index.json"
        target_index.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n", encoding="utf-8",
        )
        additional = [
            work / old_records[oldid]["path"] for oldid in missing
        ]
        history = cli.WikiHistoryResult(
            files=tuple(sorted(
                set(history.files) | set(additional),
                key=lambda path: cli.mirror_path_sort_key(path, work),
            )),
            failures=history.failures,
            revision_count=index["revisionCount"],
        )
    return history, reused_oldids, mappings, former_files, former
