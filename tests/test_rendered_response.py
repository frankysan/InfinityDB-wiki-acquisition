"""P2.4c.2a: rendered HTML transport validators without unsafe body skipping."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.rendered_response import (
    capture_rendered_response,
    validate_rendered_response,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_snapshot_update import ROOT, SITE, LANG, _build_parent, _remote, _update


def _source(version='"strong-v1"', *, status=200, effective=None):
    return cli.WikiFetchedBytes(
        b'<html><a href="/Another_Page">See more</a></html>',
        effective_url=ROOT if effective is None else effective,
        http_status=status, etag=version, last_modified="Sun, 15 Nov 2026 12:00:00 GMT",
    )


def _evidence(body=None):
    raw = _source() if body is None else body
    mirrored = cli.rewrite_html_links(
        raw.decode(), ROOT, language=LANG, site=SITE,
    ).encode()
    return capture_rendered_response(
        url=ROOT, path="Main_Page.html",
        payload=raw, rewritten=mirrored,
        fetched_at="2026-11-15T17:00:00+00:00",
        site=SITE, language=LANG,
    )


def _member(item):
    return {
        "kind": "current-page", "path": "Main_Page.html",
        "page": {"url": ROOT},
        "sha256": item["rewrittenMemberSha256"],
        "sourceIdentity": {
            "httpStatus": item["httpStatus"],
            "httpEffectiveUrl": item["httpEffectiveUrl"],
        },
        "renderedIdentity": {
            "originalResponseSha256": item["originalResponseSha256"],
        },
    }


def test_strong_http_etag_is_observed_but_not_reuse_permission():
    result = _evidence()
    assert result["strongEtag"] == '"strong-v1"'
    assert result["observedEtag"] == '"strong-v1"'
    assert result["reuseAuthorized"] is False
    assert result["replayCoverage"] == "original-body-not-archived"
    assert result["originalResponseSha256"] == hashlib.sha256(_source()).hexdigest()
    assert result["rewrittenMemberSha256"] != result["originalResponseSha256"]
    assert result["profile"]["site"] == SITE.key
    assert result["profile"]["language"] == LANG
    assert result["profile"]["requestHeaders"]["Accept-Language"]
    assert len(result["profile"]["producerSourceSha256"]) == 64
    assert len(result["profile"]["sanitizerSourceSha256"]) == 64
    validate_rendered_response(result, member=_member(result), site=SITE, language=LANG)


@pytest.mark.parametrize("status,etag,effective,expected", [
    (200, 'W/"weak"', None, None),
    (200, None, None, None),
    (200, '"quoted"', ROOT + "redirected", None),
    (302, '"quoted"', None, None),
    (None, '"quoted"', None, None),
])
def test_weak_redirected_or_unobserved_responses_cannot_offer_strong_validator(
    status, etag, effective, expected,
):
    row = _evidence(_source(etag, status=status, effective=effective))
    assert row["strongEtag"] == expected
    validate_rendered_response(row, member=_member(row), site=SITE, language=LANG)


@pytest.mark.parametrize("change", [
    "reused", "hash", "source-hash", "etag", "profile", "url", "status", "fetch-time",
])
def test_offline_validator_rejects_promoted_or_unbound_claims(change):
    row = _evidence()
    member = _member(row)
    if change == "reused":
        row["reuseAuthorized"] = True
    elif change == "hash":
        row["rewrittenMemberSha256"] = "f" * 64
    elif change == "source-hash":
        row["originalResponseSha256"] = "a" * 64
    elif change == "etag":
        row["strongEtag"] = '"different"'
    elif change == "profile":
        row["profile"]["site"] = "infinity-wiki"
    elif change == "url":
        row["pageUrl"] = "https://example.invalid/unsafe"
    elif change == "status":
        row["httpStatus"] = 304
    elif change == "fetch-time":
        row["htmlFetchedAt"] = "not-a-time"
    with pytest.raises(ValueError):
        validate_rendered_response(row, member=member, site=SITE, language=LANG)


def test_unconditional_304_is_rejected_before_page_publication():
    with pytest.raises(ValueError, match="304"):
        _evidence(_source(status=304))


def test_inventory_binds_html_response_to_verifiable_zip_bytes(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True)
    basic_fetch = cli.fetch_site_bytes

    def fetch(url, *, language, site, **kwargs):
        result = basic_fetch(url, language=language, site=site, **kwargs)
        if url == ROOT:
            return cli.WikiFetchedBytes(
                bytes(result),
                effective_url=ROOT, http_status=200, etag='"page-v1"',
            )
        return result

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    updated = _update(parent, tmp_path)
    inventory = verify_snapshot(updated.archive, updated.manifest, updated.inventory)
    page = next(
        row for row in inventory["members"]
        if row["kind"] == "current-page" and row["page"]["url"] == ROOT
    )
    observed = page["renderedResponseVersion"]
    assert observed["strongEtag"] == '"page-v1"'
    assert observed["originalResponseSha256"] == page[
        "renderedIdentity"
    ]["originalResponseSha256"]
    assert observed["rewrittenMemberSha256"] == page["sha256"]
    assert observed["reuseAuthorized"] is False
    assert inventory["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0

    broken = copy.deepcopy(inventory)
    broken_page = next(
        row for row in broken["members"]
        if row["kind"] == "current-page" and row["page"]["url"] == ROOT
    )
    broken_page["renderedResponseVersion"]["strongEtag"] = '"forged"'
    updated.inventory.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="Invalid rendered HTML"):
        verify_snapshot(updated.archive, updated.manifest, updated.inventory)


def test_html_metadata_fallback_without_http_headers_still_safe(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    updated = _update(parent, tmp_path)
    inventory = verify_snapshot(updated.archive, updated.manifest, updated.inventory)
    for page in (r for r in inventory["members"] if r["kind"] == "current-page"):
        data = page["renderedResponseVersion"]
        assert data["httpStatus"] is None
        assert data["strongEtag"] is None
        assert data["reuseAuthorized"] is False
