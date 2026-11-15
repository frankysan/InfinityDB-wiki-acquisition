"""MediaWiki imageinfo SHA-1 identities can skip verified original upload bodies."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import zipfile
from datetime import timedelta

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.mediawiki_uploads import (
    _sha1_hex, read_current_upload,
)
from infinity_wiki_acquisition.snapshot_update import (
    SnapshotUpdateIncomplete, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_snapshot_update import ASSET, NEW_TIME, PNG, _build_parent, _remote, _update


def _base36(integer: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if integer == 0:
        return "0"
    output = ""
    while integer:
        integer, remainder = divmod(integer, 36)
        output = digits[remainder] + output
    return output


def _upload_response(content=PNG, *, remote_url=ASSET, sha1=None,
                     timestamp="2026-11-01T00:00:00Z", title="File:icon.png"):
    digest = sha1 if sha1 is not None else _base36(int(hashlib.sha1(content).hexdigest(), 16))
    return json.dumps({
        "query": {"pages": [{
            "pageid": 55, "title": title,
            "imageinfo": [{
                "url": remote_url,
                "sha1": digest,
                "size": len(content),
                "timestamp": timestamp,
            }],
        }]},
    }).encode()


def _remote_with_upload(monkeypatch, *, metadata_content=PNG,
                        body=PNG, **response_overrides):
    _remote(monkeypatch, changed=False)
    original = cli.fetch_site_bytes
    calls = []
    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["imageinfo"]:
            calls.append(("imageinfo", url))
            return _upload_response(metadata_content, **response_overrides)
        if url == ASSET:
            calls.append(("asset-body", if_none_match))
            return cli.WikiFetchedBytes(
                body, effective_url=url, http_status=200,
                etag='"asset-version"', conditional_etag=if_none_match,
            )
        return original(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def test_original_upload_sha1_reuse_without_http_asset_download(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    before = [path.read_bytes() for path in parent]
    calls = _remote_with_upload(monkeypatch)
    result = _update(parent, tmp_path)
    assert [kind for kind, _ in calls] == ["imageinfo"]
    assert result.reused_members == 4
    assert [path.read_bytes() for path in parent] == before
    with zipfile.ZipFile(result.archive) as output:
        assert output.read("images/icon.png") == PNG
    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    metadata = verified["captureMetadata"]
    assert metadata["sourceVersionSkipping"] is True
    assert metadata["conditionalReusedAssetCount"] == 0
    assert metadata["uploadSha1ReusedAssetCount"] == 1
    assert metadata["uploadVersionEvidenceCount"] == 1
    asset = next(m for m in verified["members"] if m["kind"] == "asset")
    upload = asset["uploadVersion"]
    assert upload["usedForReuse"] is True
    assert upload["sourceUrl"] == ASSET
    assert upload["sha1Hex"] == hashlib.sha1(PNG).hexdigest()
    assert upload["size"] == len(PNG)
    assert upload["uploadTimestamp"] == "2026-11-01T00:00:00Z"


def test_repeated_upload_identity_reuse_across_generations(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote_with_upload(monkeypatch)
    first = _update(parent, tmp_path)
    calls = _remote_with_upload(monkeypatch)
    second = update_snapshot(
        first.archive, first.manifest, first.inventory,
        archive_directory=tmp_path / "archive-two",
        manifest_directory=tmp_path / "manifest-two",
        work_root=tmp_path / "work-two",
        now=NEW_TIME + timedelta(hours=1),
    )
    assert [kind for kind, _ in calls] == ["imageinfo"]
    assert second.reused_members == 4
    verified = verify_snapshot(second.archive, second.manifest, second.inventory)
    assert verified["captureMetadata"]["uploadSha1ReusedAssetCount"] == 1


def test_changed_upload_downloads_body_and_records_new_version(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    changed = PNG + b"updated"
    calls = _remote_with_upload(
        monkeypatch, metadata_content=changed, body=changed,
    )
    result = _update(parent, tmp_path)
    assert [kind for kind, _ in calls] == ["imageinfo", "asset-body"]
    assert result.changed_members == 1
    assert result.reused_members == 3
    with zipfile.ZipFile(result.archive) as output:
        assert output.read("images/icon.png") == changed
    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert verified["captureMetadata"]["uploadSha1ReusedAssetCount"] == 0
    assert verified["captureMetadata"]["uploadVersionEvidenceCount"] == 1
    version = next(m for m in verified["members"] if m["kind"] == "asset")["uploadVersion"]
    assert version["usedForReuse"] is False
    assert version["sha1Hex"] == hashlib.sha1(changed).hexdigest()


def test_upload_changes_between_metadata_and_body_blocks_publication(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _remote_with_upload(
        monkeypatch, metadata_content=PNG + b"remote-new", body=PNG + b"different",
    )
    with pytest.raises(SnapshotUpdateIncomplete, match="failed source URL"):
        _update(parent, tmp_path)
    assert not list((tmp_path / "archive-new").glob("*.zip"))
    assert not list((tmp_path / "manifest-new").rglob("*.json"))


@pytest.mark.parametrize("overrides", [
    {"remote_url": "https://cdn.other.invalid/image.png"},
    {"timestamp": "unparseable"},
    {"sha1": "invalid!" },
    {"title": "File:another.png"},
])
def test_uncertain_upload_identity_falls_back_to_full_http(
    tmp_path, monkeypatch, overrides,
):
    parent = _build_parent(tmp_path)
    calls = _remote_with_upload(monkeypatch, **overrides)
    result = _update(parent, tmp_path)
    assert [kind for kind, _ in calls] == ["imageinfo", "asset-body"]
    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert verified["captureMetadata"]["uploadSha1ReusedAssetCount"] == 0
    assert verified["captureMetadata"]["uploadVersionEvidenceCount"] == 0


def test_imageinfo_rejects_thumbnails_and_external_assets_without_api(monkeypatch):
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("API must not be called"),
    )
    for url in [
        "https://www.human-sphere.com/images/thumb/a/ab/icon.png/200px-icon.png",
        "https://assets.corvusbelli.net/images/icon.png",
        "https://www.human-sphere.com/images/icon.png?variant=1",
    ]:
        assert read_current_upload(
            url, site=cli.HUMAN_SPHERE_SITE, language="en",
        ) is None


def test_sha1_base36_decoding_matches_mediawiki_upload_checksum():
    sha = hashlib.sha1(PNG).hexdigest()
    assert _sha1_hex(_base36(int(sha, 16))) == sha
    assert _sha1_hex(sha) == sha
    assert _sha1_hex("xyz$") is None
    assert _sha1_hex(2**180) is None


def test_verified_inventory_rejects_forged_imageinfo_hash_or_reuse_count(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _remote_with_upload(monkeypatch)
    result = _update(parent, tmp_path)
    original = json.loads(result.inventory.read_text(encoding="utf-8"))
    for variant in ("checksum", "size", "source", "count", "missing"):
        candidate = json.loads(json.dumps(original))
        member = next(m for m in candidate["members"] if m["kind"] == "asset")
        if variant == "checksum":
            member["uploadVersion"]["sha1Hex"] = "0" * 40
        elif variant == "size":
            member["uploadVersion"]["size"] += 1
        elif variant == "source":
            member["uploadVersion"]["sourceUrl"] = "https://wrong.invalid/"
        elif variant == "count":
            candidate["captureMetadata"]["uploadSha1ReusedAssetCount"] = 0
        else:
            del member["uploadVersion"]
        result.inventory.write_text(json.dumps(candidate), encoding="utf-8")
        with pytest.raises(SnapshotVerificationError):
            verify_snapshot(result.archive, result.manifest, result.inventory)
