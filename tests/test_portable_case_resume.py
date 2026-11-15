"""Offline recovery from Infinity Wiki's old case-insensitive path collision."""

from __future__ import annotations

from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
    read_crawl_checkpoint, write_crawl_checkpoint,
)
from infinity_wiki_acquisition.page_evidence import write_page_evidence
from infinity_wiki_acquisition.rendered_response import capture_rendered_response
from infinity_wiki_acquisition.repair_crawl_paths import repair_crawl_paths
from infinity_wiki_acquisition.source_replay import (
    source_body_path, write_source_body,
)


SITE = cli.INFINITY_WIKI_SITE
ROOT = SITE.root_urls["en"]
FIRST = "https://infinitythewiki.com/No_LoF"
SECOND = "https://infinitythewiki.com/No_Lof"


def _preserved(work: Path, *, corrupt_raw: bool = False) -> None:
    """Model the old checkpoint: No_LoF was saved before No_Lof collided."""
    work.mkdir()
    original_root = (
        b'<html><a href="/No_LoF">old</a>'
        b'<a href="/No_Lof">new</a></html>'
    )
    source = {
        ROOT: original_root,
        FIRST: b"<html><body>Line of fire</body></html>",
    }
    old = {
        ROOT: b'<html><a href="No_LoF">old</a><a href="No_Lof">new</a></html>',
        FIRST: source[FIRST],
    }
    oldpaths = {ROOT: "index.html", FIRST: "No_LoF"}
    for url, raw in source.items():
        path = oldpaths[url]
        (work / path).write_bytes(old[url])
        parser = cli.LinkExtractor()
        parser.feed(raw.decode())
        evidence = cli.build_page_evidence(
            url=url, path=path, payload=raw, parser=parser, site=SITE,
        )
        evidence["renderedResponseVersion"] = capture_rendered_response(
            url=url, path=path, payload=raw, rewritten=old[url],
            fetched_at=evidence["fetchedAt"], site=SITE, language="en",
        )
        write_page_evidence(work, url, evidence)
        write_source_body(work, url, raw)
    if corrupt_raw:
        source_body_path(work, FIRST).write_bytes(b"faked source body")
    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "phase": "crawling", "complete": False, "site": SITE.key,
        "language": "en", "rootUrl": ROOT,
        "enumerationContinuation": None,
        "queue": [[SECOND, False]],
        "discovered": {ROOT: False, FIRST: False, SECOND: False},
        "pageIds": {}, "required": [ROOT],
        "saved": {url: [oldpaths[url], len(old[url])] for url in old},
        "pages": [
            {"url": ROOT, "path": "index.html", "title": "Main Page"},
            {"url": FIRST, "path": "No_LoF", "title": "No_LoF"},
        ],
        "ignored": {}, "failed": {},
    })


def test_infinity_wiki_case_variants_have_distinct_portable_paths():
    canonical = cli.local_relative_path(SECOND, site=SITE)
    alternate = cli.local_relative_path(FIRST, site=SITE)
    assert canonical == "No_Lof"
    assert alternate.startswith("No_LoF__u_")
    assert len({canonical.casefold(), alternate.casefold()}) == 2
    assert cli.local_relative_path(ROOT, site=SITE) == "index.html"
    assert alternate == cli.local_relative_path(FIRST, site=SITE, os_name="Linux")
    # URL variants are preserved independently in link rewrites too.
    html = cli.rewrite_html_links(
        '<a href="/No_LoF">old</a><a href="/No_Lof">new</a>',
        ROOT, site=SITE, language="en",
    )
    assert f'href="{alternate}"' in html
    assert f'href="{canonical}"' in html


def test_fresh_case_collision_crawl_rewrites_links_and_saves_both(
    tmp_path, monkeypatch,
):
    raw = {
        ROOT: b'<html><a href="/No_LoF">first</a><a href="/No_Lof">second</a></html>',
        FIRST: b"<html><body>first</body></html>",
        SECOND: b"<html><body>second</body></html>",
    }
    calls = []
    def fetch(url, *, language="en"):
        calls.append(url)
        return raw[url]
    monkeypatch.setattr(cli, "fetch_bytes", fetch)
    downloaded = cli.download_wiki(ROOT, tmp_path, site=SITE)
    assert downloaded.failures == ()
    assert calls == [ROOT, FIRST, SECOND]
    paths = {page.url: page.path for page in downloaded.pages}
    assert paths[FIRST] != paths[SECOND]
    assert (tmp_path / paths[FIRST]).read_bytes() == raw[FIRST]
    assert (tmp_path / paths[SECOND]).read_bytes() == raw[SECOND]


