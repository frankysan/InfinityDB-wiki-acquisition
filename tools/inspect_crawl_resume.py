"""Read-only preflight for an interrupted ordinary Wiki/Human Sphere crawl.

Validate its private checkpoint and committed file sizes without sending
requests or modifying any acquisition data. Print bounded actionable status.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, read_crawl_checkpoint,
)
from infinity_wiki_acquisition.history_checkpoint import HISTORY_CHECKPOINT_FILENAME


def inspect(work: Path, *, site_key: str, language: str) -> dict:
    site = cli.WIKI_SITES[site_key]
    if not work.is_dir() or work.is_symlink():
        raise ValueError("Crawl work path is not an ordinary directory")
    root = cli.canonical_crawl_url(site.root_urls[language], site=site)
    checkpoint_path = work / CHECKPOINT_FILENAME
    if not checkpoint_path.is_file():
        children = list(work.iterdir())
        metadata = []
        for child in sorted(children, key=lambda path: path.name):
            if not child.name.startswith("."):
                continue
            metadata.append({
                "name": child.name,
                "type": "directory" if child.is_dir() else "file",
                "entryCount": sum(1 for _ in child.iterdir())
                if child.is_dir() else None,
                "sizeBytes": child.stat().st_size if child.is_file() else None,
            })
        return {
            "site": site_key, "language": language,
            "checkpointPresent": False,
            "directResumeSafe": False,
            "retryFailedOnlyEligible": False,
            "topLevelEntries": len(children),
            "topLevelDirectories": sum(x.is_dir() for x in children),
            "privateEvidence": metadata,
            "rootFileSamples": [
                f.name for f in sorted(children, key=lambda p: p.name)
                if f.is_file() and not f.name.startswith(".")
            ][:12],
            "reason": "No checkpoint found; preserved files have no validated URL/queue map",
        }
    checkpoint = read_crawl_checkpoint(
        work / CHECKPOINT_FILENAME, work=work, site=site_key,
        language=language, root_url=root, require_complete=False,
    )
    failed = checkpoint["failed"]
    pending = checkpoint.get("queue", [])
    phase = checkpoint["phase"]
    result = {
        "site": site_key,
        "language": language,
        "checkpointPresent": True,
        "directResumeSafe": True,
        "checkpointVersion": checkpoint["version"],
        "phase": phase,
        "discoveryComplete": phase != "enumerating",
        "retryFailedOnlyEligible": phase == "complete" and bool(failed),
        "savedFiles": len(checkpoint["saved"]),
        "savedPages": len(checkpoint["pages"]),
        "discoveredUrls": len(checkpoint["discovered"]),
        "requiredUrls": len(checkpoint["required"]),
        "ignoredUrls": len(checkpoint["ignored"]),
        "failedUrls": len(failed),
        "pendingUrls": len(pending),
        "continuationPresent": checkpoint.get("enumerationContinuation") is not None,
        "failedDetails": [
            {"url": url, "error": error}
            for url, error in sorted(failed.items())
        ][:30],
        "pendingExamples": [
            {"url": url, "asset": asset}
            for url, asset in pending[:12]
        ],
        "historyCheckpointPresent": (work / HISTORY_CHECKPOINT_FILENAME).is_file(),
    }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--site", choices=tuple(cli.WIKI_SITES), required=True)
    parser.add_argument("--language", choices=("en", "es"), default="en")
    args = parser.parse_args(argv)
    try:
        result = inspect(args.work, site_key=args.site, language=args.language)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Crawl resume preflight failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
