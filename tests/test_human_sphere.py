from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from infinity_wiki_acquisition import cli as module

PNG_BYTES = b"\x89PNG\r\n\x1a\nmock image"


def _http_error(url: str, status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, "test error", hdrs=Message(), fp=None)


def test_human_sphere_site_contract_and_alias_canonicalization() -> None:
    site = module.HUMAN_SPHERE_SITE

    assert site.root_urls == {"en": "https://www.human-sphere.com/Main_Page"}
    assert module.canonical_crawl_url(
        "http://human-sphere.com/Main_Page#top",
        site=site,
    ) == "https://www.human-sphere.com/Main_Page"
    assert module.wiki_page_matches_language(
        "https://human-sphere.com/Stormbots",
        "en",
        site=site,
    )
    with pytest.raises(ValueError, match="English-only|expected one of: en"):
        module.wiki_page_matches_language(
            "https://www.human-sphere.com/Stormbots",
            "es",
            site=site,
        )




def test_human_sphere_ignores_noncontent_namespaces_and_service_endpoints() -> None:
    site = module.HUMAN_SPHERE_SITE

    assert module.ignored_url_reason(
        "https://www.human-sphere.com/Talk:PanOceania", site=site
    ) == "optional MediaWiki namespace"
    assert module.ignored_url_reason(
        "https://www.human-sphere.com/rest.php/v1/search", site=site
    ) == "optional site service endpoint"
    assert module.ignored_url_reason(
        "https://www.human-sphere.com/cdn-cgi/l/email-protection", site=site
    ) == "optional site service endpoint"
    assert module.ignored_url_reason(
        "https://www.human-sphere.com/Category:Units", site=site
    ) is None


def test_human_sphere_discovered_404s_are_ignored(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    broken_page = "https://www.human-sphere.com/Copyright"
    broken_asset = "https://www.human-sphere.com/Wiki/HumanSphereLogo.png"
    fetched: list[str] = []

    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        fetched.append(url)
        if url == root:
            return (
                b'<html><body><a href="/Copyright">Copyright</a>'
                b'<a href="/Talk:PanOceania">Talk</a>'
                b'<a href="/rest.php/v1/search">Search</a>'
                b'<a href="/cdn-cgi/l/email-protection">Email</a>'
                b'<img src="/Wiki/HumanSphereLogo.png"></body></html>'
            )
        if url in {broken_page, broken_asset}:
            raise _http_error(url, 404)
        raise AssertionError(f"unexpected network fetch: {url}")

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert fetched == [root, broken_page, broken_asset]
    ignored = {item.url: item.reason for item in result.ignored}
    assert ignored[broken_page] == "discovered URL returned HTTP 404"
    assert ignored[broken_asset] == "discovered URL returned HTTP 404"
    assert ignored["https://www.human-sphere.com/Talk:PanOceania"] == (
        "optional MediaWiki namespace"
    )
    assert ignored["https://www.human-sphere.com/rest.php/v1/search"] == (
        "optional site service endpoint"
    )
    assert ignored["https://www.human-sphere.com/cdn-cgi/l/email-protection"] == (
        "optional site service endpoint"
    )


def test_human_sphere_enumerated_404_remains_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    missing = "https://www.human-sphere.com/Missing_Page"

    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page", "Missing Page"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        if url == root:
            return b"<html><body>Main</body></html>"
        if url == missing:
            raise _http_error(url, 404)
        raise AssertionError(f"unexpected network fetch: {url}")

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert len(result.failures) == 1
    assert result.failures[0].url == missing
    assert result.ignored == ()


def test_human_sphere_allpages_enumeration_follows_continuation(monkeypatch) -> None:
    site = module.HUMAN_SPHERE_SITE
    requests: list[str] = []

    def fake_fetch(url: str, *, language: str, site: Any) -> bytes:
        requests.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "apcontinue" not in query:
            document: dict[str, Any] = {
                "continue": {"continue": "-||", "apcontinue": "Orphan Page"},
                "query": {"allpages": [{"pageid": 1, "title": "Main Page"}]},
            }
        else:
            assert query["apcontinue"] == ["Orphan Page"]
            document = {
                "query": {"allpages": [{"pageid": 2, "title": "Orphan Page"}]}
            }
        return json.dumps(document).encode()

    monkeypatch.setattr(module, "fetch_site_bytes", fake_fetch)

    assert module.fetch_all_page_titles(language="en", site=site) == (
        "Main Page",
        "Orphan Page",
    )
    assert len(requests) == 2
    first = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0]).query)
    assert first["list"] == ["allpages"]
    assert first["apnamespace"] == ["0"]
    assert first["aplimit"] == ["max"]


