"""Read-only preflight for a preserved archive-to-history upgrade checkpoint.

Verifies immutable parent triple, its exact page list and all checkpointed
oldid files. Does not contact MediaWiki, modify work, or publish artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.history_checkpoint import (
    HISTORY_CHECKPOINT_FILENAME, read_history_checkpoint,
)
from infinity_wiki_acquisition.history_service_exclusion import (
    approved_parent_service_member,
)
from infinity_wiki_acquisition.snapshot_upgrade import _reusable_parent_pages
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        parent = verify_snapshot(args.archive, args.manifest, args.inventory)
        site = cli.WIKI_SITES[parent["site"]]
        language = parent["language"]
        pages = _reusable_parent_pages(parent)
        approved = frozenset(
            member["page"]["url"]
            for member in parent["members"]
            if approved_parent_service_member(
                member, site=site, language=language,
            )
        )
        state = read_history_checkpoint(
            args.work / HISTORY_CHECKPOINT_FILENAME,
            work=args.work, site=site.key, language=language,
            pages=[[page.url, page.path, page.title] for page in pages],
            history_exempt_urls=approved,
        )
        active = (
            pages[state["pageIndex"]].url
            if state["pageIndex"] < len(pages) else None
        )
        result = {
            "parentVerification": "PASS",
            "checkpointVerification": "PASS",
            "site": site.key, "language": language,
            "parentCurrentPageCount": len(pages),
            "checkpointCompletedPageCount": state["pageIndex"],
            "checkpointSavedOldidCount": len(state["saved"]),
            "activePageUrl": active,
            "historyExemptParentPages": sorted(approved),
            "historyCompleted": state["complete"],
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, OSError, KeyError) as exc:
        print(f"History checkpoint preflight failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
