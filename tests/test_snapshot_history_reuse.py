"""P2.2: reuse verified oldids and reconcile page/title identity offline."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file, write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_upgrade import (
    SnapshotUpgradeIncomplete, upgrade_snapshot_with_history,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

from test_snapshot_upgrade import _NEW_TIME, _PARENT_TIME, _source

_NEXT_TIME = _NEW_TIME + timedelta(hours=1)


def _history_api(monkeypatch, *, oldids=(101, 102), title="Main Page",
                 reject_oldids=(), altered_identity=False,
                 aliases=False):
    calls: list[str] = []
    def fetch(url: str, *, language: str, site) -> bytes:
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            page_title = query.get("titles", [""])[0]
            name = ("Alternate Canonical" if aliases and "Alt" in page_title else title)
            return json.dumps({
                "query": {"pages": [{
                    "pageid": 81,
                    "title": name,
                    "revisions": [
                        {
                            "revid": oldid,
                            "parentid": oldid - 1 + (
                                100 if altered_identity and oldid == 101 else 0
                            ),
                            "timestamp": (
                                "2024-01-01T00:00:00Z"
                                if altered_identity and oldid == 101
                                else "2025-10-01T12:00:00Z"
                            ),
                        } for oldid in oldids
                    ],
                }]},
            }).encode()
        if "oldid" in query:
            oldid = int(query["oldid"][0])
            if oldid in reject_oldids:
                raise AssertionError(f"Previously archived oldid was requested: {oldid}")
            return f"<html>oldid {oldid}</html>".encode()
        raise AssertionError(f"Current page or asset was requested: {url}")
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def _upgrade(parent, tmp_path: Path, *, time=_NEXT_TIME):
    return upgrade_snapshot_with_history(
        *parent, archive_directory=tmp_path / "out-second",
        manifest_directory=tmp_path / "manifests-second",
        work_root=tmp_path / "work-second",
        now=time,
    )


def _history_parent(tmp_path: Path, monkeypatch, *, site=cli.DEFAULT_SITE,
                    language="en", aliases=False):
    if not aliases:
        source = _source(tmp_path, site=site, language=language)
    else:
        root = site.root_urls[language]
        work = tmp_path / "old-work"
        work.mkdir()
        first = work / "index.html"
        second = work / "alt.html"
        first.write_bytes(b"<html>main</html>")
        second.write_bytes(b"<html>alias</html>")
        source_pages = (
            cli.WikiPage(root, "index.html", "Main Page"),
            cli.WikiPage(root + "Alt_Page", "alt.html", "Alt Page"),
        )
        archive = cli.archive_wiki(
            [first, second], tmp_path / "old-archive", root=work,
            now=_PARENT_TIME, site=site, language=language,
        )
        manifest = write_snapshot_manifest(
            archive, tmp_path / "old-manifests", snapshot_type="wiki",
            acquired_at=_PARENT_TIME, source_url=root,
            document_count=2, project_root=tmp_path, language=language,
        )
        document = build_snapshot_inventory(
            archive, manifest, work=work, site=site.key, language=language,
            root_url=root, include_history=False, pages=source_pages,
        )
        inventory = tmp_path / "old-manifests" / "inventories" / (archive.stem + ".inventory.json")
        inventory.parent.mkdir()
        inventory.write_text(json.dumps(document), encoding="utf-8")
        source = (archive, manifest, inventory)
    _history_api(monkeypatch, oldids=(101, 102) if not aliases else (101,),
                 aliases=aliases)
    first_history = upgrade_snapshot_with_history(
        *source, archive_directory=tmp_path / "out-first",
        manifest_directory=tmp_path / "manifests-first",
        work_root=tmp_path / "work-first", now=_NEW_TIME - timedelta(days=2),
    )
    return first_history.archive, first_history.manifest, first_history.inventory


@pytest.mark.parametrize("site,language", [
    (cli.INFINITY_WIKI_SITE, "en"),
    (cli.INFINITY_WIKI_SITE, "es"),
    (cli.HUMAN_SPHERE_SITE, "en"),
])
def test_history_parent_reuses_oldids_and_adds_only_new_revisions(
    tmp_path: Path, monkeypatch, site, language,
):
    parent = _history_parent(tmp_path, monkeypatch, site=site, language=language)
    parent_bytes = [path.read_bytes() for path in parent]
    requests = _history_api(
        monkeypatch, oldids=(101, 102, 103), title="Renamed Canonical Page",
        reject_oldids=(101, 102),
    )
    result = _upgrade(parent, tmp_path)
    assert result.reused_members == 4  # one page + asset + two oldids
    assert result.reused_revisions == 2
    assert result.revision_count == 3
    assert len([url for url in requests if "oldid=" in url]) == 1
    assert "oldid=103" in requests[-1]
    assert [path.read_bytes() for path in parent] == parent_bytes

    with zipfile.ZipFile(parent[0]) as before, zipfile.ZipFile(result.archive) as after:
        for name in ("index.html", "images/icon.png", "_history/oldid/101.html",
                     "_history/oldid/102.html"):
            assert before.read(name) == after.read(name)
        index = json.loads(after.read("_history/index.json"))
        assert index["pages"][0]["title"] == "Renamed Canonical Page"
        assert index["revisionCount"] == 3

    original = verify_snapshot(*parent)
    new = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert new["history"] is True
    assert new["captureMetadata"]["currentContentAcquiredAt"] == _PARENT_TIME.isoformat()
    assert new["captureMetadata"]["parentHistory"] is True
    assert new["captureMetadata"]["reusedRevisionCount"] == 2
    assert new["parent"]["archiveSha256"] == sha256_file(parent[0])
    assert new["parent"]["reusedMemberCount"] == 4
    assert new["ignoredUrls"] == original["ignoredUrls"]
    assert new["members"][-2:] == original["members"][-2:]
    assert new["snapshot"]["acquiredAt"] == _NEXT_TIME.isoformat()
    assert new["captureMetadata"]["historyRetrievedAt"] == _NEXT_TIME.isoformat()


def test_reuses_shared_oldid_once_across_two_alias_pages(tmp_path: Path, monkeypatch):
    parent = _history_parent(tmp_path, monkeypatch, aliases=True)
    calls = _history_api(monkeypatch, oldids=(101,), aliases=True, reject_oldids=(101,))
    result = _upgrade(parent, tmp_path)
    assert result.reused_revisions == 1
    assert result.revision_count == 1
    # Nothing changed, so deterministic ZIP bytes can match across distinct
    # timestamped snapshots without making the lineage self-referential.
    assert sha256_file(parent[0]) == sha256_file(result.archive)
    assert len([url for url in calls if "prop=revisions" in url]) == 2
    assert all("oldid=" not in url for url in calls)
    with zipfile.ZipFile(result.archive) as source:
        index = json.loads(source.read("_history/index.json"))
        assert len(index["pages"]) == 2
        assert index["revisionCount"] == 1
        assert len({r["path"] for page in index["pages"] for r in page["revisions"]}) == 1
    assert verify_snapshot(result.archive, result.manifest, result.inventory)["history"]


def test_conflicting_oldid_metadata_fails_without_republishing(tmp_path: Path, monkeypatch):
    parent = _history_parent(tmp_path, monkeypatch)
    calls = _history_api(monkeypatch, oldids=(101, 102), altered_identity=True)
    with pytest.raises(SnapshotUpgradeIncomplete, match="revision request"):
        _upgrade(parent, tmp_path)
    assert not [url for url in calls if "oldid=" in url]
    assert not list((tmp_path / "out-second").glob("*.zip"))
    assert not list((tmp_path / "manifests-second").rglob("*.json"))


def test_parent_oldid_omitted_from_new_enumeration_fails_closed(tmp_path: Path, monkeypatch):
    parent = _history_parent(tmp_path, monkeypatch)
    calls = _history_api(monkeypatch, oldids=(101,), reject_oldids=(101,))
    with pytest.raises(ValueError, match="Previously archived oldids absent"):
        _upgrade(parent, tmp_path)
    assert all("oldid=" not in url for url in calls)
    assert not list((tmp_path / "out-second").glob("*.zip"))


def test_history_parent_cli_acceptance(tmp_path: Path, monkeypatch, capsys):
    parent = _history_parent(tmp_path, monkeypatch)
    requests = _history_api(monkeypatch, oldids=(101, 102, 103),
                            reject_oldids=(101, 102))
    assert cli.main([
        "--from-snapshot", str(parent[0]), "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]), "--include-history",
        "--root", str(tmp_path / "cli-output"),
        "--manifest-dir", str(tmp_path / "cli-manifests"),
        "--work-root", str(tmp_path / "cli-work"),
    ]) == 0
    output = capsys.readouterr().out
    assert "(2 oldid revisions)" in output
    assert len([url for url in requests if "oldid=" in url]) == 1


def test_parent_alias_association_loss_blocks_publication(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch, aliases=True)
    def api(url: str, *, language: str, site) -> bytes:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            is_alt = "Alt" in query.get("titles", [""])[0]
            oldid = 101 if is_alt else 102
            return json.dumps({"query": {"pages": [{
                "pageid": 81, "title": "Alt Page" if is_alt else "Main Page",
                "revisions": [{
                    "revid": oldid, "parentid": oldid - 1,
                    "timestamp": "2025-10-01T12:00:00Z",
                }],
            }]}}).encode()
        if query.get("oldid") == ["102"]:
            return b"<html>newer alias history</html>"
        raise AssertionError(f"Unexpected network: {url}")
    monkeypatch.setattr(cli, "fetch_site_bytes", api)
    with pytest.raises(ValueError, match="page/oldid associations disappeared"):
        _upgrade(parent, tmp_path)
    assert not list((tmp_path / "out-second").glob("*.zip"))


def test_duplicate_oldid_with_conflicting_alias_identity_fails(
    tmp_path: Path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch, aliases=True)
    calls: list[str] = []
    def api(url: str, *, language: str, site) -> bytes:
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            second = "Alt" in query.get("titles", [""])[0]
            return json.dumps({"query": {"pages": [{
                "pageid": 81,
                "title": "Alt Page" if second else "Main Page",
                "revisions": [{
                    "revid": 101,
                    "parentid": 100 if second else 777,
                    "timestamp": "2025-10-01T12:00:00Z",
                }],
            }]}}).encode()
        raise AssertionError(f"Unexpected network: {url}")
    monkeypatch.setattr(cli, "fetch_site_bytes", api)
    with pytest.raises(SnapshotUpgradeIncomplete, match="revision request"):
        _upgrade(parent, tmp_path)
    assert len([url for url in calls if "prop=revisions" in url]) == 2
    assert not list((tmp_path / "out-second").glob("*.zip"))
