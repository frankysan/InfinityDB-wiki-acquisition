"""P3.4 portable Rules Wiki path layout and legacy-parent migration acceptance."""

from __future__ import annotations

import json
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION, write_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_archive import validate_archive_member_paths
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file, write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_update import update_snapshot
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

SITE = cli.INFINITY_WIKI_SITE
ROOT = SITE.root_urls["en"]
FOO = ROOT + "Foo"
SUB = ROOT + "Foo/Bar"
PNG = b"\x89PNG\r\n\x1a\n" + b"identical tests assets"


def test_rules_page_directory_and_reserved_index_paths_portably_distinct():
    for language in SITE.languages:
        prefix = "" if language == "en" else "es/"
        base = SITE.root_urls[language]
        names = [
            cli.local_relative_path(base, site=SITE),
            cli.local_relative_path(base + "Foo", site=SITE),
            cli.local_relative_path(base + "Foo/Bar", site=SITE),
            cli.local_relative_path(base + "Foo/", site=SITE),
            cli.local_relative_path(base + "Foo/Index", site=SITE),
            cli.local_relative_path(base + "Foo/Bar.html", site=SITE),
        ]
        assert names[0] == prefix + "index.html"
        assert names[1] == prefix + "Foo.html"
        assert names[2] == prefix + "Foo/Bar.html"
        assert names[3] == prefix + "Foo/index.html"
        assert names[4].startswith(prefix + "Foo/Index__u_")
        assert names[5] == prefix + "Foo/Bar.html.html"
        assert len({name.casefold() for name in names}) == len(names)
        validate_archive_member_paths(names)
        assert [cli.local_relative_path(base + suffix, site=SITE, os_name="Linux")
                for suffix in ("Foo", "Foo/Bar")] == names[1:3]
    assert cli.local_relative_path(ROOT + "Index", site=SITE).startswith("Index__u_")
    assert cli.local_relative_path(ROOT + "wiki/Foo", site=SITE) == "wiki/Foo.html"
    assert cli.local_relative_path(ROOT + "wiki/", site=SITE) == "wiki/index.html"
    assert cli.local_relative_path(ROOT + "No_Lof", site=SITE) == "No_Lof.html"
    assert cli.local_relative_path(ROOT + "No_LoF", site=SITE).startswith("No_LoF__u_")


def test_rules_punctuation_encoded_slash_and_asset_case_are_distinct():
    page_urls = [
        ROOT + "A:B", ROOT + "A_B", ROOT + "A%2FB", ROOT + "A/B",
        ROOT + "Index", ROOT + "index", ROOT + "FoO",
        ROOT + "Foo", ROOT + "foo",
    ]
    paths = [cli.local_relative_path(url, site=SITE) for url in page_urls]
    assert paths[0].startswith("A_B__u_")
    assert paths[1] == "A_B.html"
    assert paths[2].startswith("A_B__u_")
    assert paths[3] == "A/B.html"
    assert len({path.casefold() for path in paths}) == len(paths)
    validate_archive_member_paths(paths + ["index.html"])
    for url in page_urls:
        assert cli.local_relative_path(url, site=SITE) == cli.local_relative_path(
            url, site=SITE, os_name="Linux",
        )

    assets = [
        ROOT + "images/Icon.png", ROOT + "images/icon.png",
        ROOT + "Images/icon.png", ROOT + "images/A:B.png",
        ROOT + "images/A_B.png", ROOT + "images/A%2FB.png",
        ROOT + "images/A/B.png",
    ]
    names = [
        cli.local_relative_path(url, site=SITE, asset=True) for url in assets
    ]
    assert names[1] == "images/icon.png"
    assert names[4].startswith("images/A_B__u_")
    assert names[4].endswith(".png")
    assert len({name.casefold() for name in names}) == len(names)
    validate_archive_member_paths(names)
    for url in assets:
        assert cli.local_relative_path(url, site=SITE, asset=True) == (
            cli.local_relative_path(url, site=SITE, asset=True, os_name="Linux")
        )


def _legacy_parent(tmp_path):
    work = tmp_path / "old-work"
    work.mkdir()
    pages = [
        cli.WikiPage(ROOT, "index.html", "Main Page"),
        cli.WikiPage(FOO, "Foo", "Foo"),
    ]
    contents = {
        ROOT: b'<html><body><a href="/Foo">Foo</a></body></html>',
        FOO: b"<html><body>Old Foo</body></html>",
    }
    paths = {ROOT: "index.html", FOO: "Foo"}
    for url, data in contents.items():
        (work / paths[url]).write_bytes(data)
    write_crawl_checkpoint(work / CHECKPOINT_FILENAME, {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "site": SITE.key, "language": "en", "rootUrl": ROOT,
        "phase": "complete", "complete": True, "enumerationContinuation": None,
        "queue": [], "discovered": {url: False for url in paths},
        "required": list(paths), "pageIds": {FOO: 2},
        "saved": {url: [paths[url], len(contents[url])] for url in paths},
        "pages": [
            {"url": page.url, "path": page.path, "title": page.title}
            for page in pages
        ],
        "ignored": {}, "failed": {},
    })
    parent_time = datetime(2025, 11, 15, tzinfo=timezone.utc)
    archive = cli.archive_wiki(
        [work / path for path in paths.values()],
        tmp_path / "old-zips", root=work, site=SITE, now=parent_time,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "old-manifests", snapshot_type="wiki",
        acquired_at=parent_time, source_url=ROOT, document_count=len(paths),
        project_root=tmp_path, language="en",
    )
    inventory = tmp_path / "old-manifests/inventories" / (archive.stem + ".inventory.json")
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps(build_snapshot_inventory(
        archive, manifest, work=work, site=SITE.key, language="en",
        root_url=ROOT, include_history=False, pages=pages,
        crawl_checkpoint=work / CHECKPOINT_FILENAME,
    )), encoding="utf-8")
    verify_snapshot(archive, manifest, inventory)
    return archive, manifest, inventory


