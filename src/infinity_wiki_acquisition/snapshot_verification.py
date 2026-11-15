"""Fail-closed, offline verification boundary for completed Wiki snapshots.

Only this function establishes a reusable ZIP + v3 provenance + v1 inventory
triple. A standalone manifest or inventory loader is not proof of reusability.
This does not authenticate the origin of an otherwise self-consistent set of
artifacts; callers must establish trust in the artifacts separately.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_archive import validate_archive_member_paths
from infinity_wiki_acquisition.snapshot_inventory import (
    _history_sources,
    load_snapshot_inventory,
)
from infinity_wiki_acquisition.snapshot_provenance import (
    SnapshotProvenanceError,
    load_snapshot_manifest,
    sha256_file,
    snapshot_content_sha256,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {
    "current-page": "offline-mirrored",
    "revision": "rendered-oldid",
    "generated-index": "generated",
    "asset": "source-bytes",
    "unknown": "unknown",
}


class SnapshotVerificationError(ValueError):
    """A snapshot triple is not safe or complete enough for reuse."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SnapshotVerificationError(message)


def _object(value: Any, name: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{name} must be an object")
    return value


def _string(value: Any, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name} must be a string")
    return value


def _digest(value: Any, name: str) -> str:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None,
             f"{name} must be a lowercase SHA-256")
    return value


def _positive_id(value: Any, name: str) -> None:
    _require(value is None or (type(value) is int and value > 0),
             f"{name} must be an integer ID or unknown")


def _url_list(value: Any, name: str) -> list[str]:
    _require(isinstance(value, list), f"{name} must be a list")
    for url in value:
        _string(url, f"{name} URL")
    _require(value == sorted(set(value)), f"{name} must be unique and sorted")
    return value


