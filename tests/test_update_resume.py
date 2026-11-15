"""P2.5b resume the full archive-derived current-refresh pipeline offline."""

from __future__ import annotations

import json
import urllib.parse
from datetime import timedelta

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, read_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_update import (
    SnapshotUpdateIncomplete, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from infinity_wiki_acquisition.archive_run import FILENAME
from test_snapshot_update import (
    SITE, LANG, ROOT, ADDED, ASSET, NEW, NEW_TIME,
    _build_parent, _remote,
)


def _run(parent, tmp, **kwargs):
    return update_snapshot(
        *parent,
        archive_directory=tmp / "updated",
        manifest_directory=tmp / "manifests",
        work_root=tmp / "work",
        now=kwargs.pop("now", NEW_TIME),
        **kwargs,
    )


def _fail_page_once(monkeypatch):
    calls = _remote(monkeypatch, changed=True)
    original = cli.fetch_site_bytes
    state = {"failed": False}
    def fetch(url, *, language, site, **kwargs):
        if url == ADDED and not state["failed"]:
            state["failed"] = True
            raise OSError("interrupted synthetic last page")
        return original(url, language=language, site=site, **kwargs)
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def test_resume_interrupted_allpages_continuation_without_restarting_scan(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    calls = _remote(monkeypatch, changed=True)
    base_fetch = cli.fetch_site_bytes
    fail_next = {"yes": True}

    def fetch(url, *, language, site, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["allpages"]:
            calls.append(url)
            if "apcontinue" not in query:
                return json.dumps({
                    "query": {"allpages": [{"title": "Main Page", "pageid": 1}]},
                    "continue": {"continue": "-||", "apcontinue": "next-page"},
                }).encode()
            assert query["apcontinue"] == ["next-page"]
            if fail_next["yes"]:
                fail_next["yes"] = False
                raise OSError("interrupted second enumeration batch")
            return json.dumps({
                "query": {"allpages": [
                    {"title": "New Page", "pageid": 2},
                    {"title": "Additional Page", "pageid": 4},
                ]},
            }).encode()
        return base_fetch(url, language=language, site=site, **kwargs)

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    state = read_crawl_checkpoint(
        work / CHECKPOINT_FILENAME, work=work,
        site=SITE.key, language=LANG, root_url=ROOT, require_complete=False,
    )
    assert state["phase"] == "enumerating"
    assert state["enumerationContinuation"] == "next-page"
    calls.clear()
    child = _run(parent, tmp_path, resume_work=work)
    assert verify_snapshot(child.archive, child.manifest, child.inventory)["parent"]
    assert sum("list=allpages" in url for url in calls) == 1
    assert "apcontinue=next-page" in next(
        url for url in calls if "list=allpages" in url
    )
    assert not any("list=recentchanges" in url for url in calls)


def test_resume_retries_only_unfinished_asset_without_page_refetch(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=True, failure=ASSET)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    calls = _remote(monkeypatch, changed=True)
    child = _run(parent, tmp_path, resume_work=work)
    assert verify_snapshot(child.archive, child.manifest, child.inventory)["parent"]
    assert ASSET in calls
    assert ROOT not in calls and NEW not in calls and ADDED not in calls
    assert not any("list=allpages" in url or "list=recentchanges" in url for url in calls)


def test_resume_current_crawl_uses_saved_scan_and_checkpoint_without_refetch(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    before = tuple(item.read_bytes() for item in parent)
    calls = _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    assert (work / FILENAME).is_file()
    assert (work / ".wiki-update-recentchanges.json").is_file()
    state = read_crawl_checkpoint(
        work / CHECKPOINT_FILENAME, work=work,
        site=SITE.key, language=LANG, root_url=ROOT,
    )
    assert ROOT in state["saved"] and ASSET in state["saved"]
    assert ADDED in state["failed"]
    assert (work / ".wiki-update-asset-decisions").is_dir()
    assert not list((tmp_path / "updated").glob("*.zip"))
    calls.clear()
    complete = _run(parent, tmp_path, resume_work=work)
    checked = verify_snapshot(complete.archive, complete.manifest, complete.inventory)
    assert checked["captureMetadata"]["updateMode"] == "verified-asset-version-refresh"
    assert checked["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0
    assert ADDED in calls
    assert ROOT not in calls and ASSET not in calls and NEW not in calls
    assert not any("list=allpages" in url or "list=recentchanges" in url for url in calls)
    assert tuple(item.read_bytes() for item in parent) == before
    assert not work.exists()


def test_resume_with_raw_html_replay_uses_saved_original_page_inputs(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    replay_dir = tmp_path / "raw-html"
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path, source_replay_directory=replay_dir)
    work = failure.value.work
    from infinity_wiki_acquisition.source_replay import source_body_path, verify_source_replay_bundle
    assert source_body_path(work, ROOT).is_file()
    calls = _remote(monkeypatch, changed=True)
    complete = _run(parent, tmp_path, resume_work=work, source_replay_directory=replay_dir)
    assert complete.source_replay is not None
    assert ROOT not in calls
    result = verify_source_replay_bundle(
        complete.source_replay, complete.archive, complete.manifest, complete.inventory,
    )
    assert len(result["pages"]) == 3


def test_resume_fails_before_network_on_option_parent_or_receipt_mismatch(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(ValueError, match="resume differs"):
        _run(parent, tmp_path, resume_work=work,
             source_replay_directory=tmp_path / "replay")
    document = json.loads((work / FILENAME).read_text())
    document["parentManifestSha256"] = "0" * 64
    (work / FILENAME).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="resume differs"):
        _run(parent, tmp_path, resume_work=work)


def test_resume_fails_on_same_size_saved_current_html_mutation(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    current = work / cli.local_relative_path(ROOT, site=SITE)
    original = current.read_bytes()
    current.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(ValueError, match="Saved current HTML lacks"):
        _run(parent, tmp_path, resume_work=work)


def test_resume_fails_on_saved_asset_mutation_without_refetch(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    asset = work / cli.local_relative_path(ASSET, asset=True, site=SITE)
    original = asset.read_bytes()
    asset.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(ValueError, match="Recorded update asset differs"):
        _run(parent, tmp_path, resume_work=work)


def test_resume_rejects_incomplete_recent_changes_receipt_before_network(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    evidence = work / ".wiki-update-recentchanges.json"
    data = json.loads(evidence.read_text())
    data["changes"]["observedCount"] = 99
    evidence.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    with pytest.raises(ValueError, match="Incomplete or inconsistent"):
        _run(parent, tmp_path, resume_work=work)


def test_resume_after_failed_inventory_publication_reuses_postcrawl_api_evidence(
    tmp_path, monkeypatch,
):
    import infinity_wiki_acquisition.snapshot_update as update_module

    parent = _build_parent(tmp_path)
    calls = _remote(monkeypatch, changed=True)
    original = update_module._publish_verified_inventory
    def fail(*args, **kwargs):
        raise ValueError("synthetic publication interruption")
    monkeypatch.setattr(update_module, "_publish_verified_inventory", fail)
    with pytest.raises(ValueError, match="publication interruption"):
        _run(parent, tmp_path)
    work = next((tmp_path / "work").glob("*.work"))
    assert (work / ".wiki-current-revisions.json").is_file()
    assert (work / ".wiki-page-relationships.json").is_file()
    monkeypatch.setattr(update_module, "_publish_verified_inventory", original)
    calls.clear()
    result = _run(parent, tmp_path, resume_work=work)
    assert verify_snapshot(result.archive, result.manifest, result.inventory)["parent"]
    assert calls == []
    assert not work.exists()


def test_resume_preserves_verified_http_304_asset_reuse_count(
    tmp_path, monkeypatch,
):
    import infinity_wiki_acquisition.snapshot_update as update_module
    from test_asset_conditional_reuse import _seed_etag_parent

    parent = _seed_etag_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    original_fetch = cli.fetch_site_bytes

    def fetch(url, *, language, site, if_none_match=None):
        if url == ASSET:
            assert if_none_match == '"asset-v1"'
            return cli.WikiFetchedBytes(
                b"", effective_url=url, http_status=304,
                etag=if_none_match, conditional_etag=if_none_match,
            )
        return original_fetch(url, language=language, site=site)

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    original_publisher = update_module._publish_verified_inventory
    monkeypatch.setattr(
        update_module, "_publish_verified_inventory",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("published later")),
    )
    with pytest.raises(ValueError, match="published later"):
        _run(parent, tmp_path, now=NEW_TIME + timedelta(minutes=30))
    work = next((tmp_path / "work").glob("*.work"))
    monkeypatch.setattr(update_module, "_publish_verified_inventory", original_publisher)
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    resumed = _run(parent, tmp_path, resume_work=work)
    metadata = verify_snapshot(
        resumed.archive, resumed.manifest, resumed.inventory,
    )["captureMetadata"]
    assert metadata["conditionalReusedAssetCount"] == 1
    assert metadata["uploadSha1ReusedAssetCount"] == 0
    assert metadata["sourceVersionSkipping"] is True


def test_resume_preserves_mediawiki_upload_sha1_reuse_count(
    tmp_path, monkeypatch,
):
    import infinity_wiki_acquisition.snapshot_update as update_module
    from test_mediawiki_upload_reuse import _remote_with_upload

    parent = _build_parent(tmp_path)
    calls = _remote_with_upload(monkeypatch)
    original_publisher = update_module._publish_verified_inventory
    monkeypatch.setattr(
        update_module, "_publish_verified_inventory",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("published later")),
    )
    with pytest.raises(ValueError, match="published later"):
        _run(parent, tmp_path)
    work = next((tmp_path / "work").glob("*.work"))
    assert [kind for kind, _ in calls] == ["imageinfo"]
    monkeypatch.setattr(update_module, "_publish_verified_inventory", original_publisher)
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    resumed = _run(parent, tmp_path, resume_work=work)
    metadata = verify_snapshot(
        resumed.archive, resumed.manifest, resumed.inventory,
    )["captureMetadata"]
    assert metadata["uploadSha1ReusedAssetCount"] == 1
    assert metadata["uploadVersionEvidenceCount"] == 1
    assert metadata["conditionalReusedAssetCount"] == 0


def test_resume_partially_completed_history_refresh_preserves_oldid_reuse(
    tmp_path, monkeypatch,
):
    from test_history_refresh import _history_parent, PARENT_IDS, REVISION_TIMESTAMP

    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    original_fetch = cli.fetch_site_bytes
    requests = []
    once = {"fail": True}

    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"] and "titles" in query:
            title = query["titles"][0].replace("_", " ")
            requests.append(("list", title))
            oldid = PARENT_IDS[title]
            ids = [oldid] + ([111] if title == "Main Page" else [])
            return json.dumps({"query": {"pages": [{
                "pageid": 1, "title": title,
                "revisions": [
                    {
                        "revid": ident, "parentid": ident - 1,
                        "timestamp": REVISION_TIMESTAMP,
                    }
                    for ident in ids
                ],
            }]}}).encode()
        if "oldid" in query:
            oldid = int(query["oldid"][0])
            requests.append(("oldid", oldid))
            assert oldid == 111, "Previously archived oldids must not be downloaded"
            if once["fail"]:
                once["fail"] = False
                raise OSError("first new oldid unavailable")
            return b"<html>new oldid 111</html>"
        return original_fetch(url, language=language, site=site)

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    assert (work / ".wiki-history-checkpoint.json").is_file()
    requests.clear()
    child = _run(parent, tmp_path, resume_work=work)
    assert ("list", "Main Page") not in requests
    assert ("oldid", 111) in requests
    assert child.reused_revisions == len(PARENT_IDS)
    assert child.history_revisions == len(PARENT_IDS) + 1
    verified = verify_snapshot(child.archive, child.manifest, child.inventory)
    assert verified["history"]
    assert verified["captureMetadata"]["historyRefresh"]["reusedOldidCount"] == len(PARENT_IDS)
    assert not work.exists()


def test_resume_v2_retired_page_after_publication_failure(
    tmp_path, monkeypatch,
):
    import infinity_wiki_acquisition.snapshot_update as update_module
    from test_history_refresh import _history_parent, _history_source, PARENT_IDS

    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    requests = _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210, "Additional Page": 410,
        }, fail_existing=PARENT_IDS.values(),
    )
    original_publisher = update_module._publish_verified_inventory
    monkeypatch.setattr(
        update_module, "_publish_verified_inventory",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("published later")),
    )
    with pytest.raises(ValueError, match="published later"):
        _run(parent, tmp_path, allow_historical_only=True)
    work = next((tmp_path / "work").glob("*.work"))
    assert (work / "_history/retired").is_dir()
    monkeypatch.setattr(update_module, "_publish_verified_inventory", original_publisher)
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **k: pytest.fail("network"))
    result = _run(
        parent, tmp_path, allow_historical_only=True, resume_work=work,
    )
    checked = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert checked["formatVersion"] == 2
    assert checked["captureMetadata"]["historyRefresh"]["historicalOnlyPageCount"] == 1
    assert checked["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0


def test_cli_update_supports_resume_work(tmp_path, monkeypatch, capsys):
    parent = _build_parent(tmp_path)
    _fail_page_once(monkeypatch)
    with pytest.raises(SnapshotUpdateIncomplete) as failure:
        _run(parent, tmp_path)
    work = failure.value.work
    _remote(monkeypatch, changed=True)
    monkeypatch.chdir(tmp_path)
    assert cli.main([
        "--update-from", str(parent[0]),
        "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--resume-work", str(work),
        "--root", "other-output", "--manifest-dir", "other-manifests",
    ]) == 0
    assert "Updated Wiki snapshot" in capsys.readouterr().out
    assert len(list((tmp_path / "other-output").glob("*.zip"))) == 1
