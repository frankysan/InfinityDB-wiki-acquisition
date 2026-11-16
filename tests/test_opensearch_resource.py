"""Regression for legacy Rules Wiki OpenSearch XML resource during archive updates."""

from __future__ import annotations

import copy
import urllib.parse
import zipfile
from datetime import datetime, timezone

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_provenance import sha256_file
from infinity_wiki_acquisition.snapshot_update import (
    _parent_sources, update_snapshot,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

from test_rules_portable_layout import _legacy_parent, FOO, ROOT, SITE


OPENSEARCH = ROOT + "opensearch_desc.php"
XML = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
    b"<ShortName>Current Rules Wiki</ShortName>"
    b"</OpenSearchDescription>"
)


def test_opensearch_link_and_url_identified_as_non_html_resource():
    html = (
        '<html><head><link rel="search" '
        'type="application/opensearchdescription+xml" '
        'href="/opensearch_desc.php"></head></html>'
    )
    parser = cli.LinkExtractor()
    parser.feed(html)
    assert [(ref.value, ref.asset) for ref in parser.links] == [
        ("/opensearch_desc.php", True)
    ]
    assert cli.is_asset_url("/opensearch_desc.php")
    assert cli.is_asset_url(OPENSEARCH)
    assert not cli.is_asset_url(ROOT + "other.php")
    assert not cli.is_opensearch_description_url(ROOT + "other.php", site=SITE)
    assert not cli.is_opensearch_description_url(
        "https://other.example/opensearch_desc.php", site=SITE,
    )
    assert not cli.is_opensearch_description_url(
        "https://www.human-sphere.com/opensearch_desc.php",
        site=cli.HUMAN_SPHERE_SITE,
    )
    mirrored = cli.rewrite_html_links(
        html, ROOT, site=SITE, language="en",
    )
    assert 'href="opensearch_desc.php"' in mirrored
    assert cli.local_relative_path(OPENSEARCH, site=SITE, asset=True) == (
        "opensearch_desc.php"
    )


@pytest.mark.parametrize("payload,reason", [
    (b"<html><body>challenge</body></html>", "HTML response"),
    (b"<xml>", "Invalid OpenSearch description XML"),
    (b"<root/>", "does not identify a description"),
    (b'<!DOCTYPE x><OpenSearchDescription/>', "unsupported XML DTD"),
])
def test_opensearch_resource_rejects_unusable_content(payload, reason):
    with pytest.raises(ValueError, match=reason):
        cli.validate_asset_bytes(OPENSEARCH, payload)


def test_opensearch_xml_resource_passes_validation():
    cli.validate_asset_bytes(OPENSEARCH, XML)


def test_legacy_unknown_opensearch_does_not_permit_other_unknown_members(
    tmp_path,
):
    parent = verify_snapshot(*_legacy_parent(tmp_path, with_opensearch=True))
    legacy = next(row for row in parent["members"] if row["kind"] == "unknown")
    assert legacy["sourceUrls"] == [OPENSEARCH]
    assert legacy["path"] == "opensearch_desc__u_b7ab2ebdc1fb.php"
    assert OPENSEARCH not in _parent_sources(parent)

    for changes in (
        {"sourceUrls": []},
        {"sourceUrls": [ROOT + "unknown.php"]},
        {"path": "arbitrary-unknown.php"},
        {"sourceUrls": [OPENSEARCH, ROOT + "other.php"]},
    ):
        altered = copy.deepcopy(parent)
        row = next(row for row in altered["members"] if row["kind"] == "unknown")
        row.update(changes)
        with pytest.raises(ValueError, match="Unclassified member"):
            _parent_sources(altered)


def test_existing_unknown_opensearch_parent_updates_without_reusing_old_bytes(
    tmp_path, monkeypatch,
):
    parent_paths = _legacy_parent(tmp_path, with_opensearch=True)
    parent_hashes = tuple(sha256_file(path) for path in parent_paths)
    parent = verify_snapshot(*parent_paths)
    old_member = next(row for row in parent["members"] if row["kind"] == "unknown")
    old_path = old_member["path"]
    requests = []

    def fetch(url, *, language, site, **kwargs):
        assert site.key == "infinity-wiki" and language == "en"
        requests.append((url, kwargs))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("list") == ["recentchanges"]:
            return b'{"query":{"recentchanges":[]}}'
        if query.get("list") == ["allpages"]:
            return b'{"query":{"allpages":[{"title":"Foo","pageid":2}]}}'
        if query.get("prop") in (["revisions"], ["templates|categories"]):
            raise OSError("optional source observation unavailable")
        if url == ROOT:
            return (
                b'<html><head><link rel="search" '
                b'type="application/opensearchdescription+xml" '
                b'href="/opensearch_desc.php"></head>'
                b'<body><a href="/Foo">Foo</a></body></html>'
            )
        if url == FOO:
            return b"<html><body>Updated Foo</body></html>"
        if url == OPENSEARCH:
            return XML
        raise AssertionError(f"Unplanned request: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    result = update_snapshot(
        *parent_paths, archive_directory=tmp_path / "new-archives",
        manifest_directory=tmp_path / "new-manifests",
        work_root=tmp_path / "new-work",
        source_replay_directory=tmp_path / "new-replay",
        now=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    checked = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert tuple(sha256_file(path) for path in parent_paths) == parent_hashes
    assert checked["parent"]["archiveSha256"] == parent_hashes[0]
    assert checked["captureMetadata"]["renderReuseGate"]["skippedHtmlBodyCount"] == 0
    assert not any(item["kind"] == "unknown" for item in checked["members"])
    source = next(item for item in checked["members"] if item["sourceUrls"] == [OPENSEARCH])
    assert source["kind"] == "asset"
    assert source["path"] == "opensearch_desc.php"
    assert source["sha256"] != old_member["sha256"]
    assert [url for url, _kwargs in requests].count(OPENSEARCH) == 1
    assert all(not kwargs.get("if_none_match") for url, kwargs in requests if url == OPENSEARCH)
    with zipfile.ZipFile(result.archive) as child:
        assert child.read("opensearch_desc.php") == XML
        assert old_path not in child.namelist()
        assert b'href="opensearch_desc.php"' in child.read("index.html")
        assert "Foo.html" in child.namelist()
    assert result.source_replay is not None
    report = verify_source_replay_bundle(
        result.source_replay, result.archive, result.manifest, result.inventory,
    )
    assert len(report["pages"]) == 2