def test_human_sphere_crawl_seeds_orphan_pages_and_follows_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    orphan = "https://www.human-sphere.com/Orphan_Page"
    linked = "https://www.human-sphere.com/Linked_Page"
    image = "https://www.human-sphere.com/images/logo.png"
    pages = {
        root: (
            b'<html><body><a href="/Linked_Page">Linked</a>'
            b'<img src="/images/logo.png">'
            b'<a href="/index.php?title=Main_Page&amp;oldid=100">source</a>'
            b"</body></html>"
        ),
        orphan: b"<html><body>Orphan</body></html>",
        linked: b"<html><body>Linked</body></html>",
        image: PNG_BYTES,
    }
    fetched: list[str] = []

    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page", "Orphan Page"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        assert language == "en"
        fetched.append(url)
        return pages[url]

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert fetched == [root, orphan, linked, image]
    assert [path.relative_to(tmp_path).as_posix() for path in result.files] == [
        "Linked_Page.html",
        "Main_Page.html",
        "Orphan_Page.html",
        "images/logo.png",
    ]
    assert {page.title for page in result.pages} == {
        "Linked_Page",
        "Main_Page",
        "Orphan_Page",
    }


@pytest.mark.parametrize("extension", ["pdf", "zip", "rar", "txt"])
def test_human_sphere_downloadable_files_keep_their_extensions(
    tmp_path: Path,
    monkeypatch,
    extension: str,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    resource = f"https://www.human-sphere.com/downloads/reference.{extension}"
    payload = {
        "pdf": b"%PDF-1.7\n",
        "zip": b"PK\x03\x04mock",
        "rar": b"Rar!\x1a\x07\x00mock",
        "txt": b"download",
    }[extension]
    pages = {
        root: (
            f'<html><body><a href="/downloads/reference.{extension}">Download</a>'
            "</body></html>"
        ).encode(),
        resource: payload,
    }
    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module,
        "fetch_bytes",
        lambda url, *, language="en": pages[url],
    )

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    relative = f"downloads/reference.{extension}"
    assert (tmp_path / relative).read_bytes() == payload
    assert not (tmp_path / f"{relative}.html").exists()
    mirrored = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert f'href="{relative}"' in mirrored
    assert f"{relative}.html" not in mirrored


def test_human_sphere_mediawiki_upload_tree_is_always_an_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    resource = "https://www.human-sphere.com/images/archive/source.bin"
    pages = {
        root: b'<html><body><a href="/images/archive/source.bin">Download</a></body></html>',
        resource: b"binary",
    }
    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module,
        "fetch_bytes",
        lambda url, *, language="en": pages[url],
    )

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert (tmp_path / "images/archive/source.bin").read_bytes() == b"binary"
    assert not (tmp_path / "images/archive/source.bin.html").exists()
    mirrored = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert 'href="images/archive/source.bin"' in mirrored


def test_human_sphere_page_paths_do_not_collide_with_descendant_assets() -> None:
    site = module.HUMAN_SPHERE_SITE

    page = module.local_relative_path(
        "https://www.human-sphere.com/Module:Infobox",
        site=site,
        os_name="Windows",
    )
    descendant = module.local_relative_path(
        "https://www.human-sphere.com/Module:Infobox/styles.css",
        asset=True,
        site=site,
        os_name="Windows",
    )

    assert page == "Module_Infobox.html"
    assert descendant == "Module_Infobox/styles.css"
    assert page != descendant.split("/", 1)[0]


