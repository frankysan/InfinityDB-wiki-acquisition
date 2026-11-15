"""Resumable, private wiki crawl checkpoints.

The JSON file lives in a work directory, never in published ZIPs.
Version 2 records enumeration progress and pending URLs; version 1 completed
checkpoints remain readable for retry-only use.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

CHECKPOINT_FILENAME = ".wiki-crawl-checkpoint.json"
CHECKPOINT_FORMAT = "InfinityDB wiki crawl checkpoint"
CHECKPOINT_VERSION = 2


def write_crawl_checkpoint(path: Path, document: dict[str, Any]) -> None:
    """Replace the local checkpoint atomically at a consistent work boundary."""
    payload = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def read_crawl_checkpoint(
    path: Path,
    *,
    work: Path,
    site: str,
    language: str,
    root_url: str,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Load a site-matched checkpoint with present, unchanged saved files."""
    if not path.is_file():
        raise ValueError(
            f"No completed crawl checkpoint at {path}; run a normal --resume-work "
            "once to record discovery before using --retry-failed-only"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable wiki crawl checkpoint: {path}") from exc
    if not isinstance(document, dict) or (
        document.get("format") != CHECKPOINT_FORMAT
        or document.get("version") not in (1, CHECKPOINT_VERSION)
        or document.get("site") != site
        or document.get("language") != language
        or document.get("rootUrl") != root_url
    ):
        raise ValueError("Crawl checkpoint is incomplete, incompatible or for another site/language")

    version = document["version"]
    phase = document.get("phase", "complete" if version == 1 else None)
    if (
        phase not in {"enumerating", "crawling", "complete"}
        or document.get("complete") is not (phase == "complete")
        or (require_complete and phase != "complete")
    ):
        raise ValueError("Crawl checkpoint has not completed discovery")
    queue = document.get("queue", [])
    continuation = document.get("enumerationContinuation")
    if version == 2 and not (
        isinstance(queue, list)
        and all(
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and type(item[1]) is bool
            for item in queue
        )
        and (continuation is None or isinstance(continuation, str))
        and (phase == "enumerating" or continuation is None)
        and (phase != "complete" or not queue)
    ):
        raise ValueError("Crawl checkpoint has an invalid pending queue or continuation")
    discovered = document.get("discovered")
    required = document.get("required")
    page_ids = document.get("pageIds", {})
    saved = document.get("saved")
    pages = document.get("pages")
    ignored = document.get("ignored")
    failed = document.get("failed")
    if not (
        isinstance(discovered, dict)
        and isinstance(required, list)
        and isinstance(saved, dict)
        and isinstance(page_ids, dict)
        and isinstance(pages, list)
        and isinstance(ignored, dict)
        and isinstance(failed, dict)
        and all(isinstance(url, str) and type(asset) is bool for url, asset in discovered.items())
        and all(isinstance(url, str) for url in required)
        and all(isinstance(url, str) and isinstance(error, str) for url, error in failed.items())
        and all(isinstance(url, str) and isinstance(reason, str) for url, reason in ignored.items())
        and set(required) <= set(discovered)
        and all(
            isinstance(url, str)
            and url in discovered
            and type(page_id) is int
            and page_id > 0
            for url, page_id in page_ids.items()
        )
        and (version == 1 or all(
            url in discovered and discovered[url] is asset
            for url, asset in queue
        ))
        and set(saved) <= set(discovered)
        and set(failed) <= set(discovered)
        and not (set(saved) & set(failed))
        and not (set(saved) & set(ignored))
        and (version == 1 or not (set(saved) & {url for url, _ in queue}))
    ):
        raise ValueError("Crawl checkpoint contains invalid or inconsistent URL records")

    # Validate only recorded sizes and paths. This is cheap even for 75,000
    # files, while absence or a changed size prevents a false complete snapshot.
    for url, record in saved.items():
        if not (
            isinstance(record, list)
            and len(record) == 2
            and isinstance(record[0], str)
            and type(record[1]) is int
            and record[1] >= 0
        ):
            raise ValueError(f"Invalid saved file in crawl checkpoint: {url}")
        relative, size = record
        logical = PurePosixPath(relative)
        if (
            logical.is_absolute()
            or ".." in logical.parts
            or not logical.parts
            or "\\" in relative
            or relative == CHECKPOINT_FILENAME
        ):
            raise ValueError(f"Unsafe file path in crawl checkpoint: {relative}")
        target = work.joinpath(*logical.parts)
        if not target.is_file() or target.is_symlink() or target.stat().st_size != size:
            raise ValueError(
                f"Saved crawl file missing or changed: {relative}; "
                "run normal --resume-work to reconstruct the snapshot"
            )

    for page in pages:
        if not (
            isinstance(page, dict)
            and isinstance(page.get("url"), str)
            and isinstance(page.get("path"), str)
            and isinstance(page.get("title"), str)
            and page["url"] in saved
            and page["path"] == saved[page["url"]][0]
        ):
            raise ValueError("Invalid wiki page record in crawl checkpoint")
    return document
