"""P2.4c.2b.2 explicit HTTP variant evidence and non-authorizing preflight."""

from __future__ import annotations

from email.message import Message

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.html_transport import (
    assess_html_transport_response,
    prepare_html_transport_probes,
    verify_html_transport_response,
)
from infinity_wiki_acquisition.rendered_response import (
    capture_rendered_response, validate_rendered_response,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from test_rendered_response import _member
from test_source_replay import _updated
from test_snapshot_update import LANG, ROOT, SITE


def _response(
    *, vary=None, encoding=None, content_language=None, cache_control=None,
    etag='"strong"', url=ROOT, status=200, observed=True,
):
    return cli.WikiFetchedBytes(
        b'<html><body><a href="/Other">Other</a></body></html>',
        effective_url=url, http_status=status, etag=etag,
        vary=vary, content_encoding=encoding, content_language=content_language,
        cache_control=cache_control, headers_observed=observed,
    )


def _captured(body):
    return capture_rendered_response(
        url=ROOT, path="Main_Page.html", payload=body,
        rewritten=cli.rewrite_html_links(
            body.decode(), ROOT, language=LANG, site=SITE,
        ).encode(),
        fetched_at="2026-11-15T18:00:00+00:00",
        site=SITE, language=LANG,
    )


def test_v2_response_captures_vary_and_explicit_identity_coding():
    row = _captured(_response(
        vary="Accept-Language, Accept-Encoding",
        content_language="en", cache_control="public, max-age=30",
    ))
    assert row["formatVersion"] == 2
    assert row["profile"]["requestHeaders"]["Accept-Encoding"] == "identity"
    variant = row["transportVariant"]
    assert variant["varyFields"] == ["accept-encoding", "accept-language"]
    assert variant["transportProbeEligible"] is True
    assert variant["unmetTransportConditions"] == []
    assert row["reuseAuthorized"] is variant["reuseAuthorized"] is False
    validate_rendered_response(row, member=_member(row), site=SITE, language=LANG)


@pytest.mark.parametrize("args,block", [
    ({"vary": "*"}, "vary-wildcard-or-malformed"),
    ({"vary": "Cookie"}, "vary-contains-unsupported-fields"),
    ({"vary": "Accept-Language,,Accept"}, "vary-wildcard-or-malformed"),
    ({"encoding": "gzip"}, "content-encoding-not-identity"),
    ({"content_language": "es"}, "content-language-variant-uncertain"),
    ({"cache_control": "private, no-store"}, "cache-control-no-store"),
    ({"etag": 'W/"weak"'}, "source-response-or-validator-not-exact"),
    ({"url": ROOT + "/redirect"}, "source-response-or-validator-not-exact"),
])
def test_ineligible_transport_variants_never_promote_etag(args, block):
    row = _captured(_response(**args))
    variant = row["transportVariant"]
    assert row["formatVersion"] == 2
    assert variant["transportProbeEligible"] is False
    assert block in variant["unmetTransportConditions"]
    validate_rendered_response(row, member=_member(row), site=SITE, language=LANG)


def test_no_http_header_metadata_remains_backward_compatible_v1():
    row = _captured(_response(observed=False))
    assert row["formatVersion"] == 1
    assert "transportVariant" not in row
    assert "Accept-Encoding" not in row["profile"]["requestHeaders"]
    validate_rendered_response(row, member=_member(row), site=SITE, language=LANG)


@pytest.mark.parametrize("attack", [
    "vary-list", "probe-authorized", "reuse-authorized", "profile",
    "unmet-conditions", "garbage-header",
])
def test_tampered_version_two_variant_cannot_validate(attack):
    row = _captured(_response(vary="Accept-Encoding"))
    if attack == "vary-list":
        row["transportVariant"]["varyFields"] = []
    elif attack == "probe-authorized":
        row["transportVariant"]["transportProbeEligible"] = False
    elif attack == "reuse-authorized":
        row["transportVariant"]["reuseAuthorized"] = True
    elif attack == "profile":
        row["profile"]["requestHeaders"]["Accept-Encoding"] = "gzip"
    elif attack == "unmet-conditions":
        row["transportVariant"]["unmetTransportConditions"] = ["unknown"]
    else:
        row["transportVariant"]["observedHeaders"]["Vary"] = "Accept\nEncoding"
    with pytest.raises(ValueError):
        validate_rendered_response(row, member=_member(row), site=SITE, language=LANG)


def test_http_fetch_captures_request_variant_and_response_headers(monkeypatch):
    headers = Message()
    for key, value in (
        ("ETag", '"source-v2"'),
        ("Vary", "Accept-Encoding, Accept-Language"),
        ("Content-Encoding", "identity"),
        ("Content-Language", "en"),
        ("Cache-Control", "public, max-age=60"),
    ):
        headers[key] = value
    observed = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def geturl(self):
            return ROOT
        def read(self):
            return b"<html>selected representation</html>"
        @property
        def headers(self):
            return headers

    def fake_urlopen(request, timeout=30):
        observed["request"] = request
        return Response()

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    body = cli.fetch_bytes(ROOT, language=LANG)
    assert observed["request"].get_header("Accept-encoding") == "identity"
    assert body.headers_observed is True
    assert body.vary == "Accept-Encoding, Accept-Language"
    assert body.content_encoding == "identity"
    assert body.content_language == "en"
    assert body.cache_control == "public, max-age=60"
    assert _captured(body)["transportVariant"]["transportProbeEligible"] is True


def test_http_fetch_304_retains_vary_headers_for_strict_evaluation(monkeypatch):
    import urllib.error

    headers = Message()
    headers["ETag"] = '"source-v2"'
    headers["Vary"] = "Accept-Language"

    def fake_urlopen(request, timeout=30):
        return_marker = request.get_header("If-none-match")
        assert return_marker == '"source-v2"'
        raise urllib.error.HTTPError(
            ROOT, 304, "Not Modified", hdrs=headers, fp=None,
        )

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    body = cli.fetch_bytes(ROOT, if_none_match='"source-v2"')
    assert body.headers_observed is True
    assert body.vary == "Accept-Language"
    assert body.conditional_etag == '"source-v2"'
    assert body.http_status == 304
    assert body == b""


def test_preflight_never_grants_reuse_for_old_v1_transport(tmp_path, monkeypatch):
    result, _calls = _updated(tmp_path, monkeypatch, strong=True)
    plan = prepare_html_transport_probes(
        result.source_replay, result.archive, result.manifest, result.inventory,
    )
    assert plan["reuseAuthorized"] is False
    assert plan["transportProbeCandidateCount"] == 0
    assert plan["currentPageCount"] == 3
    for item in plan["decisions"]:
        assert item["status"] == "full-html-fetch-required"
        assert item["reuseAuthorized"] is False
        assert "versioned-transport-profile-unavailable" in item["unmetTransportConditions"]


def test_preflight_identifies_only_verified_200_exact_url_variant(
    tmp_path, monkeypatch,
):
    # Build parent snapshot with one fully observed representation variant.
    from test_snapshot_update import (
        _build_parent, _remote, NEW_TIME,
    )
    from infinity_wiki_acquisition.snapshot_update import update_snapshot

    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True)
    old_fetch = cli.fetch_site_bytes

    def fetch(url, *, site, language, **kwargs):
        body = old_fetch(url, site=site, language=language, **kwargs)
        if url == ROOT:
            return cli.WikiFetchedBytes(
                body, effective_url=url, http_status=200,
                etag='"page-v2"', headers_observed=True,
                vary="Accept-Encoding, Accept-Language",
                content_encoding="identity", content_language="en",
            )
        return body

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    updated = update_snapshot(
        *parent, archive_directory=tmp_path / "archive-new",
        manifest_directory=tmp_path / "manifest-new",
        work_root=tmp_path / "work-new", now=NEW_TIME,
        source_replay_directory=tmp_path / "replay-new",
    )
    plan = prepare_html_transport_probes(
        updated.source_replay, updated.archive, updated.manifest, updated.inventory,
    )
    assert plan["currentPageCount"] == 3
    assert plan["transportProbeCandidateCount"] == 1
    eligible = next(row for row in plan["decisions"] if row["pageUrl"] == ROOT)
    assert eligible["status"] == "transport-probe-candidate"
    assert eligible["conditionalEtag"] == '"page-v2"'
    assert eligible["reuseAuthorized"] is False

    def reply(*, status=304, etag='"page-v2"', effective=ROOT,
              conditional='"page-v2"', vary="Accept-Encoding, Accept-Language",
              observed=True, payload=b""):
        return cli.WikiFetchedBytes(
            payload, effective_url=effective, http_status=status,
            etag=etag, conditional_etag=conditional,
            headers_observed=observed, vary=vary,
        )
    checked = assess_html_transport_response(eligible, reply())
    assert checked == {
        "status": "transport-304-confirmed-source-representation",
        "pageUrl": ROOT, "fetchBodyRequired": True,
        "reuseAuthorized": False,
        "missingAuthority": "render-freshness-and-atomicity-unverified",
    }
    verified = verify_html_transport_response(
        updated.source_replay, updated.archive, updated.manifest, updated.inventory,
        request_url=ROOT, result=reply(),
    )
    assert verified["status"] == checked["status"]
    assert verified["sourceArchiveSha256"] == plan["sourceArchiveSha256"]
    assert verified["sourceReplaySha256"] == plan["sourceReplaySha256"]
    assert verified["reuseAuthorized"] is False

    for response in (
        reply(vary=None), reply(vary="Accept-Language"),
        reply(etag='"other"'), reply(effective=ROOT+"/redirect"),
        reply(conditional='"other"'), reply(observed=False),
        reply(payload=b"wrong"),
    ):
        with pytest.raises(ValueError):
            assess_html_transport_response(eligible, response)
    full_body = assess_html_transport_response(
        eligible, reply(status=200, payload=b"<html>fresh</html>"),
    )
    assert full_body["fetchBodyRequired"] is True
    assert full_body["reuseAuthorized"] is False

    with pytest.raises(ValueError):
        blocked = next(
            row for row in plan["decisions"] if row["pageUrl"] != ROOT
        )
        assess_html_transport_response(blocked, reply())

    # The actual acquisition never sends conditional page headers; it still
    # refetches every current HTML member even when a transport probe
    # would have been eligible after independently verified parent replay.
    inventory = verify_snapshot(
        updated.archive, updated.manifest, updated.inventory,
    )
    assert inventory["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0
