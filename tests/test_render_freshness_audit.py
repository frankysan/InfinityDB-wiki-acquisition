"""P2.4c.2b.2b.1 differential source-rendering evidence tests."""

from __future__ import annotations

import json
import urllib.parse
from datetime import timedelta

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.html_transport import derive_transport_variant
from infinity_wiki_acquisition.render_freshness_audit import (
    compare_page_observations, compare_rendered_captures,
)
from infinity_wiki_acquisition.rendered_response import request_and_rewrite_profile
from infinity_wiki_acquisition.snapshot_update import update_snapshot
from test_snapshot_update import (
    DELETED, ROOT, ROOT_NEW, SITE, LANG, NEW_TIME, _build_parent, _remote,
)


def _row(etag: str | None, *, page_id=1, revision=101, variant=True):
    url = ROOT
    profile = request_and_rewrite_profile(
        site=SITE, language=LANG, transport_v2=True,
    )
    headers = {
        "Vary": "Accept-Encoding, Accept-Language",
        "Content-Encoding": "identity", "Content-Language": LANG,
        "Cache-Control": "public, max-age=20",
    }
    transport = derive_transport_variant(
        raw_headers=headers, status=200, effective_url=url, url=url,
        strong_etag=etag, language=LANG,
        request_headers=profile["requestHeaders"],
    )
    return {
        "page": {"url": url},
        "sourceIdentity": {"pageId": page_id},
        "currentRevision": {
            "status": "observed", "revisionId": revision,
        },
        "renderedResponseVersion": {
            "formatVersion": 2, "httpStatus": 200, "httpEffectiveUrl": url,
            "profile": profile,
            "strongEtag": etag, "transportVariant": transport,
        },
    }


@pytest.mark.parametrize("old_etag,new_etag,old_sha,new_sha,expected", [
    ('"same"', '"same"', "aa", "bb", "equal-etag-different-source-body"),
    ('"same"', '"same"', "aa", "aa", "equal-etag-same-source-body"),
    ('"old"', '"new"', "aa", "bb", "changed-etag-different-source-body"),
    ('"old"', '"new"', "aa", "aa", "changed-etag-same-source-body"),
])
def test_comparable_page_versions_classify_actual_source_bytes(
    old_etag, new_etag, old_sha, new_sha, expected,
):
    first = _row(old_etag)
    second = _row(new_etag)
    result = compare_page_observations(first, second, old_sha, new_sha)
    assert result["assessment"] == expected
    assert result["samePageRevisionRenderedDrift"] is (old_sha != new_sha)
    assert result["reuseAuthorized"] is False


def test_same_page_revision_drift_does_not_assume_what_changed():
    old, new = _row('"same"'), _row('"same"')
    changed = compare_page_observations(old, new, "aa", "bb")
    assert changed["samePageRevisionRenderedDrift"] is True
    assert changed["assessment"] == "equal-etag-different-source-body"
    new["currentRevision"]["revisionId"] = 102
    updated = compare_page_observations(old, new, "aa", "bb")
    assert updated["samePageRevisionRenderedDrift"] is False
    new["currentRevision"]["status"] = "api-unavailable"
    unknown = compare_page_observations(old, new, "aa", "bb")
    assert unknown["samePageRevisionRenderedDrift"] is False


def test_unknown_page_identity_or_transport_mismatch_is_not_compared():
    old = _row('"same"')
    other_id = _row('"same"', page_id=2)
    assert compare_page_observations(
        old, other_id, "aa", "bb",
    )["assessment"] == "page-identity-unresolved"
    different_variant = _row('"same"')
    different_variant["renderedResponseVersion"]["transportVariant"][
        "observedHeaders"
    ]["Content-Language"] = "es"
    assert compare_page_observations(
        old, different_variant, "aa", "bb",
    )["assessment"] == "representation-variant-or-transport-unverified"
    older_format = _row('"same"')
    older_format["renderedResponseVersion"]["formatVersion"] = 1
    assert compare_page_observations(
        old, older_format, "aa", "bb",
    )["assessment"] == "representation-variant-or-transport-unverified"