def test_repair_preserves_original_and_resume_downloads_only_pending(
    tmp_path, monkeypatch,
):
    original = tmp_path / "before.work"
    migrated = tmp_path / "after.work"
    _preserved(original)
    prior = (original / "index.html").read_bytes()
    report = repair_crawl_paths(original, migrated)
    assert report == {
        "savedFiles": 2, "rewrittenPages": 2,
        "renamedPaths": 1, "pendingUrls": 1,
    }
    assert (original / "index.html").read_bytes() == prior
    assert (original / "No_LoF").is_file()
    assert not (migrated / "No_LoF").exists()
    assert (migrated / cli.local_relative_path(FIRST, site=SITE)).is_file()
    assert source_body_path(migrated, FIRST).is_file()
    state = read_crawl_checkpoint(
        migrated / CHECKPOINT_FILENAME, work=migrated, site=SITE.key,
        language="en", root_url=ROOT, require_complete=False,
    )
    assert state["saved"][FIRST][0] == cli.local_relative_path(FIRST, site=SITE)
    links = (migrated / "index.html").read_text()
    assert f'href="{cli.local_relative_path(FIRST, site=SITE)}"' in links
    assert f'href="{cli.local_relative_path(SECOND, site=SITE)}"' in links
    calls = []
    def fetch(url, *, language="en"):
        calls.append(url)
        assert url == SECOND
        return b"<html><body>second page</body></html>"
    monkeypatch.setattr(cli, "fetch_bytes", fetch)
    downloaded = cli.download_wiki(
        ROOT, migrated, site=SITE,
        checkpoint_file=migrated / CHECKPOINT_FILENAME,
        retain_source_html=True,
    )
    assert downloaded.failures == ()
    assert calls == [SECOND]
    assert len(downloaded.pages) == 3
    assert source_body_path(migrated, SECOND).is_file()


def test_repaired_crawl_publishes_verified_snapshot_and_source_replay(
    tmp_path, monkeypatch,
):
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
    from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

    original, migrated = tmp_path / "old.work", tmp_path / "repaired.work"
    _preserved(original)
    repair_crawl_paths(original, migrated)
    calls = []

    def fake_fetch(url, *, language="en"):
        calls.append(url)
        assert url == SECOND
        return b"<html><body>Second page</body></html>"

    monkeypatch.setattr(cli, "fetch_bytes", fake_fetch)
    monkeypatch.chdir(tmp_path)
    assert cli.main([
        "--site", "infinity-wiki", "--language", "en",
        "--resume-work", str(migrated),
        "--source-replay-dir", str(tmp_path / "raw-replays"),
        "--root", str(tmp_path / "snapshots"),
        "--manifest-dir", str(tmp_path / "manifests"),
    ]) == 0
    assert calls == [SECOND]
    archive = next((tmp_path / "snapshots").glob("*.zip"))
    manifest = next((tmp_path / "manifests").glob("*.json"))
    inventory = next((tmp_path / "manifests" / "inventories").glob("*.json"))
    bundle = next((tmp_path / "raw-replays").glob("*.zip"))
    checked = verify_snapshot(archive, manifest, inventory)
    verified_source = verify_source_replay_bundle(
        bundle, archive, manifest, inventory,
    )
    assert len(verified_source["pages"]) == 3
    assert len([
        record for record in checked["members"] if record["kind"] == "current-page"
    ]) == 3
    assert (original / "No_LoF").is_file()
    assert not migrated.exists()


def test_repair_rejects_altered_source_body_and_never_touches_original(
    tmp_path,
):
    original = tmp_path / "before.work"
    output = tmp_path / "after.work"
    _preserved(original, corrupt_raw=True)
    with pytest.raises(ValueError, match="inconsistent"):
        repair_crawl_paths(original, output)
    assert not output.exists()
    assert (original / "No_LoF").is_file()


def test_repair_never_overwrites_preexisting_output(tmp_path):
    original = tmp_path / "before.work"
    output = tmp_path / "after.work"
    _preserved(original)
    output.mkdir()
    (output / "untouched").write_text("safe")
    with pytest.raises(ValueError, match="already exists"):
        repair_crawl_paths(original, output)
    assert (output / "untouched").read_text() == "safe"
