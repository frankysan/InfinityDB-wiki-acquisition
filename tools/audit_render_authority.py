"""Read-only verified source-render authority report (always non-authorizing).

Usage:
  python -m tools.audit_render_authority REPLAY ZIP MANIFEST INVENTORY
  python -m tools.audit_render_authority REPLAY ZIP MANIFEST INVENTORY \
      --previous OLD_REPLAY OLD_ZIP OLD_MANIFEST OLD_INVENTORY
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from infinity_wiki_acquisition.render_freshness_authority import (
    assess_html_source_authority,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("replay", "archive", "manifest", "inventory"):
        parser.add_argument(name, type=Path)
    parser.add_argument(
        "--previous", type=Path, nargs=4, metavar=(
            "OLD_REPLAY", "OLD_ZIP", "OLD_MANIFEST", "OLD_INVENTORY",
        ),
    )
    args = parser.parse_args(argv)
    try:
        assessment = assess_html_source_authority(
            args.replay, args.archive, args.manifest, args.inventory,
            previous=tuple(args.previous) if args.previous is not None else None,
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(assessment, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
