"""P2.4c deterministic rendered-HTML reuse gate and negative cases."""

from __future__ import annotations

import copy

import pytest

from infinity_wiki_acquisition.render_reuse_gate import (
    GLOBAL_BLOCKERS,
    assess_render_reuse,
)

URL = "https://www.human-sphere.com/Main_Page"


def _inputs(*, revision="same-source-revision", relationship="observed"):
    page = {
        "kind": "current-page",
        "path": "Main_Page.html",
        "page": {"url": URL},
        "sourceIdentity": {"pageId": 7},
        "pageRelationships": {"status": relationship},
    }
    comparisons = [{
        "pageUrl": URL,
        "pageId": 7,
        "comparison": revision,
        "htmlRefetched": True,
    }]
    invalidation = {
        "renderReusePolicy": "refetch-all-current-html",
        "recentChangesRetention": "unverified",
        "observedTemplateChangeRcids": [],
        "observedModuleChangeRcids": [],
        "observedCategoryChangeRcids": [],
        "observedCategorizationRcids": [],
        "observedIndexChangeRcids": [],
        "unverifiedLinkCandidates": [],
        "apiObservedTransclusionCandidates": [],
        "apiObservedCategoryCandidates": [],
        "unresolvedPageUrlAssociationCount": 0,
        "absentCurrentPageIdCount": 0,
    }
    source_time = {
        "consistency": "not-verified-atomic",
        "renderDependencies": "unverified",
    }
    changes = {"retentionCoverage": "unverified", "events": []}
    return page, comparisons, invalidation, source_time, changes


def _assess(state):
    page, comparisons, invalidation, coverage, recent = state
    return assess_render_reuse(
        [page],
        revision_comparisons=comparisons,
        invalidation=invalidation,
        source_time=coverage,
        recent_changes=recent,
    )


def test_no_events_same_revision_and_observed_api_still_refetch():
    result = _assess(_inputs())
    assert result["reuseAuthorized"] is False
    assert result["currentPageCount"] == result["refetchedPageCount"] == 1
    assert result["skippedHtmlBodyCount"] == 0
    row = result["decisions"][0]
    assert row["revisionComparison"] == "same-source-revision"
    assert row["decision"] == "refetch-required"
    assert row["htmlRefetched"] is True
    assert row["unmetConditions"] == list(GLOBAL_BLOCKERS)
    assert row["observedRiskSignals"] == []


def test_changed_template_module_category_and_index_signals_are_separate():
    state = _inputs()
    page, comparisons, invalidation, _coverage, _recent = state
    invalidation.update(
        observedTemplateChangeRcids=[1],
        observedModuleChangeRcids=[2],
        observedCategoryChangeRcids=[3],
        observedCategorizationRcids=[4],
        observedIndexChangeRcids=[5],
        apiObservedTransclusionCandidates=[{"pageUrl": URL}],
        apiObservedCategoryCandidates=[{"pageUrl": URL}],
        unverifiedLinkCandidates=[{"pageUrl": URL}],
    )
    row = _assess(state)["decisions"][0]
    assert row["observedRiskSignals"] == [
        "api-observed-category-change-candidate",
        "api-observed-transclusion-change-candidate",
        "category-or-index-change-activity",
        "template-or-module-impact-not-closed",
        "unverified-html-link-to-changed-source",
    ]
    assert row["decision"] == "refetch-required"


@pytest.mark.parametrize("revision", [
    "changed-source-revision", "new-page-id",
    "parent-revision-unknown", "current-revision-unknown", "missing-page-id",
])
def test_change_or_unknown_revision_cannot_reuse(revision):
    state = _inputs(revision=revision)
    row = _assess(state)["decisions"][0]
    assert "same-source-revision-not-established" in row["unmetConditions"]
    assert "source-revision-or-identity-transition" in row["observedRiskSignals"]


@pytest.mark.parametrize("relationship", [
    None, "api-unavailable", "identity-unresolved", "missing-page-id",
])
def test_partial_missing_or_unknown_relationships_block(relationship):
    state = _inputs(relationship=relationship)
    if relationship is None:
        del state[0]["pageRelationships"]
    row = _assess(state)["decisions"][0]
    assert "page-relationship-observation-incomplete" in row["unmetConditions"]


def test_all_current_pages_are_covered_but_historical_are_excluded():
    state = _inputs()
    page, comparisons, invalidation, coverage, recent = state
    other = copy.deepcopy(page)
    other["page"]["url"] = URL + "/Second"
    other["path"] = "Second.html"
    other["sourceIdentity"]["pageId"] = 8
    other["pageRelationships"] = {"status": "api-unavailable"}
    second = copy.deepcopy(comparisons[0])
    second.update(pageUrl=other["page"]["url"], pageId=8)
    archive = [
        {"kind": "historical-page", "path": "_history/retired/4/old.html"},
        {"kind": "revision", "path": "_history/oldid/123.html"},
        other, page,
    ]
    result = assess_render_reuse(
        archive, revision_comparisons=[second, *comparisons],
        invalidation=invalidation, source_time=coverage, recent_changes=recent,
    )
    assert result["currentPageCount"] == result["refetchedPageCount"] == 2
    assert [item["pageUrl"] for item in result["decisions"]] == sorted([
        URL, other["page"]["url"],
    ])


@pytest.mark.parametrize("tamper", [
    "missing-comparison", "duplicate-comparison", "claimed-skip",
    "mismatched-id", "verified-retention", "claimed-atomic",
    "invented-reuse-policy",
])
def test_untrusted_or_inconsistent_reuse_claims_rejected(tamper):
    state = _inputs()
    page, comparisons, invalidation, coverage, recent = state
    if tamper == "missing-comparison":
        comparisons.clear()
    elif tamper == "duplicate-comparison":
        comparisons.append(copy.deepcopy(comparisons[0]))
    elif tamper == "claimed-skip":
        comparisons[0]["htmlRefetched"] = False
    elif tamper == "mismatched-id":
        comparisons[0]["pageId"] = 99
    elif tamper == "verified-retention":
        recent["retentionCoverage"] = "verified"
    elif tamper == "claimed-atomic":
        coverage["consistency"] = "verified-atomic"
    else:
        invalidation["renderReusePolicy"] = "skip-html-on-same-revision"
    with pytest.raises(ValueError):
        _assess(state)


def test_missing_change_window_never_promotes_to_reuse():
    state = _inputs()
    state[2]["absentCurrentPageIdCount"] = 1
    state[2]["unresolvedPageUrlAssociationCount"] = 1
    row = _assess(state)["decisions"][0]
    assert "unresolved-page-identity-or-absence" in row["observedRiskSignals"]
    assert "recentchanges-retention-unverified" in row["unmetConditions"]
