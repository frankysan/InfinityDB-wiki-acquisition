"""Strict P2.4c.2b.2 HTTP transport evidence and offline probe planning.

This is *not* a positive rendered-HTML reuse policy. The plan is a pure
assessment of whether a conditional request could be attempted after a
verified original-body replay. Even a valid 304 never authorizes a cache
body skip because source-time and rendered dependency closure are unresolved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FORMAT = "InfinityDB wiki HTML conditional transport preflight"
VERSION = 1
VARIANT_VERSION = 1
VARIANT_FIELDS = ("Vary", "Content-Encoding", "Content-Language", "Cache-Control")
SUPPORTED_VARY = frozenset({
    "accept", "accept-language", "user-agent", "accept-encoding",
})
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9a-zA-Z-]+$")


def _clean_header(value: Any) -> bool:
    return (value is None or (
        isinstance(value, str) and 0 < len(value) <= 512
        and all(32 <= ord(c) < 127 for c in value)
    ))


def _vary_list(value: str | None) -> list[str] | None:
    if value is None:
        return []
    parts = [part.strip().lower() for part in value.split(",")]
    if any(not _TOKEN.fullmatch(part) for part in parts):
        return None
    return sorted(set(parts))


def derive_transport_variant(
    *,
    raw_headers: dict[str, str | None],
    status: int | None,
    effective_url: str | None,
    url: str,
    strong_etag: str | None,
    language: str,
    request_headers: dict[str, str],
) -> dict[str, Any]:
    """Recompute conditional-probe constraints from retained response headers."""
    if (not isinstance(raw_headers, dict)
            or set(raw_headers) != set(VARIANT_FIELDS)
            or any(not _clean_header(v) for v in raw_headers.values())):
        raise ValueError("Invalid observed transport headers")
    vary = _vary_list(raw_headers["Vary"])
    blockers: set[str] = set()
    if status != 200 or effective_url != url or not strong_etag:
        blockers.add("source-response-or-validator-not-exact")
    if (request_headers.get("Accept-Encoding") != "identity"
            or set(request_headers) != {
                "User-Agent", "Accept", "Accept-Language", "Accept-Encoding",
            }):
        blockers.add("request-profile-not-fully-explicit")
    if vary is None or "*" in vary:
        blockers.add("vary-wildcard-or-malformed")
    elif not set(vary).issubset(SUPPORTED_VARY):
        blockers.add("vary-contains-unsupported-fields")
    encoding = raw_headers["Content-Encoding"]
    if encoding is not None and encoding.strip().lower() != "identity":
        blockers.add("content-encoding-not-identity")
    content_language = raw_headers["Content-Language"]
    if (content_language is not None
            and (
                "," in content_language
                or content_language.split("-")[0].strip().casefold() != language.casefold()
            )):
        blockers.add("content-language-variant-uncertain")
    cache_control = raw_headers["Cache-Control"]
    if cache_control and any(
        token.strip().casefold() == "no-store"
        for token in cache_control.split(",")
    ):
        blockers.add("cache-control-no-store")
    return {
        "formatVersion": VARIANT_VERSION,
        "observedHeaders": dict(raw_headers),
        "varyFields": vary,
        "transportProbeEligible": not blockers,
        "unmetTransportConditions": sorted(blockers),
        "reuseAuthorized": False,
    }


def prepare_html_transport_probes(
    bundle: Path,
    archive: Path,
    manifest: Path,
    inventory: Path,
) -> dict[str, Any]:
    """Validate the parent trio and raw-source companion before probe planning.

    The plan is advisory and does not itself initiate HTTP traffic or
    change a snapshot member, checkpoint, or rendered HTML reuse gate.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.rendered_response import (
        request_and_rewrite_profile,
    )
    from infinity_wiki_acquisition.snapshot_provenance import sha256_file
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
    from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

    verify_source_replay_bundle(bundle, archive, manifest, inventory)
    parent = verify_snapshot(archive, manifest, inventory)
    site = cli.WIKI_SITES[parent["site"]]
    language = parent["language"]
    expected_profile = request_and_rewrite_profile(
        site=site, language=language, transport_v2=True,
    )

    decisions = []
    for member in sorted(
        (row for row in parent["members"] if row["kind"] == "current-page"),
        key=lambda row: row["page"]["url"],
    ):
        evidence = member.get("renderedResponseVersion")
        unmet = set()
        variant = evidence.get("transportVariant") if isinstance(evidence, dict) else None
        if (not isinstance(evidence, dict)
                or evidence.get("formatVersion") != 2
                or evidence.get("profile") != expected_profile
                or not isinstance(variant, dict)):
            unmet.add("versioned-transport-profile-unavailable")
        if isinstance(variant, dict):
            unmet.update(variant["unmetTransportConditions"])
        # Non-versioned parent responses have no independently validated
        # Vary/encoding data, even when an earlier strong ETag was observed.
        can_probe = not unmet
        decisions.append({
            "pageUrl": member["page"]["url"],
            "memberPath": member["path"],
            "status": (
                "transport-probe-candidate" if can_probe
                else "full-html-fetch-required"
            ),
            "conditionalEtag": evidence["strongEtag"] if can_probe else None,
            "parentVariant": variant if can_probe else None,
            "unmetTransportConditions": sorted(unmet),
            "reuseAuthorized": False,
        })
    return {
        "format": FORMAT, "formatVersion": VERSION,
        "scope": "verified-parent-transport-probes-not-render-reuse",
        "sourceArchiveSha256": sha256_file(archive),
        "sourceManifestSha256": sha256_file(manifest),
        "sourceInventorySha256": sha256_file(inventory),
        "sourceReplaySha256": sha256_file(bundle),
        "currentPageCount": len(decisions),
        "transportProbeCandidateCount": sum(
            row["status"] == "transport-probe-candidate"
            for row in decisions
        ),
        "reuseAuthorized": False,
        "decisions": decisions,
    }


