"""Read-only live main-namespace comparison for a verified Rules Wiki snapshot.

This command makes paginated MediaWiki *API* requests only when --live is
explicitly supplied. It never fetches HTML pages or modifies the snapshot.
The later source listing is observational, not an atomic source-time proof.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from infinity_wiki_acquisition.allpages_coverage import (
    collect_live_main_namespace, compare_verified_rules_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument(
        "--live", action="store_true", required=False,
        help="Explicitly contact the source API and enumerate all main-namespace pages",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print complete JSON report instead of a concise summary",
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("this comparison requires explicit --live API access")
    try:
        from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
        from infinity_wiki_acquisition import cli

        source = verify_snapshot(args.archive, args.manifest, args.inventory)
        if source["site"] != cli.INFINITY_WIKI_SITE.key:
            raise ValueError("The input snapshot must be from infinity-wiki")
        language = source["language"]
        pages = collect_live_main_namespace(language=language)
        report = compare_verified_rules_snapshot(
            args.archive, args.manifest, args.inventory,
            enumerated=pages,
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Rules Wiki allpages comparison failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    print(
        f"Verified {report['language']} snapshot: "
        f"{report['archiveCurrentPageCount']} archived current HTML pages"
    )
    print(
        f"MediaWiki allpages: {report['apiMainNamespacePageCount']} main-namespace "
        f"pages; {report['apiPagesFoundInSnapshot']} exact URL matches; "
        f"{report['apiPagesAbsentFromSnapshot']} missing from this archive"
    )
    print(
        f"Page-ID mismatches: {report['apiPageIdMismatchCount']}; "
        f"matching pages lacking archived numeric IDs: "
        f"{report['matchingPagesWithoutArchivedNumericId']}"
    )
    print(
        f"Other archived pages outside this API listing: "
        f"{report['archivePagesNotInApiMainNamespaceCount']} "
        "(may include a separate root URL or non-main namespaces)"
    )
    for row in report["apiMissingPages"][:25]:
        print(f"  MISSING: {row['title']} (pageid={row['pageId']}): {row['url']}")
    for row in report["apiPageIdMismatches"][:25]:
        print(
            f"  CONFLICT: {row['title']}: "
            f"API {row['apiPageId']}, archive {row['archivedPageId']}"
        )
    print(
        "This API observation is not atomic with the historical archive; "
        "no HTML has been fetched or reused."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
