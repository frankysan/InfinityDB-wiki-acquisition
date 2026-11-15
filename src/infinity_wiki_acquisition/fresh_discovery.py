"""Private mode receipt for opt-in complete fresh MediaWiki discovery.

Legacy link-only checkpoints have no receipt. A full allpages crawl records
the decision before its first API request; interrupted work cannot silently
switch discovery scope on resume.
"""

from __future__ import annotations

import json
from pathlib import Path

NAME = ".wiki-fresh-discovery.json"
FORMAT = "InfinityDB wiki fresh discovery"
VERSION = 1


def ensure_fresh_discovery_mode(
    work: Path, *, site: str, language: str, full_allpages: bool,
    resume: bool,
) -> None:
    if work.is_symlink() or not work.is_dir():
        raise ValueError("Invalid fresh wiki work directory")
    target = work / NAME
    if target.is_symlink():
        raise ValueError("Unsafe fresh discovery receipt")
    if not resume:
        if not full_allpages:
            return  # Existing link-based work remains backward compatible.
        record = {
            "format": FORMAT, "version": VERSION,
            "site": site, "language": language, "discovery": "allpages",
        }
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        return
    if not target.exists():
        if full_allpages:
            raise ValueError(
                "Cannot enable --allpages on an existing link-only checkpoint; "
                "start a new full discovery work directory"
            )
        return
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Unreadable fresh discovery receipt") from exc
    if (
        not isinstance(record, dict)
        or set(record) != {"format", "version", "site", "language", "discovery"}
        or record != {
            "format": FORMAT, "version": VERSION,
            "site": site, "language": language, "discovery": "allpages",
        }
        or not full_allpages
    ):
        raise ValueError(
            "Discovery scope differs from checkpoint: resume an allpages "
            "crawl with --allpages and the original site/language"
        )
