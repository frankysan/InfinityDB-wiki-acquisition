"""P2.3 conservative archive-derived full refresh and lineage tests.

The intentionally conservative refresh downloads current content before
marking a parent member reusable; it does not infer remote freshness from IDs.
"""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
    write_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import sha256_file, write_snapshot_manifest
from infinity_wiki_acquisition.snapshot_update import (
    SnapshotUpdateIncomplete, scan_recent_changes, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)

SITE = cli.HUMAN_SPHERE_SITE
LANG = "en"
ROOT = SITE.root_urls[LANG]
OLD = cli.page_url_from_title("Old Page", language=LANG, site=SITE)
DELETED = cli.page_url_from_title("Deleted Page", language=LANG, site=SITE)
NEW = cli.page_url_from_title("New Page", language=LANG, site=SITE)
ADDED = cli.page_url_from_title("Additional Page", language=LANG, site=SITE)
ASSET = "https://www.human-sphere.com/images/icon.png"
OLD_TIME = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
NEW_TIME = datetime(2026, 11, 15, 16, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic parent image bytes"
ROOT_OLD = b'<html><body><a href="/Old_Page">Old</a><img src="/images/icon.png"></body></html>'
ROOT_NEW = b'<html><body><a href="/New_Page">New</a><img src="/images/icon.png"></body></html>'
PAGES = {
    ROOT: ("Main Page", 1, ROOT_OLD),
    OLD: ("Old Page", 2, b"<html>old page</html>"),
    DELETED: ("Deleted Page", 3, b"<html>removed page</html>"),
}


def _build_parent(tmp_path: Path):
    work = tmp_path / "parent_work"
    work.mkdir()
    files: list[Path] = []
    saved = {}
    discovered = {}
    pages = []
    for url, (title, page_id, raw) in PAGES.items():
        path = cli.local_relative_path(url, site=SITE)
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            cli.rewrite_html_links(
                raw.decode(), url, site=SITE, language=LANG,
            ).encode(),
        )
        files.append(target)
        saved[url] = [path, target.stat().st_size]
        discovered[url] = False
        pages.append({"url": url, "path": path, "title": title})
    asset_target = work / cli.local_relative_path(ASSET, asset=True, site=SITE)
    asset_target.parent.mkdir(parents=True, exist_ok=True)
    asset_target.write_bytes(PNG)
    files.append(asset_target)
    saved[ASSET] = [asset_target.relative_to(work).as_posix(), len(PNG)]
    discovered[ASSET] = True
    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "phase": "complete", "complete": True, "site": SITE.key,
        "language": LANG, "rootUrl": ROOT,
        "enumerationContinuation": None, "queue": [],
        "discovered": discovered, "pageIds": {url: ident for url, (_, ident, _) in PAGES.items()},
        "required": list(PAGES), "saved": saved, "pages": pages,
        "ignored": {}, "failed": {},
    })
    archive = cli.archive_wiki(
        files, tmp_path / "archive-parent", root=work,
        now=OLD_TIME, site=SITE, language=LANG,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifest-parent",
        snapshot_type="wiki", acquired_at=OLD_TIME, source_url=ROOT,
        document_count=len(files), project_root=tmp_path, language=LANG,
    )
    parent = build_snapshot_inventory(
        archive, manifest, work=work, site=SITE.key,
        language=LANG, root_url=ROOT, include_history=False,
        pages=[cli.WikiPage(p["url"], p["path"], p["title"]) for p in pages],
        crawl_checkpoint=work / CHECKPOINT_FILENAME,
    )
    inventory = tmp_path / "manifest-parent" / "inventories" / f"{archive.stem}.inventory.json"
    inventory.parent.mkdir()
    inventory.write_text(json.dumps(parent), encoding="utf-8")
    verify_snapshot(archive, manifest, inventory)
    return archive, manifest, inventory


