"""Deterministic P2.4 signals: never infer a complete render dependency graph."""

from __future__ import annotations

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.render_invalidation import assess_render_invalidation


SITE = cli.HUMAN_SPHERE_SITE


def _event(rcid, title, kind="edit", **extra):
    return {"rcid": rcid, "title": title, "type": kind, **extra}


def _page(title, *links):
    return {
        "kind": "current-page",
        "page": {"url": cli.page_url_from_title(title, site=SITE, language="en")},
        "references": [
            {
                "targetUrl": cli.page_url_from_title(target, site=SITE, language="en"),
                "targetType": relation,
                "dependencyVerified": False,
            }
            for target, relation in links
        ],
    }


def _assess(pages, events, *, moves=None, absent=None):
    return assess_render_invalidation(
        pages, recent_changes={"events": events},
        page_moves=moves or [], absent_page_ids=absent or [],
        site=SITE, language="en",
    )


def test_template_module_changes_are_observed_not_proven_transclusions():
    linked = _page(
        "Main Page",
        ("Template:Card", "template-page"),
        ("Module:Logic", "module-page"),
    )
    unlinked = _page("Other Page")
    report = _assess([unlinked, linked], [
        _event(2, "Module:Logic"),
        _event(1, "Template:Card"),
        _event(3, "Template:Unlinked"),
    ])
    assert report["observedTemplateChangeRcids"] == [1, 3]
    assert report["observedModuleChangeRcids"] == [2]
    assert report["unverifiedLinkCandidates"] == [{
        "pageUrl": linked["page"]["url"],
        "changeRcids": [1, 2],
        "basis": "unverified-source-html-links",
    }]
    assert report["directDependencyCoverage"] == "unknown"
    assert report["transitiveDependencyCoverage"] == "unknown"
    assert report["moduleInvocationCoverage"] == "unknown"
    assert report["renderReusePolicy"] == "refetch-all-current-html"
    assert report["currentPageCount"] == 2


def test_category_categorization_and_page_index_signals_are_not_completeness():
    report = _assess([_page("Main Page")], [
        _event(5, "Category:Old", "edit"),
        _event(6, "Main Page", "categorize"),
        _event(7, "New Page", "new"),
        _event(8, "Template:Changed", "log", logtype="move"),
        _event(9, "Absent", "log", logtype="delete"),
    ], moves=[{"interpretation": "unresolved-url-association"}],
        absent=[{"pageId": 42}])
    assert report["observedCategoryChangeRcids"] == [5]
    assert report["observedCategorizationRcids"] == [6]
    assert report["observedIndexChangeRcids"] == [6, 7, 8, 9]
    assert report["categoryMembershipCoverage"] == "unknown"
    assert report["recentChangesRetention"] == "unverified"
    assert report["discoveryPolicy"] == "fresh-allpages-and-linked-pages"
    assert report["unresolvedPageUrlAssociationCount"] == 1
    assert report["absentCurrentPageIdCount"] == 1


def test_no_events_or_source_links_still_requires_full_html_refresh():
    pages = [_page("Main Page"), _page("Other Page")]
    report = _assess(pages, [])
    assert report["unverifiedLinkCandidates"] == []
    assert report["observedIndexChangeRcids"] == []
    assert report["renderReusePolicy"] == "refetch-all-current-html"
    assert report["directDependencyCoverage"] == "unknown"
    assert report["currentPageCount"] == 2


def test_localized_namespace_signals_preserve_identity_without_false_dependency():
    report = _assess([_page("Main Page")], [
        _event(1, "Plantilla:Ficha"), _event(2, "Módulo:Ficha"),
        _event(3, "Categoría:Unidades"),
        _event(4, "Template talk:Card"),
    ])
    assert report["observedTemplateChangeRcids"] == [1]
    assert report["observedModuleChangeRcids"] == [2]
    assert report["observedCategoryChangeRcids"] == [3]
    assert report["unverifiedLinkCandidates"] == []