def _updated_capture(
    tmp_path, monkeypatch, *,
    parent=None, root_body=ROOT_NEW, etag='"stable"',
    when=NEW_TIME,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if parent is None:
        parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True)
    basic = cli.fetch_site_bytes

    def fetch(url, *, language, site, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if (parent is not None and query.get("list") == ["recentchanges"]
                and when > NEW_TIME):
            # The base fixture contains November 1-3 events, outside this
            # subsequent November 15-16 comparison window.
            return b'{"query":{"recentchanges":[]}}'
        if query.get("prop") == ["revisions"] and "pageids" in query:
            ids = [int(value) for value in query["pageids"][0].split("|")]
            titles = {1: "Main Page", 2: "New Page", 4: "Additional Page"}
            return json.dumps({"query": {"pages": [{
                "pageid": ident, "title": titles[ident],
                "revisions": [{
                    "revid": 2020 + ident,
                    "timestamp": "2026-10-02T00:00:00Z",
                }],
            } for ident in ids]}}).encode()
        if url == ROOT:
            return cli.WikiFetchedBytes(
                root_body, effective_url=url, http_status=200,
                etag=etag, vary="Accept-Language, Accept-Encoding",
                content_encoding="identity", content_language="en",
                cache_control="public, max-age=10",
                headers_observed=True,
            )
        return basic(url, language=language, site=site, **kwargs)

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    result = update_snapshot(
        *parent, archive_directory=tmp_path / "new-archives",
        manifest_directory=tmp_path / "new-manifests",
        work_root=tmp_path / "new-work",
        source_replay_directory=tmp_path / "source-replay",
        now=when,
    )
    return result


def _comparison(previous, current):
    return compare_rendered_captures(
        previous.source_replay, previous.archive,
        previous.manifest, previous.inventory,
        current.source_replay, current.archive,
        current.manifest, current.inventory,
    )


def test_verified_direct_lineage_exposes_same_etag_different_rendered_body(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    changed = ROOT_NEW.replace(
        b"</body>", b"<p>Rendered from a changed template</p></body>",
    )
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        root_body=changed,
        when=NEW_TIME + timedelta(days=1),
    )
    audit = _comparison(first, second)
    assert audit["lineage"] == "direct-parent"
    assert audit["sameUrlPageCount"] == 3
    assert audit["newlyEnumeratedCount"] == 0
    assert audit["notCurrentlyEnumeratedCount"] == 0
    assert audit["equalStrongEtagDifferentBodyCount"] == 1
    assert audit["samePageRevisionRenderedDriftCount"] == 1
    root = next(row for row in audit["pageObservations"] if row["pageUrl"] == ROOT)
    assert root["assessment"] == "equal-etag-different-source-body"
    assert root["previousRevisionId"] == root["currentRevisionId"] == 2021
    assert root["sameStrongEtag"] is True
    assert root["reuseAuthorized"] is False
    assert audit["reuseAuthorized"] is False
    assert audit["sourceTimeConsistency"] == "not-verified-atomic"
    assert audit["dependencyClosure"] == "unverified"


def test_different_etag_for_changed_html_is_observed_but_never_proves_future_reuse(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    next_body = ROOT_NEW.replace(
        b"</body>", b"<p>updated render</p></body>",
    )
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        root_body=next_body, etag='"new"', when=NEW_TIME + timedelta(days=1),
    )
    audit = _comparison(first, second)
    assert audit["equalStrongEtagDifferentBodyCount"] == 0
    root = next(row for row in audit["pageObservations"] if row["pageUrl"] == ROOT)
    assert root["assessment"] == "changed-etag-different-source-body"
    assert root["samePageRevisionRenderedDrift"] is True
    assert audit["reuseAuthorized"] is False


def test_comparison_rejects_tampered_source_replay_before_reporting(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        when=NEW_TIME + timedelta(days=1),
    )
    import zipfile

    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(second.source_replay) as source, zipfile.ZipFile(forged, "x") as out:
        for name in source.namelist():
            payload = source.read(name)
            if name.startswith("html/"):
                payload += b"unexpected bytes"
            out.writestr(name, payload)
    bad = second._replace(source_replay=forged)
    with pytest.raises(ValueError, match="HTML|source|replay"):
        _comparison(first, bad)


def test_v2_historical_only_members_are_never_compared_as_current_html(
    tmp_path, monkeypatch,
):
    from test_historical_only import _retired_update

    first_root = tmp_path / "historical-first"
    second_root = tmp_path / "historical-second"
    first_root.mkdir()
    second_root.mkdir()
    _, first = _retired_update(
        first_root, monkeypatch,
        source_replay_directory=first_root / "replay",
    )
    _, second = _retired_update(
        second_root, monkeypatch,
        source_replay_directory=second_root / "replay",
    )
    audit = _comparison(first, second)
    assert audit["lineage"] == "independent-or-nonadjacent"
    assert audit["sameUrlPageCount"] == 3
    assert audit["newlyEnumeratedCount"] == 0
    assert audit["notCurrentlyEnumeratedCount"] == 0
    assert all(row["pageUrl"] != DELETED for row in audit["pageObservations"])
    assert audit["reuseAuthorized"] is False


def test_read_only_operator_command_emits_deterministic_json(
    tmp_path, monkeypatch, capsys,
):
    from tools.audit_render_freshness import main

    first = _updated_capture(tmp_path / "first", monkeypatch)
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        when=NEW_TIME + timedelta(days=1),
    )
    argv = [
        str(first.source_replay), str(first.archive),
        str(first.manifest), str(first.inventory),
        str(second.source_replay), str(second.archive),
        str(second.manifest), str(second.inventory),
    ]
    capsys.readouterr()  # Ignore the synthetic crawler's progress output.
    assert main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["lineage"] == "direct-parent"
    assert report["reuseAuthorized"] is False
    assert report == _comparison(first, second)
    assert main(argv[:7] + ["does-not-exist.json"]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_reverse_capture_order_rejected_even_with_valid_artifacts(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        when=NEW_TIME + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="reverse order"):
        _comparison(second, first)
