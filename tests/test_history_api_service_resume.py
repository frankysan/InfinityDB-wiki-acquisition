"""History-only API service exclusion and preserved-checkpoint resume tests."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import datetime, timezone

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
    write_crawl_checkpoint,
)
from infinity_wiki_acquisition.history_checkpoint import (
    HISTORY_CHECKPOINT_FILENAME, read_history_checkpoint,
)
from infinity_wiki_acquisition.history_service_exclusion import (
    NO_HISTORY_API_ENDPOINT, approved_parent_service_member,
)
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file, write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_upgrade import (
    SnapshotUpgradeIncomplete, upgrade_snapshot_with_history,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)

SITE = cli.INFINITY_WIKI_SITE
ROOT = SITE.root_urls["en"]
API = SITE.api_urls["en"]
PAGE = ROOT + "Foo"
PAGES = (
    cli.WikiPage(PAGE, "Foo.html", "Foo"),
    cli.WikiPage(API, "api.php.html", "api.php"),
    cli.WikiPage(ROOT, "index.html", "Main Page"),
)
OLDIDS = {"Foo": 701, "Main Page": 702}


def _parent(tmp_path):
    work = tmp_path / "source-work"
    work.mkdir()
    content = {
        PAGE: b"<html><body>Foo old capture</body></html>",
        API: b"<html><body>MediaWiki API documentation</body></html>",
        ROOT: b'<html><body><a href="api.php">API</a></body></html>',
    }
    for page in PAGES:
        (work / page.path).write_bytes(content[page.url])
    state = {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "site": SITE.key, "language": "en", "rootUrl": ROOT,
        "phase": "complete", "complete": True,
        "enumerationContinuation": None,
        "queue": [], "discovered": {url: False for url in content},
        "required": list(content), "pageIds": {PAGE: 9, ROOT: 10},
        "saved": {
            page.url: [page.path, len(content[page.url])] for page in PAGES
        },
        "pages": [
            {"url": page.url, "path": page.path, "title": page.title}
            for page in PAGES
        ],
        "ignored": {}, "failed": {},
    }
    checkpoint = work / CHECKPOINT_FILENAME
    write_crawl_checkpoint(checkpoint, state)
    parent_time = datetime(2025, 11, 16, tzinfo=timezone.utc)
    archive = cli.archive_wiki(
        [work / page.path for page in PAGES],
        tmp_path / "parent-archives", root=work, site=SITE, now=parent_time,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "parent-manifests", snapshot_type="wiki",
        acquired_at=parent_time, source_url=ROOT,
        document_count=len(PAGES), project_root=tmp_path, language="en",
    )
    inventory_path = (
        tmp_path / "parent-manifests" / "inventories"
        / (archive.stem + ".inventory.json")
    )
    inventory_path.parent.mkdir()
    inventory_path.write_text(json.dumps(build_snapshot_inventory(
        archive, manifest, work=work, site=SITE.key, language="en",
        root_url=ROOT, include_history=False, pages=PAGES,
        crawl_checkpoint=checkpoint,
    )), encoding="utf-8")
    doc = verify_snapshot(archive, manifest, inventory_path)
    api_member = next(
        row for row in doc["members"]
        if row["kind"] == "current-page" and row["page"]["url"] == API
    )
    assert approved_parent_service_member(
        api_member, site=SITE, language="en",
    )
    return archive, manifest, inventory_path


def _source_fetch(monkeypatch, *, fail_api_once=True, api_missing=True):
    calls = []
    missing_network = {"value": fail_api_once}

    def fetch(url, *, site, language, **kwargs):
        calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            title = query["titles"][0]
            if title == "api.php":
                if missing_network["value"]:
                    missing_network["value"] = False
                    raise OSError("temporary API failure")
                if api_missing:
                    return json.dumps({
                        "query": {"pages": [{"ns": 0, "title": "api.php", "missing": True}]},
                    }).encode()
                return json.dumps({
                    "query": {"pages": [{
                        "title": "api.php", "pageid": 99,
                        "revisions": [{
                            "revid": 799, "parentid": 0,
                            "timestamp": "2025-01-01T12:00:00Z",
                        }],
                    }]},
                }).encode()
            oldid = OLDIDS[title]
            return json.dumps({
                "query": {"pages": [{
                    "title": title, "pageid": oldid,
                    "revisions": [{
                        "revid": oldid, "parentid": 0,
                        "timestamp": "2025-01-01T12:00:00Z",
                    }],
                }]},
            }).encode()
        if "oldid" in query:
            return f"<html>oldid {query['oldid'][0]}</html>".encode()
        raise AssertionError(f"Unexpected network access: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return calls


def _upgrade(triple, tmp_path, *, resume=None):
    return upgrade_snapshot_with_history(
        *triple,
        archive_directory=tmp_path / "child-archives",
        manifest_directory=tmp_path / "child-manifests",
        work_root=tmp_path / "child-work",
        resume_work=resume,
        now=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
    )


def test_history_upgrade_resumes_api_pseudopage_after_verified_missing_response(
    tmp_path, monkeypatch,
):
    parent = _parent(tmp_path)
    original = tuple(sha256_file(path) for path in parent)
    calls = _source_fetch(monkeypatch)
    with pytest.raises(SnapshotUpgradeIncomplete) as raised:
        _upgrade(parent, tmp_path)
    work = raised.value.work
    checkpoint = work / HISTORY_CHECKPOINT_FILENAME
    state = read_history_checkpoint(
        checkpoint, work=work, site=SITE.key, language="en",
        pages=[[p.url, p.path, p.title] for p in sorted(PAGES, key=lambda p: p.path)],
        history_exempt_urls=frozenset({API}),
    )
    assert state["pageIndex"] == 1
    assert len(state["saved"]) == 1
    assert "temporary API failure" in raised.value.failures[0].error
    calls.clear()
    result = _upgrade(parent, tmp_path, resume=work)
    assert not work.exists()
    assert tuple(sha256_file(path) for path in parent) == original
    assert sum("oldid=701" in url for url in calls) == 0
    assert sum("oldid=702" in url for url in calls) == 1
    assert sum("prop=revisions" in url and "titles=Foo" in url for url in calls) == 0
    assert sum("prop=revisions" in url and "titles=api.php" in url for url in calls) == 1

    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert verified["history"] is True
    assert verified["parent"]["archiveSha256"] == original[0]
    assert len([m for m in verified["members"] if m["kind"] == "revision"]) == 2
    assert len([m for m in verified["members"] if m["kind"] == "current-page"]) == 3
    with zipfile.ZipFile(result.archive) as bundle:
        indexed = json.loads(bundle.read("_history/index.json"))
        assert indexed["revisionCount"] == 2
        assert len(indexed["pages"]) == 3
        service = next(row for row in indexed["pages"] if row["currentUrl"] == API)
        assert service["revisions"] == []
        assert service["title"] == "api.php"
        assert service["historyExclusion"]["reason"] == NO_HISTORY_API_ENDPOINT
        assert len(service["historyExclusion"]["responseSha256"]) == 64
        assert bundle.read("api.php.html") == (
            b"<html><body>MediaWiki API documentation</body></html>"
        )


def test_history_exclusion_requires_exact_service_identity_and_response_evidence(
    tmp_path,
):
    from infinity_wiki_acquisition.history_service_exclusion import (
        validate_history_exclusion,
    )

    verified = verify_snapshot(*_parent(tmp_path))
    member = next(
        item for item in verified["members"]
        if item["kind"] == "current-page" and item["page"]["url"] == API
    )
    page = {
        "currentUrl": API, "currentPath": "api.php.html",
        "title": "api.php", "revisions": [],
        "historyExclusion": {
            "reason": NO_HISTORY_API_ENDPOINT,
            "queryUrl": cli.revision_query_url(
                "api.php", site=SITE, language="en",
            ),
            "responseSha256": "a" * 64,
        },
    }
    validate_history_exclusion(
        page, member=member, site=SITE, language="en",
    )
    for change in (
        {"title": "Other"},
        {"currentUrl": ROOT + "Other"},
        {"revisions": [{"oldid": 55}]},
        {"historyExclusion": {**page["historyExclusion"],
                              "responseSha256": "wrong"}},
        {"historyExclusion": {**page["historyExclusion"],
                              "reason": "some-other-reason"}},
    ):
        altered = {**page, **change}
        with pytest.raises(ValueError, match="not justified"):
            validate_history_exclusion(
                altered, member=member, site=SITE, language="en",
            )


def test_unrelated_missing_wiki_page_stays_fatal(tmp_path, monkeypatch):
    parent = _parent(tmp_path)
    def fetch(url, *, site, language, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"] and query["titles"] == ["Foo"]:
            return json.dumps({"query": {"pages": [{"title": "Foo", "missing": True}]}}).encode()
        raise AssertionError(f"Unexpected fetch {url}")
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    with pytest.raises(SnapshotUpgradeIncomplete) as raised:
        _upgrade(parent, tmp_path)
    assert len(raised.value.failures) == 1
    assert "page not found" in raised.value.failures[0].error
    assert len(list((tmp_path / "child-archives").glob("*.zip"))) == 0


def test_nonmissing_api_errors_are_not_exempted(tmp_path, monkeypatch):
    parent = _parent(tmp_path)
    calls = _source_fetch(monkeypatch, fail_api_once=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as raised:
        _upgrade(parent, tmp_path)
    assert "temporary API failure" in raised.value.failures[0].error


def test_new_crawls_ignore_actual_api_endpoint_not_other_php_pages():
    assert cli.ignored_url_reason(API, site=SITE) == (
        "MediaWiki API service endpoint, not a wiki page"
    )
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/Foo.php", site=SITE,
    ) is None
    assert cli.ignored_url_reason(
        "https://www.human-sphere.com/api.php",
        site=cli.HUMAN_SPHERE_SITE,
    ) is None
    assert cli.ignored_url_reason(
        SITE.api_urls["es"], site=SITE,
    ) == "MediaWiki API service endpoint, not a wiki page"
