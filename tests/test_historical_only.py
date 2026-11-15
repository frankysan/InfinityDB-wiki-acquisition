"""P2.3c.2: explicit v2 historical-only retention without false deletion claims."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import timedelta

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_update import update_snapshot
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_history_refresh import (
    _history_parent, _history_source, _run_update,
    PARENT_IDS,
)
from test_snapshot_update import (
    OLD, DELETED, NEW, ROOT, NEW_TIME, _remote,
)


def _retired_update(tmp_path, monkeypatch, *, source_replay_directory=None):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210,
            "Additional Page": 410,
        },
        fail_existing=PARENT_IDS.values(),
    )
    result = update_snapshot(
        *parent,
        archive_directory=tmp_path / "v2-archive",
        manifest_directory=tmp_path / "v2-manifests",
        work_root=tmp_path / "v2-work",
        now=NEW_TIME, allow_historical_only=True,
        source_replay_directory=source_replay_directory,
    )
    return parent, result


def test_opt_in_retains_deleted_html_and_revision_under_v2_not_current(
    tmp_path, monkeypatch,
):
    parent, child = _retired_update(tmp_path, monkeypatch)
    checked = verify_snapshot(child.archive, child.manifest, child.inventory)
    assert checked["formatVersion"] == 2
    assert checked["history"]
    reuse_gate = checked["captureMetadata"]["renderReuseGate"]
    current_pages = [
        item for item in checked["members"] if item["kind"] == "current-page"
    ]
    assert reuse_gate["currentPageCount"] == len(current_pages)
    assert reuse_gate["refetchedPageCount"] == len(current_pages)
    assert reuse_gate["skippedHtmlBodyCount"] == 0
    assert DELETED not in {
        decision["pageUrl"] for decision in reuse_gate["decisions"]
    }
    assert checked["captureMetadata"]["historyRefresh"][
        "historyCutoff"
    ] == "current-all-observed-former-last-archived"
    with zipfile.ZipFile(child.archive) as new:
        index = json.loads(new.read("_history/index.json"))
        assert index["formatVersion"] == 2
        assert index["historyCoverage"] == (
            "current-all-observed-former-last-archived"
        )
        retired = index["historicalOnlyPages"]
        assert len(retired) == 1
        assert retired[0]["formerUrl"] == DELETED
        assert retired[0]["pageId"] == 3
        assert retired[0]["status"] == "absent-from-current-enumeration"
        assert retired[0]["historyScope"] == "last-archived-only"
        assert {x["oldid"] for x in retired[0]["revisions"]} == {310}
        assert DELETED not in [p["currentUrl"] for p in index["pages"]]
        assert "Deleted_Page.html" not in new.namelist()
        assert new.read(retired[0]["archivedPath"]) == (
            b"<html>removed page</html>"
        )
        assert new.read("_history/oldid/310.html") == (
            b"<html>historical oldid 310</html>"
        )
    former = [
        member for member in checked["members"]
        if member["kind"] == "historical-page"
    ]
    assert len(former) == 1
    assert "renderedResponseVersion" not in former[0]
    assert all(
        "renderedResponseVersion" not in item
        for item in checked["members"] if item["kind"] == "revision"
    )
    assert former[0]["formerPage"]["formerUrl"] == DELETED
    assert former[0]["formerPage"]["originalPath"] == "Deleted_Page.html"
    oldid = next(
        member for member in checked["members"]
        if member["kind"] == "revision"
        and member["revision"]["oldid"] == 310
    )
    assert oldid["revision"]["pages"] == [{
        "url": DELETED, "title": "Deleted Page",
    }]
    assert oldid["sourceIdentity"]["pageIds"] == [
        {"url": DELETED, "pageId": 3},
    ]
    assert checked["captureMetadata"]["historyRefresh"][
        "historicalOnlyPageCount"
    ] == 1
    # Parent untouched and still independently verifiable.
    assert verify_snapshot(*parent)["formatVersion"] == 1


def test_v2_source_replay_only_includes_current_html(tmp_path, monkeypatch):
    from infinity_wiki_acquisition.source_replay import (
        verify_source_replay_bundle,
    )

    parent, child = _retired_update(
        tmp_path, monkeypatch,
        source_replay_directory=tmp_path / "replay-v2",
    )
    assert child.source_replay is not None
    replay = verify_source_replay_bundle(
        child.source_replay, child.archive, child.manifest, child.inventory,
    )
    checked = verify_snapshot(child.archive, child.manifest, child.inventory)
    assert checked["formatVersion"] == 2
    assert len(replay["pages"]) == sum(
        row["kind"] == "current-page" for row in checked["members"]
    )
    assert all(row["pageUrl"] != DELETED for row in replay["pages"])
    assert all(not row["rawMember"].startswith("_history/")
               for row in replay["pages"])
    assert verify_snapshot(*parent)["formatVersion"] == 1


def test_retired_page_preserves_distinct_historical_canonical_title(
    tmp_path, monkeypatch,
):
    from infinity_wiki_acquisition.snapshot_upgrade import (
        upgrade_snapshot_with_history,
    )
    from test_snapshot_update import _build_parent

    seed = _build_parent(tmp_path)
    _history_source(monkeypatch)
    original = cli.fetch_site_bytes

    def canonical_history_title(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        data = original(
            url, language=language, site=site,
            **({"if_none_match": if_none_match} if if_none_match else {}),
        )
        if (query.get("prop") == ["revisions"]
                and query.get("titles") == ["Deleted Page"]):
            document = json.loads(data)
            document["query"]["pages"][0]["title"] = "Former Canonical Title"
            return json.dumps(document).encode()
        return data

    monkeypatch.setattr(cli, "fetch_site_bytes", canonical_history_title)
    prior = upgrade_snapshot_with_history(
        *seed,
        archive_directory=tmp_path / "different-title-archive",
        manifest_directory=tmp_path / "different-title-manifests",
        work_root=tmp_path / "different-title-work",
        now=NEW_TIME - timedelta(days=1),
    )
    _remote(monkeypatch, changed=True)
    _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210,
            "Additional Page": 410,
        }, fail_existing=PARENT_IDS.values(),
    )
    following_fetch = cli.fetch_site_bytes

    def valid_recent_window(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["recentchanges"]:
            return b'{"query":{"recentchanges":[]}}'
        return following_fetch(
            url, language=language, site=site,
            **({"if_none_match": if_none_match} if if_none_match else {}),
        )

    monkeypatch.setattr(cli, "fetch_site_bytes", valid_recent_window)
    updated = update_snapshot(
        prior.archive, prior.manifest, prior.inventory,
        archive_directory=tmp_path / "different-title-v2",
        manifest_directory=tmp_path / "different-title-v2-manifest",
        work_root=tmp_path / "different-title-v2-work",
        now=NEW_TIME, allow_historical_only=True,
    )
    verify_snapshot(updated.archive, updated.manifest, updated.inventory)
    with zipfile.ZipFile(updated.archive) as source:
        index = json.loads(source.read("_history/index.json"))
    former = index["historicalOnlyPages"][0]
    assert former["formerTitle"] == "Former Canonical Title"
    assert former["lastKnownCurrentTitle"] == "Deleted Page"


def test_without_opt_in_missing_historical_page_remains_fail_closed(
    tmp_path, monkeypatch,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    with pytest.raises(ValueError, match="allow-historical-only"):
        _run_update(parent, tmp_path)
    assert not list((tmp_path / "refreshed-archives").glob("*.zip"))


def test_v2_parent_retains_retired_page_on_subsequent_update(
    tmp_path, monkeypatch,
):
    _parent, first = _retired_update(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210,
            "Additional Page": 410,
        },
        fail_existing=(110, 210, 310, 410),
    )
    previous_fetch = cli.fetch_site_bytes

    def later_window(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["recentchanges"]:
            # First parent update consumed the Nov 1 event; this window
            # begins Nov 15 and must not return events from before then.
            return b'{"query":{"recentchanges":[]}}'
        return previous_fetch(
            url, language=language, site=site,
            **({"if_none_match": if_none_match} if if_none_match else {}),
        )

    monkeypatch.setattr(cli, "fetch_site_bytes", later_window)
    second = update_snapshot(
        first.archive, first.manifest, first.inventory,
        archive_directory=tmp_path / "v2-second-archive",
        manifest_directory=tmp_path / "v2-second-manifests",
        work_root=tmp_path / "v2-second-work",
        now=NEW_TIME + timedelta(hours=1), allow_historical_only=True,
    )
    record = verify_snapshot(second.archive, second.manifest, second.inventory)
    assert record["formatVersion"] == 2
    assert record["captureMetadata"]["historyRefresh"][
        "historicalOnlyPageCount"
    ] == 1
    with zipfile.ZipFile(second.archive) as z:
        index = json.loads(z.read("_history/index.json"))
        assert index["historicalOnlyPages"][0]["formerUrl"] == DELETED
        assert z.read("_history/oldid/310.html") == (
            b"<html>historical oldid 310</html>"
        )


@pytest.mark.parametrize("mutation", [
    "index-version", "archive-url", "claim-deleted", "claim-current",
    "sha", "status", "inventory-version",
])
def test_tampered_v2_claims_fail_verification(tmp_path, monkeypatch, mutation):
    _parent, child = _retired_update(tmp_path, monkeypatch)
    document = json.loads(child.inventory.read_text(encoding="utf-8"))
    former = next(m for m in document["members"]
                  if m["kind"] == "historical-page")
    if mutation == "index-version":
        # The inventory can never claim v1 for a v2 ZIP.
        document["formatVersion"] = 1
    elif mutation == "archive-url":
        former["formerPage"]["formerUrl"] = "https://false.invalid/"
    elif mutation == "claim-deleted":
        former["formerPage"]["status"] = "confirmed-deleted"
    elif mutation == "claim-current":
        former["kind"] = "current-page"
    elif mutation == "sha":
        former["formerPage"]["originalSha256"] = "0" * 64
    elif mutation == "status":
        document["captureMetadata"]["historyRefresh"]["historyCutoff"] = (
            "all-observed"
        )
    elif mutation == "inventory-version":
        document["formatVersion"] = 99
    child.inventory.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError):
        verify_snapshot(child.archive, child.manifest, child.inventory)


def test_cli_requires_opt_in_and_publishes_v2_when_selected(
    tmp_path, monkeypatch, capsys,
):
    parent = _history_parent(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=True)
    _history_source(
        monkeypatch, ids={
            "Main Page": 110, "New Page": 210,
            "Additional Page": 410,
        }, fail_existing=PARENT_IDS.values(),
    )
    args = [
        "--update-from", str(parent[0]),
        "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--root", str(tmp_path / "cli-output"),
        "--manifest-dir", str(tmp_path / "cli-manifest"),
        "--work-root", str(tmp_path / "cli-work"),
    ]
    assert cli.main(args) == 1
    assert "allow-historical-only" in capsys.readouterr().err
    assert not list((tmp_path / "cli-output").glob("*.zip"))
    assert cli.main(args + ["--allow-historical-only"]) == 0
    result_text = capsys.readouterr()
    assert "Preserved history" in result_text.out
    assert "Old v1 consumers may ignore" in result_text.err
    archive = next((tmp_path / "cli-output").glob("*.zip"))
    manifest = next((tmp_path / "cli-manifest").glob("*.json"))
    inventory = next(
        (tmp_path / "cli-manifest" / "inventories").glob("*.json"),
    )
    assert verify_snapshot(archive, manifest, inventory)["formatVersion"] == 2


def test_reappearance_of_historical_page_requires_explicit_review(
    tmp_path, monkeypatch,
):
    _parent, first = _retired_update(tmp_path, monkeypatch)
    _remote(monkeypatch, changed=False)
    with pytest.raises(ValueError, match="reappeared"):
        update_snapshot(
            first.archive, first.manifest, first.inventory,
            archive_directory=tmp_path / "reappearance-archive",
            manifest_directory=tmp_path / "reappearance-manifests",
            work_root=tmp_path / "reappearance-work",
            now=NEW_TIME + timedelta(hours=1),
            allow_historical_only=True,
        )
    assert not list((tmp_path / "reappearance-archive").glob("*.zip"))


def test_v2_parent_without_opt_in_is_rejected_before_network(
    tmp_path, monkeypatch,
):
    _parent, first = _retired_update(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **k: pytest.fail("No network should be requested"),
    )
    with pytest.raises(ValueError, match="requires --allow-historical-only"):
        update_snapshot(
            first.archive, first.manifest, first.inventory,
            archive_directory=tmp_path / "unopted-archive",
            manifest_directory=tmp_path / "unopted-manifests",
            work_root=tmp_path / "unopted-work",
            now=NEW_TIME + timedelta(hours=1),
        )


def test_no_historical_only_v2_without_required_history_parent(
    tmp_path, monkeypatch,
):
    from test_snapshot_update import _build_parent

    parent = _build_parent(tmp_path)
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **k: pytest.fail("Network must not be called"),
    )
    with pytest.raises(ValueError, match="requires a history-enabled parent"):
        update_snapshot(
            *parent,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "man",
            work_root=tmp_path / "work",
            now=NEW_TIME,
            allow_historical_only=True,
        )