def test_human_sphere_crawl_allows_page_and_descendant_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    module_page = "https://www.human-sphere.com/Module:Infobox"
    module_asset = "https://www.human-sphere.com/Module:Infobox/styles.css"
    legacy_page = tmp_path / "Module_Infobox"
    legacy_page.write_bytes(b"legacy page")
    pages = {
        root: b'<html><body><a href="/Module:Infobox">Module</a></body></html>',
        module_page: (
            b'<html><head><link rel="stylesheet" href="/Module:Infobox/styles.css">'
            b"</head><body>Module</body></html>"
        ),
        module_asset: b".infobox {}",
    }
    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page", "Module:Infobox"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module,
        "fetch_bytes",
        lambda url, *, language="en": pages[url],
    )

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert legacy_page.is_dir()
    assert not legacy_page.is_file()
    assert [path.relative_to(tmp_path).as_posix() for path in result.files] == [
        "Main_Page.html",
        "Module_Infobox.html",
        "Module_Infobox/styles.css",
    ]


def test_human_sphere_resume_reuses_existing_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    image = "https://www.human-sphere.com/images/logo.png"
    existing = tmp_path / "images" / "logo.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(PNG_BYTES)
    monkeypatch.setattr(
        module,
        "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    fetched: list[str] = []

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        fetched.append(url)
        if url == root:
            return b'<html><body><img src="/images/logo.png"></body></html>'
        raise AssertionError(f"unexpected network fetch: {url}")

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert fetched == [root]
    assert existing.read_bytes() == PNG_BYTES
    assert image not in fetched
    assert [path.relative_to(tmp_path).as_posix() for path in result.files] == [
        "Main_Page.html",
        "images/logo.png",
    ]


def test_human_sphere_link_rewriting_keeps_external_links_external() -> None:
    site = module.HUMAN_SPHERE_SITE
    html = (
        '<a href="http://human-sphere.com/Stormbots#Profile">Stormbots</a>'
        '<img src="https://www.human-sphere.com/images/unit.png">'
        '<a href="https://infinitythewiki.com/">Rules</a>'
    )

    rewritten = module.rewrite_html_links(
        html,
        site.root_urls["en"],
        language="en",
        site=site,
    )

    assert 'href="Stormbots.html#Profile"' in rewritten
    assert 'src="images/unit.png"' in rewritten
    assert 'href="https://infinitythewiki.com/"' in rewritten


def test_human_sphere_archive_and_work_names_are_source_specific(tmp_path: Path) -> None:
    site = module.HUMAN_SPHERE_SITE
    staging = tmp_path / "staging"
    staging.mkdir()
    page = staging / "Main_Page.html"
    page.write_text("Human Sphere", encoding="utf-8")
    timestamp = datetime(2026, 9, 30, 14, 30, 0)

    archive = module.archive_wiki(
        [page],
        tmp_path / "archives",
        root=staging,
        site=site,
        now=timestamp,
    )
    history_archive = module.archive_wiki(
        [page],
        tmp_path / "archives",
        root=staging,
        site=site,
        now=timestamp,
        include_history=True,
    )
    work = module.create_wiki_work_directory(
        tmp_path / "work",
        language="en",
        site=site,
        now=timestamp,
    )

    assert archive.name == "HUMAN-SPHERE 20260930-143000.zip"
    assert history_archive.name == "HUMAN-SPHERE-history 20260930-143000.zip"
    assert work.name == "HUMAN-SPHERE 20260930-143000.work"


def test_human_sphere_fetch_applies_courtesy_delay(monkeypatch) -> None:
    site = module.HUMAN_SPHERE_SITE
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    monkeypatch.setattr(module, "fetch_bytes", lambda url, *, language="en": b"ok")

    assert module.fetch_site_bytes(site.root_urls["en"], language="en", site=site) == b"ok"
    assert sleeps == [site.request_delay_seconds]


def test_human_sphere_history_urls_use_its_mediawiki_install() -> None:
    site = module.HUMAN_SPHERE_SITE
    query = module.revision_query_url("Stormbots", language="en", site=site)
    oldid = module.oldid_url("Stormbots", 43097, language="en", site=site)

    assert query.startswith("https://www.human-sphere.com/api.php?")
    assert oldid.startswith("https://www.human-sphere.com/index.php?")


def test_main_human_sphere_snapshot_uses_distinct_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot_root = tmp_path / "wiki"
    work_root = tmp_path / "work"
    manifest_directory = tmp_path / "manifests"
    site = module.HUMAN_SPHERE_SITE

    def fake_download(
        root_url: str,
        staging: Path,
        *,
        language: str = "en",
        site: Any = module.DEFAULT_SITE,
        progress=None,
    ) -> Any:
        assert root_url == module.HUMAN_SPHERE_SITE.root_urls["en"]
        assert site is module.HUMAN_SPHERE_SITE
        page = staging / "Main_Page.html"
        page.write_text("<html></html>", encoding="utf-8")
        return module.WikiDownloadResult(files=(page,), failures=())

    monkeypatch.setattr(module, "download_wiki", fake_download)

    assert (
        module.main(
            [
                "--site",
                "human-sphere",
                "--root",
                str(snapshot_root),
                "--work-root",
                str(work_root),
                "--manifest-dir",
                str(manifest_directory),
            ]
        )
        == 0
    )

    archives = list(snapshot_root.glob("HUMAN-SPHERE *.zip"))
    manifests = list(manifest_directory.glob("HUMAN-SPHERE *.json"))
    assert len(archives) == 1
    assert len(manifests) == 1
    assert list(work_root.glob("HUMAN-SPHERE *.work")) == []

    from infinity_wiki_acquisition.snapshot_provenance import load_snapshot_manifest

    document = load_snapshot_manifest(manifests[0], archive=archives[0])
    assert document["snapshot"]["type"] == "wiki"
    assert document["source"] == {
        "url": site.root_urls["en"],
        "language": "en",
    }


def test_human_sphere_srcset_downloads_variants_and_original_upload(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    base = "https://www.human-sphere.com/images"
    image = "Dasyus_Hacker.png"
    small = f"{base}/thumb/a/ab/{image}/160px-{image}"
    large = f"{base}/thumb/a/ab/{image}/320px-{image}"
    original = f"{base}/a/ab/{image}"
    html = (
        f'<html><body><picture><source srcset="/images/thumb/a/ab/{image}/160px-{image} 1x, '
        f'/images/thumb/a/ab/{image}/320px-{image} 2x"></picture></body></html>'
    )
    payloads = {root: html.encode(), small: PNG_BYTES, large: PNG_BYTES, original: PNG_BYTES}
    fetched: list[str] = []
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        fetched.append(url)
        return payloads[url]

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)
    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert set(fetched) == set(payloads)
    assert (tmp_path / f"images/a/ab/{image}").read_bytes() == PNG_BYTES
    assert (tmp_path / f"images/thumb/a/ab/{image}/320px-{image}").read_bytes() == PNG_BYTES
    mirrored = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert f"images/thumb/a/ab/{image}/160px-{image} 1x" in mirrored
    assert f"images/thumb/a/ab/{image}/320px-{image} 2x" in mirrored


def test_human_sphere_wrong_binary_payload_is_failure_not_published(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    image = "https://www.human-sphere.com/images/sculpt.jpg"
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": (
            b'<html><body><h1>Access denied</h1></body></html>'
            if url == image else b'<img src="/images/sculpt.jpg">'
        ),
    )

    result = module.download_wiki(
        root, tmp_path, language="en", site=site,
        progress=module.ConsoleProgressReporter(),
    )

    assert "Wiki crawl failure 1" in capsys.readouterr().err
    assert len(result.failures) == 1
    assert result.failures[0].url == image
    assert "HTML response" in result.failures[0].error
    assert not (tmp_path / "images/sculpt.jpg").exists()


