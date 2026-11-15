"""P2.4c.2b offline source replay companion and strict 304 evidence tests."""

from __future__ import annotations

import json
import zipfile

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.source_replay import (
    create_source_replay_bundle,
    source_body_path,
    validate_conditional_html_304,
    verify_conditional_html_304,
    verify_source_replay_bundle,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from test_snapshot_update import (
    ROOT, LANG, SITE, OLD, NEW, _build_parent, _remote, NEW_TIME,
    ROOT_NEW, ASSET,
)
from infinity_wiki_acquisition.snapshot_update import update_snapshot


def _updated(tmp_path, monkeypatch, *, strong=False):
    parent = _build_parent(tmp_path)
    calls = _remote(monkeypatch, changed=True)
    if strong:
        base = cli.fetch_site_bytes

        def fetch(url, *, site, language, **kwargs):
            fetched = base(url, language=language, site=site, **kwargs)
            if url == ROOT:
                return cli.WikiFetchedBytes(
                    fetched, effective_url=url, http_status=200,
                    etag='"root-2026"', last_modified="Sun, 15 Nov 2026 17:00:00 GMT",
                )
            return fetched

        monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    result = update_snapshot(
        *parent, archive_directory=tmp_path / "updated",
        manifest_directory=tmp_path / "manifests",
        work_root=tmp_path / "work",
        now=NEW_TIME, source_replay_directory=tmp_path / "source-replay",
    )
    assert result.source_replay is not None
    return result, calls


def test_update_opt_in_publishes_independently_replayable_html(tmp_path, monkeypatch):
    result, calls = _updated(tmp_path, monkeypatch, strong=True)
    assert ROOT in calls and NEW in calls
    assert result.source_replay.is_file()
    replay = verify_source_replay_bundle(
        result.source_replay, result.archive, result.manifest, result.inventory,
    )
    snapshot = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert replay["formatVersion"] == 1
    assert replay["scope"] == "current-pages-original-http-bodies-not-reuse-authority"
    assert len(replay["pages"]) == sum(
        row["kind"] == "current-page" for row in snapshot["members"]
    ) == 3
    assert all(row["sourceLinksSha256"] for row in replay["pages"])
    assert not any("oldid" in row["rawMember"] for row in replay["pages"])
    with zipfile.ZipFile(result.source_replay) as bundle, zipfile.ZipFile(result.archive) as mirrored:
        root = next(row for row in replay["pages"] if row["pageUrl"] == ROOT)
        raw = bundle.read(root["rawMember"])
        assert raw == ROOT_NEW
        assert root["sourceLinkCount"] == 2
        assert raw != mirrored.read(root["memberPath"])
        assert all(not name.startswith(".wiki-") for name in mirrored.namelist())
    assert snapshot["captureMetadata"]["renderReuseGate"]["reuseAuthorized"] is False
    assert snapshot["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0
    # Saved complete original source bodies are not silently added to the
    # current snapshot ZIP/v3 manifest or inventory v1/v2.
    assert set(replay["pages"][0]) == {
        "pageUrl", "memberPath", "rawMember", "originalSha256",
        "rewrittenSha256", "sourceLinkCount", "sourceLinksSha256",
    }


def test_missing_raw_input_retracts_new_snapshot_publication(tmp_path, monkeypatch):
    import infinity_wiki_acquisition.source_replay as replay_mod

    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True)
    monkeypatch.setattr(replay_mod, "write_source_body", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="Missing raw HTML"):
        update_snapshot(
            *parent, archive_directory=tmp_path / "updated",
            manifest_directory=tmp_path / "manifests",
            work_root=tmp_path / "work",
            now=NEW_TIME, source_replay_directory=tmp_path / "source-replay",
        )
    assert not list((tmp_path / "updated").glob("*.zip"))
    assert not list((tmp_path / "manifests").rglob("*.json"))
    assert not list((tmp_path / "source-replay").glob("*.zip"))


@pytest.mark.parametrize("tamper", ["raw", "identity", "links", "extra"])
def test_replay_zip_tampering_is_rejected(tmp_path, monkeypatch, tamper):
    result, _ = _updated(tmp_path, monkeypatch)
    bundle = result.source_replay
    with zipfile.ZipFile(bundle) as source:
        members = {name: source.read(name) for name in source.namelist()}
    index = json.loads(members["index.json"])
    if tamper == "raw":
        member = index["pages"][0]["rawMember"]
        members[member] = b"<html>forged source</html>"
    elif tamper == "identity":
        index["sourceArchiveSha256"] = "0" * 64
        members["index.json"] = json.dumps(index).encode()
    elif tamper == "links":
        index["pages"][0]["sourceLinkCount"] += 1
        members["index.json"] = json.dumps(index).encode()
    else:
        members["extra.txt"] = b"not an original page"
    altered = tmp_path / "altered-replay.zip"
    with zipfile.ZipFile(altered, "x") as target:
        for name, raw in members.items():
            target.writestr(name, raw)
    with pytest.raises((ValueError, KeyError), match="replay|HTML|member|source"):
        verify_source_replay_bundle(
            altered, result.archive, result.manifest, result.inventory,
        )


def test_replay_input_missing_is_not_recovered_from_rewritten_bytes(tmp_path, monkeypatch):
    result, _ = _updated(tmp_path, monkeypatch)
    # The successful update removed work. Recreating a bundle using a new
    # empty staging directory must fail rather than guessing original HTML
    # from the published mirrored page.
    scratch = tmp_path / "incomplete"
    scratch.mkdir()
    with pytest.raises(ValueError, match="Missing raw HTML"):
        create_source_replay_bundle(
            scratch, result.archive, result.manifest, result.inventory,
            tmp_path / "second",
        )


def _response(status=304, etag='"root-2026"', effective=ROOT, conditional='"root-2026"', body=b""):
    return cli.WikiFetchedBytes(
        body, effective_url=effective, http_status=status,
        etag=etag, conditional_etag=conditional,
    )


def test_independent_304_validation_still_does_not_authorize_reuse(tmp_path, monkeypatch):
    result, _ = _updated(tmp_path, monkeypatch, strong=True)
    checked = verify_conditional_html_304(
        result.source_replay, result.archive, result.manifest, result.inventory,
        request_url=ROOT, result=_response(),
    )
    assert checked["status"] == "conditional-response-matches-parent-validator"
    assert checked["reuseAuthorized"] is False
    assert checked["reason"] == "dependency-and-source-time-closure-unverified"


@pytest.mark.parametrize("bad_response", [
    _response(status=200),
    _response(etag='W/"root-2026"'),
    _response(effective="https://www.human-sphere.com/Redirect"),
    _response(conditional='"other"'),
    _response(body=b"unexpected-body"),
    b"",
])
def test_invalid_304_is_never_considered_a_verified_response(
    tmp_path, monkeypatch, bad_response,
):
    result, _ = _updated(tmp_path, monkeypatch, strong=True)
    with pytest.raises(ValueError, match="304"):
        verify_conditional_html_304(
            result.source_replay, result.archive, result.manifest, result.inventory,
            request_url=ROOT, result=bad_response,
        )


def test_prior_etag_alone_never_qualifies_without_exact_request_profile(tmp_path, monkeypatch):
    result, _ = _updated(tmp_path, monkeypatch, strong=True)
    parent = verify_snapshot(result.archive, result.manifest, result.inventory)
    prior = next(row for row in parent["members"] if row["kind"] == "current-page"
                 and row["page"]["url"] == ROOT)["renderedResponseVersion"]
    prior = json.loads(json.dumps(prior))
    prior["profile"]["requestHeaders"]["Accept-Language"] = "other"
    with pytest.raises(ValueError, match="304"):
        validate_conditional_html_304(
            parent_response=prior, request_url=ROOT, result=_response(),
            site=SITE, language=LANG,
        )


def test_cli_fresh_opt_in_produces_companion_without_changing_snapshot_format(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    child = cli.page_url_from_title("Other", site=SITE, language=LANG)
    sources = {
        ROOT: b'<html><body><a href="/Other">Other</a></body></html>',
        child: b"<html><body>Other page</body></html>",
    }

    def fetch(url, *, site, language, **kwargs):
        import urllib.parse

        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["allpages"]:
            return json.dumps({"query": {"allpages": [
                {"title": "Main Page", "pageid": 7},
                {"title": "Other", "pageid": 8},
            ]}}).encode()
        if query.get("prop") == ["revisions"]:
            return json.dumps({"query": {"pages": [
                {"pageid": 7, "title": "Main Page", "revisions": [{
                    "revid": 123, "timestamp": "2026-11-14T12:00:00Z",
                }]},
                {"pageid": 8, "title": "Other", "revisions": [{
                    "revid": 124, "timestamp": "2026-11-14T12:00:00Z",
                }]},
            ]}}).encode()
        if url in sources:
            return sources[url]
        raise AssertionError(f"Unexpected external URL: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    assert cli.main([
        "--site", SITE.key,
        "--root", "archives",
        "--work-root", "work",
        "--manifest-dir", "manifests",
        "--source-replay-dir", "companions",
    ]) == 0
    archive = next((tmp_path / "archives").glob("*.zip"))
    manifest = next((tmp_path / "manifests").glob("*.json"))
    inventory = next((tmp_path / "manifests/inventories").glob("*.json"))
    bundle = next((tmp_path / "companions").glob("*.zip"))
    replay = verify_source_replay_bundle(bundle, archive, manifest, inventory)
    assert len(replay["pages"]) == 2
    assert verify_snapshot(archive, manifest, inventory)["formatVersion"] == 1
    assert not list((tmp_path / "work").glob("*.work"))


def test_history_only_upgrade_does_not_synthesize_a_new_source_companion(
    tmp_path, capsys,
):
    assert cli.main([
        "--from-snapshot", str(tmp_path / "does-not-exist.zip"),
        "--include-history",
        "--source-replay-dir", str(tmp_path / "companions"),
    ]) == 1
    assert "requires fresh current HTML acquisition" in capsys.readouterr().err


def test_checkpoint_private_html_path_is_url_scoped(tmp_path):
    first = source_body_path(tmp_path, ROOT)
    other = source_body_path(tmp_path, OLD)
    assert first != other and first.parent == other.parent
    assert first.name.endswith(".html")
    assert first.parent.name == ".wiki-original-html"
