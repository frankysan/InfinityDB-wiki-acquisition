"""Asset HTTP conditional reuse is never inferred from names or weak validators."""

from __future__ import annotations

import json
import urllib.error
import zipfile
from datetime import timedelta

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.asset_evidence import (
    build_asset_evidence, read_asset_evidence, write_asset_evidence,
)
from infinity_wiki_acquisition.snapshot_update import (
    SnapshotUpdateIncomplete, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_snapshot_update import (
    ASSET, NEW_TIME, PNG, _build_parent, _remote, _update,
)


def _seed_etag_parent(tmp_path, monkeypatch, *, etag='"asset-v1"',
                      effective_url=ASSET):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            assert if_none_match is None
            return cli.WikiFetchedBytes(
                PNG, effective_url=effective_url, http_status=200,
                etag=etag, last_modified="Sun, 01 Nov 2026 00:00:00 GMT",
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    result = _update(parent, tmp_path)
    return result.archive, result.manifest, result.inventory


def _second_update(parent, tmp_path):
    return update_snapshot(
        *parent, archive_directory=tmp_path / "archive-second",
        manifest_directory=tmp_path / "manifest-second",
        work_root=tmp_path / "work-second",
        now=NEW_TIME + timedelta(minutes=30),
    )


def test_two_generation_strong_etag_304_reuses_bytes_without_asset_body_download(
    tmp_path, monkeypatch,
):
    parent = _seed_etag_parent(tmp_path, monkeypatch)
    parent_bytes = [path.read_bytes() for path in parent]
    first = verify_snapshot(*parent)
    asset = next(m for m in first["members"] if m["kind"] == "asset")
    assert asset["sourceVersion"]["responseStatus"] == 200
    assert asset["sourceVersion"]["strongEtag"] == '"asset-v1"'
    assert first["captureMetadata"]["httpHeadersAvailable"] is True

    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    requested = []
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            requested.append(if_none_match)
            assert if_none_match == '"asset-v1"'
            # The transport supplies no asset body; only the HTTP validator
            # and the previously verified ZIP establish reusable bytes.
            return cli.WikiFetchedBytes(
                b"", effective_url=ASSET, http_status=304,
                etag='"asset-v1"', conditional_etag=if_none_match,
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    second = _second_update(parent, tmp_path)
    assert requested == ['"asset-v1"']
    assert [path.read_bytes() for path in parent] == parent_bytes
    assert second.reused_members == 4
    with zipfile.ZipFile(second.archive) as result_zip:
        assert result_zip.read("images/icon.png") == PNG
    updated = verify_snapshot(second.archive, second.manifest, second.inventory)
    data = updated["captureMetadata"]
    assert data["updateMode"] == "verified-asset-version-refresh"
    assert data["sourceVersionSkipping"] is True
    assert data["conditionalReusedAssetCount"] == 1
    asset = next(m for m in updated["members"] if m["kind"] == "asset")
    assert asset["sourceVersion"]["responseStatus"] == 304
    assert asset["sourceVersion"]["conditionalRevalidated"] is True
    assert asset["sourceVersion"]["strongEtag"] == '"asset-v1"'
    assert updated["parent"]["archiveName"] == parent[0].name


@pytest.mark.parametrize("bad_response", [
    lambda etag: cli.WikiFetchedBytes(
        b"", effective_url=ASSET, http_status=304,
        etag=None, conditional_etag=etag,
    ),
    lambda etag: cli.WikiFetchedBytes(
        b"", effective_url=ASSET, http_status=304,
        etag='"different"', conditional_etag=etag,
    ),
    lambda etag: cli.WikiFetchedBytes(
        b"", effective_url="https://elsewhere.invalid/icon.png",
        http_status=304, etag=etag, conditional_etag=etag,
    ),
    lambda etag: cli.WikiFetchedBytes(
        b"unexpected", effective_url=ASSET, http_status=304,
        etag=etag, conditional_etag=etag,
    ),
])
def test_invalid_304_blocks_publication(tmp_path, monkeypatch, bad_response):
    parent = _seed_etag_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            assert if_none_match == '"asset-v1"'
            return bad_response(if_none_match)
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    with pytest.raises(SnapshotUpdateIncomplete, match="failed source URL"):
        _second_update(parent, tmp_path)
    assert not list((tmp_path / "archive-second").glob("*.zip"))
    assert not list((tmp_path / "manifest-second").rglob("*.json"))


def test_server_200_downloads_changed_asset_even_with_old_validator(
    tmp_path, monkeypatch,
):
    parent = _seed_etag_parent(tmp_path, monkeypatch)
    replacement = PNG + b"-new"
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    requested = []
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            requested.append(if_none_match)
            return cli.WikiFetchedBytes(
                replacement, effective_url=ASSET,
                http_status=200, etag='"asset-v2"',
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    result = _second_update(parent, tmp_path)
    assert requested == ['"asset-v1"']
    with zipfile.ZipFile(result.archive) as archive:
        assert archive.read("images/icon.png") == replacement
    inventory = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert inventory["captureMetadata"]["conditionalReusedAssetCount"] == 0
    assert inventory["captureMetadata"]["sourceVersionSkipping"] is False
    assert next(m for m in inventory["members"] if m["kind"] == "asset")[
        "sourceVersion"
    ]["strongEtag"] == '"asset-v2"'


@pytest.mark.parametrize("etag,effective", [
    ('W/"weak"', ASSET),
    ('"asset-v1"', "https://cdn.example.invalid/file.png"),
])
def test_weak_or_redirected_parent_validator_never_conditions(
    tmp_path, monkeypatch, etag, effective,
):
    parent = _seed_etag_parent(
        tmp_path, monkeypatch, etag=etag, effective_url=effective,
    )
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    requested = []
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            requested.append(if_none_match)
            return cli.WikiFetchedBytes(
                PNG, effective_url=ASSET, http_status=200,
                etag='"asset-v1"',
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    result = _second_update(parent, tmp_path)
    assert requested == [None]
    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert verified["captureMetadata"]["sourceVersionSkipping"] is False


def test_inventory_rejects_forged_conditional_reuse_counts(tmp_path, monkeypatch):
    parent = _seed_etag_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    def response(url, *, language, site, if_none_match=None):
        if url == ASSET:
            return cli.WikiFetchedBytes(
                b"", effective_url=url, http_status=304,
                etag=if_none_match, conditional_etag=if_none_match,
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", response)
    result = _second_update(parent, tmp_path)
    before = json.loads(result.inventory.read_text(encoding="utf-8"))
    for mutation in ("count", "etag", "url", "sha"):
        document = json.loads(json.dumps(before))
        asset = next(m for m in document["members"] if m["kind"] == "asset")
        if mutation == "count":
            document["captureMetadata"]["conditionalReusedAssetCount"] = 0
        elif mutation == "etag":
            asset["sourceVersion"]["strongEtag"] = 'W/"weak"'
        elif mutation == "url":
            asset["sourceVersion"]["sourceUrl"] = "https://other.invalid/icon.png"
        else:
            asset["sourceVersion"]["sha256"] = "0" * 64
        result.inventory.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(SnapshotVerificationError):
            verify_snapshot(result.archive, result.manifest, result.inventory)


def test_direct_fetch_bytes_304_requires_a_strong_conditional_etag(monkeypatch):
    requested = []
    def fake_open(request, timeout):
        requested.append(request.get_header("If-none-match"))
        raise urllib.error.HTTPError(
            request.full_url, 304, "Not Modified", {"ETag": '"asset-v1"'}, None,
        )
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_open)
    payload = cli.fetch_bytes(ASSET, if_none_match='"asset-v1"')
    assert payload == b""
    assert payload.http_status == 304
    assert payload.etag == '"asset-v1"'
    assert requested == ['"asset-v1"']
    with pytest.raises(ValueError, match="strong HTTP ETags"):
        cli.fetch_bytes(ASSET, if_none_match='W/"weak"')
    assert len(requested) == 1


def test_private_asset_evidence_rejects_wrong_bound_sha(tmp_path):
    payload = cli.WikiFetchedBytes(
        PNG, effective_url=ASSET, http_status=200,
        etag='"asset-v1"',
    )
    record = build_asset_evidence(ASSET, "images/icon.png", payload)
    assert record is not None
    write_asset_evidence(tmp_path, record)
    assert read_asset_evidence(
        tmp_path, ASSET, "images/icon.png", record["sha256"],
    )["strongEtag"] == '"asset-v1"'
    with pytest.raises(ValueError, match="contradicts archived member"):
        read_asset_evidence(tmp_path, ASSET, "images/icon.png", "0" * 64)