def test_human_sphere_resume_refetches_corrupt_image(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    image = "https://www.human-sphere.com/images/sculpt.png"
    stored = tmp_path / "images" / "sculpt.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"<html>Challenge</html>")
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    fetched: list[str] = []

    def fake_fetch(url: str, *, language: str = "en") -> bytes:
        fetched.append(url)
        return PNG_BYTES if url == image else b'<img src="/images/sculpt.png">'

    monkeypatch.setattr(module, "fetch_bytes", fake_fetch)
    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert fetched == [root, image]
    assert stored.read_bytes() == PNG_BYTES


def test_human_sphere_colliding_normalized_page_paths_fail_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    first = module.canonical_crawl_url(
        "https://www.human-sphere.com/A%3AB", site=site,
    )
    second = module.canonical_crawl_url(
        "https://www.human-sphere.com/A%2AB", site=site,
    )
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page",),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    payloads = {
        root: b'<a href="/A%3AB">First</a><a href="/A%2AB">Second</a>',
        first: b"<html>First</html>",
        second: b"<html>Second</html>",
    }
    monkeypatch.setattr(
        module, "fetch_bytes", lambda url, *, language="en": payloads[url],
    )

    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert len(result.failures) == 1
    assert "Portable mirror path collision" in result.failures[0].error
    assert (tmp_path / "A_B.html").read_text(encoding="utf-8") == "<html>First</html>"


