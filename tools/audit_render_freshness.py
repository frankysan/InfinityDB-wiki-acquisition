"""Read-only command to compare original rendered HTML across verified captures.

Usage:
    python -m tools.audit_render_freshness \
      PREVIOUS_REPLAY_ZIP PREVIOUS_SNAPSHOT_ZIP PREVIOUS_MANIFEST PREVIOUS_INVENTORY \
      CURRENT_REPLAY_ZIP CURRENT_SNAPSHOT_ZIP CURRENT_MANIFEST CURRENT_INVENTORY
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from infinity_wiki_acquisition.render_freshness_audit import compare_rendered_captures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "previous_replay", "previous_archive", "previous_manifest",
        "previous_inventory", "current_replay", "current_archive",
        "current_manifest", "current_inventory",
    ):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare_rendered_captures(
            args.previous_replay, args.previous_archive,
            args.previous_manifest, args.previous_inventory,
            args.current_replay, args.current_archive,
            args.current_manifest, args.current_inventory,
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