def test_archive_update_migrates_legacy_parent_immutably_and_keeps_lineage(
    tmp_path, monkeypatch,
):
    parent = _legacy_parent(tmp_path)
    before = [sha256_file(item) for item in parent]
    urls = {
        ROOT: (
            b'<html><body><a href="/Foo">Foo</a>'
            b'<a href="/Foo/Bar">Bar</a>'
            b'<img src="/images/Logo.png">'
            b'<img src="/images/logo.png"></body></html>'
        ),
        FOO: b'<html><body><a href="/Foo/Bar">Bar</a></body></html>',
        SUB: b"<html><body>New subpage</body></html>",
        ROOT + "images/Logo.png": PNG,
        ROOT + "images/logo.png": PNG,
    }
    fetched = []

    def fetch(url, *, language, site, **kwargs):
        fetched.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["recentchanges"]:
            return b'{"query":{"recentchanges":[]}}'
        if query.get("list") == ["allpages"]:
            return b'{"query":{"allpages":[{"title":"Foo","pageid":2},{"title":"Foo/Bar","pageid":3}]}}'
        if query.get("prop") == ["imageinfo"]:
            return b'{"query":{"pages":[{"title":"File:logo.png","missing":true}]}}'
        if query.get("prop") in (["revisions"], ["templates|categories"]):
            raise OSError("optional metadata deliberately unavailable")
        return urls[url]

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    updated = update_snapshot(
        *parent,
        archive_directory=tmp_path / "new-zips",
        manifest_directory=tmp_path / "new-manifests",
        work_root=tmp_path / "new-work",
        source_replay_directory=tmp_path / "new-replay",
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    child = verify_snapshot(updated.archive, updated.manifest, updated.inventory)
    with zipfile.ZipFile(updated.archive) as output:
        paths = output.namelist()
        assert "Foo.html" in paths and "Foo/Bar.html" in paths
        assert "Foo" not in paths
        assert len([path for path in paths if path.startswith("images/")]) == 2
        assert b'href="Foo.html"' in output.read("index.html")
        assert b'href="Foo/Bar.html"' in output.read("index.html")
        assert b'href="Foo/Bar.html"' in output.read("Foo.html")
        output.extractall(tmp_path / "unpacked")
    assert child["parent"]["archiveSha256"] == before[0]
    assert [sha256_file(item) for item in parent] == before
    assert any(
        entry["oldUrl"] == entry["newUrl"] == FOO
        and entry["oldPath"] == "Foo"
        and entry["newPath"] == "Foo.html"
        and entry["interpretation"] == "mirror-path-layout-change"
        for entry in child["captureMetadata"]["pageMovesById"]
    )
    assert child["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0
    assert updated.source_replay is not None
    verify_source_replay_bundle(
        updated.source_replay, updated.archive, updated.manifest, updated.inventory,
    )
    assert len([url for url in fetched if url in (ROOT, FOO, SUB)]) == 3


def test_legacy_history_upgrade_preserves_extensionless_parent_paths(
    tmp_path, monkeypatch,
):
    from infinity_wiki_acquisition.snapshot_upgrade import upgrade_snapshot_with_history

    parent = _legacy_parent(tmp_path)
    hashes = [sha256_file(item) for item in parent]
    requests = []

    def source(url, *, site, language, **kwargs):
        requests.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            title = query["titles"][0]
            ident = 101 if title == "Main Page" else 102
            return json.dumps({"query": {"pages": [{
                "title": title, "pageid": 1 if ident == 101 else 2,
                "revisions": [{
                    "revid": ident, "parentid": 0,
                    "timestamp": "2025-11-15T12:00:00Z",
                }],
            }]}}).encode()
        if "oldid" in query:
            return b"<html><body>Old revision</body></html>"
        raise AssertionError(f"History-only upgrade refetched a current page: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", source)
    upgraded = upgrade_snapshot_with_history(
        *parent, archive_directory=tmp_path / "history-zips",
        manifest_directory=tmp_path / "history-manifests",
        work_root=tmp_path / "history-work",
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    verified = verify_snapshot(upgraded.archive, upgraded.manifest, upgraded.inventory)
    with zipfile.ZipFile(upgraded.archive) as output:
        assert "Foo" in output.namelist()
        assert "Foo.html" not in output.namelist()
        assert output.read("Foo") == b"<html><body>Old Foo</body></html>"
        assert "_history/oldid/101.html" in output.namelist()
        assert "_history/oldid/102.html" in output.namelist()
    assert verified["history"] is True
    assert verified["parent"]["archiveSha256"] == hashes[0]
    assert [sha256_file(item) for item in parent] == hashes
    assert all("prop=revisions" in url or "oldid=" in url for url in requests)


def test_old_rules_resume_rejected_without_layout_receipt(tmp_path, capsys):
    from test_portable_case_resume import _preserved

    old = tmp_path / "old.work"
    _preserved(old)
    assert cli.main([
        "--site", "infinity-wiki", "--language", "en",
        "--resume-work", str(old),
        "--root", str(tmp_path / "outputs"),
    ]) == 1
    error = capsys.readouterr().err
    assert "portable path-layout receipt" in error
    assert (old / "No_LoF").is_file()
    assert not list((tmp_path / "outputs").glob("*.zip"))