def test_human_sphere_srcset_rewriting_decodes_html_query_entities() -> None:
    site = module.HUMAN_SPHERE_SITE
    url = site.root_urls["en"]
    first_url = "https://www.human-sphere.com/images/figure.png?size=100&v=1"
    second_url = "https://www.human-sphere.com/images/figure.png?size=200&v=1"
    html = (
        '<source srcset="/images/figure.png?size=100&amp;v=1 1x, '
        '/images/figure.png?size=200&amp;v=1 2x">'
    )

    rewritten = module.rewrite_html_links(html, url, site=site)
    first = module.local_relative_path(first_url, asset=True, site=site)
    second = module.local_relative_path(second_url, asset=True, site=site)
    assert f'srcset="{first} 1x, {second} 2x"' in rewritten


def test_asset_validation_rejects_misnamed_binary_format() -> None:
    with pytest.raises(ValueError, match="Invalid .jpg file signature"):
        module.validate_asset_bytes(
            "https://www.human-sphere.com/images/model.jpg",
            PNG_BYTES,
        )
    module.validate_asset_bytes(
        "https://www.human-sphere.com/images/model.png",
        PNG_BYTES,
    )


def test_mediawiki_original_url_handles_svg_thumbnails() -> None:
    url = (
        "https://human-sphere.com/images/thumb/5/58/Unit_Profile.svg/"
        "240px-Unit_Profile.svg.png"
    )
    assert module.mediawiki_original_image_url(url) == (
        "https://human-sphere.com/images/5/58/Unit_Profile.svg"
    )
    assert module.mediawiki_original_image_url(
        "https://human-sphere.com/images/5/58/Unit_Profile.svg"
    ) is None


def test_human_sphere_nested_pages_use_correct_relative_image_paths() -> None:
    site = module.HUMAN_SPHERE_SITE
    base_url = "https://www.human-sphere.com/Factions/Unit"
    html = (
        '<img src="/images/sculpt.png" '
        'srcset="/images/sculpt.png 1x, /images/sculpt_large.png 2x">'
        '<a href="/Starter_Boxes">Product</a>'
    )

    rewritten = module.rewrite_html_links(html, base_url, site=site)

    assert 'src="../images/sculpt.png"' in rewritten
    assert 'srcset="../images/sculpt.png 1x, ../images/sculpt_large.png 2x"' in rewritten
    assert 'href="../Starter_Boxes.html"' in rewritten


def test_file_namespace_image_titles_are_html_pages_not_binary_assets(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    description = "https://www.human-sphere.com/File:Event_Interplanetary2016.JPG"
    image = "https://www.human-sphere.com/images/0/01/Event_Interplanetary2016.JPG"
    page = b'<html><body><h1>File description</h1></body></html>'
    jpeg = b"\xff\xd8\xff\xe0JPEG test"
    payloads = {
        root: (
            b'<html><body><a href="/File:Event_Interplanetary2016.JPG">Photo</a>'
            b'<img src="/images/0/01/Event_Interplanetary2016.JPG"></body></html>'
        ),
        description: page,
        image: jpeg,
    }
    monkeypatch.setattr(module, "fetch_all_page_titles", lambda **_: ("Main Page",))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": payloads[url],
    )

    assert not module.is_asset_url(description)
    assert not module.is_asset_url(
        "https://www.human-sphere.com/Image%3AEvent_Interplanetary2016.JPG"
    )
    assert module.is_asset_url(image)
    result = module.download_wiki(root, tmp_path, language="en", site=site)

    assert result.failures == ()
    assert (tmp_path / "File_Event_Interplanetary2016.JPG.html").read_bytes() == page
    assert (tmp_path / "images/0/01/Event_Interplanetary2016.JPG").read_bytes() == jpeg
    mirrored = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert 'href="File_Event_Interplanetary2016.JPG.html"' in mirrored
    assert 'src="images/0/01/Event_Interplanetary2016.JPG"' in mirrored
    assert {record.title for record in result.pages} == {
        "Main_Page",
        "File:Event_Interplanetary2016.JPG",
    }


