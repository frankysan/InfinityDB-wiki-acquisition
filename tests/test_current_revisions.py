"""P2.3b.3: batched current revision identities are evidence, not HTML reuse."""

from __future__ import annotations

import json
import urllib.parse
from datetime import timedelta
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.current_revisions import (
    BATCH_SIZE, FILENAME, load_current_revision_evidence,
    observe_current_revisions,
)
from infinity_wiki_acquisition.snapshot_update import update_snapshot
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from test_snapshot_update import (
    LANG, NEW_TIME, ROOT, SITE, _build_parent, _remote, _update,
)


def _revision_api(monkeypatch, *, delta: int = 0, bad_title=False,
                  missing_revision=False, fail_api=False):
    base = cli.fetch_site_bytes
    queries: list[list[int]] = []

    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"] and "pageids" in query:
            requested = [int(value) for value in query["pageids"][0].split("|")]
            queries.append(requested)
            if fail_api:
                raise OSError("synthetic revision metadata outage")
            items = []
            titles = {1: "Main Page", 2: "Old Page", 3: "Deleted Page",
                      4: "Additional Page"}
            for page_id in requested:
                item = {
                    "pageid": page_id,
                    "title": ("Unrelated Page" if bad_title else titles.get(page_id, f"Page {page_id}")),
                }
                if not missing_revision:
                    item["revisions"] = [{
                        "revid": page_id * 100 + delta,
                        "timestamp": "2026-10-01T00:00:00Z",
                    }]
                items.append(item)
            return json.dumps({"query": {"pages": items}}).encode()
        return base(url, language=language, site=site)

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return queries


def test_batches_current_revision_queries_and_writes_private_work(tmp_path, monkeypatch):
    pages = [
        cli.WikiPage(
            cli.page_url_from_title(f"Page {i}", language=LANG, site=SITE),
            f"Page_{i}.html", f"Page {i}",
        )
        for i in range(1, BATCH_SIZE * 2 + 6)
    ]
    ids = {page.url: i for i, page in enumerate(pages, start=1)}
    calls = []

    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        requested = [int(n) for n in query["pageids"][0].split("|")]
        calls.append(requested)
        assert query["rvlimit"] == ["1"]
        assert query["rvdir"] == ["older"]
        return json.dumps({"query": {"pages": [{
            "pageid": i, "title": f"Page {i}",
            "revisions": [{"revid": i * 100, "timestamp": "2026-01-01T00:00:00Z"}],
        } for i in requested]}}).encode()

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    document = observe_current_revisions(
        pages, ids, tmp_path, site=SITE, language=LANG,
    )
    assert [len(item) for item in calls] == [40, 40, 5]
    assert document["records"][pages[0].url]["revisionId"] == 100
    assert all(row["renderCorrespondence"] == "not-verified"
               for row in document["records"].values())
    assert (tmp_path / FILENAME).exists()
    assert load_current_revision_evidence(
        tmp_path, site=SITE.key, language=LANG,
    ) == document["records"]


@pytest.mark.parametrize("condition,expected", [
    ("no-page-id", "missing-page-id"),
    ("mismatched-title", "identity-unresolved"),
    ("unavailable-revision", "revision-unavailable"),
    ("api-error", "api-unavailable"),
])
def test_unknown_evidence_never_guesses_a_revision(
    tmp_path, monkeypatch, condition, expected,
):
    page = cli.WikiPage(ROOT, "Main_Page.html", "Main Page")
    ids = {} if condition == "no-page-id" else {ROOT: 1}
    _revision_api(
        monkeypatch, bad_title=condition == "mismatched-title",
        missing_revision=condition == "unavailable-revision",
        fail_api=condition == "api-error",
    )
    document = observe_current_revisions(
        [page], ids, tmp_path, site=SITE, language=LANG,
    )
    record = document["records"][ROOT]
    assert record["status"] == expected
    assert record["revisionId"] is None
    assert record["renderCorrespondence"] == "not-verified"


def test_two_updates_compare_revisions_but_refetch_html(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    first_calls = _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch)
    first = _update(parent, tmp_path)
    verified = verify_snapshot(first.archive, first.manifest, first.inventory)
    observed = [
        m["currentRevision"] for m in verified["members"]
        if m["kind"] == "current-page"
    ]
    assert len(observed) == 3
    assert all(r["status"] == "observed" for r in observed)
    assert verified["captureMetadata"]["currentRevisionEvidence"] == {
        "pageCount": 3, "observedCount": 3,
        "renderCorrespondence": "not-verified",
    }
    comparisons = verified["captureMetadata"]["currentRevisionComparisons"]
    assert all(r["comparison"] == "parent-revision-unknown" for r in comparisons)
    assert all(r["htmlRefetched"] is True for r in comparisons)

    second_calls = _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch)
    second = update_snapshot(
        first.archive, first.manifest, first.inventory,
        archive_directory=tmp_path / "second-archives",
        manifest_directory=tmp_path / "second-manifests",
        work_root=tmp_path / "second-work",
        now=NEW_TIME + timedelta(hours=2),
    )
    updated = verify_snapshot(second.archive, second.manifest, second.inventory)
    comparisons = updated["captureMetadata"]["currentRevisionComparisons"]
    assert len(comparisons) == 3
    assert {c["comparison"] for c in comparisons} == {"same-source-revision"}
    assert all(c["htmlRefetched"] is True for c in comparisons)
    assert ROOT in second_calls  # HTML was downloaded again


