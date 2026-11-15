"""Source identity and relationship evidence without inventing transclusions."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME,
    read_crawl_checkpoint,
)
from infinity_wiki_acquisition.page_evidence import read_page_evidence
from infinity_wiki_acquisition.snapshot_inventory import load_snapshot_inventory


def test_current_page_inventory_preserves_ids_hints_and_observed_links(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    site = cli.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    template = "https://www.human-sphere.com/Template:UnitCard"
    module = "https://www.human-sphere.com/Module:Infobox/styles.css"
    image = "https://www.human-sphere.com/images/unit.png"
    css = "https://www.human-sphere.com/resources/site.css"
    root_html = (
        '<!DOCTYPE html><html><head>'
        '<link rel="canonical" href="/Main_Page">'
        '<meta http-equiv="refresh" content="0; url=/Redirect_Target">'
        '<link rel="stylesheet" href="/resources/site.css">'
        '<link rel="stylesheet" href="/Module:Infobox/styles.css">'
        '<script>mw.config.set({"wgArticleId":999})</script>'
        '</head><body><a href="/Template:UnitCard">View template</a>'
        '<a href="/Ordinary_Page">Ordinary link</a>'
        '<img src="/images/unit.png"></body></html>'
    ).encode()
    payloads = {
        root: root_html,
        template: b"<html><body>Template page</body></html>",
        module: b"<html><body>Module source</body></html>",
        image: b"\x89PNG\r\n\x1a\nmock image",
        css: b"body { color: red; }",
        "https://www.human-sphere.com/Ordinary_Page": b"<html>Link</html>",
    }

    def fetch(url: str, *, language: str = "en") -> bytes:
        if "api.php?" in url:
            return json.dumps({
                "query": {"allpages": [
                    {"title": "Main Page", "pageid": 42},
                ]},
            }).encode()
        return payloads[url]

    monkeypatch.setattr(cli, "fetch_bytes", fetch)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    assert cli.main([
        "--site", "human-sphere", "--root", "archives",
        "--work-root", "work", "--manifest-dir", "manifests",
    ]) == 0

    archive = next((tmp_path / "archives").glob("*.zip"))
    inventory = next((tmp_path / "manifests/inventories").glob("*.json"))
    doc = load_snapshot_inventory(inventory, archive=archive)
    assert doc["captureMetadata"]["mediaWikiPageIdsAvailable"] is True
    records = {item["path"]: item for item in doc["members"]}
    main = records["Main_Page.html"]
    source = main["sourceIdentity"]
    assert source["requestUrl"] == root
    assert source["pageId"] == 42
    assert source["pageIdBasis"] == "mediawiki-allpages"
    assert source["htmlPageIdHint"] == 999
    assert source["pageIdConsistency"] == "conflicting"
    assert source["canonicalDeclarations"] == [root]
    assert source["metaRefreshTargets"] == [
        "https://www.human-sphere.com/Redirect_Target"
    ]
    assert source["httpRedirectVerified"] is False
    assert main["transclusionCoverage"] == {"template": "unknown", "module": "unknown"}
    with zipfile.ZipFile(archive) as bundle:
        original_hash = hashlib.sha256(root_html).hexdigest()
        assert main["renderedIdentity"]["originalResponseSha256"] == original_hash
        assert main["renderedIdentity"]["originalResponseEvidence"] == "captured-pre-rewrite"
        mirrored = bundle.read("Main_Page.html")
        assert main["renderedIdentity"]["sha256"] == hashlib.sha256(mirrored).hexdigest()
        assert mirrored != root_html
        assert not any(name.startswith(".wiki-") for name in bundle.namelist())

    refs = {(ref["targetUrl"], ref["relation"]): ref for ref in main["references"]}
    template_ref = refs[(template, "navigation")]
    assert template_ref["targetType"] == "template-page"
    assert template_ref["dependencyVerified"] is False
    assert template_ref["resolution"] == "archived"
    assert template_ref["targetPath"] == cli.local_relative_path(template, site=site)
    module_ref = refs[(module, "stylesheet")]
    assert module_ref["targetType"] == "module-page"
    assert module_ref["resolution"] == "archived"
    assert module_ref["targetPath"] == "Module_Infobox/styles.css.html"
    image_ref = refs[(image, "embedded-image")]
    assert image_ref["targetType"] == "asset"
    assert image_ref["resolution"] == "archived"
    assert refs[(css, "stylesheet")]["resolution"] == "archived"
    assert refs[(root, "declared-canonical")]["resolution"] == "archived"
    assert refs[(
        "https://www.human-sphere.com/Redirect_Target", "meta-refresh"
    )]["resolution"] == "unresolved"
    assert not any("Ordinary_Page" in url for url, _ in refs)


def test_source_evidence_survives_resume_and_is_not_in_zip(
    tmp_path: Path, monkeypatch,
) -> None:
    site = cli.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    child = "https://www.human-sphere.com/Child"
    payloads = {
        root: b'<html><body><img src="/images/unit.png"><a href="/Child">Child</a></body></html>',
        child: b"<html>child page</html>",
        "https://www.human-sphere.com/images/unit.png": b"\x89PNG\r\n\x1a\nmock image",
    }
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "fetch_all_page_title_batch",
        lambda **_: (("Main Page",), None),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    interrupted = True

    def fetch(url: str, *, language: str = "en") -> bytes:
        nonlocal interrupted
        calls.append(url)
        if url == child and interrupted:
            interrupted = False
            raise KeyboardInterrupt()
        return payloads[url]

    monkeypatch.setattr(cli, "fetch_bytes", fetch)
    checkpoint = tmp_path / CHECKPOINT_FILENAME
    with pytest.raises(KeyboardInterrupt):
        cli.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    evidence = read_page_evidence(tmp_path, root, path="Main_Page.html")
    assert evidence is not None
    assert evidence["originalSha256"] == hashlib.sha256(payloads[root]).hexdigest()
    assert any(ref["relation"] == "embedded-image" for ref in evidence["references"])

    resumed = cli.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert resumed.failures == ()
    # The crawl checkpoint is periodic: the most recent uncommitted
    # attempts may be replayed, but source evidence remains consistent.
    assert calls.count(root) == 2
    replayed = read_page_evidence(tmp_path, root, path="Main_Page.html")
    assert replayed is not None
    # A replay is a fresh HTTP observation: all stable evidence remains
    # identical, but its fetchedAt timestamp may advance across seconds.
    assert {
        key: value for key, value in replayed.items() if key != "fetchedAt"
    } == {
        key: value for key, value in evidence.items() if key != "fetchedAt"
    }
    assert replayed["fetchedAt"] >= evidence["fetchedAt"]


def test_allpages_page_ids_are_independent_and_invalid_ids_fail(
    tmp_path: Path, monkeypatch,
) -> None:
    site = cli.HUMAN_SPHERE_SITE
    root = site.root_urls["en"]
    payload = json.dumps({"query": {"allpages": [
        {"title": "Main Page", "pageid": 121},
        {"title": "Different Page", "pageid": 122},
    ]}}).encode()
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda url, *, language, site: payload,
    )
    titles, _ = cli.fetch_all_page_title_batch(language="en", site=site)
    assert titles == ("Main Page", "Different Page")
    assert [title.page_id for title in titles] == [121, 122]

    checkpoint = tmp_path / CHECKPOINT_FILENAME
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        cli, "fetch_bytes", lambda url, *, language="en": b"<html>Content</html>",
    )
    result = cli.download_wiki(root, tmp_path, site=site, checkpoint_file=checkpoint)
    assert result.failures == ()
    state = read_crawl_checkpoint(
        checkpoint, work=tmp_path, site=site.key, language="en", root_url=root,
    )
    assert state["pageIds"][root] == 121
    assert state["pageIds"]["https://www.human-sphere.com/Different_Page"] == 122

    changed = json.loads(checkpoint.read_text(encoding="utf-8"))
    changed["pageIds"][root] = -1
    checkpoint.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid or inconsistent URL records"):
        read_crawl_checkpoint(
            checkpoint, work=tmp_path, site=site.key,
            language="en", root_url=root,
        )


def test_missing_evidence_stays_unknown_on_legacy_inventory(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    root = cli.ROOT_URL
    monkeypatch.setattr(
        cli, "download_wiki",
        lambda base_url, work, **kwargs: _fake_legacy_download(root, work),
    )
    assert cli.main([
        "--root", "archives", "--work-root", "work", "--manifest-dir", "manifests",
    ]) == 0
    inventory = next((tmp_path / "manifests/inventories").glob("*.json"))
    doc = load_snapshot_inventory(inventory)
    page = doc["members"][0]
    assert page["kind"] == "current-page"
    assert page["sourceIdentity"]["pageId"] is None
    assert page["sourceIdentity"]["canonicalDeclarations"] is None
    assert page["renderedIdentity"]["originalResponseSha256"] is None
    assert page["references"] is None
    assert page["transclusionCoverage"]["template"] == "unknown"


def _fake_legacy_download(root: str, work: Path) -> cli.WikiDownloadResult:
    page = work / "index.html"
    page.write_text("<html>From older acquisition</html>", encoding="utf-8")
    return cli.WikiDownloadResult(
        files=(page,),
        failures=(),
        pages=(cli.WikiPage(root, "index.html", "Main Page"),),
    )



def test_canonical_evidence_retains_observed_query_without_merging_aliases() -> None:
    url = "https://www.human-sphere.com/Main_Page"
    raw = (
        '<html><head><link rel="canonical" '
        'href="/index.php?title=Main_Page&amp;oldid=123">'
        '</head><body></body></html>'
    ).encode()
    parser = cli.LinkExtractor()
    parser.feed(raw.decode())
    record = cli.build_page_evidence(
        url=url, path="Main_Page.html", payload=raw, parser=parser,
        site=cli.HUMAN_SPHERE_SITE,
    )
    observed = "https://www.human-sphere.com/index.php?title=Main_Page&oldid=123"
    assert record["canonicalHints"] == [observed]
    ref = next(ref for ref in record["references"] if ref["relation"] == "declared-canonical")
    assert ref["observedUrl"] == observed
    assert ref["targetUrl"] == "https://www.human-sphere.com/index.php"
    assert record["redirectHints"] == []



def test_http_redirect_preserves_source_request_and_confirmed_target(
    tmp_path: Path, monkeypatch,
) -> None:
    url = "https://www.human-sphere.com/Main_Page"
    target = "https://www.human-sphere.com/Redirect_Destination"
    body = b"<html><body>Redirected response</body></html>"
    calls: list[str] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return body

        def geturl(self) -> str:
            return target

    def open_url(request, timeout=30):
        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr(cli.urllib.request, "urlopen", open_url)
    payload = cli.fetch_bytes(url)
    assert payload == body
    assert payload.effective_url == target
    assert payload.http_status == 200
    assert calls == [url]

    parser = cli.LinkExtractor()
    parser.feed(body.decode())
    evidence = cli.build_page_evidence(
        url=url, path="Main_Page.html", payload=payload,
        parser=parser, site=cli.HUMAN_SPHERE_SITE,
    )
    assert evidence["url"] == url
    assert evidence["httpEffectiveUrl"] == target
    assert evidence["httpRedirectTarget"] == target
    assert evidence["httpStatus"] == 200

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli, "fetch_all_page_title_batch",
        lambda **_: (("Main Page",), None),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    # Only the original request URL gets a local member. The observed final
    # URL is evidence of an HTTP redirect, not another silently merged page.
    assert cli.main([
        "--site", "human-sphere", "--root", "archives", "--work-root", "work",
        "--manifest-dir", "manifests",
    ]) == 0
    inventory = next((tmp_path / "manifests/inventories").glob("*.json"))
    document = load_snapshot_inventory(inventory)
    page = document["members"][0]
    assert page["sourceIdentity"]["requestUrl"] == url
    assert page["sourceIdentity"]["httpRedirectTarget"] == target
    assert page["sourceIdentity"]["httpRedirectVerified"] is True
    assert page["sourceIdentity"]["httpEffectiveUrl"] == target
    assert page["sourceIdentity"]["httpStatus"] == 200
    assert page["sourceUrls"] == [url]
