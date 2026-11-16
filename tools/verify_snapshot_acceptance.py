"""Offline acceptance check for a completed wiki snapshot and optional parent.

Verifies the entire ZIP/provenance/inventory, replays original HTML with
exact rewritten-byte matching when supplied, verifies immutable parent
lineage when supplied, and reports source-URL continuity independently of
local filenames. No network requests or file writes.

Usage:
  python -m tools.verify_snapshot_acceptance CHILD.zip CHILD.json CHILD.inventory.json \\
      --replay CHILD.source-replay.zip \\
      --parent PARENT.zip PARENT.json PARENT.inventory.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_provenance import sha256_file
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle


def assess(
    archive: Path, manifest: Path, inventory: Path, *,
    replay: Path | None = None,
    parent: tuple[Path, Path, Path] | None = None,
) -> dict[str, Any]:
    document = verify_snapshot(archive, manifest, inventory)
    members = document["members"]
    counts = collections.Counter(member["kind"] for member in members)
    pages = {
        member["page"]["url"]: member
        for member in members if member["kind"] == "current-page"
    }
    assets = {
        url: member
        for member in members if member["kind"] == "asset"
        for url in member["sourceUrls"]
    }
    result: dict[str, Any] = {
        "site": document["site"],
        "language": document["language"],
        "snapshot": archive.name,
        "snapshotVerification": "PASS",
        "history": document["history"],
        "memberCount": len(members),
        "kinds": dict(sorted(counts.items())),
        "mainNamespaceIdsRecorded": sum(
            page["sourceIdentity"].get("pageIdBasis") == "mediawiki-allpages"
            for page in pages.values()
        ),
        "currentPagesWithoutNumericId": sum(
            page["sourceIdentity"].get("pageId") is None
            for page in pages.values()
        ),
        "unknownMembers": [
            member["path"] for member in members if member["kind"] == "unknown"
        ],
        "sourceReplayVerification": "not-requested",
    }
    if replay is not None:
        checked = verify_source_replay_bundle(replay, archive, manifest, inventory)
        result["sourceReplayVerification"] = "PASS"
        result["replayedCurrentPages"] = len(checked["pages"])
    if parent is not None:
        parent_archive, parent_manifest, parent_inventory = parent
        previous = verify_snapshot(*parent)
        if previous["site"] != document["site"] or previous["language"] != document["language"]:
            raise ValueError("Parent site/language differ from verified child")
        link = document.get("parent")
        if link is None:
            raise ValueError("Child lacks parent lineage")
        hashes = {
            "archiveName": parent_archive.name,
            "archiveSha256": sha256_file(parent_archive),
            "manifestSha256": sha256_file(parent_manifest),
            "inventorySha256": sha256_file(parent_inventory),
            "contentSha256": previous["snapshot"]["contentSha256"],
            "acquiredAt": previous["snapshot"]["acquiredAt"],
        }
        for key, expected in hashes.items():
            if link.get(key) != expected:
                raise ValueError(f"Parent lineage mismatch: {key}")
        older_pages = {
            member["page"]["url"]: member
            for member in previous["members"] if member["kind"] == "current-page"
        }
        older_assets = {
            url: member for member in previous["members"]
            if member["kind"] == "asset" for url in member["sourceUrls"]
        }
        shared_pages = pages.keys() & older_pages.keys()
        result["parent"] = {
            "snapshot": parent_archive.name,
            "snapshotVerification": "PASS",
            "lineageVerification": "PASS",
            "memberCount": len(previous["members"]),
            "currentPages": len(older_pages),
            "commonPageUrls": len(shared_pages),
            "addedPageUrls": len(pages.keys() - older_pages.keys()),
            "absentParentPageUrls": len(older_pages.keys() - pages.keys()),
            "pagePathsMigrated": sum(
                pages[url]["path"] != older_pages[url]["path"] for url in shared_pages
            ),
            "sameOriginalHtmlSha256": sum(
                pages[url].get("renderedIdentity", {}).get("originalResponseSha256")
                == older_pages[url].get("renderedIdentity", {}).get("originalResponseSha256")
                for url in shared_pages
                if pages[url].get("renderedIdentity", {}).get("originalResponseSha256")
                and older_pages[url].get("renderedIdentity", {}).get("originalResponseSha256")
            ),
            "addedAssetUrls": len(assets.keys() - older_assets.keys()),
            "absentParentAssetUrls": len(older_assets.keys() - assets.keys()),
        }
        old_unknown = [
            member for member in previous["members"]
            if member["kind"] == "unknown"
        ]
        result["parent"]["previousUnknownMembers"] = [
            {"path": m["path"], "urls": m["sourceUrls"]}
            for m in old_unknown
        ]
        result["parent"]["resolvedUnknownSourceUrls"] = sorted({
            url for old in old_unknown for url in old["sourceUrls"]
            if url in assets
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument(
        "--parent", type=Path, nargs=3,
        metavar=("PARENT_ZIP", "PARENT_MANIFEST", "PARENT_INVENTORY"),
    )
    args = parser.parse_args(argv)
    try:
        report = assess(
            args.archive, args.manifest, args.inventory,
            replay=args.replay,
            parent=tuple(args.parent) if args.parent else None,
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Snapshot acceptance failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
