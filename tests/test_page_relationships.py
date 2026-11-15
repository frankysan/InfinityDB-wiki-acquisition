"""P2.4b offline MediaWiki relationship evidence and fail-closed pagination."""

from __future__ import annotations

import hashlib
import json
import urllib.parse

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.page_relationships import (
    BATCH_SIZE,
    FILENAME,
    assess_relationship_coverage,
    load_page_relationship_evidence,
    observe_page_relationships,
    validate_page_relationship,
)

SITE = cli.HUMAN_SPHERE_SITE
ROOT = SITE.root_urls["en"]
PAGE = cli.WikiPage(ROOT, "Main_Page.html", "Main Page")


def _observe(tmp_path, monkeypatch, fetch, *, page_ids=None):
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    return observe_page_relationships(
        [PAGE], {ROOT: 7} if page_ids is None else page_ids,
        tmp_path, site=SITE, language="en",
    )["records"][ROOT]


def _response(title="Main Page", *, templates=None, categories=None, cont=None):
    body = {"query": {"pages": [{
        "pageid": 7, "title": title,
        **({"templates": templates} if templates is not None else {}),
        **({"categories": categories} if categories is not None else {}),
    }]}}
    if cont is not None:
        body["continue"] = cont
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def test_pagination_combines_api_observed_transclusions_and_categories(tmp_path, monkeypatch):
    first = _response(
        templates=[{"ns": 10, "title": "Template:A"}],
        categories=[{"ns": 14, "title": "Category:First"}],
        cont={"continue": "||", "tlcontinue": "7|10|Template:B"},
    )
    second = _response(
        templates=[{"ns": 828, "title": "Module:Helper"},
                   {"ns": 10, "title": "Template:B"}],
        categories=[{"ns": 14, "title": "Category:Second"}],
    )
    calls = []

    def fetch(url, *, language, site):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["prop"] == ["templates|categories"]
        assert query["pageids"] == ["7"]
        assert query["tllimit"] == query["cllimit"] == ["max"]
        calls.append(query)
        return first if len(calls) == 1 else second

    observed = _observe(tmp_path, monkeypatch, fetch)
    assert len(calls) == 2
    assert calls[1]["tlcontinue"] == ["7|10|Template:B"]
    assert calls[1]["continue"] == ["||"]
    assert observed["status"] == "observed"
    assert observed["templates"] == [
        {"ns": 10, "title": "Template:A"},
        {"ns": 10, "title": "Template:B"},
        {"ns": 828, "title": "Module:Helper"},
    ]
    assert observed["categories"] == [
        {"ns": 14, "title": "Category:First"},
        {"ns": 14, "title": "Category:Second"},
    ]
    assert observed["responseSha256s"] == [
        hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest(),
    ]
    assert observed["queryPageIds"] == [7]
    assert observed["moduleCoverage"] == "unknown"
    validate_page_relationship(observed, url=ROOT)
    assert load_page_relationship_evidence(
        tmp_path, site=SITE.key, language="en",
    )[ROOT] == observed
    assert (tmp_path / FILENAME).is_file()
    audit = assess_relationship_coverage([{
        "kind": "current-page", "pageRelationships": observed,
    }])
    assert audit["observedCount"] == 1
    assert audit["templatePageCount"] == audit["categorizedPageCount"] == 1
    assert audit["directness"] == audit["transitiveClosure"] == "not-established"