def test_human_sphere_unicode_and_ascii_titles_get_distinct_portable_paths() -> None:
    site = module.HUMAN_SPHERE_SITE
    ascii_url = "https://www.human-sphere.com/Aibot"
    unicode_url = "https://www.human-sphere.com/Aib%C5%8Dt"
    plain = module.local_relative_path(ascii_url, site=site, os_name="Windows")
    accented = module.local_relative_path(unicode_url, site=site, os_name="Windows")

    assert plain == "Aibot.html"
    assert re.fullmatch(r"Aibot__u_[0-9a-f]{12}\.html", accented)
    assert accented == module.local_relative_path(unicode_url, site=site, os_name="Linux")
    assert accented == module.local_relative_path(
        "https://www.human-sphere.com/Aibo\u0304t", site=site
    )
    assert plain != accented
    assert module.local_relative_path(
        "https://www.human-sphere.com/images/Aib%C5%8Dt.jpg",
        asset=True,
        site=site,
    ).startswith("images/Aibot__u_")
    assert module.local_relative_path(
        "https://www.human-sphere.com/images/Aib%C5%8Dt.jpg",
        asset=True,
        site=site,
    ).endswith(".jpg")


def test_human_sphere_redirect_alias_crawl_preserves_both_source_paths(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    alias = "https://www.human-sphere.com/Aibot"
    canonical = "https://www.human-sphere.com/Aib%C5%8Dt"
    alias_body = b'<html><body>Redirected from Aibot</body></html>'
    canonical_body = b'<html><body>Aibot canonical page</body></html>'
    paths = {
        root: (
            b'<html><a href="/Aibot">Alias</a>'
            b'<a href="/Aib%C5%8Dt">Canonical</a></html>'
        ),
        alias: alias_body,
        canonical: canonical_body,
    }
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page", "Aibot", "Aib\u014dt"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": paths[url],
    )
    previous = tmp_path / "Aibot.html"
    previous.write_bytes(b"previous incomplete run")

    result = module.download_wiki(root, tmp_path, site=site)
    target = module.local_relative_path(canonical, site=site)

    assert result.failures == ()
    assert previous.read_bytes() == alias_body
    assert (tmp_path / target).read_bytes() == canonical_body
    assert {page.path for page in result.pages} == {
        "Main_Page.html", "Aibot.html", target,
    }
    html = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert 'href="Aibot.html"' in html
    assert f'href="{target}"' in html
    archive = module.archive_wiki(
        list(result.files), tmp_path / "archives", root=tmp_path, site=site
    )
    import zipfile
    with zipfile.ZipFile(archive) as output:
        assert {name for name in output.namelist()} == {
            "Main_Page.html", "Aibot.html", target,
        }
        assert output.testzip() is None
        output.extractall(tmp_path / "extracted")
    assert (tmp_path / "extracted" / target).read_bytes() == canonical_body


def test_human_sphere_case_only_titles_have_distinct_portable_paths() -> None:
    site = module.HUMAN_SPHERE_SITE
    base = "https://www.human-sphere.com/"
    conventional = module.local_relative_path(base + "Haetae", site=site)
    mixed = module.local_relative_path(base + "HaeTae", site=site)
    all_caps = module.local_relative_path(base + "HAETAE", site=site)
    lowercase = module.local_relative_path(base + "haetae", site=site)

    assert conventional == "Haetae.html"
    assert re.fullmatch(r"HaeTae__u_[a-f0-9]{12}\.html", mixed)
    assert len({path.casefold() for path in (conventional, mixed, all_caps, lowercase)}) == 4
    assert mixed == module.local_relative_path(
        base + "HaeTae", site=site, os_name="Linux"
    )
    assert module.local_relative_path(
        base + "File:Event_Interplanetary2016.JPG", site=site
    ) == "File_Event_Interplanetary2016.JPG.html"
    # Existing assets should retain their previous paths for efficient resume.
    assert module.local_relative_path(
        base + "images/thumb/2/23/1121.jpg", site=site, asset=True
    ) == "images/thumb/2/23/1121.jpg"


