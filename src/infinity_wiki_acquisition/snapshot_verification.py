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
    "historical-page": "inherited-offline-mirrored",
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
    site: Any,
    language: str,
) -> None:
    kind = member.get("kind")
    _require(kind in _KINDS, f"Unknown inventory member kind at {path}")
    _require(member.get("representation") == _KINDS[kind],
             f"Representation/kind mismatch at {path}")
    urls = _url_list(member.get("sourceUrls"), f"{path}.sourceUrls")
    if kind != "current-page":
        _require("pageRelationships" not in member
                 and "renderedResponseVersion" not in member,
                 f"Only current pages may have HTTP response observations: {path}")
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
        rendered_response = member.get("renderedResponseVersion")
        if rendered_response is not None:
            from infinity_wiki_acquisition.rendered_response import (
                validate_rendered_response,
            )

            try:
                validate_rendered_response(
                    rendered_response, member=member,
                    site=site, language=language,
                )
            except ValueError as exc:
                raise SnapshotVerificationError(
                    f"Invalid rendered HTML response version at {path}: {exc}"
                ) from exc
        current = member.get("currentRevision")
        if rendered_response is not None and current is not None:
            _require(
                rendered_response["htmlFetchedAt"] == current["htmlFetchedAt"],
                f"Rendered response time contradicts revision observation: {path}",
            )
        if current is not None:
            from infinity_wiki_acquisition.current_revisions import (
                _same_title, validate_current_revision_record,
            )
            from infinity_wiki_acquisition.cli import page_title_from_url
            try:
                validate_current_revision_record(current, url=url)
            except ValueError as exc:
                raise SnapshotVerificationError(
                    f"Invalid current revision observation at {path}: {exc}"
                ) from exc
            _require(current["memberPath"] == path
                     and current["pageId"] == page_id,
                     f"Current revision observation contradicts page ID/path: {path}")
            if current["status"] == "observed":
                expected_title = page_title_from_url(
                    url, site=site, language=language,
                )
                _require(_same_title(current["apiTitle"], expected_title),
                         f"Current revision API title contradicts page URL: {path}")
        relationship = member.get("pageRelationships")
        if rendered_response is not None and relationship is not None:
            _require(
                rendered_response["htmlFetchedAt"] == relationship["htmlFetchedAt"],
                f"Rendered response time contradicts relationship observation: {path}",
            )
        if relationship is not None:
            from infinity_wiki_acquisition.page_relationships import (
                validate_page_relationship,
            )
            from infinity_wiki_acquisition.current_revisions import _same_title
            from infinity_wiki_acquisition.cli import page_title_from_url

            try:
                validate_page_relationship(relationship, url=url)
            except ValueError as exc:
                raise SnapshotVerificationError(
                    f"Invalid page relationship evidence at {path}: {exc}"
                ) from exc
            _require(
                relationship["memberPath"] == path
                and relationship["pageId"] == page_id,
                f"Page relationship identity contradicts page: {path}",
            )
            expected_title = page_title_from_url(
                url, site=site, language=language,
            )
            if relationship["status"] == "observed":
                _require(_same_title(relationship["apiTitle"], expected_title),
                         f"Relationship API title differs from source: {path}")
            if relationship["status"] == "identity-unresolved":
                _require(not _same_title(relationship["apiTitle"], expected_title),
                         f"Incorrectly unresolved API title: {path}")
            if current is not None:
                _require(
                    relationship["htmlFetchedAt"] == current["htmlFetchedAt"],
                    f"Relationship HTML observation time contradicts revision: {path}",
                )
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
    elif kind == "historical-page":
        from infinity_wiki_acquisition.historical_only import (
            FORMER_REVISION_SCOPE, FORMER_STATUS, historical_page_path,
        )

        _require(history and path.startswith("_history/retired/"),
                 f"Historical-only content outside the history archive: {path}")
        former = _object(member.get("formerPage"), f"{path}.formerPage")
        _require(set(former) == {
            "pageId", "formerUrl", "formerTitle", "lastKnownCurrentTitle",
            "originalPath", "originalSha256", "lastKnownCurrentAcquiredAt",
            "status", "historyScope",
        }, f"Historical-only page metadata incomplete at {path}")
        page_id = former["pageId"]
        url = _string(former["formerUrl"], f"{path}.formerUrl")
        _require(type(page_id) is int and page_id > 0
                 and path == historical_page_path(page_id, url)
                 and urls == [url]
                 and isinstance(former["formerTitle"], str)
                 and bool(former["formerTitle"])
                 and isinstance(former["lastKnownCurrentTitle"], str)
                 and bool(former["lastKnownCurrentTitle"])
                 and isinstance(former["originalPath"], str)
                 and bool(former["originalPath"])
                 and former["status"] == FORMER_STATUS
                 and former["historyScope"] == FORMER_REVISION_SCOPE
                 and _digest(former["originalSha256"],
                             f"{path}.originalSha256") == member["sha256"],
                 f"Historical-only page identity invalid at {path}")
        stamp = datetime.fromisoformat(
            _string(former["lastKnownCurrentAcquiredAt"],
                    f"{path}.lastKnownCurrentAcquiredAt"),
        )
        _require(stamp.tzinfo is not None and stamp.utcoffset() is not None,
                 f"Historical-only acquisition timestamp invalid: {path}")
    elif kind == "asset":
        _require(bool(urls), f"Asset lacks source URLs at {path}")
        version = member.get("sourceVersion")
        if version is not None:
            from infinity_wiki_acquisition.cli import _is_strong_etag

            version = _object(version, f"{path}.sourceVersion")
            _require(set(version) == {
                "sourceUrl", "sha256", "responseStatus", "effectiveUrl",
                "strongEtag", "lastModified", "conditionalRevalidated",
                "observedAt",
            }, f"Unsupported asset source-version evidence at {path}")
            _require(len(urls) == 1
                     and version.get("sourceUrl") == urls[0]
                     and version.get("sha256") == member["sha256"],
                     f"Asset source version is not bound to member bytes at {path}")
            status = version.get("responseStatus")
            validator = version.get("strongEtag")
            revalidated = version.get("conditionalRevalidated")
            _require(type(status) is int and status in (200, 304)
                     and type(revalidated) is bool
                     and revalidated is (status == 304)
                     and isinstance(version.get("effectiveUrl"), str)
                     and (validator is None or (
                         _is_strong_etag(validator)
                         and version["effectiveUrl"] == urls[0]
                     ))
                     and (version.get("lastModified") is None
                          or isinstance(version["lastModified"], str)),
                     f"Invalid HTTP asset version evidence at {path}")
            _require(not revalidated or (
                _is_strong_etag(validator)
                and version["effectiveUrl"] == urls[0]
            ), f"Unverified conditional asset reuse at {path}")
            observed = datetime.fromisoformat(
                _string(version.get("observedAt"), f"{path}.observedAt"),
            )
            _require(observed.tzinfo is not None
                     and observed.utcoffset() is not None,
                     f"Asset observation timestamp has no timezone at {path}")
        upload = member.get("uploadVersion")
        if upload is not None:
            upload = _object(upload, f"{path}.uploadVersion")
            _require(set(upload) == {
                "sourceUrl", "apiTitle", "remoteUrl", "sha1Hex", "size",
                "uploadTimestamp", "checkedAt", "usedForReuse", "evidence",
            }, f"Unsupported MediaWiki upload evidence at {path}")
            _require(len(urls) == 1
                     and upload.get("sourceUrl") == urls[0]
                     and upload.get("remoteUrl") == urls[0]
                     and isinstance(upload.get("apiTitle"), str)
                     and upload["apiTitle"].startswith("File:")
                     and isinstance(upload.get("sha1Hex"), str)
                     and len(upload["sha1Hex"]) == 40
                     and all(char in "0123456789abcdef"
                             for char in upload["sha1Hex"])
                     and type(upload.get("size")) is int
                     and upload["size"] == member["size"]
                     and type(upload.get("usedForReuse")) is bool
                     and upload.get("evidence") == "mediawiki-imageinfo",
                     f"MediaWiki upload identity does not match member at {path}")
            for field in ("uploadTimestamp", "checkedAt"):
                value = _string(upload.get(field), f"{path}.{field}")
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                _require(timestamp.tzinfo is not None
                         and timestamp.utcoffset() is not None,
                         f"MediaWiki upload timestamp is not aware at {path}")
    # An unidentified member may still have observed URLs: the association
    # alone does not prove whether it is a page or an asset.

    if kind in {"current-page", "revision", "historical-page"}:
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
        _require(document["formatVersion"] in (1, 2),
                 "Unsupported inventory version")
        _require(document["formatVersion"] != 2 or document["history"],
                 "Historical inventory v2 requires revision history")
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
            # Distinct timestamped snapshots may legitimately contain
            # identical deterministic ZIP bytes (no new revisions).
            _require(parent["archiveName"] != archive_name,
                     "Parent snapshot cannot be the resulting archive")
            _require(type(parent["reusedMemberCount"]) is int
                     and parent["reusedMemberCount"] >= 0,
                     "Invalid parent reused-member count")
            input_artifact = _object(
                provenance.get("inputArtifact"), "provenance.inputArtifact",
            )
            _require(input_artifact.get("name") == parent["archiveName"]
                     and input_artifact.get("sha256") == parent["archiveSha256"],
                     "Provenance inputArtifact does not identify the parent ZIP")
            metadata = _object(document.get("captureMetadata"), "captureMetadata")
            mode = metadata.get("updateMode")
            if mode in {
                "verified-full-source-refresh", "conditional-asset-refresh",
                "verified-asset-version-refresh",
            }:
                history_refresh = metadata.get("historyRefresh")
                if mode in {
                    "conditional-asset-refresh", "verified-asset-version-refresh",
                }:
                    conditional_count = metadata.get("conditionalReusedAssetCount")
                    upload_reuse = (
                        metadata.get("uploadSha1ReusedAssetCount")
                        if mode == "verified-asset-version-refresh" else 0
                    )
                    upload_evidence = (
                        metadata.get("uploadVersionEvidenceCount")
                        if mode == "verified-asset-version-refresh" else 0
                    )
                    _require(
                        document["history"] is (history_refresh is not None)
                        and type(conditional_count) is int
                        and type(upload_reuse) is int
                        and type(upload_evidence) is int
                        and 0 <= conditional_count
                        and 0 <= upload_reuse <= upload_evidence
                        and conditional_count + upload_reuse
                        <= parent["reusedMemberCount"]
                        and metadata.get("sourceVersionSkipping")
                        is (conditional_count + upload_reuse > 0)
                        and metadata.get("assetVersionComparison")
                        == (
                            "strong-etag-304-or-imageinfo-sha1-or-body-hash"
                            if mode == "verified-asset-version-refresh"
                            else "strong-etag-304-or-body-hash"
                        )
                        and metadata.get("pageRevisionComparison")
                        == "not-safe-for-rendered-html",
                        "Unsupported verified asset reuse evidence",
                    )
                else:
                    _require(document["history"] is False
                             and metadata.get("sourceVersionSkipping") is False
                             and metadata.get("assetVersionComparison")
                             == "content-hash-after-download",
                             "Unsupported archive-derived refresh evidence")
                for key in ("matchedParentMemberCount", "changedMemberCount",
                            "newMemberCount", "removedMemberCount"):
                    value = metadata.get(key)
                    _require(type(value) is int and value >= 0,
                             f"Invalid archive update count: {key}")
                _require(
                    metadata["matchedParentMemberCount"]
                    == parent["reusedMemberCount"]
                    and metadata["matchedParentMemberCount"]
                    + metadata["changedMemberCount"]
                    + metadata["newMemberCount"] == record["memberCount"],
                    "Archive update member accounting is inconsistent",
                )
                for key in ("pageMovesById", "parentPageIdsAbsentFromEnumeration"):
                    _require(isinstance(metadata.get(key), list),
                             f"Invalid archive update evidence: {key}")
                if mode == "verified-asset-version-refresh" and (
                    "currentRevisionComparisons" in metadata
                ):
                    comparisons = metadata["currentRevisionComparisons"]
                    _require(
                        isinstance(comparisons, list)
                        and len(comparisons) == sum(
                            item.get("kind") == "current-page"
                            for item in document["members"]
                        ),
                        "Current revision comparison coverage is incomplete",
                    )
                    seen_urls: set[str] = set()
                    current_pages = {
                        item["page"]["url"]: item
                        for item in document["members"]
                        if item.get("kind") == "current-page"
                    }
                    for row in comparisons:
                        _require(isinstance(row, dict)
                                 and set(row) == {
                                     "pageUrl", "pageId", "previousRevisionId",
                                     "currentRevisionId", "comparison",
                                     "htmlRefetched",
                                 }, "Invalid current revision comparison")
                        row_url = row["pageUrl"]
                        member = current_pages.get(row_url)
                        _require(
                            member is not None and row_url not in seen_urls
                            and row["htmlRefetched"] is True
                            and row["pageId"] == member["sourceIdentity"]["pageId"]
                            and row["comparison"] in {
                                "missing-page-id", "new-page-id",
                                "current-revision-unknown", "parent-revision-unknown",
                                "same-source-revision", "changed-source-revision",
                            }
                            and (row["previousRevisionId"] is None or (
                                type(row["previousRevisionId"]) is int
                                and row["previousRevisionId"] > 0
                            )),
                            "Current revision comparison identity is inconsistent",
                        )
                        current = member.get("currentRevision")
                        expected_id = (
                            current["revisionId"]
                            if current is not None
                            and current["status"] == "observed" else None
                        )
                        _require(
                            row["currentRevisionId"] == expected_id
                            and (
                                row["comparison"] != "same-source-revision"
                                or (expected_id is not None
                                    and expected_id == row["previousRevisionId"])
                            )
                            and (
                                row["comparison"] != "changed-source-revision"
                                or (expected_id is not None
                                    and row["previousRevisionId"] is not None
                                    and expected_id != row["previousRevisionId"])
                            ),
                            "Current revision comparison contradicts member evidence",
                        )
                        seen_urls.add(row_url)
                changes = _object(metadata.get("recentChanges"), "recentChanges")
                _require(set(changes) == {
                    "requestedFrom", "requestedThrough", "retentionCoverage",
                    "observedCount", "events",
                } and changes["retentionCoverage"] == "unverified",
                         "RecentChanges cannot certify source retention coverage")
                recent_events = changes["events"]
                _require(isinstance(recent_events, list)
                         and type(changes["observedCount"]) is int
                         and changes["observedCount"] == len(recent_events),
                         "RecentChanges count differs from recorded events")
                start_time = datetime.fromisoformat(
                    _string(changes["requestedFrom"], "recentChanges.requestedFrom")
                )
                end_time = datetime.fromisoformat(
                    _string(changes["requestedThrough"], "recentChanges.requestedThrough")
                )
                _require(all(date.tzinfo is not None and date.utcoffset() is not None
                             for date in (start_time, end_time))
                         and start_time <= end_time,
                         "Invalid RecentChanges requested window")
                event_ids: set[int] = set()
                for event in recent_events:
                    observed = _object(event, "RecentChanges event")
                    rcid = observed.get("rcid")
                    timestamp = datetime.fromisoformat(
                        _string(observed.get("timestamp"), "RecentChanges timestamp")
                    )
                    _require(type(rcid) is int and rcid > 0
                             and rcid not in event_ids
                             and observed.get("type") in {
                                 "edit", "new", "log", "categorize", "external",
                             }
                             and isinstance(observed.get("title"), str)
                             and bool(observed["title"])
                             and timestamp.tzinfo is not None
                             and timestamp.utcoffset() is not None
                             and start_time <= timestamp <= end_time,
                             "Invalid RecentChanges event evidence")
                    event_ids.add(rcid)
                if history_refresh is not None:
                    history_refresh = _object(history_refresh, "historyRefresh")
                    _require(set(history_refresh) == ({
                        "sourceOldidCount", "reusedOldidCount",
                        "freshOldidCount", "pageMappings", "historyCutoff",
                        "historyRetrievedAt",
                    } | ({
                        "historicalOnlyPageCount",
                    } if document["formatVersion"] == 2 else set())),
                             "Unsupported historical refresh metadata")
                    _require(
                        history_refresh["historyCutoff"] == (
                            "current-all-observed-former-last-archived"
                            if document["formatVersion"] == 2
                            else "all-observed"
                        )
                        and type(history_refresh["sourceOldidCount"]) is int
                        and history_refresh["sourceOldidCount"] > 0
                        and type(history_refresh["reusedOldidCount"]) is int
                        and 0 <= history_refresh["reusedOldidCount"]
                        <= history_refresh["sourceOldidCount"]
                        and history_refresh["reusedOldidCount"]
                        <= parent["reusedMemberCount"]
                        and type(history_refresh["freshOldidCount"]) is int
                        and history_refresh["freshOldidCount"]
                        >= history_refresh["sourceOldidCount"]
                        and isinstance(history_refresh["pageMappings"], list)
                        and bool(history_refresh["pageMappings"])
                        and (
                            document["formatVersion"] != 2
                            or (
                                type(history_refresh["historicalOnlyPageCount"])
                                is int
                                and history_refresh["historicalOnlyPageCount"] > 0
                            )
                        ),
                        "History refresh cannot claim incomplete oldid coverage",
                    )
                    historical_at = datetime.fromisoformat(
                        _string(
                            history_refresh["historyRetrievedAt"],
                            "historyRefresh.historyRetrievedAt",
                        ).replace("Z", "+00:00")
                    )
                    _require(
                        historical_at.tzinfo is not None
                        and historical_at.utcoffset() is not None
                        and historical_at <= datetime.fromisoformat(
                            record["acquiredAt"],
                        ),
                        "History refresh retrieval is after snapshot publication",
                    )
                parent_time = datetime.fromisoformat(parent["acquiredAt"])
                current_time = datetime.fromisoformat(record["acquiredAt"])
                _require(parent_time.tzinfo is not None
                         and parent_time.utcoffset() is not None
                         and current_time.tzinfo is not None
                         and current_time.utcoffset() is not None
                         and parent_time <= current_time,
                         "Archive refresh chronology is inconsistent")
            else:
                parent_history = metadata.get("parentHistory", False)
                reused_revisions = metadata.get("reusedRevisionCount", 0)
                _require(type(parent_history) is bool
                     and type(reused_revisions) is int
                     and 0 <= reused_revisions <= parent["reusedMemberCount"]
                     and (parent_history or reused_revisions == 0),
                     "Invalid inherited historical revision evidence")
                current_at = _string(
                    metadata.get("currentContentAcquiredAt"), "currentContentAcquiredAt",
                )
                _require(document["history"] is True
                         and (parent_history or current_at == parent["acquiredAt"])
                         and metadata.get("historyCutoff") == "all-observed",
                         "Archive-derived current/history acquisition evidence is inconsistent")
                history_at = _string(
                    metadata.get("historyRetrievedAt"), "historyRetrievedAt",
                )
                parent_time = datetime.fromisoformat(parent["acquiredAt"])
                current_time = datetime.fromisoformat(current_at)
                history_time = datetime.fromisoformat(history_at)
                snapshot_time = datetime.fromisoformat(record["acquiredAt"])
                _require(all(value.tzinfo is not None
                             and value.utcoffset() is not None
                             for value in (current_time, parent_time, history_time, snapshot_time))
                         and current_time <= parent_time <= history_time <= snapshot_time,
                         "Archive-derived acquisition chronology is inconsistent")
                max_reused = record["memberCount"] - (1 if parent_history else 2)
                _require(parent["reusedMemberCount"] <= max_reused,
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
                from infinity_wiki_acquisition.historical_only import (
                    FORMER_STATUS, FORMER_REVISION_SCOPE,
                    HISTORICAL_COVERAGE, read_history_index,
                )
                history_index = read_history_index(source_zip)
                index_version = history_index["formatVersion"]
                _require(
                    document["formatVersion"] == index_version,
                    "History index/inventory versions must match",
                )
                for page in history_index["pages"]:
                    current_path = page.get("currentPath")
                    _require(isinstance(current_path, str)
                             and current_path in paths
                             and entries[current_path].get("kind") == "current-page",
                             "History index references absent current-page member")
                    _require(page["currentUrl"] in entries[current_path]["sourceUrls"],
                             "History index current-page URL differs from inventory")
                if index_version == 2:
                    former_entries = history_index["historicalOnlyPages"]
                    former_paths: set[str] = set()
                    former_urls: set[str] = set()
                    live_ids = {
                        member["sourceIdentity"]["pageId"]
                        for member in entries.values()
                        if member["kind"] == "current-page"
                        and member["sourceIdentity"]["pageId"] is not None
                    }
                    for former in former_entries:
                        _require(
                            isinstance(former, dict)
                            and set(former) == {
                                "pageId", "formerUrl", "formerTitle",
                                "lastKnownCurrentTitle", "originalPath",
                                "archivedPath", "originalSha256",
                                "lastKnownCurrentAcquiredAt",
                                "status", "historyScope", "revisions",
                            }, "Unsupported historical-only index record")
                        former_path = former["archivedPath"]
                        url = former["formerUrl"]
                        item = entries.get(former_path)
                        _require(
                            isinstance(url, str) and url not in former_urls
                            and isinstance(former_path, str)
                            and former_path not in former_paths
                            and item is not None
                            and item["kind"] == "historical-page"
                            and item["formerPage"] == {
                                key: former[key] for key in (
                                    "pageId", "formerUrl", "formerTitle",
                                    "lastKnownCurrentTitle", "originalPath",
                                    "originalSha256",
                                    "lastKnownCurrentAcquiredAt",
                                    "status", "historyScope",
                                )
                            }
                            and former["pageId"] not in live_ids
                            and former["status"] == FORMER_STATUS
                            and former["historyScope"] == FORMER_REVISION_SCOPE
                            and isinstance(former["revisions"], list)
                            and bool(former["revisions"]),
                            "Historical-only index identity conflicts with current pages",
                        )
                        former_paths.add(former_path)
                        former_urls.add(url)
                        for rev in former["revisions"]:
                            _require(
                                isinstance(rev, dict)
                                and type(rev.get("oldid")) is int
                                and rev["oldid"] > 0
                                and rev.get("path") ==
                                f"_history/oldid/{rev['oldid']}.html",
                                "Historical-only revision reference invalid",
                            )
                            oldid_entry = entries.get(rev["path"])
                            _require(
                                oldid_entry is not None
                                and oldid_entry["kind"] == "revision"
                                and {"url": url, "pageId": former["pageId"]}
                                in oldid_entry["sourceIdentity"]["pageIds"]
                                and {
                                    "url": url, "title": former["formerTitle"],
                                } in oldid_entry["revision"]["pages"],
                                "Historical-only revision lost page ID/title association",
                            )
                    _require(
                        former_paths == {
                            path for path, item in entries.items()
                            if item["kind"] == "historical-page"
                        },
                        "Historical-only index and inventory member sets differ",
                    )
                else:
                    _require(
                        not any(item["kind"] == "historical-page"
                                for item in entries.values()),
                        "Historical-only members require history index v2",
                    )
            else:
                _require(document["formatVersion"] == 1,
                         "History inventory v2 cannot be non-history")
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
                upload_digest = (
                    hashlib.sha1() if item.get("uploadVersion") is not None else None
                )
                size = 0
                with source_zip.open(info) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        if upload_digest is not None:
                            upload_digest.update(chunk)
                        size += len(chunk)
                _require(size == info.file_size == item["size"]
                         and digest.hexdigest() == item["sha256"],
                         f"Inventory member differs: {info.filename}")
                if upload_digest is not None:
                    _require(
                        upload_digest.hexdigest() ==
                        item["uploadVersion"].get("sha1Hex"),
                        f"MediaWiki upload SHA-1 differs from member at {info.filename}",
                    )
                _member_metadata(
                    item, path=info.filename, paths=paths, history=history,
                    history_sources=revision_sources,
                    site=site, language=language,
                )

            history_refresh = document["captureMetadata"].get("historyRefresh")
            if history_refresh is not None:
                current_by_url = {
                    item["page"]["url"]: item
                    for item in entries.values()
                    if item["kind"] == "current-page"
                }
                page_mappings = history_refresh["pageMappings"]
                previous_urls: set[str] = set()
                for mapping in page_mappings:
                    _require(
                        isinstance(mapping, dict)
                        and set(mapping) == {
                            "pageId", "previousUrl", "currentUrl",
                            "identityBasis", "changedUrl",
                        }, "Malformed historical page mapping")
                    prior_url = mapping["previousUrl"]
                    new_url = mapping["currentUrl"]
                    current_member = current_by_url.get(new_url)
                    if new_url is None:
                        former_index = history_index.get(
                            "historicalOnlyPages", [],
                        )
                        candidates = [
                            row for row in former_index
                            if row["formerUrl"] == prior_url
                        ]
                        missing_ids = metadata["parentPageIdsAbsentFromEnumeration"]
                        _require(
                            document["formatVersion"] == 2
                            and len(candidates) == 1
                            and mapping["pageId"] == candidates[0]["pageId"]
                            and mapping["identityBasis"]
                            == "verified-parent-id-absent-from-allpages"
                            and mapping["changedUrl"] is True
                            and any(
                                isinstance(row, dict)
                                and row.get("pageId") == mapping["pageId"]
                                and row.get("oldUrl") == prior_url
                                and row.get("status")
                                == "absent-from-current-enumeration"
                                for row in missing_ids
                            ),
                            "Unverified historical-only page mapping",
                        )
                    else:
                        _require(
                            current_member is not None
                            and current_member["sourceIdentity"]["pageId"]
                            == mapping["pageId"]
                            and mapping["identityBasis"]
                            == "verified-allpages-page-id"
                            and mapping["changedUrl"] is (prior_url != new_url),
                            "Historical page mapping contradicts current identity",
                        )
                    _require(
                        isinstance(prior_url, str)
                        and prior_url not in previous_urls
                        and type(mapping["pageId"]) is int
                        and mapping["pageId"] > 0,
                        "Historical mapping previous identity is invalid",
                    )
                    previous_urls.add(prior_url)
                if document["formatVersion"] == 2:
                    _require(
                        history_refresh["historicalOnlyPageCount"] ==
                        len(history_index["historicalOnlyPages"])
                        and history_index["historyCoverage"] ==
                        "current-all-observed-former-last-archived",
                        "Historical-only coverage differs from index",
                    )
                _require(
                    history_refresh["freshOldidCount"] ==
                    sum(item["kind"] == "revision"
                        for item in entries.values()),
                    "Historical refresh member/index coverage mismatch",
                )
            conditional_count = sum(
                1 for item in entries.values()
                if item.get("kind") == "asset"
                and item.get("sourceVersion", {}).get(
                    "conditionalRevalidated", False,
                )
            )
            revision_observations = [
                item["currentRevision"] for item in entries.values()
                if item["kind"] == "current-page"
                and "currentRevision" in item
            ]
            revision_coverage = document["captureMetadata"].get(
                "currentRevisionEvidence",
            )
            if revision_coverage is not None:
                revision_coverage = _object(
                    revision_coverage, "currentRevisionEvidence",
                )
                _require(
                    set(revision_coverage) == {
                        "pageCount", "observedCount", "renderCorrespondence",
                    }
                    and revision_coverage["renderCorrespondence"] == "not-verified"
                    and type(revision_coverage["pageCount"]) is int
                    and revision_coverage["pageCount"] == len(revision_observations)
                    and revision_coverage["pageCount"]
                    == sum(item["kind"] == "current-page"
                           for item in entries.values())
                    and type(revision_coverage["observedCount"]) is int
                    and revision_coverage["observedCount"] == sum(
                        item["status"] == "observed"
                        for item in revision_observations
                    ),
                    "Current revision observation coverage contradicts members",
                )
            else:
                _require(
                    not revision_observations,
                    "Unreported current revision observations",
                )
            metadata = document["captureMetadata"]
            inherited_update = metadata.get("inheritedCurrentUpdateEvidence")
            if inherited_update is not None:
                _require(
                    parent is not None and history
                    and metadata.get("historyCutoff") == "all-observed"
                    and "updateMode" not in metadata
                    and isinstance(inherited_update, dict)
                    and inherited_update.get("updateMode") in {
                        "verified-full-source-refresh",
                        "conditional-asset-refresh",
                        "verified-asset-version-refresh",
                    }
                    and all(
                        metadata.get(key) == inherited_update.get(key)
                        for key in (
                            "httpHeadersAvailable", "mediaWikiPageIdsAvailable",
                            "currentRevisionEvidence", "pageRelationshipEvidence",
                        )
                    ),
                    "Inherited current-update evidence has invalid scope",
                )
                if "sourceTimeCoverage" in inherited_update:
                    from infinity_wiki_acquisition.source_time_coverage import (
                        assess_source_time_coverage,
                    )

                    _require(
                        inherited_update["sourceTimeCoverage"]
                        == assess_source_time_coverage(
                            entries.values(),
                            recent_changes=inherited_update.get("recentChanges"),
                            page_moves=inherited_update.get("pageMovesById"),
                            absent_page_ids=inherited_update.get(
                                "parentPageIdsAbsentFromEnumeration",
                            ),
                        ),
                        "Inherited source-time assessment is inconsistent",
                    )
                if "renderInvalidation" in inherited_update:
                    from infinity_wiki_acquisition.render_invalidation import (
                        assess_render_invalidation,
                    )

                    _require(
                        inherited_update["renderInvalidation"]
                        == assess_render_invalidation(
                            entries.values(),
                            recent_changes=inherited_update["recentChanges"],
                            page_moves=inherited_update["pageMovesById"],
                            absent_page_ids=inherited_update[
                                "parentPageIdsAbsentFromEnumeration"
                            ],
                            site=site, language=language,
                        ),
                        "Inherited render invalidation audit is inconsistent",
                    )
                if "renderReuseGate" in inherited_update:
                    from infinity_wiki_acquisition.render_reuse_gate import (
                        assess_render_reuse,
                    )

                    _require(
                        inherited_update["renderReuseGate"] == assess_render_reuse(
                            entries.values(),
                            revision_comparisons=inherited_update[
                                "currentRevisionComparisons"
                            ],
                            invalidation=inherited_update["renderInvalidation"],
                            source_time=inherited_update["sourceTimeCoverage"],
                            recent_changes=inherited_update["recentChanges"],
                        ),
                        "Inherited rendered HTML reuse gate is inconsistent",
                    )
            relationship_coverage = metadata.get(
                "pageRelationshipEvidence",
            )
            relationship_rows = [
                item["pageRelationships"] for item in entries.values()
                if item["kind"] == "current-page"
                and "pageRelationships" in item
            ]
            if relationship_coverage is not None:
                from infinity_wiki_acquisition.page_relationships import (
                    assess_relationship_coverage,
                )

                _require(
                    parent is not None
                    and (
                        metadata.get("updateMode") == "verified-asset-version-refresh"
                        or (
                            metadata.get("historyCutoff") == "all-observed"
                            and isinstance(inherited_update, dict)
                            and inherited_update.get("updateMode")
                            == "verified-asset-version-refresh"
                        )
                    )
                    and len(relationship_rows) == sum(
                        item["kind"] == "current-page"
                        for item in entries.values()
                    )
                    and relationship_coverage == assess_relationship_coverage(
                        entries.values(),
                    ),
                    "Page relationship coverage contradicts member evidence",
                )
            else:
                _require(not relationship_rows,
                         "Unreported MediaWiki page relationship observations")
            source_time = document["captureMetadata"].get("sourceTimeCoverage")
            if source_time is not None:
                from infinity_wiki_acquisition.source_time_coverage import (
                    assess_source_time_coverage,
                )

                metadata = document["captureMetadata"]
                observed_time = assess_source_time_coverage(
                    entries.values(),
                    recent_changes=metadata.get("recentChanges"),
                    page_moves=metadata.get("pageMovesById"),
                    absent_page_ids=metadata.get("parentPageIdsAbsentFromEnumeration"),
                )
                _require(
                    source_time == observed_time,
                    "Source-time coverage claims contradict acquired evidence",
                )
            invalidation = document["captureMetadata"].get("renderInvalidation")
            if invalidation is not None:
                from infinity_wiki_acquisition.render_invalidation import (
                    assess_render_invalidation,
                )

                update_metadata = document["captureMetadata"]
                _require(
                    parent is not None
                    and update_metadata.get("updateMode") in {
                        "verified-full-source-refresh",
                        "conditional-asset-refresh",
                        "verified-asset-version-refresh",
                    }
                    and update_metadata.get("recentChanges") is not None,
                    "Render invalidation audit requires update evidence",
                )
                expected_invalidation = assess_render_invalidation(
                    entries.values(),
                    recent_changes=update_metadata["recentChanges"],
                    page_moves=update_metadata["pageMovesById"],
                    absent_page_ids=update_metadata["parentPageIdsAbsentFromEnumeration"],
                    site=site, language=language,
                )
                _require(invalidation == expected_invalidation,
                         "Render invalidation claims contradict acquisition evidence")
            reuse_gate = metadata.get("renderReuseGate")
            if reuse_gate is not None:
                from infinity_wiki_acquisition.render_reuse_gate import (
                    assess_render_reuse,
                )

                _require(
                    parent is not None
                    and metadata.get("updateMode")
                    == "verified-asset-version-refresh"
                    and metadata.get("renderInvalidation") is not None
                    and metadata.get("sourceTimeCoverage") is not None
                    and isinstance(metadata.get("currentRevisionComparisons"), list)
                    and isinstance(metadata.get("recentChanges"), dict),
                    "Rendered HTML reuse gate requires update evidence",
                )
                _require(
                    reuse_gate == assess_render_reuse(
                        entries.values(),
                        revision_comparisons=metadata["currentRevisionComparisons"],
                        invalidation=metadata["renderInvalidation"],
                        source_time=metadata["sourceTimeCoverage"],
                        recent_changes=metadata["recentChanges"],
                    ),
                    "Rendered HTML reuse gate contradicts source evidence",
                )
            upload_evidence = [
                item["uploadVersion"] for item in entries.values()
                if item.get("kind") == "asset"
                and item.get("uploadVersion") is not None
            ]
            upload_reuse_count = sum(
                1 for entry in upload_evidence if entry["usedForReuse"]
            )
            if parent is None:
                _require(
                    conditional_count == 0 and upload_reuse_count == 0,
                    "A standalone snapshot cannot claim reused remote assets",
                )
            else:
                mode = document["captureMetadata"].get("updateMode")
                if mode in {
                    "conditional-asset-refresh", "verified-asset-version-refresh",
                }:
                    _require(
                        conditional_count ==
                        document["captureMetadata"]["conditionalReusedAssetCount"],
                        "Conditional asset count differs from recorded member evidence",
                    )
                if mode == "verified-asset-version-refresh":
                    _require(
                        upload_reuse_count ==
                        document["captureMetadata"]["uploadSha1ReusedAssetCount"]
                        and len(upload_evidence) ==
                        document["captureMetadata"]["uploadVersionEvidenceCount"],
                        "MediaWiki upload reuse count differs from member evidence",
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