@pytest.mark.parametrize("bad", [
    "repeated", "category-ns", "malformed", "wrong-id", "redirect",
])
def test_incomplete_api_evidence_never_claims_partial_lists(
    tmp_path, monkeypatch, bad,
):
    partial = _response(
        templates=[{"ns": 10, "title": "Template:Partial"}],
        cont={"continue": "||", "tlcontinue": "7|10|Next"},
    )
    def fetch(url, *, language, site):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if bad == "repeated":
            return partial
        if bad == "category-ns":
            return _response(categories=[{"ns": 10, "title": "Category:False"}])
        if bad == "malformed":
            return b'{"query":{"pages":null}}'
        if bad == "redirect":
            return json.dumps({
                "query": {
                    "redirects": [{"from": "Main Page", "to": "Other Page"}],
                    "pages": [{"pageid": 7, "title": "Main Page"}],
                },
            }).encode()
        return json.dumps({"query": {"pages": [{
            "pageid": 99, "title": "Main Page",
            "templates": [{"ns": 10, "title": "Template:False"}],
        }]}}).encode()

    observed = _observe(tmp_path, monkeypatch, fetch)
    assert observed["status"] == "api-unavailable"
    assert observed["templates"] == observed["categories"] == []
    assert observed["moduleCoverage"] == "unknown"
    validate_page_relationship(observed, url=ROOT)


def test_mismatched_title_does_not_publish_relationships(tmp_path, monkeypatch):
    def fetch(url, *, language, site):
        return _response(
            "Other Page",
            templates=[{"ns": 10, "title": "Template:Card"}],
        )
    observed = _observe(tmp_path, monkeypatch, fetch)
    assert observed["status"] == "identity-unresolved"
    assert observed["apiTitle"] == "Other Page"
    assert observed["templates"] == []
    validate_page_relationship(observed, url=ROOT)


def test_missing_numeric_id_is_unknown_without_network(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Missing page ID must not trigger relationship query")
    observed = _observe(tmp_path, monkeypatch, blocked, page_ids={})
    assert observed["status"] == "missing-page-id"
    assert observed["queryPageIds"] == []
    assert observed["responseSha256s"] == []
    validate_page_relationship(observed, url=ROOT)


def test_validator_rejects_claims_of_render_coverage_and_unsorted_links(
    tmp_path, monkeypatch,
):
    observed = _observe(
        tmp_path, monkeypatch,
        lambda *a, **k: _response(
            templates=[{"ns": 10, "title": "Template:A"}],
        ),
    )
    observed["moduleCoverage"] = "complete"
    with pytest.raises(ValueError, match="scope"):
        validate_page_relationship(observed, url=ROOT)
    observed["moduleCoverage"] = "unknown"
    observed["templates"] *= 2
    with pytest.raises(ValueError, match="sorted"):
        validate_page_relationship(observed, url=ROOT)


def test_duplicate_numeric_ids_on_distinct_urls_remain_explicitly_unresolved(
    tmp_path, monkeypatch,
):
    alias = cli.WikiPage(
        cli.page_url_from_title("Alias", site=SITE, language="en"),
        "Alias.html", "Alias",
    )
    requests = []
    def fetch(url, *, language, site):
        requests.append(url)
        return _response(templates=[{"ns": 10, "title": "Template:A"}])
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    rows = observe_page_relationships(
        [PAGE, alias], {ROOT: 7, alias.url: 7},
        tmp_path, site=SITE, language="en",
    )["records"]
    assert len(requests) == 1
    assert rows[ROOT]["status"] == "observed"
    assert rows[alias.url]["status"] == "identity-unresolved"
    assert rows[alias.url]["templates"] == []


def test_page_relationship_batch_size_is_bounded():
    assert BATCH_SIZE <= 40


def test_spanish_root_title_is_matched_without_english_namespace_guessing(
    tmp_path, monkeypatch,
):
    site = cli.INFINITY_WIKI_SITE
    page = cli.WikiPage(site.root_urls["es"], "es/index.html", "Página principal")
    def fetch(url, *, language, site):
        assert language == "es"
        return json.dumps({"query": {"pages": [{
            "pageid": 27, "title": "Página principal",
            "templates": [{"ns": 10, "title": "Plantilla:Ficha"}],
        }]}}).encode()
    monkeypatch.setattr(cli, "fetch_site_bytes", fetch)
    observed = observe_page_relationships(
        [page], {page.url: 27}, tmp_path, site=site, language="es",
    )["records"][page.url]
    assert observed["status"] == "observed"
    assert observed["templates"] == [{"ns": 10, "title": "Plantilla:Ficha"}]
    validate_page_relationship(observed, url=page.url)