def test_human_sphere_case_only_redirects_preserve_both_pages_and_links(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    alias = "https://www.human-sphere.com/Haetae"
    canonical = "https://www.human-sphere.com/HaeTae"
    data = {
        root: (
            b'<html><a href="/Haetae">Alias</a>'
            b'<a href="/HaeTae">Canonical</a></html>'
        ),
        alias: b'<html><body>Redirect from Haetae</body></html>',
        canonical: b'<html><body>HaeTae article</body></html>',
    }
    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: ("Main Page", "Haetae", "HaeTae"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": data[url],
    )

    # Resume on a work directory containing the previous colliding page.
    existing = tmp_path / "Haetae.html"
    existing.write_bytes(b"old page, previously overwritten")
    result = module.download_wiki(root, tmp_path, site=site)
    canonical_path = module.local_relative_path(canonical, site=site)

    assert result.failures == ()
    assert existing.read_bytes() == data[alias]
    assert (tmp_path / canonical_path).read_bytes() == data[canonical]
    main = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert 'href="Haetae.html"' in main
    assert f'href="{canonical_path}"' in main
    assert {p.path for p in result.pages} == {
        "Main_Page.html", "Haetae.html", canonical_path,
    }

    import zipfile
    output = module.archive_wiki(
        list(result.files), tmp_path / "archives", root=tmp_path, site=site
    )
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "Main_Page.html", "Haetae.html", canonical_path,
        }
        archive.extractall(tmp_path / "extracted")
    assert (tmp_path / "extracted" / canonical_path).read_bytes() == data[canonical]


def test_link_metadata_does_not_turn_canonical_pages_into_assets(
    tmp_path: Path, monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    canonical = "https://www.human-sphere.com/Haetae"
    css = "https://www.human-sphere.com/load.php?modules=skin"
    markup = (
        b'<html><head><link rel="canonical" href="/Haetae">'
        b'<link rel="stylesheet" href="/load.php?modules=skin">'
        b'<link rel="license" href="/Copyright"></head><body></body></html>'
    )
    parser = module.LinkExtractor()
    parser.feed(markup.decode("utf-8"))
    assert ("", False) not in parser.links
    assert module.WikiLink(value="/Haetae", asset=False) in parser.links
    assert module.WikiLink(value="/Copyright", asset=False) in parser.links
    assert module.WikiLink(value="/load.php?modules=skin", asset=True) in parser.links

    payloads = {
        root: markup,
        canonical: b"<html><body>Haetae article</body></html>",
        css: b"body {}",
        "https://www.human-sphere.com/Copyright": (
            b"<html><body>License page</body></html>"
        ),
    }
    monkeypatch.setattr(module, "fetch_all_page_titles", lambda **_: ("Main Page",))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        module, "fetch_bytes",
        lambda url, *, language="en": payloads[url],
    )
    result = module.download_wiki(root, tmp_path, site=site)

    assert result.failures == ()
    assert (tmp_path / "Haetae.html").read_bytes() == payloads[canonical]
    assert (tmp_path / "Copyright.html").read_bytes() == payloads[
        "https://www.human-sphere.com/Copyright"
    ]
    css_path = module.local_relative_path(css, asset=True, site=site)
    assert (tmp_path / css_path).read_bytes() == payloads[css]
    main = (tmp_path / "Main_Page.html").read_text(encoding="utf-8")
    assert 'rel="canonical" href="Haetae.html"' in main
    assert 'rel="stylesheet" href="' in main



