"""P3.5 Rules Wiki source-policy decisions and offline audit contracts."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_provenance import sha256_file
from tools.audit_rules_source_policy import audit, main, observed_page_title_class
from test_rules_portable_layout import _legacy_parent


def test_source_policy_audit_is_verified_read_only_and_reports_page_types(
    tmp_path: Path, capsys,
):
    parent = _legacy_parent(tmp_path)
    hashes = tuple(sha256_file(item) for item in parent)
    report = audit(*parent)
    assert report["memberKinds"] == {"current-page": 2}
    assert report["pageNamespaces"] == {"ordinary-title": 1, "entry-root": 1}
    assert report["assetHosts"] == {}
    assert report["thumbOriginalCandidates"] == []
    assert report["nonarchivedReferenceCounts"] == {}
    assert report["ignoredUrls"] == []
    assert report["source"]["site"] == "infinity-wiki"
    assert main([str(item) for item in parent]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"]["archive"] == parent[0].name
    assert tuple(sha256_file(item) for item in parent) == hashes


def test_rules_source_specific_host_404_namespace_and_service_policies():
    rules = cli.INFINITY_WIKI_SITE
    human = cli.HUMAN_SPHERE_SITE
    assert rules.ignored_discovered_http_statuses == frozenset()
    assert human.ignored_discovered_http_statuses == frozenset({404})
    missing = urllib.error.HTTPError(
        "https://infinitythewiki.com/Missing", 404, "Not Found", {}, None,
    )
    assert cli.ignored_fetch_failure_reason(
        missing, required=False, site=rules,
    ) is None
    assert cli.ignored_fetch_failure_reason(
        missing, required=False, site=human,
    ) == "discovered URL returned HTTP 404"
    assert cli.ignored_fetch_failure_reason(
        missing, required=True, site=human,
    ) is None
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/Infinity:About", site=rules,
    ) == "MediaWiki project namespace"
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/Category:ARO", site=rules,
    ) is None
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/Talk:Example", site=rules,
    ) is None
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/Fireteam:_Duo", site=rules,
    ) is None
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/rest.php/v1/page/Example", site=rules,
    ) is None
    assert cli.ignored_url_reason(
        "https://infinitythewiki.com/cdn-cgi/trace", site=rules,
    ) is None
    assert cli.ignored_url_reason(rules.api_urls["en"], site=rules) == (
        "MediaWiki API service endpoint, not a wiki page"
    )
    assert cli.is_asset_url("https://infinitythewiki.com/load.php?only=styles")
    assert cli.is_asset_url("https://infinitythewiki.com/opensearch_desc.php")
    assert cli.should_skip_url("https://www.infinitythewiki.com/Foo", site=rules)
    assert not cli.should_skip_url(
        "https://assets.corvusbelli.net/wiki/sample.png", site=rules,
    )
    # Spanish quick-start PDF references are external hyperlinks, not
    # additional required downloads authorized by the source-site scope.
    assert cli.should_skip_url(
        "https://downloads.corvusbelli.com/infinity/rules/qsr-n5-es.pdf",
        site=rules,
    )


def test_spanish_localized_namespace_looking_titles_are_only_url_evidence():
    spanish = "https://infinitythewiki.com/es/"
    assert observed_page_title_class(spanish) == "entry-root"
    assert observed_page_title_class(spanish + "Categor%C3%ADa:Antimaterial") == (
        "namespace:categoría"
    )
    assert observed_page_title_class(spanish + "Plantilla:Modifiers-explained") == (
        "namespace:plantilla"
    )
    assert observed_page_title_class(spanish + "Fireteam:_D%C3%BAo") == (
        "ordinary-title-containing-colon"
    )
    assert observed_page_title_class(spanish + "ARM") == "ordinary-title"


def test_unknown_colon_title_is_navigation_not_namespace_asset():
    parser = cli.LinkExtractor()
    parser.feed(
        '<a href="/Fireteam:_Duo">Duo</a>'
        '<a href="/Category:ARO">ARO category</a>'
        '<link rel="stylesheet" href="/load.php?only=styles">'
    )
    assert [
        (link.value, link.asset) for link in parser.links
    ] == [
        ("/Fireteam:_Duo", False),
        ("/Category:ARO", False),
        ("/load.php?only=styles", True),
    ]