def _remote(monkeypatch, *, changed=True, failure=None, empty_ids=False):
    if changed:
        entries = [
            ("Main Page", 1, ROOT_NEW),
            ("New Page", 2, b"<html>moved content</html>"),
            ("Additional Page", 4, b"<html>new content</html>"),
        ]
    else:
        entries = [(title, ident, content) for title, ident, content in PAGES.values()]
    remote = {
        cli.canonical_crawl_url(
            cli.page_url_from_title(title, site=SITE, language=LANG), site=SITE,
        ): body
        for title, _, body in entries
    }
    remote[ROOT] = entries[0][2]
    remote[ASSET] = PNG
    calls: list[str] = []

    def fetch(url, *, language, site):
        assert language == LANG and site.key == SITE.key
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["imageinfo"]:
            # Optional upload-version evidence is absent in this baseline
            # fixture, so the updater must fetch the binary normally.
            return b'{"query":{"pages":[{"title":"File:icon.png","missing":true}]}}'
        if query.get("list") == ["recentchanges"]:
            return json.dumps({
                "query": {"recentchanges": [] if not changed else [
                    {
                        "rcid": 101, "type": "log", "title": "Old Page",
                        "pageid": 2, "timestamp": "2026-11-01T00:00:00Z",
                        "logtype": "move", "logaction": "move",
                        "logparams": {"target_title": "New Page"},
                    },
                    {
                        "rcid": 102, "type": "log", "title": "Deleted Page",
                        "pageid": 3, "timestamp": "2026-11-02T00:00:00Z",
                        "logtype": "delete", "logaction": "delete",
                    },
                    {
                        "rcid": 103, "type": "log", "title": "File:icon.png",
                        "timestamp": "2026-11-03T00:00:00Z",
                        "logtype": "upload", "logaction": "overwrite",
                    },
                ]},
            }).encode()
        if query.get("list") == ["allpages"]:
            return json.dumps({
                "query": {"allpages": [
                    {"title": name, **({} if empty_ids else {"pageid": ident})}
                    for name, ident, _ in entries
                ]},
            }).encode()
        if url == failure:
            raise OSError("synthetic required page unavailable")
        if url in remote:
            return remote[url]
        raise AssertionError(f"Unexpected source request: {url}")
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def _update(parent, tmp_path):
    return update_snapshot(
        *parent, archive_directory=tmp_path / "archive-new",
        manifest_directory=tmp_path / "manifest-new",
        work_root=tmp_path / "work-new", now=NEW_TIME,
    )