def verify_html_transport_response(
    bundle: Path,
    archive: Path,
    manifest: Path,
    inventory: Path,
    *,
    request_url: str,
    result: bytes,
) -> dict[str, Any]:
    """Full offline parent binding before evaluating a conditional HTTP result.

    Call this entrypoint for external responses. A stand-alone decision is
    advisory and must not be accepted as a claimed verified parent.
    """
    plan = prepare_html_transport_probes(bundle, archive, manifest, inventory)
    candidate = next(
        (item for item in plan["decisions"] if item["pageUrl"] == request_url),
        None,
    )
    if candidate is None:
        raise ValueError("Conditional URL is not a verified current source page")
    assessment = assess_html_transport_response(candidate, result)
    return {
        **assessment,
        "sourceArchiveSha256": plan["sourceArchiveSha256"],
        "sourceReplaySha256": plan["sourceReplaySha256"],
    }


def assess_html_transport_response(
    decision: dict[str, Any],
    result: bytes,
) -> dict[str, Any]:
    """Check a synthetic/live HTTP conditional result against a bound probe.

    Invalid 304, redirect, unobserved Vary or negotiated encoding fail.
    An HTTP 200 means the source body must be consumed by the ordinary
    full-fetch/rewrite workflow; no cached representation is substituted.
    """
    from infinity_wiki_acquisition.cli import _is_strong_etag

    if (decision.get("status") != "transport-probe-candidate"
            or decision.get("reuseAuthorized") is not False
            or not _is_strong_etag(decision.get("conditionalEtag"))
            or not isinstance(decision.get("parentVariant"), dict)
            or decision["parentVariant"].get("transportProbeEligible") is not True
            or decision.get("unmetTransportConditions") != []):
        raise ValueError("Transport probe was not eligible")
    url, etag = decision["pageUrl"], decision["conditionalEtag"]
    if (not isinstance(result, bytes)
            or getattr(result, "effective_url", None) != url
            or getattr(result, "conditional_etag", None) != etag):
        raise ValueError("Conditional HTML request identity is inconsistent")
    status = getattr(result, "http_status", None)
    if status == 200:
        if not result:
            raise ValueError("Empty HTML 200 is not proof of retained content")
        return {
            "status": "changed-or-unvalidated-html-body",
            "pageUrl": url, "fetchBodyRequired": True,
            "reuseAuthorized": False,
        }
    if status != 304 or result != b"" or getattr(result, "etag", None) != etag:
        raise ValueError("Unverified conditional HTML 304")
    if getattr(result, "headers_observed", False) is not True:
        raise ValueError("Unobserved conditional HTTP response headers")
    parent = decision["parentVariant"]["observedHeaders"]
    reply_headers = {
        "Vary": getattr(result, "vary", None),
        "Content-Encoding": getattr(result, "content_encoding", None),
        "Content-Language": getattr(result, "content_language", None),
        "Cache-Control": getattr(result, "cache_control", None),
    }
    if any(not _clean_header(item) for item in reply_headers.values()):
        raise ValueError("Malformed conditional HTTP headers")
    # RFC 9110 requires Vary on a 304 if it would be sent with 200.
    if _vary_list(reply_headers["Vary"]) != decision["parentVariant"]["varyFields"]:
        raise ValueError("Conditional HTTP Vary differs from source representation")
    if (reply_headers["Content-Encoding"] is not None
            and reply_headers["Content-Encoding"] != parent["Content-Encoding"]):
        raise ValueError("Conditional response content encoding changed")
    if (reply_headers["Content-Language"] is not None
            and reply_headers["Content-Language"] != parent["Content-Language"]):
        raise ValueError("Conditional response content language changed")
    if (reply_headers["Cache-Control"] is not None
            and "no-store" in {
                token.strip().casefold()
                for token in reply_headers["Cache-Control"].split(",")
            }):
        raise ValueError("Conditional response disallows storing the source body")
    return {
        "status": "transport-304-confirmed-source-representation",
        "pageUrl": url, "fetchBodyRequired": True,
        "reuseAuthorized": False,
        "missingAuthority": "render-freshness-and-atomicity-unverified",
    }
