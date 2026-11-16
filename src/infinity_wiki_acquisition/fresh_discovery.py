"""Private fresh crawl discovery and portable path-layout receipts.

Rules Wiki layout v2 records the .html page and escaped component mapping.
Existing work from the former extensionless layout must be explicitly copied
and re-rendered with retained original HTML, never silently resumed.
"""

from __future__ import annotations

import json
from pathlib import Path

NAME = ".wiki-fresh-discovery.json"
FORMAT = "InfinityDB wiki fresh discovery"
VERSION = 2
RULES_PATH_LAYOUT = "rules-html-escaped-v1"


def read_original_discovery_mode(
    work: Path, *, site: str, language: str,
) -> bool:
    """Inspect a previous fresh Rules Wiki work receipt before offline migration.

    Missing receipt is the old link-only mode; old v1 receipts are allpages.
    Invalid/mismatched recorded source identity blocks all migration.
    """
    target = work / NAME
    if target.is_symlink():
        raise ValueError("Unsafe fresh discovery receipt")
    if not target.exists():
        return False
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Unreadable fresh discovery receipt") from exc
    if not isinstance(record, dict):
        raise ValueError("Invalid fresh discovery receipt")
    base = {
        "format": FORMAT, "site": site, "language": language,
    }
    if record.get("version") == 1:
        expected = {**base, "version": 1, "discovery": "allpages"}
        if record != expected:
            raise ValueError("Previous discovery receipt has wrong site or mode")
        return True
    if record.get("version") == VERSION:
        expected = {
            **base, "version": VERSION,
            "pathLayout": RULES_PATH_LAYOUT,
            "discovery": record.get("discovery"),
        }
        if record != expected or record["discovery"] not in {"links", "allpages"}:
            raise ValueError("Previous discovery receipt has wrong source or layout")
        return record["discovery"] == "allpages"
    raise ValueError("Unsupported fresh discovery receipt version")


def ensure_fresh_discovery_mode(
    work: Path, *, site: str, language: str, full_allpages: bool,
    resume: bool,
) -> None:
    if work.is_symlink() or not work.is_dir():
        raise ValueError("Invalid fresh wiki work directory")
    target = work / NAME
    if site != "infinity-wiki":
        if full_allpages:
            raise ValueError("The Human Sphere allpages mode is already automatic")
        return
    expected = {
        "format": FORMAT, "version": VERSION,
        "site": site, "language": language,
        "discovery": "allpages" if full_allpages else "links",
        "pathLayout": RULES_PATH_LAYOUT,
    }
    if not resume:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(expected, sort_keys=True) + "\n")
        return
    if not target.is_file() or target.is_symlink():
        if full_allpages:
            raise ValueError(
                "Cannot enable --allpages on an existing link-only checkpoint; "
                "previous Rules Wiki work must be migrated or restarted"
            )
        raise ValueError(
            "Previous Rules Wiki work has no portable path-layout receipt; "
            "use offline repair_crawl_paths with original HTML or start a fresh run"
        )
    previous_allpages = read_original_discovery_mode(
        work, site=site, language=language,
    )
    if previous_allpages != full_allpages:
        raise ValueError(
            "Discovery scope differs from checkpoint: repeat the original "
            "--allpages selection"
        )
    record = json.loads(target.read_text(encoding="utf-8"))
    if record != expected:
        raise ValueError(
            "Previous Rules Wiki work uses an old portable path layout; "
            "use offline repair_crawl_paths or restart from the verified parent"
        )
