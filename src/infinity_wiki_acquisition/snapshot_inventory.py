"""Immutable per-member acquisition inventories for Wiki/Human Sphere snapshots.

This producer-owned sidecar augments (but never changes) the existing v3
ZIP/provenance handoff. Unknown source identities remain explicitly unknown.
Version 1 records mirrored/current and rendered-oldid identities; future
incremental acquisition can add verified source revisions and lineage.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Iterable

from infinity_wiki_acquisition.asset_evidence import read_asset_evidence
from infinity_wiki_acquisition.crawl_checkpoint import read_crawl_checkpoint
from infinity_wiki_acquisition.current_revisions import load_current_revision_evidence
from infinity_wiki_acquisition.source_time_coverage import assess_source_time_coverage
from infinity_wiki_acquisition.page_evidence import read_page_evidence
from infinity_wiki_acquisition.snapshot_provenance import load_snapshot_manifest

INVENTORY_FORMAT = "InfinityDB wiki snapshot inventory"
INVENTORY_VERSION = 1
HISTORICAL_INVENTORY_VERSION = 2


def _member_hash(stream: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _history_sources(archive: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    """Get exact oldid-to-member references from the published history index."""
    try:
        document = json.loads(archive.read("_history/index.json"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("History inventory requires a valid _history/index.json") from exc
    from infinity_wiki_acquisition.historical_only import read_history_index

    document = read_history_index(archive)
    refs: dict[str, dict[str, Any]] = {}
    entries = list(document["pages"])
    if document["formatVersion"] == 2:
        entries.extend(
            {
                "currentUrl": page["formerUrl"],
                "title": page["formerTitle"],
                "revisions": page["revisions"],
            }
            for page in document["historicalOnlyPages"]
        )
    for page in entries:
        if not (
            isinstance(page, dict)
            and isinstance(page.get("currentUrl"), str)
            and isinstance(page.get("title"), str)
            and isinstance(page.get("revisions"), list)
        ):
            raise ValueError("Invalid page in archived history index")
        for revision in page["revisions"]:
            if not (
                isinstance(revision, dict)
                and type(revision.get("oldid")) is int
                and type(revision.get("parentid")) is int
                and isinstance(revision.get("timestamp"), str)
                and isinstance(revision.get("url"), str)
                and revision.get("path") == (
                    f"_history/oldid/{revision['oldid']}.html"
                )
            ):
                raise ValueError("Invalid revision reference in archived history index")
            path = revision["path"]
            base = {
                "oldid": revision["oldid"],
                "parentid": revision["parentid"],
                "timestamp": revision["timestamp"],
            }
            if path not in refs:
                refs[path] = {
                    "revision": base,
                    "sourceUrls": set(),
                    "pages": set(),
                }
            elif refs[path]["revision"] != base:
                raise ValueError(f"Conflicting revision identity in history index: {path}")
            refs[path]["sourceUrls"].add(revision["url"])
            refs[path]["pages"].add((page["currentUrl"], page["title"]))
    if document.get("revisionCount") != len(refs):
        raise ValueError("Archived history index revision count does not match identities")
    return refs


def build_snapshot_inventory(
    archive: Path,
    manifest: Path,
    *,
    work: Path,
    site: str,
    language: str,
    root_url: str,
    include_history: bool,
    pages: Iterable[Any],
    crawl_checkpoint: Path | None = None,
) -> dict[str, Any]:
    """Inventory all actual ZIP members and their supported source identities.

    Page/asset source URLs come from the successful crawl checkpoint, when
    present. A legacy/no-checkpoint file without a known page URL is tagged
    as 'unknown' instead of deriving an identity from its filename.
    """
    provenance = load_snapshot_manifest(manifest)
    snapshot = provenance["snapshot"]
    if (
        snapshot["type"] != "wiki"
        or snapshot["archive"]["name"] != archive.name
        or provenance["source"].get("url") != root_url
        or provenance["source"].get("language") != language
    ):
        raise ValueError("Snapshot inventory and provenance refer to different acquisitions")
    state = None
    if crawl_checkpoint is not None and crawl_checkpoint.exists():
        state = read_crawl_checkpoint(
            crawl_checkpoint,
            work=work,
            site=site,
            language=language,
            root_url=root_url,
        )
        if state["failed"]:
            raise ValueError("Cannot inventory a crawl checkpoint with unresolved failures")

    current_revisions = load_current_revision_evidence(
        work, site=site, language=language,
    )
    source_urls: dict[str, list[str]] = {}
    page_ids: dict[str, int] = dict(state.get("pageIds", {})) if state else {}
    is_asset: dict[str, bool] = {}
    page_info: dict[str, dict[str, str]] = {}
    for page in pages:
        page_info[page.path] = {"url": page.url, "title": page.title}
    if state is not None:
        page_info = {
            item["path"]: {"url": item["url"], "title": item["title"]}
            for item in state["pages"]
        }
        for url, record in state["saved"].items():
            path = record[0]
            source_urls.setdefault(path, []).append(url)
            is_asset[path] = state["discovered"][url]

    ignored = []
    if state is not None:
        ignored = [
            {"url": url, "reason": reason}
            for url, reason in sorted(state["ignored"].items())
        ]

    members: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive) as source:
        history_index = None
        former_by_path: dict[str, dict[str, Any]] = {}
        former_by_url: dict[str, dict[str, Any]] = {}
        if include_history:
            from infinity_wiki_acquisition.historical_only import read_history_index
            history_index = read_history_index(source)
            if history_index["formatVersion"] == 2:
                former_by_path = {
                    page["archivedPath"]: page
                    for page in history_index["historicalOnlyPages"]
                }
                former_by_url = {
                    page["formerUrl"]: page
                    for page in history_index["historicalOnlyPages"]
                }
        revision_sources = _history_sources(source) if include_history else {}
        names = {info.filename for info in source.infolist() if not info.is_dir()}
        if any(path not in names for path in revision_sources):
            raise ValueError("History index references absent ZIP members")
        for info in sorted(source.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                raise ValueError("Wiki snapshot inventory does not support directory members")
            path = info.filename
            if path in page_info:
                kind = "current-page"
                page = page_info[path]
                url = page["url"]
                refs = sorted(source_urls.get(path, [url]))
                evidence = read_page_evidence(work, url, path=path)
                page_id = page_ids.get(url)
                markup_id = evidence.get("htmlPageIdHint") if evidence else None
                if page_id is not None and markup_id is not None:
                    identity_consistency = (
                        "consistent" if page_id == markup_id else "conflicting"
                    )
                else:
                    identity_consistency = "not-comparable"
                source_identity = {
                    "requestUrl": url,
                    "title": page["title"],
                    "pageId": page_id,
                    "pageIdBasis": "mediawiki-allpages" if page_id else None,
                    "htmlPageIdHint": markup_id,
                    "pageIdConsistency": identity_consistency,
                    "canonicalDeclarations": (
                        evidence["canonicalHints"] if evidence else None
                    ),
                    "metaRefreshTargets": (
                        evidence["redirectHints"] if evidence else None
                    ),
                    "httpEffectiveUrl": (
                        evidence.get("httpEffectiveUrl") if evidence else None
                    ),
                    "httpStatus": (
                        evidence.get("httpStatus") if evidence else None
                    ),
                    "httpRedirectTarget": (
                        evidence.get("httpRedirectTarget") if evidence else None
                    ),
                    "httpRedirectVerified": bool(
                        evidence and evidence.get("httpRedirectTarget")
                    ),
                }
                relationships: list[dict[str, Any]] | None = None
                if evidence:
                    relationships = []
                    for reference in evidence["references"]:
                        target_url = reference["targetUrl"]
                        saved_record = state["saved"].get(target_url) if state else None
                        target_path = saved_record[0] if saved_record else None
                        if target_path is not None and target_path in names:
                            resolution = "archived"
                        elif state and target_url in state["ignored"]:
                            resolution = "ignored"
                        else:
                            resolution = "unresolved"
                            target_path = None
                        relationships.append({
                            **reference,
                            "targetPath": target_path,
                            "resolution": resolution,
                            "dependencyVerified": False,
                        })
                entry: dict[str, Any] = {
                    "page": page,
                    "representation": "offline-mirrored",
                    "sourceIdentity": source_identity,
                    "renderedIdentity": {
                        "memberPath": path,
                        "originalResponseSha256": (
                            evidence["originalSha256"] if evidence else None
                        ),
                        "originalResponseEvidence": (
                            "captured-pre-rewrite" if evidence else "not-captured"
                        ),
                    },
                    "references": relationships,
                    "transclusionCoverage": {
                        "template": "unknown",
                        "module": "unknown",
                    },
                }
                if current_revisions is not None:
                    revision = current_revisions.get(url)
                    if (revision is None
                            or revision["memberPath"] != path
                            or revision["pageId"] != page_id):
                        raise ValueError(
                            f"Current revision observation does not match page: {path}"
                        )
                    entry["currentRevision"] = revision
            elif path in former_by_path:
                kind = "historical-page"
                former = former_by_path[path]
                refs = [former["formerUrl"]]
                entry = {
                    "representation": "inherited-offline-mirrored",
                    "formerPage": {
                        key: former[key] for key in (
                            "pageId", "formerUrl", "formerTitle",
                            "lastKnownCurrentTitle", "originalPath", "originalSha256",
                            "lastKnownCurrentAcquiredAt",
                            "status", "historyScope",
                        )
                    },
                    "renderedIdentity": {"memberPath": path},
                }
            elif path in revision_sources:
                kind = "revision"
                revision = revision_sources[path]
                refs = sorted(revision["sourceUrls"])
                entry = {
                    "revision": {
                        **revision["revision"],
                        "pages": [
                            {"url": url, "title": title}
                            for url, title in sorted(revision["pages"])
                        ],
                    },
                    "sourceIdentity": {
                        "revisionId": revision["revision"]["oldid"],
                        "pageIds": [
                            {
                                "url": url,
                                "pageId": (
                                    page_ids.get(url)
                                    if url not in former_by_url
                                    else former_by_url[url]["pageId"]
                                ),
                            }
                            for url, _title in sorted(revision["pages"])
                        ],
                    },
                    "renderedIdentity": {"memberPath": path},
                    "representation": "rendered-oldid",
                }
            elif path == "_history/index.json" and include_history:
                kind = "generated-index"
                refs = []
                entry = {"representation": "generated"}
            else:
                refs = sorted(source_urls.get(path, []))
                kind = "asset" if refs and is_asset.get(path) else "unknown"
                entry = {"representation": "source-bytes" if kind == "asset" else "unknown"}
            with source.open(info) as stream:
                digest = _member_hash(stream)
            if kind == "asset" and len(refs) == 1:
                version = read_asset_evidence(work, refs[0], path, digest)
                if version is not None:
                    entry["sourceVersion"] = version
            if kind in {"current-page", "revision", "historical-page"}:
                entry["renderedIdentity"]["sha256"] = digest
            members.append({
                "path": path,
                "size": info.file_size,
                "sha256": digest,
                "kind": kind,
                "sourceUrls": refs,
                **entry,
            })

    if len(members) != snapshot["documentCount"]:
        raise ValueError("Inventory ZIP member count differs from published provenance")
    return {
        "format": INVENTORY_FORMAT,
        "formatVersion": (
            HISTORICAL_INVENTORY_VERSION
            if history_index is not None and history_index["formatVersion"] == 2
            else INVENTORY_VERSION
        ),
        "site": site,
        "language": language,
        "history": include_history,
        "snapshot": {
            "archiveName": archive.name,
            "archiveSha256": snapshot["archive"]["sha256"],
            "contentSha256": snapshot["contentSha256"],
            "acquiredAt": snapshot["acquiredAt"],
            "memberCount": len(members),
        },
        "source": {"rootUrl": root_url},
        # Source-page HTTP response headers are not currently retained.
        # Numeric page IDs are available only for enumerated API pages.
        "captureMetadata": {
            "httpHeadersAvailable": any(
                member["kind"] == "asset"
                and bool(member.get("sourceVersion", {}).get("strongEtag")
                         or member.get("sourceVersion", {}).get("lastModified"))
                for member in members
            ),
            "mediaWikiPageIdsAvailable": bool(page_ids),
            **({
                "currentRevisionEvidence": {
                    "pageCount": len(current_revisions),
                    "observedCount": sum(
                        item["status"] == "observed"
                        for item in current_revisions.values()
                    ),
                    "renderCorrespondence": "not-verified",
                }
            } if current_revisions is not None else {}),
            **({
                "sourceTimeCoverage": assess_source_time_coverage(members),
            } if current_revisions is not None else {}),
        },
        "parent": None,
        "members": members,
        "ignoredUrls": ignored,
    }


def write_snapshot_inventory(
    archive: Path,
    manifest: Path,
    inventory_directory: Path,
    *,
    work: Path,
    site: str,
    language: str,
    root_url: str,
    include_history: bool,
    pages: Iterable[Any],
    crawl_checkpoint: Path | None = None,
) -> Path:
    """Publish one immutable inventory without replacing previous evidence."""
    document = build_snapshot_inventory(
        archive, manifest, work=work, site=site, language=language,
        root_url=root_url, include_history=include_history,
        pages=pages, crawl_checkpoint=crawl_checkpoint,
    )
    target = inventory_directory / f"{archive.stem}.inventory.json"
    inventory_directory.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ValueError(f"Snapshot inventory already exists: {target}")
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = target.with_name(f"{target.name}.tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(payload)
        os.link(temporary, target)
    except FileExistsError as exc:
        raise ValueError(f"Snapshot inventory path or staging already exists: {target}") from exc
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return target


def load_snapshot_inventory(
    path: Path, *, archive: Path | None = None,
) -> dict[str, Any]:
    """Load a versioned inventory; optionally verify its ZIP member hashes."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable snapshot inventory: {path}") from exc
    if not (
        isinstance(document, dict)
        and document.get("format") == INVENTORY_FORMAT
        and document.get("formatVersion") in (
            INVENTORY_VERSION, HISTORICAL_INVENTORY_VERSION,
        )
        and isinstance(document.get("snapshot"), dict)
        and isinstance(document.get("members"), list)
        and document["snapshot"].get("memberCount") == len(document["members"])
    ):
        raise ValueError("Invalid or unsupported snapshot inventory")
    if archive is not None:
        if archive.name != document["snapshot"].get("archiveName"):
            raise ValueError("Snapshot inventory refers to another archive")
        with zipfile.ZipFile(archive) as source:
            expected = {
                item["path"]: (item["size"], item["sha256"])
                for item in document["members"]
            }
            infos = [info for info in source.infolist() if not info.is_dir()]
            if len(infos) != len(expected) or {info.filename for info in infos} != set(expected):
                raise ValueError("Snapshot inventory ZIP member set differs")
            for info in infos:
                size, digest = expected[info.filename]
                with source.open(info) as stream:
                    actual_hash = _member_hash(stream)
                if info.file_size != size or actual_hash != digest:
                    raise ValueError(f"Snapshot inventory member differs: {info.filename}")
    return document