@pytest.mark.parametrize(
    "title",
    [
        "Táowù,_Mastermind_and_Schemer",
        "Oktavia_Grimmsdottir,_Icebreaker_Harpooner",
        "Arslan,_Security_Chief",
        "Dogtags,_Markers_and_Silhouettes",
        "Cliff-Jumpers,_Extreme_Jump_Unit",
        "Gearheads,_Tech_Savant_Team",
        "Harbingers,_New_Mankind_Defense_Infantry",
        "Raindancers,_Light_Assault_Company",
        "Stingers,_Security_Tactical_Unit",
        "Wraith-1,_Clandestine_Action_Group",
    ],
)
def test_equivalent_encoded_and_literal_comma_urls_share_crawl_identity(title: str) -> None:
    site = module.HUMAN_SPHERE_SITE
    encoded = module.page_url_from_title(title, language="en", site=site)
    plain = encoded.replace("%2C", ",")
    assert "%2C" in encoded
    assert module.canonical_crawl_url(encoded, site=site) == (
        module.canonical_crawl_url(plain, site=site)
    )
    assert module.canonical_crawl_url(
        plain, site=site
    ) == module.canonical_crawl_url(plain.replace(",", "%2c"), site=site)
    assert module.local_relative_path(encoded, site=site) == (
        module.local_relative_path(plain, site=site)
    )


def test_canonical_crawl_normalizes_escapes_without_merging_distinct_titles() -> None:
    site = module.HUMAN_SPHERE_SITE
    root = "https://www.human-sphere.com/"
    assert module.canonical_crawl_url(root + "Arslan%2C_Security_Chief", site=site) == (
        module.canonical_crawl_url(root + "Arslan,_Security_Chief", site=site)
    )
    assert module.canonical_crawl_url(root + "Arslan%252C_Security_Chief", site=site) != (
        module.canonical_crawl_url(root + "Arslan%2C_Security_Chief", site=site)
    )
    assert module.canonical_crawl_url(root + "A%2FB", site=site) != (
        module.canonical_crawl_url(root + "A/B", site=site)
    )
    assert module.canonical_crawl_url(
        root + "Arslan%2C_Security_Chief?view=1", site=site
    ) == module.canonical_crawl_url(root + "Arslan,_Security_Chief", site=site)
    first = root + "images/image%2Cmodel.png?size=1"
    second = root + "images/image,model.png?size=2"
    assert module.canonical_crawl_url(first, asset=True, site=site) != (
        module.canonical_crawl_url(second, asset=True, site=site)
    )
    assert module.canonical_crawl_url(first, asset=True, site=site).startswith(
        root + "images/image,model.png?"
    )


def test_human_sphere_crawl_deduplicates_encoded_comma_and_literal_comma_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    site = module.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    titles = (
        "Táowù,_Mastermind_and_Schemer",
        "Oktavia_Grimmsdottir,_Icebreaker_Harpooner",
        "Arslan,_Security_Chief",
        "Dogtags,_Markers_and_Silhouettes",
        "Cliff-Jumpers,_Extreme_Jump_Unit",
        "Gearheads,_Tech_Savant_Team",
        "Harbingers,_New_Mankind_Defense_Infantry",
        "Raindancers,_Light_Assault_Company",
        "Stingers,_Security_Tactical_Unit",
        "Wraith-1,_Clandestine_Action_Group",
    )
    # allpages emits a percent-encoded comma; HTML links can contain a raw one.
    initial_urls = [
        module.page_url_from_title(title, language="en", site=site)
        for title in titles
    ]
    canonical_urls = [
        module.canonical_crawl_url(url, site=site) for url in initial_urls
    ]
    raw_links = [
        url.replace("%2C", ",") for url in initial_urls
    ]
    markup = "<html><body>" + "".join(
        f'<a href="{url}">page</a>' for url in raw_links
    ) + "</body></html>"
    pages = {
        root: markup.encode("utf-8"),
        **{url: b"<html><body>Content</body></html>" for url in canonical_urls},
    }
    calls: list[str] = []

    def fetch(url: str, *, language: str = "en") -> bytes:
        calls.append(url)
        return pages[url]

    monkeypatch.setattr(
        module, "fetch_all_page_titles",
        lambda *, language, site: tuple(t.replace("_", " ") for t in titles),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "fetch_bytes", fetch)

    result = module.download_wiki(root, tmp_path, site=site)

    assert result.failures == ()
    assert len(calls) == len(titles) + 1
    assert len(calls) == len(set(calls))
    assert len(result.pages) == len(titles) + 1
    assert all(
        (tmp_path / module.local_relative_path(url, site=site)).is_file()
        for url in canonical_urls
    )
    assert len({page.path.casefold() for page in result.pages}) == len(result.pages)
