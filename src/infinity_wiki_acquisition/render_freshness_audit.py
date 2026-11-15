"""Offline differential audit of rendered HTML versions across verified captures.

A 304 is only as reliable as the source implementation of its ETag.
These comparisons detect cases where a server reused a strong validator for
different original bytes or changed rendered HTML without changing the page's
own revision. A finite comparison can *disprove* validator sufficiency, but it
cannot prove completeness or authorize future HTML reuse.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

FORMAT = "InfinityDB wiki rendered response differential audit"
VERSION = 1


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Differential audit requires timezone-aware observations")
    return result


def _source_revision(member: dict[str, Any]) -> int | None:
    row = member.get("currentRevision")
    if not isinstance(row, dict) or row.get("status") != "observed":
        return None
    revision = row.get("revisionId")
    return revision if type(revision) is int and revision > 0 else None


def _comparable_response(first: dict[str, Any], second: dict[str, Any]) -> bool:
    old, new = first.get("renderedResponseVersion"), second.get("renderedResponseVersion")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    if (old.get("formatVersion") != 2 or new.get("formatVersion") != 2
            or old.get("httpStatus") != 200 or new.get("httpStatus") != 200
            or old.get("httpEffectiveUrl") != first["page"]["url"]
            or new.get("httpEffectiveUrl") != second["page"]["url"]
            or old.get("profile") != new.get("profile")):
        return False
    lhs, rhs = old.get("transportVariant"), new.get("transportVariant")
    if (not isinstance(lhs, dict) or not isinstance(rhs, dict)
            or lhs.get("transportProbeEligible") is not True
            or rhs.get("transportProbeEligible") is not True):
        return False
    old_headers, new_headers = lhs["observedHeaders"], rhs["observedHeaders"]
    return (
        lhs.get("varyFields") == rhs.get("varyFields")
        and old_headers["Content-Encoding"] == new_headers["Content-Encoding"]
        and old_headers["Content-Language"] == new_headers["Content-Language"]
    )


def compare_page_observations(
    previous: dict[str, Any],
    current: dict[str, Any],
    previous_raw_sha: str,
    current_raw_sha: str,
) -> dict[str, Any]:
    """Compare two *already verified* current-page members with the same URL.

    This pure helper does not authenticate an untrusted member. Use the
    archive-pair entrypoint for any external files or remote source evidence.
    """
    url = previous["page"]["url"]
    if current["page"]["url"] != url:
        raise ValueError("Cannot compare different current page URLs")
    old_id = previous["sourceIdentity"]["pageId"]
    new_id = current["sourceIdentity"]["pageId"]
    before = _source_revision(previous)
    after = _source_revision(current)
    revision_unchanged = (
        before is not None and after is not None and before == after
    )
    original_changed = previous_raw_sha != current_raw_sha
    identity_stable = (
        type(old_id) is int and old_id > 0
        and type(new_id) is int and old_id == new_id
    )
    label = "page-identity-unresolved"
    etag_equal = None
    if identity_stable:
        if not _comparable_response(previous, current):
            label = "representation-variant-or-transport-unverified"
        else:
            lhs = previous["renderedResponseVersion"]["strongEtag"]
            rhs = current["renderedResponseVersion"]["strongEtag"]
            if lhs is None or rhs is None:
                label = "strong-etag-unavailable"
            else:
                etag_equal = lhs == rhs
                label = (
                    "equal-etag-different-source-body"
                    if etag_equal and original_changed else
                    "equal-etag-same-source-body"
                    if etag_equal else
                    "changed-etag-different-source-body"
                    if original_changed else
                    "changed-etag-same-source-body"
                )
    return {
        "pageUrl": url,
        "previousPageId": old_id,
        "currentPageId": new_id,
        "previousRevisionId": before,
        "currentRevisionId": after,
        "originalSourceChanged": original_changed,
        "samePageRevisionRenderedDrift": (
            original_changed and revision_unchanged and identity_stable
        ),
        "sameStrongEtag": etag_equal,
        "assessment": label,
        "reuseAuthorized": False,
    }


def compare_rendered_captures(
    previous_bundle: Path,
    previous_archive: Path,
    previous_manifest: Path,
    previous_inventory: Path,
    current_bundle: Path,
    current_archive: Path,
    current_manifest: Path,
    current_inventory: Path,
) -> dict[str, Any]:
    """Verify both full archive/replay sets, then compare actual source bytes.

    Compares matching current page URLs only. Missing pages are not inferred
    deleted, and historical-only/oldid entries are not current-page evidence.
    """
    from infinity_wiki_acquisition.snapshot_provenance import sha256_file
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
    from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

    old_index = verify_source_replay_bundle(
        previous_bundle, previous_archive, previous_manifest, previous_inventory,
    )
    new_index = verify_source_replay_bundle(
        current_bundle, current_archive, current_manifest, current_inventory,
    )
    old = verify_snapshot(previous_archive, previous_manifest, previous_inventory)
    new = verify_snapshot(current_archive, current_manifest, current_inventory)
    if old["site"] != new["site"] or old["language"] != new["language"]:
        raise ValueError("Rendered captures must have the same site and language")
    old_at = _timestamp(old["snapshot"]["acquiredAt"])
    new_at = _timestamp(new["snapshot"]["acquiredAt"])
    if new_at < old_at:
        raise ValueError("Rendered capture observations are in reverse order")

    def members(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            row["page"]["url"]: row
            for row in document["members"] if row["kind"] == "current-page"
        }

    earlier, later = members(old), members(new)
    old_sha = {row["pageUrl"]: row["originalSha256"] for row in old_index["pages"]}
    new_sha = {row["pageUrl"]: row["originalSha256"] for row in new_index["pages"]}
    rows = []
    for url in sorted(earlier.keys() | later.keys()):
        if url not in earlier:
            rows.append({
                "pageUrl": url, "assessment": "newly-enumerated-page",
                "reuseAuthorized": False,
            })
        elif url not in later:
            rows.append({
                "pageUrl": url, "assessment": "not-in-current-enumeration-not-deleted",
                "reuseAuthorized": False,
            })
        else:
            before = earlier[url]["renderedResponseVersion"]
            after = later[url]["renderedResponseVersion"]
            if (_timestamp(after["htmlFetchedAt"]) < _timestamp(before["htmlFetchedAt"])):
                raise ValueError("Page response observations run backward")
            rows.append(compare_page_observations(
                earlier[url], later[url], old_sha[url], new_sha[url],
            ))

    def fingerprint(archive: Path, manifest: Path, inventory: Path, replay: Path,
                    document: dict[str, Any]) -> dict[str, Any]:
        return {
            "archiveSha256": sha256_file(archive),
            "manifestSha256": sha256_file(manifest),
            "inventorySha256": sha256_file(inventory),
            "sourceReplaySha256": sha256_file(replay),
            "acquiredAt": document["snapshot"]["acquiredAt"],
        }

    parent = new["parent"]
    direct_parent = (
        parent is not None
        and parent["archiveSha256"] == sha256_file(previous_archive)
        and parent["inventorySha256"] == sha256_file(previous_inventory)
        and parent["manifestSha256"] == sha256_file(previous_manifest)
        and parent["archiveName"] == previous_archive.name
    )
    return {
        "format": FORMAT, "version": VERSION,
        "scope": "verified-source-body-comparisons-not-render-freshness-proof",
        "site": old["site"], "language": old["language"],
        "previous": fingerprint(
            previous_archive, previous_manifest, previous_inventory,
            previous_bundle, old,
        ),
        "current": fingerprint(
            current_archive, current_manifest, current_inventory,
            current_bundle, new,
        ),
        "lineage": "direct-parent" if direct_parent else "independent-or-nonadjacent",
        "sameUrlPageCount": len(earlier.keys() & later.keys()),
        "newlyEnumeratedCount": len(later.keys() - earlier.keys()),
        "notCurrentlyEnumeratedCount": len(earlier.keys() - later.keys()),
        "equalStrongEtagDifferentBodyCount": sum(
            row["assessment"] == "equal-etag-different-source-body"
            for row in rows
        ),
        "samePageRevisionRenderedDriftCount": sum(
            row.get("samePageRevisionRenderedDrift") is True for row in rows
        ),
        "sourceTimeConsistency": "not-verified-atomic",
        "dependencyClosure": "unverified",
        "recentChangesRetention": "unverified",
        "reuseAuthorized": False,
        "pageObservations": rows,
    }