def _member_metadata(
    member: dict[str, Any],
    *,
    path: str,
    paths: set[str],
    history: bool,
    history_sources: dict[str, dict[str, Any]],
) -> None:
    kind = member.get("kind")
    _require(kind in _KINDS, f"Unknown inventory member kind at {path}")
    _require(member.get("representation") == _KINDS[kind],
             f"Representation/kind mismatch at {path}")
    urls = _url_list(member.get("sourceUrls"), f"{path}.sourceUrls")
    if kind == "generated-index":
        _require(history and path == "_history/index.json" and not urls,
                 f"Invalid generated index at {path}")
    elif kind == "revision":
        _require(history and path in history_sources, f"Unindexed revision at {path}")
        history_record = history_sources[path]
        revision = _object(member.get("revision"), f"{path}.revision")
        _require(
            {key: revision.get(key) for key in ("oldid", "parentid", "timestamp")}
            == history_record["revision"],
            f"Revision identity differs from history index at {path}",
        )
        _require(
            revision.get("pages")
            == [{"url": url, "title": title}
                for url, title in sorted(history_record["pages"])],
            f"Revision source pages differ from history index at {path}",
        )
        _require(urls == sorted(history_record["sourceUrls"]),
                 f"Revision source URLs differ from history index at {path}")
        identity = _object(member.get("sourceIdentity"), f"{path}.sourceIdentity")
        _require(identity.get("revisionId") == revision["oldid"],
                 f"Revision ID mismatch at {path}")
        page_ids = identity.get("pageIds")
        _require(
            isinstance(page_ids, list)
            and len(page_ids) == len(revision["pages"])
            and all(
                isinstance(item, dict)
                and item.get("url") == page["url"]
                and (item.get("pageId") is None
                     or (type(item["pageId"]) is int and item["pageId"] > 0))
                for item, page in zip(page_ids, revision["pages"], strict=True)
            ),
            f"Revision page IDs invalid at {path}",
        )
    elif kind == "current-page":
        page = _object(member.get("page"), f"{path}.page")
        url = _string(page.get("url"), f"{path}.page.url")
        _string(page.get("title"), f"{path}.page.title")
        _require(url in urls, f"Current-page URL absent from sources at {path}")
        identity = _object(member.get("sourceIdentity"), f"{path}.sourceIdentity")
        _require(identity.get("requestUrl") == url
                 and identity.get("title") == page["title"],
                 f"Current-page source identity mismatch at {path}")
        page_id = identity.get("pageId")
        hint = identity.get("htmlPageIdHint")
        _positive_id(page_id, f"{path}.pageId")
        _positive_id(hint, f"{path}.htmlPageIdHint")
        expected_consistency = (
            ("consistent" if page_id == hint else "conflicting")
            if page_id is not None and hint is not None else "not-comparable"
        )
        _require(identity.get("pageIdBasis") == (
            "mediawiki-allpages" if page_id is not None else None
        ) and identity.get("pageIdConsistency") == expected_consistency,
                 f"Contradictory page ID evidence at {path}")
        target = identity.get("httpRedirectTarget")
        _require(identity.get("httpRedirectVerified") is bool(target),
                 f"Contradictory HTTP redirect evidence at {path}")
        _require(member.get("transclusionCoverage") == {
            "template": "unknown", "module": "unknown",
        }, f"Unsupported transclusion claim at {path}")
        references = member.get("references")
        _require(references is None or isinstance(references, list),
                 f"Invalid source references at {path}")
        for reference in references or []:
            ref = _object(reference, f"{path} reference")
            resolution = ref.get("resolution")
            target_path = ref.get("targetPath")
            _require(ref.get("dependencyVerified") is False
                     and resolution in {"archived", "ignored", "unresolved"}
                     and ((resolution == "archived" and target_path in paths)
                          or (resolution != "archived" and target_path is None)),
                     f"Contradictory source relationship at {path}")
    elif kind == "asset":
        _require(bool(urls), f"Asset lacks source URLs at {path}")
    # An unidentified member may still have observed URLs: the association
    # alone does not prove whether it is a page or an asset.

    if kind in {"current-page", "revision"}:
        rendered = _object(member.get("renderedIdentity"), f"{path}.renderedIdentity")
        _require(rendered.get("memberPath") == path
                 and rendered.get("sha256") == member["sha256"],
                 f"Rendered identity differs from member at {path}")
        if kind == "current-page":
            original = rendered.get("originalResponseSha256")
            _require(original is None or (
                isinstance(original, str) and _SHA256.fullmatch(original) is not None
            ), f"Invalid original response hash at {path}")
            _require(rendered.get("originalResponseEvidence") == (
                "captured-pre-rewrite" if original is not None else "not-captured"
            ), f"Contradictory original-response evidence at {path}")


