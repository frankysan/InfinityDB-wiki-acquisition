"""P2.4c.2a: bounded source-response and HTML rewrite provenance.

Recorded response metadata can support *future* conditional validation but does
not prove that a cached rewritten page can replay original navigation. A strong
ETag is a source-observed validator, never HTML reuse permission on its own.
"""

from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PROFILE_VERSION = 1
SCOPE = "observed-full-body-not-reuse-authority"
REPLAY_COVERAGE = "original-body-not-archived"
REWRITE_METHOD = "rewrite_html_links-utf8-replace-v1"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=16)
def _source_hash(path: str) -> str:
    """Producer implementation is fixed during a single acquisition process."""
    return _digest(Path(path).read_bytes())


def request_and_rewrite_profile(
    *,
    site: Any,
    language: str,
    transport_v2: bool = False,
) -> dict[str, Any]:
    """Fingerprint the exact installed rewrite implementation and request headers."""
    from infinity_wiki_acquisition import cli, path_sanitization

    return {
        "site": site.key,
        "language": language,
        "requestMethod": "GET",
        "requestHeaders": {
            "User-Agent": cli.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": cli._site_accept_language(language),
            **({"Accept-Encoding": "identity"} if transport_v2 else {}),
        },
        "rewriteMethod": REWRITE_METHOD,
        "producerSourceSha256": _source_hash(cli.__file__),
        "sanitizerSourceSha256": _source_hash(path_sanitization.__file__),
        "pythonVersion": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _clean_header(value: Any) -> str | None:
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value


def capture_rendered_response(
    *,
    url: str,
    path: str,
    payload: bytes,
    rewritten: bytes,
    fetched_at: str,
    site: Any,
    language: str,
) -> dict[str, Any]:
    from infinity_wiki_acquisition.cli import _is_strong_etag

    status = getattr(payload, "http_status", None)
    if type(status) is not int or status < 100 or status > 599:
        status = None
    effective_url = getattr(payload, "effective_url", None)
    if not isinstance(effective_url, str) or not effective_url:
        effective_url = None
    observed_etag = _clean_header(getattr(payload, "etag", None))
    last_modified = _clean_header(getattr(payload, "last_modified", None))
    validator = (
        observed_etag
        if (status == 200 and effective_url == url
            and _is_strong_etag(observed_etag))
        else None
    )
    if status == 304:
        raise ValueError("A source HTML 304 requires a verified conditional reuse path")
    if not isinstance(payload, bytes) or not isinstance(rewritten, bytes):
        raise ValueError("Expected original and rewritten HTML bytes")
    from infinity_wiki_acquisition.html_transport import derive_transport_variant

    raw_headers = {
        "Vary": getattr(payload, "vary", None),
        "Content-Encoding": getattr(payload, "content_encoding", None),
        "Content-Language": getattr(payload, "content_language", None),
        "Cache-Control": getattr(payload, "cache_control", None),
    }
    variant = None
    if getattr(payload, "headers_observed", False) is True:
        try:
            variant = derive_transport_variant(
                raw_headers=raw_headers,
                status=status, effective_url=effective_url, url=url,
                strong_etag=validator, language=language,
                request_headers=request_and_rewrite_profile(
                    site=site, language=language, transport_v2=True,
                )["requestHeaders"],
            )
        except ValueError:
            # Unusable HTTP headers do not turn a successful source-body
            # download into permission to reuse the response.
            variant = None
    return {
        "formatVersion": 2 if variant is not None else PROFILE_VERSION,
        "scope": SCOPE,
        "pageUrl": url,
        "memberPath": path,
        "htmlFetchedAt": fetched_at,
        "originalResponseSha256": _digest(payload),
        "rewrittenMemberSha256": _digest(rewritten),
        "httpStatus": status,
        "httpEffectiveUrl": effective_url,
        "observedEtag": observed_etag,
        "strongEtag": validator,
        "lastModified": last_modified,
        "profile": request_and_rewrite_profile(
            site=site, language=language, transport_v2=variant is not None,
        ),
        **({"transportVariant": variant} if variant is not None else {}),
        "replayCoverage": REPLAY_COVERAGE,
        "reuseAuthorized": False,
    }


def validate_rendered_response(
    evidence: Any,
    *,
    member: dict[str, Any],
    site: Any,
    language: str,
) -> None:
    """Offline structural/ZIP binding checks, not remote-server authentication."""
    from infinity_wiki_acquisition.cli import _is_strong_etag
    from infinity_wiki_acquisition.page_relationships import _time

    version = evidence.get("formatVersion") if isinstance(evidence, dict) else None
    required = {
        "formatVersion", "scope", "pageUrl", "memberPath",
        "htmlFetchedAt", "originalResponseSha256", "rewrittenMemberSha256",
        "httpStatus", "httpEffectiveUrl", "observedEtag", "strongEtag",
        "lastModified", "profile", "replayCoverage", "reuseAuthorized",
    }
    if (not isinstance(evidence, dict)
            or version not in {1, 2}
            or set(evidence) != (required | (
                {"transportVariant"} if version == 2 else set()
            ))
            or evidence["scope"] != SCOPE
            or evidence["replayCoverage"] != REPLAY_COVERAGE
            or evidence["reuseAuthorized"] is not False
            or evidence["pageUrl"] != member["page"]["url"]
            or evidence["memberPath"] != member["path"]
            or evidence["rewrittenMemberSha256"] != member["sha256"]
            or evidence["originalResponseSha256"]
            != member["renderedIdentity"]["originalResponseSha256"]
            or evidence["httpStatus"] != member["sourceIdentity"]["httpStatus"]
            or evidence["httpEffectiveUrl"]
            != member["sourceIdentity"]["httpEffectiveUrl"]):
        raise ValueError("Contradictory rendered HTML source/ZIP evidence")
    _time(evidence["htmlFetchedAt"])
    for name in ("originalResponseSha256", "rewrittenMemberSha256"):
        value = evidence[name]
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError("Invalid HTML response digest")
    status = evidence["httpStatus"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599
                               or status == 304):
        raise ValueError("Unsupported HTML response HTTP status")
    effective = evidence["httpEffectiveUrl"]
    if effective is not None and (not isinstance(effective, str) or not effective):
        raise ValueError("Invalid HTML effective URL")
    for name in ("observedEtag", "lastModified"):
        value = evidence[name]
        if value is not None and _clean_header(value) != value:
            raise ValueError("Invalid HTML response header")
    validator = (
        evidence["observedEtag"]
        if (status == 200 and effective == evidence["pageUrl"]
            and _is_strong_etag(evidence["observedEtag"]))
        else None
    )
    if validator != evidence["strongEtag"]:
        raise ValueError("HTML validator is not a matching strong ETag")

    profile = evidence["profile"]
    if (not isinstance(profile, dict)
            or set(profile) != {
                "site", "language", "requestMethod", "requestHeaders",
                "rewriteMethod", "producerSourceSha256",
                "sanitizerSourceSha256", "pythonVersion",
            }
            or profile["site"] != site.key
            or profile["language"] != language
            or profile["requestMethod"] != "GET"
            or profile["rewriteMethod"] != REWRITE_METHOD):
        raise ValueError("Invalid HTML request/rewrite profile")
    headers = profile["requestHeaders"]
    if (not isinstance(headers, dict)
            or set(headers) != (
                {"User-Agent", "Accept", "Accept-Language"}
                | ({"Accept-Encoding"} if version == 2 else set())
            )
            or (version == 2 and headers["Accept-Encoding"] != "identity")
            or any(not isinstance(v, str) or not v or _clean_header(v) != v
                   for v in headers.values())
            or headers["Accept"]
            != "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            or not isinstance(profile["pythonVersion"], str)
            or not profile["pythonVersion"]
            or any(
                not isinstance(profile[key], str)
                or len(profile[key]) != 64
                or any(c not in "0123456789abcdef" for c in profile[key])
                for key in ("producerSourceSha256", "sanitizerSourceSha256")
            )):
        raise ValueError("Incomplete HTML request/rewrite fingerprint")
    if version == 2:
        from infinity_wiki_acquisition.html_transport import (
            derive_transport_variant,
        )

        variant = evidence["transportVariant"]
        if (not isinstance(variant, dict)
                or set(variant) != {
                    "formatVersion", "observedHeaders", "varyFields",
                    "transportProbeEligible", "unmetTransportConditions",
                    "reuseAuthorized",
                }
                or variant["reuseAuthorized"] is not False):
            raise ValueError("Invalid versioned HTML transport evidence")
        expected_variant = derive_transport_variant(
            raw_headers=variant["observedHeaders"],
            status=status, effective_url=effective,
            url=evidence["pageUrl"], strong_etag=evidence["strongEtag"],
            language=language, request_headers=headers,
        )
        if variant != expected_variant:
            raise ValueError("HTML transport probe evidence is inconsistent")
