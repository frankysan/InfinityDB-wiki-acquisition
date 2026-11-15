"""Offline snapshot inventory publication, identity, and failure tests."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import CHECKPOINT_FILENAME
from infinity_wiki_acquisition.snapshot_inventory import (
    INVENTORY_FORMAT,
    INVENTORY_VERSION,
    build_snapshot_inventory,
    load_snapshot_inventory,
    write_snapshot_inventory,
)
from infinity_wiki_acquisition.snapshot_provenance import write_snapshot_manifest


def test_current_snapshot_publishes_inventory_with_source_urls_and_mirrored_hashes(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    site = cli.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    child = "https://www.human-sphere.com/Child"
    logo = "https://www.human-sphere.com/images/test.png"
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        cli, "fetch_all_page_title_batch",
        lambda **_: (("Main Page", "Child"), None),
    )
    payloads = {
        root: b'<html><body><a href="/Child">Child</a><img src="/images/test.png"></body></html>',
        child: b"<html><body>Child page</body></html>",
        logo: b"\x89PNG\r\n\x1a\npng image",
    }
    monkeypatch.setattr(cli, "fetch_bytes", lambda url, *, language="en": payloads[url])

    assert cli.main([
        "--site", "human-sphere", "--work-root", "work",
        "--root", "archives", "--manifest-dir", "manifests",
    ]) == 0
    archive = next((tmp_path / "archives").glob("*.zip"))
    manifest = tmp_path / "manifests" / f"{archive.stem}.json"
    inventory = tmp_path / "manifests/inventories" / f"{archive.stem}.inventory.json"
    assert manifest.is_file()
    document = load_snapshot_inventory(inventory, archive=archive)

    assert document["format"] == INVENTORY_FORMAT
    assert document["formatVersion"] == INVENTORY_VERSION
    assert document["site"] == "human-sphere"
    assert document["language"] == "en"
    assert document["history"] is False
    assert document["parent"] is None
    assert document["snapshot"]["archiveName"] == archive.name
    assert document["snapshot"]["memberCount"] == 3
    records = {record["path"]: record for record in document["members"]}
    assert set(records) == {"Main_Page.html", "Child.html", "images/test.png"}
    main = records["Main_Page.html"]
    assert main["kind"] == "current-page"
    assert main["sourceUrls"] == [root]
    assert main["page"]["url"] == root
    assert main["representation"] == "offline-mirrored"
    assert records["Child.html"]["sourceUrls"] == [child]
    assert records["images/test.png"]["kind"] == "asset"
    assert records["images/test.png"]["sourceUrls"] == [logo]
    with zipfile.ZipFile(archive) as bundle:
        mirrored = bundle.read("Main_Page.html")
        assert main["sha256"] == hashlib.sha256(mirrored).hexdigest()
        assert b"href=\"Child.html\"" in mirrored
        assert CHECKPOINT_FILENAME not in bundle.namelist()
        assert not any("inventory" in name for name in bundle.namelist())
    assert not list((tmp_path / "work").glob("*.work"))
    assert len(list((tmp_path / "manifests").glob("*.json"))) == 1


def test_history_inventory_records_oldids_and_keeps_index_distinct(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    current = work / "index.html"
    current.write_bytes(b"<html>current</html>")
    revision = work / "_history/oldid/44.html"
    revision.parent.mkdir(parents=True)
    revision.write_bytes(b"<html>oldid 44</html>")
    history_index = work / "_history/index.json"
    history_index.write_text(json.dumps({
        "format": cli.HISTORY_INDEX_FORMAT,
        "formatVersion": cli.HISTORY_INDEX_VERSION,
        "pages": [{
            "currentUrl": cli.ROOT_URL,
            "currentPath": "index.html",
            "title": "Main Page",
            "revisions": [{
                "oldid": 44, "parentid": 43,
                "timestamp": "2025-12-01T00:00:00Z",
                "url": cli.oldid_url("Main Page", 44, language="en"),
                "path": "_history/oldid/44.html",
            }],
        }],
        "revisionCount": 1,
    }), encoding="utf-8")
    acquired = datetime(2026, 11, 15, tzinfo=timezone.utc)
    archive = cli.archive_wiki(
        [current, revision, history_index],
        tmp_path / "archives",
        root=work, include_history=True, now=acquired,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests",
        snapshot_type="wiki", acquired_at=acquired,
        source_url=cli.ROOT_URL, document_count=3,
        project_root=tmp_path, language="en",
    )
    doc = build_snapshot_inventory(
        archive, manifest, work=work, site=cli.DEFAULT_SITE.key,
        language="en", root_url=cli.ROOT_URL, include_history=True,
        pages=(cli.WikiPage(cli.ROOT_URL, "index.html", "Main Page"),),
    )
    paths = {entry["path"]: entry for entry in doc["members"]}
    assert paths["index.html"]["kind"] == "current-page"
    assert paths["_history/index.json"]["kind"] == "generated-index"
    assert paths["_history/index.json"]["sourceUrls"] == []
    entry = paths["_history/oldid/44.html"]
    assert entry["kind"] == "revision"
    assert entry["sourceUrls"] == [cli.oldid_url("Main Page", 44, language="en")]
    assert entry["revision"]["oldid"] == 44
    assert entry["revision"]["parentid"] == 43
    assert entry["revision"]["timestamp"] == "2025-12-01T00:00:00Z"
    assert entry["revision"]["pages"] == [
        {"url": cli.ROOT_URL, "title": "Main Page"},
    ]


def test_inventory_never_invents_missing_source_identity(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "index.html").write_bytes(b"<html>test</html>")
    (work / "asset.css").write_bytes(b"body {color:black}")
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    archive = cli.archive_wiki(
        [work / "asset.css", work / "index.html"],
        tmp_path / "archives", root=work, now=now,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests",
        snapshot_type="wiki", acquired_at=now,
        source_url=cli.ROOT_URL, document_count=2,
        project_root=tmp_path, language="en",
    )
    doc = build_snapshot_inventory(
        archive, manifest, work=work, site=cli.DEFAULT_SITE.key,
        language="en", root_url=cli.ROOT_URL, include_history=False,
        pages=(cli.WikiPage(cli.ROOT_URL, "index.html", "Main Page"),),
    )
    records = {item["path"]: item for item in doc["members"]}
    assert records["index.html"]["sourceUrls"] == [cli.ROOT_URL]
    assert records["asset.css"]["kind"] == "unknown"
    assert records["asset.css"]["sourceUrls"] == []
    assert records["asset.css"]["representation"] == "unknown"


def test_inventory_is_immutable_and_detects_modified_archive_member(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    file = work / "index.html"
    file.write_text("original", encoding="utf-8")
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    archive = cli.archive_wiki([file], tmp_path / "archives", root=work, now=now)
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests",
        snapshot_type="wiki", acquired_at=now,
        source_url=cli.ROOT_URL, document_count=1,
        project_root=tmp_path, language="en",
    )
    output = write_snapshot_inventory(
        archive, manifest, tmp_path / "manifests/inventories",
        work=work, site=cli.DEFAULT_SITE.key, language="en",
        root_url=cli.ROOT_URL, include_history=False, pages=(),
    )
    before = output.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        write_snapshot_inventory(
            archive, manifest, tmp_path / "manifests/inventories",
            work=work, site=cli.DEFAULT_SITE.key, language="en",
            root_url=cli.ROOT_URL, include_history=False, pages=(),
        )
    assert output.read_bytes() == before
    with zipfile.ZipFile(archive, "w") as dest:
        dest.writestr("index.html", b"modified")
    with pytest.raises(ValueError, match="member differs"):
        load_snapshot_inventory(output, archive=archive)


def test_inventory_publication_failure_rolls_back_manifest_and_archive(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "fetch_bytes", lambda url, *, language="en": b"<html><body>Content</body></html>")

    def rejected(*args, **kwargs) -> Path:
        raise ValueError("inventory destination unavailable")

    monkeypatch.setattr(cli, "write_snapshot_inventory", rejected)
    assert cli.main([
        "--root", "archives", "--work-root", "work",
        "--manifest-dir", "manifests",
    ]) == 1
    assert list((tmp_path / "archives").glob("*.zip")) == []
    assert list((tmp_path / "manifests").glob("*.json")) == []
    assert len(list((tmp_path / "work").glob("*.work"))) == 1


def test_inventory_rejects_history_index_with_nonexistent_oldid(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    index = work / "_history/index.json"
    index.parent.mkdir()
    index.write_text(json.dumps({
        "format": cli.HISTORY_INDEX_FORMAT, "formatVersion": 1,
        "revisionCount": 1,
        "pages": [{
            "currentUrl": cli.ROOT_URL, "title": "Main Page",
            "revisions": [{
                "oldid": 999, "parentid": 0,
                "timestamp": "2025-01-01T00:00:00Z",
                "path": "_history/oldid/999.html",
                "url": cli.oldid_url("Main Page", 999, language="en"),
            }],
        }],
    }), encoding="utf-8")
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    archive = cli.archive_wiki([index], tmp_path / "archives",
                               root=work, now=now, include_history=True)
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests",
        snapshot_type="wiki", acquired_at=now,
        source_url=cli.ROOT_URL, document_count=1,
        project_root=tmp_path, language="en",
    )
    with pytest.raises(ValueError, match="absent ZIP members"):
        build_snapshot_inventory(
            archive, manifest, work=work, site=cli.DEFAULT_SITE.key,
            language="en", root_url=cli.ROOT_URL,
            include_history=True, pages=(),
        )