def verify_snapshot(
    archive: Path,
    manifest: Path,
    inventory: Path,
) -> dict[str, Any]:
    """Verify a completed snapshot without writing or extracting any inputs.

    Returns its checked inventory for P1.4/P2 consumers, or raises
    SnapshotVerificationError. All three files are required; no inferences are
    made from a work directory, missing inventory, or optional source metadata.
    """
    try:
        provenance = load_snapshot_manifest(manifest)
        document = load_snapshot_inventory(inventory)
        snapshot = _object(provenance["snapshot"], "provenance.snapshot")
        source = _object(provenance["source"], "provenance.source")
        record = _object(document.get("snapshot"), "inventory.snapshot")
        _require(provenance["formatVersion"] == 3 and snapshot["type"] == "wiki",
                 "Reuse requires Wiki provenance v3")
        _require(archive.is_file(), "Snapshot archive is missing")
        archive_name = archive.name
        _require(snapshot["archive"]["name"] == archive_name
                 and record.get("archiveName") == archive_name,
                 "Archive identity differs across snapshot artifacts")

        _require(set(document) == {
            "format", "formatVersion", "site", "language", "history", "snapshot",
            "source", "captureMetadata", "parent", "members", "ignoredUrls",
        }, "Inventory top-level fields are incomplete or unsupported")
        _require(set(record) == {
            "archiveName", "archiveSha256", "contentSha256", "acquiredAt", "memberCount",
        }, "Inventory snapshot fields are incomplete or unsupported")
        _require(type(document["history"]) is bool, "Inventory history must be boolean")
        parent = document["parent"]
        if parent is not None:
            _require(isinstance(parent, dict) and set(parent) == {
                "archiveName", "archiveSha256", "contentSha256", "manifestSha256",
                "inventorySha256", "acquiredAt", "reusedMemberCount",
            }, "Unsupported snapshot parent lineage")
            _string(parent["archiveName"], "parent.archiveName")
            for field in ("archiveSha256", "contentSha256",
                          "manifestSha256", "inventorySha256"):
                _digest(parent[field], f"parent.{field}")
            _string(parent["acquiredAt"], "parent.acquiredAt")
            _require(parent["archiveName"] != archive_name
                     and parent["archiveSha256"] != record["archiveSha256"],
                     "Parent snapshot cannot be the resulting archive")
            _require(type(parent["reusedMemberCount"]) is int
                     and parent["reusedMemberCount"] > 0,
                     "Invalid parent reused-member count")
            input_artifact = _object(
                provenance.get("inputArtifact"), "provenance.inputArtifact",
            )
            _require(input_artifact.get("name") == parent["archiveName"]
                     and input_artifact.get("sha256") == parent["archiveSha256"],
                     "Provenance inputArtifact does not identify the parent ZIP")
            metadata = _object(document.get("captureMetadata"), "captureMetadata")
            _require(document["history"] is True
                     and metadata.get("currentContentAcquiredAt") == parent["acquiredAt"]
                     and metadata.get("historyCutoff") == "all-observed",
                     "Archive-derived current/history acquisition evidence is inconsistent")
            history_at = _string(
                metadata.get("historyRetrievedAt"), "historyRetrievedAt",
            )
            parent_time = datetime.fromisoformat(parent["acquiredAt"])
            history_time = datetime.fromisoformat(history_at)
            snapshot_time = datetime.fromisoformat(record["acquiredAt"])
            _require(all(value.tzinfo is not None
                         and value.utcoffset() is not None
                         for value in (parent_time, history_time, snapshot_time))
                     and parent_time <= history_time <= snapshot_time,
                     "Archive-derived acquisition chronology is inconsistent")
            _require(parent["reusedMemberCount"] <= record["memberCount"] - 2,
                     "Parent reuse count exceeds history snapshot size")
        site_key = _string(document["site"], "inventory.site")
        language = _string(document["language"], "inventory.language")
        # Site definitions are producer-owned; imported lazily to avoid a CLI
        # dependency during ordinary provenance/inventory parsing.
        from infinity_wiki_acquisition.cli import WIKI_SITES, _site_archive_prefix

        site = WIKI_SITES.get(site_key)
        _require(site is not None and language in site.languages,
                 "Unsupported inventory site/language")
        root_url = site.root_urls[language]
        _require(_object(document["source"], "inventory.source").get("rootUrl") == root_url
                 and source.get("url") == root_url
                 and source.get("language") == language,
                 "Source site/language/root URL mismatch")
        prefix = _site_archive_prefix(
            site, language=language, include_history=document["history"],
        )
        _require(archive_name.startswith(prefix + " ") and archive_name.endswith(".zip"),
                 "Archive name contradicts site/language/history mode")
        _require(record["acquiredAt"] == snapshot["acquiredAt"],
                 "Acquisition times disagree")
        _require(type(record["memberCount"]) is int and record["memberCount"] >= 0,
                 "Invalid inventory member count")
        _require(record["memberCount"] == snapshot["documentCount"],
                 "Provenance and inventory member counts disagree")
        _require(_digest(record["archiveSha256"], "inventory.archiveSha256")
                 == snapshot["archive"]["sha256"],
                 "ZIP hashes differ between provenance and inventory")
        _require(_digest(record["contentSha256"], "inventory.contentSha256")
                 == snapshot["contentSha256"],
                 "Logical content hashes differ between provenance and inventory")
        _require(isinstance(document["captureMetadata"], dict)
                 and isinstance(document["captureMetadata"].get("httpHeadersAvailable"), bool)
                 and isinstance(document["captureMetadata"].get("mediaWikiPageIdsAvailable"), bool),
                 "Invalid capture metadata")
        ignored = document["ignoredUrls"]
        _require(isinstance(ignored, list)
                 and all(isinstance(row, dict)
                         and isinstance(row.get("url"), str)
                         and isinstance(row.get("reason"), str) for row in ignored),
                 "Invalid ignored URL evidence")

        actual_zip_sha = sha256_file(archive)
        _require(actual_zip_sha == record["archiveSha256"],
                 "Snapshot ZIP SHA-256 mismatch")

        members = document["members"]
        _require(isinstance(members, list)
                 and len(members) == record["memberCount"],
                 "Inventory member count mismatch")
        entries: dict[str, dict[str, Any]] = {}
        for member in members:
            item = _object(member, "inventory member")
            path = _string(item.get("path"), "inventory member.path")
            _require(path not in entries, f"Duplicate inventory member path: {path}")
            _require(type(item.get("size")) is int and item["size"] >= 0,
                     f"Invalid member size: {path}")
            _digest(item.get("sha256"), f"{path}.sha256")
            entries[path] = item
        _require(list(entries) == sorted(entries), "Inventory members must be path-sorted")

        with zipfile.ZipFile(archive) as source_zip:
            infos = source_zip.infolist()
            _require(all(not info.is_dir() for info in infos),
                     "Directory members are not supported")
            validate_archive_member_paths(info.filename for info in infos)
            for info in infos:
                file_mode = stat.S_IFMT(info.external_attr >> 16) if info.create_system == 3 else 0
                _require(file_mode in (0, stat.S_IFREG),
                         f"Non-regular ZIP member: {info.filename}")
                _require(not info.flag_bits & 1, f"Encrypted ZIP member: {info.filename}")
            paths = {info.filename for info in infos}
            _require(len(infos) == len(entries) and paths == set(entries),
                     "Inventory ZIP member set differs")
            history = document["history"]
            _require(("_history/index.json" in paths) == history,
                     "History mode/index mismatch")
            _require(history or not any(path.startswith("_history/") for path in paths),
                     "History entries in non-history snapshot")
            revision_sources = _history_sources(source_zip) if history else {}
            if history:
                history_index = json.loads(source_zip.read("_history/index.json"))
                for page in history_index["pages"]:
                    current_path = page.get("currentPath")
                    _require(isinstance(current_path, str)
                             and current_path in paths
                             and entries[current_path].get("kind") == "current-page",
                             "History index references absent current-page member")
                    _require(page["currentUrl"] in entries[current_path]["sourceUrls"],
                             "History index current-page URL differs from inventory")
            _require(all(path in paths for path in revision_sources),
                     "History index references absent ZIP members")
            _require(
                {path for path, entry in entries.items() if entry.get("kind") == "revision"}
                == set(revision_sources),
                "Revision inventory and history index differ",
            )
            for info in infos:
                item = entries[info.filename]
                digest = hashlib.sha256()
                size = 0
                with source_zip.open(info) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                _require(size == info.file_size == item["size"]
                         and digest.hexdigest() == item["sha256"],
                         f"Inventory member differs: {info.filename}")
                _member_metadata(
                    item, path=info.filename, paths=paths, history=history,
                    history_sources=revision_sources,
                )

        _require(snapshot_content_sha256(archive) == record["contentSha256"],
                 "Snapshot logical-content SHA-256 mismatch")
        return document
    except SnapshotVerificationError:
        raise
    except (
        ValueError, KeyError, TypeError, OSError, EOFError, RuntimeError,
        zipfile.BadZipFile, SnapshotProvenanceError,
    ) as exc:
        raise SnapshotVerificationError(f"Snapshot verification failed: {exc}") from exc
