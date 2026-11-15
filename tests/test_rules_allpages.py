"""P3.3 complete Rules Wiki discovery and verified coverage comparison."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.allpages_coverage import (
    collect_live_main_namespace,
    compare_verified_rules_snapshot,
)
from infinity_wiki_acquisition.crawl_checkpoint import CHECKPOINT_FILENAME
from infinity_wiki_acquisition.fresh_discovery import NAME as RECEIPT_NAME
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot


def _remote(monkeypatch, *, language="en", fail_second=False, no_ids=False):
    site = cli.INFINITY_WIKI_SITE
    root = site.root_urls[language]
    main_title = "Main Page" if language == "en" else "Página principal"
    linked_title = "Linked Page" if language == "en" else "Página enlazada"
    orphan_title = "Orphan Page" if language == "en" else "Página huérfana"
    titles = [(main_title, 1), (linked_title, 2), (orphan_title, 3)]
    links = {
        title: cli.canonical_crawl_url(
            cli.page_url_from_title(title, site=site, language=language),
            site=site,
        )
        for title, _id in titles
    }
    payload = {
        root: (
            '<html><body>'
            f'<a href="{links[linked_title]}">Linked</a>'
            '</body></html>'
        ).encode(),
        links[main_title]: b"<html><body>Main page</body></html>",
        links[linked_title]: b"<html><body>Linked page</body></html>",
        links[orphan_title]: b"<html><body>Orphan page</body></html>",
    }
    calls: list[str] = []
    interrupted = {"pending": fail_second}

    def fetch(url, *, site, language, **kwargs):
        calls.append(url)
        assert site.key == "infinity-wiki"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["allpages"]:
            assert query["apnamespace"] == ["0"]
            assert query["aplimit"] == ["max"]
            if "apcontinue" not in query:
                rows = [{"title": main_title, **({} if no_ids else {"pageid": 1})}]
                return json.dumps({
                    "query": {"allpages": rows},
                    "continue": {"continue": "-||", "apcontinue": "NEXT"},
                }).encode()
            assert query["apcontinue"] == ["NEXT"]
            if interrupted["pending"]:
                interrupted["pending"] = False
                raise OSError("synthetic allpages second batch unavailable")
            return json.dumps({"query": {"allpages": [
                {"title": title, "pageid": ident} for title, ident in titles[1:]
            ]}}).encode()
        if query.get("prop") == ["revisions"] and "pageids" in query:
            pageids = [int(item) for item in query["pageids"][0].split("|")]
            return json.dumps({"query": {"pages": [
                {"pageid": page_id, "title": titles[page_id - 1][0],
                 "revisions": [{"revid": 100 + page_id,
                                "timestamp": "2026-11-01T12:00:00Z"}]}
                for page_id in pageids
            ]}}).encode()
        return payload[url]

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return site, root, titles, links, calls


def _args(tmp_path: Path, *, language="en", full=True):
    args = [
        "--site", "infinity-wiki", "--language", language,
        "--root", str(tmp_path / "archives"),
        "--work-root", str(tmp_path / "work"),
        "--manifest-dir", str(tmp_path / "manifests"),
        "--source-replay-dir", str(tmp_path / "replay"),
    ]
    if full:
        args.append("--allpages")
    return args


def _published(tmp_path):
    archive = next((tmp_path / "archives").glob("*.zip"))
    manifest = next((tmp_path / "manifests").glob("*.json"))
    inventory = next((tmp_path / "manifests" / "inventories").glob("*.json"))
    return archive, manifest, inventory


@pytest.mark.parametrize("language", ["en", "es"])
def test_opt_in_full_discovery_adds_orphan_and_verifies_artifacts(
    tmp_path, monkeypatch, language,
):
    site, root, titles, links, calls = _remote(monkeypatch, language=language)
    monkeypatch.chdir(tmp_path)
    assert cli.main(_args(tmp_path, language=language)) == 0
    archive, manifest, inventory = _published(tmp_path)
    verified = verify_snapshot(archive, manifest, inventory)
    pages = {
        item["page"]["url"]: item
        for item in verified["members"] if item["kind"] == "current-page"
    }
    assert len(pages) == 4
    assert root in pages
    assert links[titles[2][0]] in pages
    assert links[titles[2][0]] in calls
    assert len([url for url in calls if "list=allpages" in url]) == 2
    for title, page_id in titles:
        assert pages[links[title]]["sourceIdentity"]["pageId"] == page_id
    assert verified["captureMetadata"]["mediaWikiPageIdsAvailable"] is True
    from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

    replay = next((tmp_path / "replay").glob("*.zip"))
    assert len(verify_source_replay_bundle(
        replay, archive, manifest, inventory,
    )["pages"]) == 4
    assert not list((tmp_path / "work").glob("*.work"))


def test_full_discovery_checkpoint_resumes_only_remaining_api_batch(
    tmp_path, monkeypatch, capsys,
):
    _, _, _titles, links, calls = _remote(monkeypatch, fail_second=True)
    assert cli.main(_args(tmp_path)) == 1
    work = next((tmp_path / "work").glob("*.work"))
    assert (work / RECEIPT_NAME).exists()
    assert (work / CHECKPOINT_FILENAME).exists()
    assert "incomplete" in capsys.readouterr().err.lower()
    calls.clear()
    assert cli.main([*_args(tmp_path, full=False),
                     "--resume-work", str(work)]) == 1
    assert "Discovery scope differs" in capsys.readouterr().err
    assert calls == []
    # Fix the source, re-run with the required discovery mode.
    _site, _root, _titles, _links, resumed_calls = _remote(monkeypatch)
    assert cli.main([*_args(tmp_path), "--resume-work", str(work)]) == 0
    allpages_requests = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        for url in resumed_calls if "list=allpages" in url
    ]
    assert len(allpages_requests) == 1
    assert allpages_requests[0]["apcontinue"] == ["NEXT"]
    assert verify_snapshot(*_published(tmp_path))["history"] is False


def test_cannot_turn_link_only_checkpoint_into_allpages_crawl(
    tmp_path, monkeypatch, capsys,
):
    site, root, titles, links, calls = _remote(monkeypatch)
    work = tmp_path / "ordinary.work"
    work.mkdir()
    from infinity_wiki_acquisition.crawl_checkpoint import (
        CHECKPOINT_FORMAT, CHECKPOINT_VERSION, write_crawl_checkpoint,
    )

    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "phase": "crawling", "complete": False,
        "site": site.key, "language": "en", "rootUrl": root,
        "enumerationContinuation": None,
        "queue": [[root, False]],
        "discovered": {root: False}, "pageIds": {}, "required": [root],
        "saved": {}, "pages": [], "ignored": {}, "failed": {},
    })
    assert cli.main([*_args(tmp_path), "--resume-work", str(work)]) == 1
    assert "Cannot enable --allpages" in capsys.readouterr().err
    assert calls == []


def test_allpages_batch_rejects_non_main_namespace(monkeypatch):
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **k: json.dumps({
            "query": {"allpages": [
                {"title": "Out of namespace", "ns": 10, "pageid": 7}
            ]},
        }).encode(),
    )
    with pytest.raises(ValueError, match="non-main namespace"):
        cli.fetch_all_page_title_batch(
            language="en", site=cli.INFINITY_WIKI_SITE,
        )


def test_complete_api_collection_rejects_repeated_continuation(monkeypatch):
    def batch(*, language, continuation, site):
        return (
            (cli.WikiEnumeratedTitle("Main Page", 1),),
            "again" if continuation is None else "again",
        )
    monkeypatch.setattr(cli, "fetch_all_page_title_batch", batch)
    with pytest.raises(ValueError, match="continuation token repeated"):
        collect_live_main_namespace(language="en")


def test_full_discovery_rejects_api_without_page_ids(tmp_path, monkeypatch, capsys):
    _remote(monkeypatch, no_ids=True)
    assert cli.main(_args(tmp_path)) == 1
    assert "positive numeric page ID" in capsys.readouterr().err
    assert not list((tmp_path / "archives").glob("*.zip"))


@pytest.mark.parametrize("mode", ["--update-from", "--from-snapshot"])
def test_allpages_rejects_parent_derived_modes_before_io(tmp_path, capsys, mode):
    assert cli.main(["--allpages", mode, str(tmp_path / "missing.zip")]) == 1
    assert "--allpages is only for fresh acquisitions" in capsys.readouterr().err


def test_history_upgrade_from_allpages_parent_includes_orphan_without_current_refetch(
    tmp_path, monkeypatch,
):
    from datetime import datetime, timezone
    from infinity_wiki_acquisition.snapshot_upgrade import upgrade_snapshot_with_history
    from infinity_wiki_acquisition.snapshot_provenance import sha256_file

    _site, _root, titles, links, fresh_calls = _remote(monkeypatch)
    assert cli.main(_args(tmp_path)) == 0
    original = _published(tmp_path)
    original_hashes = [sha256_file(item) for item in original]
    observed = []
    ids = {title: 1000 + numeric for title, numeric in titles}

    def history_fetch(url, *, language, site, **kwargs):
        observed.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            title = query["titles"][0].replace("_", " ")
            return json.dumps({"query": {"pages": [{
                "title": title, "pageid": ids[title] - 1000,
                "revisions": [{
                    "revid": ids[title], "parentid": 0,
                    "timestamp": "2026-10-01T00:00:00Z",
                }],
            }]}}).encode()
        if "oldid" in query:
            return f"<html>oldid {query['oldid'][0]}</html>".encode()
        raise AssertionError(f"History upgrade must not refetch current HTML: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", history_fetch)
    upgraded = upgrade_snapshot_with_history(
        *original, archive_directory=tmp_path / "history-archives",
        manifest_directory=tmp_path / "history-manifests",
        work_root=tmp_path / "history-work",
        now=datetime.now(timezone.utc),
    )
    verified = verify_snapshot(upgraded.archive, upgraded.manifest, upgraded.inventory)
    assert verified["history"] is True
    assert verified["parent"]["archiveSha256"] == original_hashes[0]
    assert len([
        row for row in verified["members"] if row["kind"] == "current-page"
    ]) == 4
    assert links[titles[2][0]] in {
        row["page"]["url"]
        for row in verified["members"] if row["kind"] == "current-page"
    }
    assert len(observed) >= 3
    assert all("prop=revisions" in url or "oldid=" in url for url in observed)
    assert [sha256_file(item) for item in original] == original_hashes


def test_offline_snapshot_coverage_comparison_marks_missing_api_pages(
    tmp_path, monkeypatch,
):
    _, _root, titles, links, calls = _remote(monkeypatch)
    assert cli.main(_args(tmp_path)) == 0
    archive, manifest, inventory = _published(tmp_path)
    collected = [
        cli.WikiEnumeratedTitle(title, page_id) for title, page_id in titles
    ]
    collected.append(cli.WikiEnumeratedTitle("New Orphan", 4))
    compared = compare_verified_rules_snapshot(
        archive, manifest, inventory, enumerated=collected,
    )
    assert compared["archiveCurrentPageCount"] == 4
    assert compared["apiMainNamespacePageCount"] == 4
    assert compared["apiPagesFoundInSnapshot"] == 3
    assert compared["apiPagesAbsentFromSnapshot"] == 1
    assert compared["apiMissingPages"][0]["title"] == "New Orphan"
    assert compared["archivePagesNotInApiMainNamespaceCount"] == 1
    assert compared["apiPageIdMismatchCount"] == 0
    assert compared["coverageStatus"] == "missing-or-conflicting-api-pages"
    assert compared["reuseAuthorized"] is False
    with pytest.raises(ValueError, match="Conflicting allpages page ID"):
        compare_verified_rules_snapshot(
            archive, manifest, inventory,
            enumerated=[
                cli.WikiEnumeratedTitle("One", 21),
                cli.WikiEnumeratedTitle("Another", 21),
            ],
        )


def test_read_only_audit_cli_requires_explicit_live_api_opt_in(
    tmp_path, monkeypatch, capsys,
):
    from tools.audit_rules_allpages import main
    _, _root, titles, links, calls = _remote(monkeypatch)
    assert cli.main(_args(tmp_path)) == 0
    paths = [str(file) for file in _published(tmp_path)]
    calls.clear()
    with pytest.raises(SystemExit):
        main(paths)
    assert calls == []
    monkeypatch.setattr(
        "tools.audit_rules_allpages.collect_live_main_namespace",
        lambda *, language: [
            cli.WikiEnumeratedTitle(t, ident) for t, ident in titles
        ],
    )
    assert main([*paths, "--live"]) == 0
    assert "3 main-namespace pages" in capsys.readouterr().out
    assert calls == []