def test_changed_revision_is_reported_but_page_still_downloaded(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch)
    first = _update(parent, tmp_path)

    calls = _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch, delta=1)
    second = update_snapshot(
        first.archive, first.manifest, first.inventory,
        archive_directory=tmp_path / "other-out",
        manifest_directory=tmp_path / "other-manifests",
        work_root=tmp_path / "other-work",
        now=NEW_TIME + timedelta(hours=2),
    )
    checked = verify_snapshot(second.archive, second.manifest, second.inventory)
    assert {c["comparison"] for c in checked["captureMetadata"][
        "currentRevisionComparisons"
    ]} == {"changed-source-revision"}
    assert ROOT in calls


@pytest.mark.parametrize("edit", ["page-id", "revision-id", "title", "status",
                                   "render-claim", "coverage"])
def test_offline_verifier_rejects_forged_revision_evidence(
    tmp_path, monkeypatch, edit,
):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch)
    result = _update(parent, tmp_path)
    inventory = json.loads(result.inventory.read_text(encoding="utf-8"))
    page = next(m for m in inventory["members"] if m["kind"] == "current-page"
                and m["sourceIdentity"]["pageId"] == 1)
    record = page["currentRevision"]
    if edit == "page-id":
        record["pageId"] = 999
    elif edit == "revision-id":
        record["revisionId"] = -1
    elif edit == "title":
        record["apiTitle"] = "Not Main Page"
    elif edit == "status":
        record["status"] = "observed-and-fresh"
    elif edit == "render-claim":
        record["renderCorrespondence"] = "verified"
    else:
        inventory["captureMetadata"]["currentRevisionEvidence"]["observedCount"] = 0
    result.inventory.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError):
        verify_snapshot(result.archive, result.manifest, result.inventory)


def test_normal_snapshot_captures_revision_evidence_without_publishing_work(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    from infinity_wiki_acquisition import cli

    api_requests = []
    def fetch(url, *, language, site, if_none_match=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["allpages"]:
            return b'{"query":{"allpages":[{"title":"Main Page","pageid":42}]}}'
        if query.get("prop") == ["revisions"]:
            api_requests.append(query)
            assert query["pageids"] == ["42"]
            return json.dumps({
                "query": {"pages": [{
                    "pageid": 42, "title": "Main Page",
                    "revisions": [{"revid": 250, "timestamp": "2026-10-01T00:00:00Z"}],
                }]},
            }).encode()
        if url == SITE.root_urls[LANG]:
            return b"<html><body>Current Main Page</body></html>"
        raise AssertionError(f"Unexpected request: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    assert cli.main([
        "--site", "human-sphere", "--root", "archives",
        "--work-root", "work", "--manifest-dir", "manifests",
    ]) == 0
    assert len(api_requests) == 1
    archive = next((tmp_path / "archives").glob("*.zip"))
    manifest = next((tmp_path / "manifests").glob("*.json"))
    inventory = next((tmp_path / "manifests" / "inventories").glob("*.json"))
    verified = verify_snapshot(archive, manifest, inventory)
    page = next(m for m in verified["members"] if m["kind"] == "current-page")
    assert page["currentRevision"]["revisionId"] == 250
    assert page["currentRevision"]["pageId"] == 42
    assert page["currentRevision"]["htmlFetchedAt"] is not None
    assert page["currentRevision"]["renderCorrespondence"] == "not-verified"
    import zipfile
    with zipfile.ZipFile(archive) as source:
        assert not any("current-revisions" in name for name in source.namelist())


def test_corrupt_private_current_revision_evidence_fails_closed(
    tmp_path, monkeypatch,
):
    page = cli.WikiPage(ROOT, "Main_Page.html", "Main Page")
    _revision_api(monkeypatch)
    observe_current_revisions([page], {ROOT: 1}, tmp_path, site=SITE, language=LANG)
    path = tmp_path / FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    document["records"][ROOT]["revisionId"] = "some-revision"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete observed"):
        load_current_revision_evidence(tmp_path, site=SITE.key, language=LANG)
