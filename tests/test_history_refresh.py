"""P2.3c offline history-parent update integration scenarios.

All source responses are synthetic. Previously archived current HTML is
refetched, oldid bytes remain immutable, and missing/deleted historical
pages cannot be silently dropped.
"""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_upgrade import (
    upgrade_snapshot_with_history,
)
from infinity_wiki_acquisition.snapshot_update import (
    SnapshotUpdateIncomplete, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_snapshot_update import (
    SITE, LANG, ROOT, OLD, DELETED, NEW, ASSET, PNG,
    OLD_TIME, NEW_TIME, ROOT_NEW, PAGES, _build_parent, _remote,
)

PARENT_HISTORY_TIME = OLD_TIME + timedelta(days=1)
REVISION_TIMESTAMP = "2026-09-01T00:00:00Z"
PARENT_IDS = {"Main Page": 110, "Old Page": 210, "Deleted Page": 310}
PARENT_BY_NAME = {
    "Main Page": 110,
    "Old Page": 210,
    "Deleted Page": 310,
    "New Page": 210,
}


def _history_source(monkeypatch, *, ids=None, fail_existing=(), changed_meta=()):
    previous = cli.fetch_site_bytes
    calls: list[tuple[str, int | str]] = []
    oldids = ids or PARENT_IDS
    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"] and "titles" in query:
            title = query["titles"][0].replace("_", " ")
            oldid = oldids[title]
            calls.append(("revision-list", title))
            return json.dumps({
                "query": {"pages": [{
                    "pageid": 1, "title": title,
                    "revisions": [{
                        "revid": oldid,
                        "parentid": (
                            999 if oldid in changed_meta else oldid - 1
                        ),
                        "timestamp": REVISION_TIMESTAMP,
                    }],
                }]},
            }).encode()
        if "oldid" in query:
            oldid = int(query["oldid"][0])
            calls.append(("oldid-body", oldid))
            if oldid in fail_existing:
                raise AssertionError(f"Archived oldid {oldid} was re-downloaded")
            return f"<html>historical oldid {oldid}</html>".encode()
        return previous(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def _history_parent(tmp_path: Path, monkeypatch):
    parent = _build_parent(tmp_path)
    _history_source(monkeypatch)
    old = upgrade_snapshot_with_history(
        *parent,
        archive_directory=tmp_path / "old-history-archives",
        manifest_directory=tmp_path / "old-history-manifests",
        work_root=tmp_path / "old-history-work",
        now=PARENT_HISTORY_TIME,
    )
    return old.archive, old.manifest, old.inventory


def _run_update(parent, tmp_path: Path, *, require_history=False):
    return update_snapshot(
        *parent,
        archive_directory=tmp_path / "refreshed-archives",
        manifest_directory=tmp_path / "refreshed-manifests",
        work_root=tmp_path / "refreshed-work",
        now=NEW_TIME, require_history=require_history,
    )


def _move_remote(monkeypatch, *, new_revision=True):
    _remote(monkeypatch, changed=False)
    base = cli.fetch_site_bytes
    calls = []
    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        calls.append(url)
        if query.get("list") == ["allpages"]:
            return json.dumps({"query": {"allpages": [
                {"pageid": 1, "title": "Main Page"},
                {"pageid": 2, "title": "New Page"},
                {"pageid": 3, "title": "Deleted Page"},
            ]}}).encode()
        if query.get("list") == ["recentchanges"]:
            return json.dumps({"query": {"recentchanges": [{
                "rcid": 901, "type": "log", "logtype": "move",
                "logaction": "move", "pageid": 2,
                "title": "Old Page",
                "timestamp": "2026-11-01T00:00:00Z",
                "logparams": {"target_title": "New Page"},
            }]}}).encode()
        if url == ROOT:
            return ROOT_NEW
        if url == NEW:
            return b"<html>new current title for page ID 2</html>"
        return base(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def test_history_parent_preserves_all_oldids_and_refetches_current(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    original = [path.read_bytes() for path in parent]
    with zipfile.ZipFile(parent[0]) as source:
        prior_pages = source.read("Main_Page.html")
    current_calls = _remote(monkeypatch, changed=False)
    oldid_calls = _history_source(
        monkeypatch,
        ids={**PARENT_IDS, "Deleted Page": 310},
        fail_existing=PARENT_IDS.values(),
    )
    child = _run_update(parent, tmp_path, require_history=True)
    assert child.history_revisions == 3
    assert child.reused_revisions == 3
    assert len([call for call in oldid_calls if call[0] == "oldid-body"]) == 0
    assert ROOT in current_calls and OLD in current_calls and DELETED in current_calls
    assert [path.read_bytes() for path in parent] == original
    with zipfile.ZipFile(child.archive) as source:
        for oldid in PARENT_IDS.values():
            assert source.read(f"_history/oldid/{oldid}.html") == (
                f"<html>historical oldid {oldid}</html>".encode()
            )
        assert source.read("Main_Page.html") == prior_pages
    checked = verify_snapshot(child.archive, child.manifest, child.inventory)
    assert checked["history"] is True
    audit = checked["captureMetadata"]["historyRefresh"]
    assert audit["sourceOldidCount"] == 3
    assert audit["freshOldidCount"] == 3
    assert audit["reusedOldidCount"] == 3
    assert audit["historyCutoff"] == "all-observed"
    assert len(audit["pageMappings"]) == 3
    assert child.archive.name.startswith("HUMAN-SPHERE-history ")


def test_page_move_preserves_oldid_identity_and_new_history_index(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    calls = _move_remote(monkeypatch)
    oldid_calls = _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210, "Deleted Page": 310,
        }, fail_existing=PARENT_IDS.values(),
    )
    child = _run_update(parent, tmp_path)
    assert child.reused_revisions == 3
    assert NEW in calls
    assert OLD not in calls
    assert not [c for c in oldid_calls if c[0] == "oldid-body"]
    with zipfile.ZipFile(child.archive) as source:
        assert "New_Page.html" in source.namelist()
        assert "Old_Page.html" not in source.namelist()
        index = json.loads(source.read("_history/index.json"))
        move = next(p for p in index["pages"] if p["currentUrl"] == NEW)
        assert move["currentPath"] == "New_Page.html"
        assert {r["oldid"] for r in move["revisions"]} == {210}
        assert source.read("_history/oldid/210.html") == (
            b"<html>historical oldid 210</html>"
        )
    verified = verify_snapshot(child.archive, child.manifest, child.inventory)
    mappings = verified["captureMetadata"]["historyRefresh"]["pageMappings"]
    move = next(row for row in mappings if row["previousUrl"] == OLD)
    assert move == {
        "pageId": 2, "previousUrl": OLD, "currentUrl": NEW,
        "identityBasis": "verified-allpages-page-id",
        "changedUrl": True,
    }
    assert verified["captureMetadata"]["pageMovesById"][0][
        "interpretation"
    ] == "move-corroborated-by-recentchanges"


def test_history_update_downloads_only_new_oldids_and_preserves_old(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    base = cli.fetch_site_bytes
    calls = []
    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"] and "titles" in query:
            title = query["titles"][0].replace("_", " ")
            oldid = PARENT_IDS[title]
            return json.dumps({"query": {"pages": [{
                "pageid": 1, "title": title,
                "revisions": [
                    {"revid": oldid, "parentid": oldid - 1,
                     "timestamp": REVISION_TIMESTAMP},
                    *([{"revid": 111, "parentid": 110,
                        "timestamp": "2026-11-01T00:00:00Z"}]
                      if title == "Main Page" else []),
                ],
            }]}}).encode()
        if "oldid" in query:
            oldid = int(query["oldid"][0])
            calls.append(oldid)
            if oldid != 111:
                raise AssertionError(f"Already present oldid {oldid} was fetched")
            return b"<html>new historical version 111</html>"
        return base(url, language=language, site=site)
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    child = _run_update(parent, tmp_path)
    assert calls == [111]
    assert child.reused_revisions == 3
    assert child.history_revisions == 4
    verified = verify_snapshot(child.archive, child.manifest, child.inventory)
    assert verified["captureMetadata"]["historyRefresh"]["freshOldidCount"] == 4


def test_historical_alias_urls_converge_without_losing_oldid(
    tmp_path: Path, monkeypatch,
):
    from infinity_wiki_acquisition.crawl_checkpoint import (
        CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
        write_crawl_checkpoint,
    )
    from infinity_wiki_acquisition.snapshot_inventory import (
        build_snapshot_inventory,
    )
    from infinity_wiki_acquisition.snapshot_provenance import (
        write_snapshot_manifest,
    )

    alias_url = cli.page_url_from_title(
        "Alias Page", language=LANG, site=SITE,
    )
    old_pages = [
        (ROOT, "Main Page", 1, b"<html>main</html>"),
        (OLD, "Old Page", 2, b"<html>old</html>"),
        (alias_url, "Alias Page", 2, b"<html>alias</html>"),
        (DELETED, "Deleted Page", 3, b"<html>unchanged</html>"),
    ]
    work = tmp_path / "alias-parent-work"
    work.mkdir()
    saved = {}
    discovered = {}
    files = []
    pages = []
    page_ids = {}
    for url, title, page_id, content in old_pages:
        path = cli.local_relative_path(url, site=SITE)
        file = work / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        saved[url] = [path, len(content)]
        discovered[url] = False
        page_ids[url] = page_id
        pages.append(cli.WikiPage(url, path, title))
        files.append(file)
    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "phase": "complete", "complete": True,
        "site": SITE.key, "language": LANG, "rootUrl": ROOT,
        "enumerationContinuation": None, "queue": [],
        "discovered": discovered, "pageIds": page_ids,
        "required": list(saved), "saved": saved,
        "pages": [
            {"url": item.url, "title": item.title, "path": item.path}
            for item in pages
        ],
        "ignored": {}, "failed": {},
    })
    initial = cli.archive_wiki(
        files, tmp_path / "alias-initial-archive",
        root=work, site=SITE, language=LANG, now=OLD_TIME,
    )
    manifest = write_snapshot_manifest(
        initial, tmp_path / "alias-initial-manifests",
        snapshot_type="wiki", acquired_at=OLD_TIME,
        source_url=ROOT, document_count=len(files),
        project_root=tmp_path, language=LANG,
    )
    initial_doc = build_snapshot_inventory(
        initial, manifest, work=work, site=SITE.key, language=LANG,
        root_url=ROOT, include_history=False, pages=pages,
        crawl_checkpoint=work / CHECKPOINT_FILENAME,
    )
    inventory = (
        tmp_path / "alias-initial-manifests" / "inventories"
        / f"{initial.stem}.inventory.json"
    )
    inventory.parent.mkdir()
    inventory.write_text(json.dumps(initial_doc), encoding="utf-8")
    verify_snapshot(initial, manifest, inventory)
    _history_source(monkeypatch, ids={
        **PARENT_IDS, "Alias Page": 210,
    })
    parent_result = upgrade_snapshot_with_history(
        initial, manifest, inventory,
        archive_directory=tmp_path / "alias-history-archive",
        manifest_directory=tmp_path / "alias-history-manifests",
        work_root=tmp_path / "alias-history-work",
        now=PARENT_HISTORY_TIME,
    )
    parent = (
        parent_result.archive, parent_result.manifest,
        parent_result.inventory,
    )

    # Fresh enumeration retains page ID 2 only under the new URL.
    _move_remote(monkeypatch)
    _history_source(monkeypatch, ids={
        "Main Page": 110, "New Page": 210, "Deleted Page": 310,
    }, fail_existing=PARENT_IDS.values())
    child = _run_update(parent, tmp_path)
    assert child.reused_revisions == 3
    assert child.history_revisions == 3
    verified = verify_snapshot(
        child.archive, child.manifest, child.inventory,
    )
    mappings = verified["captureMetadata"]["historyRefresh"]["pageMappings"]
    assert {
        row["previousUrl"]: row["currentUrl"] for row in mappings
    }[OLD] == NEW
    assert {
        row["previousUrl"]: row["currentUrl"] for row in mappings
    }[alias_url] == NEW
    with zipfile.ZipFile(child.archive) as source:
        index = json.loads(source.read("_history/index.json"))
        new_page = next(p for p in index["pages"] if p["currentUrl"] == NEW)
        assert {rev["oldid"] for rev in new_page["revisions"]} == {210}


def test_deleted_historical_page_cannot_silently_disappear(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    with pytest.raises(ValueError, match="missing from fresh discovery"):
        _run_update(parent, tmp_path)
    assert not list((tmp_path / "refreshed-archives").glob("*.zip"))
    assert not list((tmp_path / "refreshed-manifests").rglob("*.json"))


def test_previously_archived_oldid_omitted_from_new_history_blocks_publication(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    _history_source(monkeypatch, ids={
        "Main Page": 110, "Old Page": 211, "Deleted Page": 310,
    }, fail_existing={110, 310})
    with pytest.raises(ValueError, match="Previously archived oldids absent"):
        _run_update(parent, tmp_path)
    assert not list((tmp_path / "refreshed-archives").glob("*.zip"))
    assert not list((tmp_path / "refreshed-manifests").rglob("*.json"))


def test_history_revision_identity_conflict_blocks_publication(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    _history_source(
        monkeypatch, changed_meta={210},
    )
    with pytest.raises(SnapshotUpdateIncomplete):
        _run_update(parent, tmp_path)
    assert not list((tmp_path / "refreshed-archives").glob("*.zip"))


def test_history_parent_does_not_allow_unrequested_history_loss(
    tmp_path: Path, monkeypatch, capsys,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    _history_source(monkeypatch, fail_existing=PARENT_IDS.values())
    assert cli.main([
        "--update-from", str(parent[0]),
        "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--root", str(tmp_path / "cli-archives"),
        "--work-root", str(tmp_path / "cli-work"),
        "--manifest-dir", str(tmp_path / "cli-manifests"),
    ]) == 0
    assert "Preserved history: 3 oldids" in capsys.readouterr().out
    archived = list((tmp_path / "cli-archives").glob("*.zip"))
    assert len(archived) == 1
    assert verify_snapshot(
        archived[0],
        next((tmp_path / "cli-manifests").glob("*.json")),
        next((tmp_path / "cli-manifests" / "inventories").glob("*.json")),
    )["history"] is True


def test_non_history_parent_cannot_request_synthetic_history(
    tmp_path: Path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **k: pytest.fail("No network should be requested"),
    )
    with pytest.raises(ValueError, match="requires a history-enabled parent"):
        _run_update(parent, tmp_path, require_history=True)


@pytest.mark.parametrize("field", [
    "sourceOldidCount", "reusedOldidCount", "freshOldidCount",
    "historyCutoff", "pageMappings",
])
def test_forged_history_refresh_evidence_rejected(
    tmp_path: Path, monkeypatch, field,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    _history_source(monkeypatch, fail_existing=PARENT_IDS.values())
    child = _run_update(parent, tmp_path)
    original = json.loads(child.inventory.read_text(encoding="utf-8"))
    for tamper in (field,):
        document = json.loads(json.dumps(original))
        record = document["captureMetadata"]["historyRefresh"]
        if tamper == "sourceOldidCount":
            record[tamper] = 0
        elif tamper == "reusedOldidCount":
            record[tamper] = 999
        elif tamper == "freshOldidCount":
            record[tamper] -= 1
        elif tamper == "historyCutoff":
            record[tamper] = "partial"
        else:
            record[tamper][0]["pageId"] = 999
        child.inventory.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(SnapshotVerificationError):
            verify_snapshot(child.archive, child.manifest, child.inventory)
