"""P2.4c: deterministic, fail-closed rendered-HTML reuse decisions.

This assessment is *after* a complete crawl. It documents why the current
producer may not omit an HTML body request and is independently reproducible
from the published update evidence. It is not a source-side freshness proof,
and may not be used to authorize transport or archive-member reuse.
"""

from __future__ import annotations

from typing import Any, Iterable

GATE_FORMAT = "InfinityDB wiki rendered HTML reuse assessment"
GATE_VERSION = 1

# These missing proofs cannot be satisfied by the producer's current APIs.
# A future opt-in fast path requires a separately versioned gate, source-side
# representation validation and independently checked rewrite equivalence.
GLOBAL_BLOCKERS = (
    "category-and-index-consistency-unverified",
    "dependency-closure-unverified",
    "original-rendered-response-unvalidated",
    "recentchanges-retention-unverified",
    "rewrite-equivalence-unverified",
    "source-time-not-atomic",
)


def assess_render_reuse(
    members: Iterable[dict[str, Any]],
    *,
    revision_comparisons: list[dict[str, Any]],
    invalidation: dict[str, Any],
    source_time: dict[str, Any],
    recent_changes: dict[str, Any],
) -> dict[str, Any]:
    """Recompute conservative page decisions from current snapshot evidence.

    A same-source-revision observation is *never* sufficient for reuse.
    Observed API relationships are not known to be render-time direct or
    transitive closure. Even a complete RecentChanges response has no proven
    retention horizon. Rewritten HTML has no verified source-body replay.
    """
    pages = sorted(
        (row for row in members if row["kind"] == "current-page"),
        key=lambda row: row["page"]["url"],
    )
    comparison_by_url = {
        row["pageUrl"]: row for row in revision_comparisons
    }
    if (len(comparison_by_url) != len(revision_comparisons)
            or set(comparison_by_url) != {page["page"]["url"] for page in pages}):
        raise ValueError("Incomplete rendered HTML page comparison coverage")
    if (invalidation.get("renderReusePolicy") != "refetch-all-current-html"
            or invalidation.get("recentChangesRetention") != "unverified"
            or recent_changes.get("retentionCoverage") != "unverified"
            or source_time.get("consistency") != "not-verified-atomic"
            or source_time.get("renderDependencies") != "unverified"):
        raise ValueError("Unsupported source-time or render reuse evidence")

    def candidate_urls(field: str) -> set[str]:
        return {item["pageUrl"] for item in invalidation[field]}

    link_candidates = candidate_urls("unverifiedLinkCandidates")
    transclusion_candidates = candidate_urls("apiObservedTransclusionCandidates")
    category_candidates = candidate_urls("apiObservedCategoryCandidates")
    namespace_changes = bool(
        invalidation["observedTemplateChangeRcids"]
        or invalidation["observedModuleChangeRcids"]
    )
    category_or_index = bool(
        invalidation["observedCategoryChangeRcids"]
        or invalidation["observedCategorizationRcids"]
        or invalidation["observedIndexChangeRcids"]
    )
    ambiguous_identity = bool(
        invalidation["unresolvedPageUrlAssociationCount"]
        or invalidation["absentCurrentPageIdCount"]
    )

    decisions = []
    for page in pages:
        url = page["page"]["url"]
        comparison = comparison_by_url[url]
        if (comparison["pageId"] != page["sourceIdentity"]["pageId"]
                or comparison["htmlRefetched"] is not True):
            raise ValueError("Rendered HTML was not recorded as fully refetched")
        blockers = set(GLOBAL_BLOCKERS)
        risk = set()
        if comparison["comparison"] != "same-source-revision":
            blockers.add("same-source-revision-not-established")
            risk.add("source-revision-or-identity-transition")
        relationship = page.get("pageRelationships")
        if not isinstance(relationship, dict) or relationship["status"] != "observed":
            blockers.add("page-relationship-observation-incomplete")
        if url in link_candidates:
            risk.add("unverified-html-link-to-changed-source")
        if url in transclusion_candidates:
            risk.add("api-observed-transclusion-change-candidate")
        if url in category_candidates:
            risk.add("api-observed-category-change-candidate")
        if namespace_changes:
            risk.add("template-or-module-impact-not-closed")
        if category_or_index:
            risk.add("category-or-index-change-activity")
        if ambiguous_identity:
            risk.add("unresolved-page-identity-or-absence")
        decisions.append({
            "pageUrl": url,
            "memberPath": page["path"],
            "pageId": page["sourceIdentity"]["pageId"],
            "revisionComparison": comparison["comparison"],
            "decision": "refetch-required",
            "htmlRefetched": True,
            "unmetConditions": sorted(blockers),
            "observedRiskSignals": sorted(risk),
        })

    return {
        "format": GATE_FORMAT,
        "version": GATE_VERSION,
        "scope": "post-crawl-observations-not-preflight-authorization",
        "reuseAuthorized": False,
        "currentPageCount": len(pages),
        "refetchedPageCount": len(decisions),
        "skippedHtmlBodyCount": 0,
        "decisions": decisions,
    }
