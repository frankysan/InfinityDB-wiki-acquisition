"""Conservative v1 inventory reconstruction for completed pre-inventory snapshots.

Input ZIP/provenance bytes are immutable. Only the archived revision index
supplies page and oldid identities; filenames and rendered HTML do not establish
original URLs, image classification, HTTP observations or MediaWiki page IDs.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_archive import validate_archive_member_paths
from infinity_wiki_acquisition.snapshot_inventory import (
    INVENTORY_FORMAT,
    INVENTORY_VERSION,
    _history_sources,
    _member_hash,
)
from infinity_wiki_acquisition.snapshot_provenance import load_snapshot_manifest
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError,
    verify_snapshot,
)


def build_legacy_snapshot_inventory(archive: Path, manifest: Path) -> dict[str, Any]:
    """Reconstruct only supported identities from a verified v3 ZIP/manifest pair.

    An inventory returned here is not permission to reuse a snapshot: publish
    and check it with `write_legacy_snapshot_inventory` / `verify_snapshot`.
    """
    try:
        # Explicitly verify the exact archive hash AND logical-content hash
        # before deriving any content. V1/v2 provenance is not sufficient.
        provenance = load_snapshot_manifest(manifest, archive=archive)
        snapshot = provenance["snapshot"]
        if provenance["formatVersion"] != 3 or snapshot["type"] != "wiki":
            raise ValueError("Legacy indexing requires Wiki provenance v3")

        from infinity_wiki_acquisition.cli import WIKI_SITES, _site_archive_prefix

        source = provenance["source"]
        language = source.get("language")
        matches = [
            site for site in WIKI_SITES.values()
            if language in site.languages and source["url"] == site.root_urls[language]
        ]
        if len(matches) != 1:
            raise ValueError("Provenance site/language/root URL is unsupported or ambiguous")
        site = matches[0]

        with zipfile.ZipFile(archive) as source_zip:
            infos = source_zip.infolist()
            if any(info.is_dir() for info in infos):
                raise ValueError("Legacy snapshot has directory ZIP members")
            validate_archive_member_paths(info.filename for info in infos)
            for info in infos:
                mode = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                if mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
                    raise ValueError(f"Unsupported ZIP member type: {info.filename}")
            paths = {info.filename for info in infos}
            if len(infos) != snapshot["documentCount"]:
                raise ValueError("Provenance member count differs from archive")

            history = "_history/index.json" in paths
            prefix = _site_archive_prefix(
                site, language=language, include_history=history,
            )
            if not (archive.name.startswith(prefix + " ") and archive.name.endswith(".zip")):
                raise ValueError("ZIP name contradicts provenance site or history mode")
            if not history and any(path.startswith("_history/") for path in paths):
                raise ValueError("Unindexed history members in non-history archive")

            revisions = _history_sources(source_zip) if history else {}
            if any(path not in paths for path in revisions):
                raise ValueError("History index references absent oldid members")
            if any(path.startswith("_history/oldid/") and path not in revisions
                   for path in paths):
                raise ValueError("History archive contains unindexed oldid members")

            pages: dict[str, dict[str, str]] = {}
            if history:
                index = json.loads(source_zip.read("_history/index.json"))
                for item in index["pages"]:
                    if not isinstance(item, dict):
                        raise ValueError("History index contains an invalid page")
                    path = item.get("currentPath")
                    url = item.get("currentUrl")
                    title = item.get("title")
                    if not all(isinstance(v, str) and v.strip() for v in (path, url, title)):
                        raise ValueError("History index lacks explicit current-page identity")
                    if path not in paths or path.startswith("_history/"):
                        raise ValueError("History index references absent current-page member")
                    page = {"url": url, "title": title}
                    if path in pages and pages[path] != page:
                        raise ValueError(f"Conflicting current-page identity for {path}")
                    pages[path] = page

            members: list[dict[str, Any]] = []
            for info in sorted(infos, key=lambda value: value.filename):
                path = info.filename
                if path in pages:
                    page = pages[path]
                    url = page["url"]
                    kind = "current-page"
                    refs = [url]
                    entry: dict[str, Any] = {
                        "page": page,
                        "representation": "offline-mirrored",
                        "sourceIdentity": {
                            "requestUrl": url,
                            "title": page["title"],
                            "pageId": None,
                            "pageIdBasis": None,
                            "htmlPageIdHint": None,
                            "pageIdConsistency": "not-comparable",
                            "canonicalDeclarations": None,
                            "metaRefreshTargets": None,
                            "httpEffectiveUrl": None,
                            "httpStatus": None,
                            "httpRedirectTarget": None,
                            "httpRedirectVerified": False,
                        },
                        "renderedIdentity": {
                            "memberPath": path,
                            "originalResponseSha256": None,
                            "originalResponseEvidence": "not-captured",
                        },
                        "references": None,
                        "transclusionCoverage": {
                            "template": "unknown", "module": "unknown",
                        },
                    }
                elif path in revisions:
                    kind = "revision"
                    ref = revisions[path]
                    refs = sorted(ref["sourceUrls"])
                    associated_pages = [
                        {"url": url, "title": title}
                        for url, title in sorted(ref["pages"])
                    ]
                    entry = {
                        "revision": {
                            **ref["revision"], "pages": associated_pages,
                        },
                        "sourceIdentity": {
                            "revisionId": ref["revision"]["oldid"],
                            "pageIds": [
                                {"url": page["url"], "pageId": None}
                                for page in associated_pages
                            ],
                        },
                        "renderedIdentity": {"memberPath": path},
                        "representation": "rendered-oldid",
                    }
                elif path == "_history/index.json" and history:
                    kind, refs = "generated-index", []
                    entry = {"representation": "generated"}
                else:
                    # A name or extension is not proof of the source URL,
                    # classification, origin bytes or whether a page was rewritten.
                    kind, refs = "unknown", []
                    entry = {"representation": "unknown"}

                with source_zip.open(info) as stream:
                    member_sha256 = _member_hash(stream)
                if kind in {"current-page", "revision"}:
                    entry["renderedIdentity"]["sha256"] = member_sha256
                members.append({
                    "path": path,
                    "size": info.file_size,
                    "sha256": member_sha256,
                    "kind": kind,
                    "sourceUrls": refs,
                    **entry,
                })

        return {
            "format": INVENTORY_FORMAT,
            "formatVersion": INVENTORY_VERSION,
            "site": site.key,
            "language": language,
            "history": history,
            "snapshot": {
                "archiveName": archive.name,
                "archiveSha256": snapshot["archive"]["sha256"],
                "contentSha256": snapshot["contentSha256"],
                "acquiredAt": snapshot["acquiredAt"],
                "memberCount": len(members),
            },
            "source": {"rootUrl": source["url"]},
            "captureMetadata": {
                "httpHeadersAvailable": False,
                "mediaWikiPageIdsAvailable": False,
                "ignoredUrlsAvailable": False,
                "legacyIndexed": True,
            },
            "parent": None,
            "members": members,
            # An empty list means no retained evidence, not proof no URLs
            # were excluded in the old acquisition.
            "ignoredUrls": [],
        }
    except SnapshotVerificationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, EOFError, RuntimeError,
            zipfile.BadZipFile) as exc:
        raise SnapshotVerificationError(f"Could not index legacy snapshot: {exc}") from exc


def write_legacy_snapshot_inventory(
    archive: Path,
    manifest: Path,
    inventory_directory: Path | None = None,
) -> Path:
    """Verify and publish a new sidecar exclusively; never change the inputs.

    The completed inventory is independently validated before installation.
    A failed or interrupted operation never replaces an earlier sidecar.
    """
    document = build_legacy_snapshot_inventory(archive, manifest)
    directory = inventory_directory if inventory_directory is not None else manifest.parent / "inventories"
    target = directory / f"{archive.stem}.inventory.json"
    if target.exists() or target.is_symlink():
        raise SnapshotVerificationError(f"Inventory already exists: {target}")
    directory.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    payload = (json.dumps(
        document, ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(payload)
        # Fail before publishing when any cross-artifact invariant disagrees.
        verify_snapshot(archive, manifest, temporary)
        os.link(temporary, target)
    except FileExistsError as exc:
        raise SnapshotVerificationError(
            f"Inventory destination or staging already exists: {target}"
        ) from exc
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    """Explicit, offline indexing command for legacy ZIP/provenance pairs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Completed Wiki ZIP archive")
    parser.add_argument("manifest", type=Path, help="Matching v3 provenance JSON")
    parser.add_argument(
        "--inventory-dir", type=Path,
        help="Output directory (default: <manifest directory>/inventories)",
    )
    args = parser.parse_args(argv)
    try:
        path = write_legacy_snapshot_inventory(
            args.archive, args.manifest, args.inventory_dir,
        )
    except (OSError, ValueError) as exc:
        print(f"Legacy snapshot indexing failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified legacy snapshot inventory: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