def test_full_refresh_detects_move_missing_id_and_new_content(
    tmp_path: Path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    before = [path.read_bytes() for path in parent]
    calls = _remote(monkeypatch, changed=True)
    result = _update(parent, tmp_path)
    assert result.reused_members == 1
    assert result.changed_members == 1
    assert result.new_members == 2
    assert result.removed_members == 2
    assert result.moved_pages == 1
    assert len([url for url in calls if "list=allpages" in url]) == 1
    assert len([url for url in calls if "list=recentchanges" in url]) == 1
    # All current content really was checked over the network, including
    # parent binary assets; no unsupported source-version skipping.
    assert ASSET in calls
    assert ROOT in calls
    assert NEW in calls and ADDED in calls
    assert OLD not in calls and DELETED not in calls
    assert [path.read_bytes() for path in parent] == before

    with zipfile.ZipFile(result.archive) as source:
        assert sorted(source.namelist()) == sorted([
            "Main_Page.html", "New_Page.html", "Additional_Page.html",
            "images/icon.png",
        ])
        assert source.read("images/icon.png") == PNG
        assert b"New_Page.html" in source.read("Main_Page.html")
        assert b"Old_Page.html" not in source.read("Main_Page.html")
    updated = verify_snapshot(result.archive, result.manifest, result.inventory)
    data = updated["captureMetadata"]
    assert data["updateMode"] == "verified-asset-version-refresh"
    assert data["conditionalReusedAssetCount"] == 0
    assert data["sourceVersionSkipping"] is False
    assert data["sourceVersionSkipping"] is False
    assert data["pageMovesById"] == [{
        "pageId": 2,
        "oldUrl": OLD, "newUrl": NEW,
        "oldPath": "Old_Page.html", "newPath": "New_Page.html",
        "interpretation": "move-corroborated-by-recentchanges",
    }]
    assert data["parentPageIdsAbsentFromEnumeration"] == [{
        "pageId": 3, "oldUrl": DELETED, "oldPath": "Deleted_Page.html",
        "status": "absent-from-current-enumeration",
    }]
    assert data["matchedParentMemberCount"] == 1
    assert data["recentChanges"]["observedCount"] == 3
    assert data["recentChanges"]["retentionCoverage"] == "unverified"
    assert data["recentChanges"]["events"][0]["targetTitle"] == "New Page"
    assert updated["parent"]["archiveSha256"] == sha256_file(parent[0])
    assert not list((tmp_path / "work-new").glob("*.work"))


def test_no_changes_all_members_byte_identical(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    calls = _remote(monkeypatch, changed=False)
    result = _update(parent, tmp_path)
    assert result.reused_members == 4
    assert result.changed_members == result.new_members == result.removed_members == 0
    assert result.moved_pages == 0
    assert ROOT in calls and ASSET in calls
    assert sha256_file(result.archive) == sha256_file(parent[0])
    assert verify_snapshot(result.archive, result.manifest, result.inventory)["history"] is False


def test_required_page_failure_keeps_work_and_never_publishes(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True, failure=NEW)
    with pytest.raises(SnapshotUpdateIncomplete, match="failed source URL") as err:
        _update(parent, tmp_path)
    assert (err.value.work / CHECKPOINT_FILENAME).is_file()
    assert not list((tmp_path / "archive-new").glob("*.zip"))
    assert not list((tmp_path / "manifest-new").rglob("*.json"))


def test_parent_verification_failure_prevents_network(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    parent[2].write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(SnapshotVerificationError):
        _update(parent, tmp_path)


def test_parent_unclassified_member_blocks_update(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    contents = json.loads(parent[2].read_text())
    asset = next(x for x in contents["members"] if x["kind"] == "asset")
    asset.update(kind="unknown", representation="unknown", sourceUrls=[])
    parent[2].write_text(json.dumps(contents), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(ValueError, match="Unclassified member"):
        _update(parent, tmp_path)


def test_allpages_missing_ids_fail_closed(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, empty_ids=True)
    with pytest.raises(ValueError, match="allpages did not provide page IDs"):
        _update(parent, tmp_path)
    assert not list((tmp_path / "archive-new").glob("*.zip"))


def test_update_from_cli_and_mode_exclusivity(tmp_path: Path, monkeypatch, capsys):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True)
    assert cli.main([
        "--update-from", str(parent[0]),
        "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--root", str(tmp_path / "cli-archives"),
        "--manifest-dir", str(tmp_path / "cli-manifests"),
        "--work-root", str(tmp_path / "cli-work"),
    ]) == 0
    assert "assets verified or downloaded" in capsys.readouterr().out
    assert len(list((tmp_path / "cli-archives").glob("*.zip"))) == 1
    for option in ("--retry-failed-only", "--from-snapshot"):
        value = (["other.zip"] if option == "--from-snapshot" else [])
        assert cli.main(["--update-from", str(parent[0]), option, *value]) == 1
        assert "cannot be combined" in capsys.readouterr().err
    assert cli.main([
        "--update-from", str(parent[0]),
        "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--include-history",
    ]) == 1
    assert "requires a history-enabled parent" in capsys.readouterr().err


@pytest.mark.parametrize("site,language", [
    (cli.INFINITY_WIKI_SITE, "en"),
    (cli.INFINITY_WIKI_SITE, "es"),
    (cli.HUMAN_SPHERE_SITE, "en"),
])
def test_update_supported_site_languages(tmp_path: Path, monkeypatch, site, language):
    from test_snapshot_upgrade import _source as basic_source

    parent = basic_source(tmp_path, site=site, language=language)
    source_root = site.root_urls[language]
    main_page = cli.canonical_crawl_url(
        cli.page_url_from_title("Main Page", site=site, language=language),
        site=site,
    )
    requests = []
    def fetch(url, *, language: str, site):
        requests.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["recentchanges"]:
            return b'{"query":{"recentchanges":[]}}'
        if query.get("list") == ["allpages"]:
            return b'{"query":{"allpages":[{"title":"Main Page","pageid":81}]}}'
        if url in (source_root, main_page):
            return b"<html><body>new source page</body></html>"
        raise AssertionError(f"Unexpected request: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    result = _update(parent, tmp_path)
    checked = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert checked["site"] == site.key
    assert checked["language"] == language
    assert checked["captureMetadata"]["recentChanges"]["retentionCoverage"] == "unverified"
    assert any("list=allpages" in url for url in requests)
    # The root path may be an alias for /Main_Page; a shared numeric page ID
    # alone does not prove that a genuine page move occurred.
    for item in checked["captureMetadata"]["pageMovesById"]:
        assert item["interpretation"] == "unresolved-url-association"


def test_history_parent_is_accepted_only_with_explicit_current_source_mapping(
    tmp_path: Path, monkeypatch,
):
    from test_snapshot_history_reuse import _history_parent
    from infinity_wiki_acquisition.snapshot_update import _parent_sources

    parent = _history_parent(tmp_path, monkeypatch)
    checked = verify_snapshot(*parent)
    sources = _parent_sources(checked)
    assert sources
    assert all(row["kind"] in {"current-page", "asset"} for row in sources.values())
    assert all(not url.startswith("_history/") for url in sources)
    assert checked["history"] is True


def test_update_inventory_refuses_retention_completeness_claim(
    tmp_path: Path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    result = _update(parent, tmp_path)
    data = json.loads(result.inventory.read_text())
    data["captureMetadata"]["recentChanges"]["retentionCoverage"] = "complete"
    result.inventory.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="retention coverage"):
        verify_snapshot(result.archive, result.manifest, result.inventory)


def test_recentchanges_pagination_and_audited_move(tmp_path: Path, monkeypatch):
    calls = []
    def api(url, *, language, site):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        calls.append(query)
        if "rccontinue" not in query:
            return json.dumps({
                "query": {"recentchanges": [{
                    "rcid": 10, "type": "log", "title": "Old Page",
                    "timestamp": "2026-11-01T00:00:00Z",
                    "logtype": "move", "logaction": "move",
                    "logparams": {"target_title": "New Page"},
                }]},
                "continue": {"continue": "-||", "rccontinue": "next-page"},
            }).encode()
        assert query["rccontinue"] == ["next-page"]
        return json.dumps({"query": {"recentchanges": [{
            "rcid": 11, "type": "edit", "title": "New Page",
            "timestamp": "2026-11-10T00:00:00Z", "revid": 88,
        }]}}).encode()
    monkeypatch.setattr(cli, "fetch_site_bytes", api)
    record = scan_recent_changes(
        SITE, LANG, since=OLD_TIME, until=NEW_TIME,
    )
    assert len(calls) == 2
    assert record["observedCount"] == 2
    assert record["events"][0]["targetTitle"] == "New Page"
    assert record["events"][1]["revid"] == 88
    assert record["retentionCoverage"] == "unverified"


def test_recentchanges_malformed_or_out_of_window_rejected(monkeypatch):
    for bad in [
        {"query": {"recentchanges": [{"rcid": 1, "title": "Test",
                                      "type": "edit",
                                      "timestamp": "2025-01-01T00:00:00Z"}]}},
        {"query": {"recentchanges": [{"rcid": 1, "title": "Test",
                                      "type": "edit",
                                      "timestamp": "not-a-time"}]}},
        {"query": {"recentchanges": "invalid"}},
        {"errors": [{"code": "permissiondenied"}]},
    ]:
        monkeypatch.setattr(
            cli, "fetch_site_bytes",
            lambda *a, d=bad, **k: json.dumps(d).encode(),
        )
        with pytest.raises(ValueError, match="RecentChanges"):
            scan_recent_changes(SITE, LANG, since=OLD_TIME, until=NEW_TIME)


def test_update_inventory_rejects_false_reuse_count(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    result = _update(parent, tmp_path)
    inventory = json.loads(result.inventory.read_text(encoding="utf-8"))
    inventory["captureMetadata"]["matchedParentMemberCount"] = 999
    result.inventory.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="accounting"):
        verify_snapshot(result.archive, result.manifest, result.inventory)


def test_incorrect_source_site_is_rejected_before_network(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(ValueError, match="site mismatch"):
        update_snapshot(
            *parent, archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "manifests",
            work_root=tmp_path / "work",
            expected_site="infinity-wiki",
        )
