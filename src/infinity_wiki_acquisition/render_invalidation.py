"""P2.4 conservative rendered-HTML invalidation audit for snapshot updates.

RecentChanges and source HTML links are *signals*, not a transclusion graph.
The API window has no verified retention horizon and the post-crawl revision
observations cannot bind HTML to its template, module or category inputs.
This report is reproducible from the published inventory and deliberately
never authorizes skipping rendered HTML.
"""

from __future__ import annotations

from typing import Any, Iterable


_NAMESPACES = {
    "template": ("template:", "plantilla:"),
    "module": ("module:", "módulo:"),
    "category": ("category:", "categoría:"),
}


def _normalized_title(title: str) -> str:
    return title.replace("_", " ").strip().casefold()


def _namespace(title: str) -> str | None:
    normalized = _normalized_title(title)
    for kind, prefixes in _NAMESPACES.items():
        if normalized.startswith(prefixes):
            return kind
    return None


def assess_render_invalidation(
    members: Iterable[dict[str, Any]],
    *,
    recent_changes: dict[str, Any],
    page_moves: list[dict[str, Any]],
    absent_page_ids: list[dict[str, Any]],
    site: Any,
    language: str,
) -> dict[str, Any]:
    """Classify *observed* update signals without claiming render freshness.

    A navigation link to a template/module is not proof of transclusion.
    An empty candidate list does not mean that a page has no dependencies:
    missing, transitive and Scribunto dependencies are all unresolved.
    Category/index observations likewise do not prove category membership or
    a complete RecentChanges retention window. Fresh allpages enumeration
    and current HTML refetch are therefore mandatory for every update.
    """
    from infinity_wiki_acquisition.cli import page_title_from_url

    pages = sorted(
        (row for row in members if row["kind"] == "current-page"),
        key=lambda row: row["page"]["url"],
    )
    event_ids: dict[str, list[int]] = {
        "template": [], "module": [], "category": [],
    }
    categorization_ids: list[int] = []
    index_ids: list[int] = []
    # Preserve exact event IDs in recentChanges.events. Normalized titles
    # are used only for unverified source-link matching.
    title_events: dict[tuple[str, str], set[int]] = {}
    for event in recent_changes["events"]:
        namespace = _namespace(event["title"])
        if namespace is not None:
            event_ids[namespace].append(event["rcid"])
            if namespace in {"template", "module"}:
                title_events.setdefault(
                    (namespace, _normalized_title(event["title"])), set(),
                ).add(event["rcid"])
        if event["type"] == "categorize":
            categorization_ids.append(event["rcid"])
        if (event["type"] in {"new", "categorize"}
                or (event["type"] == "log"
                    and event.get("logtype") in {"move", "delete"})):
            index_ids.append(event["rcid"])

    candidates = []
    for page in pages:
        matched: set[int] = set()
        for link in page.get("references") or []:
            kind = {
                "template-page": "template",
                "module-page": "module",
            }.get(link.get("targetType"))
            if kind is None or link.get("dependencyVerified") is not False:
                continue
            title = page_title_from_url(
                link["targetUrl"], site=site, language=language,
            )
            matched.update(title_events.get((kind, _normalized_title(title)), ()))
        if matched:
            candidates.append({
                "pageUrl": page["page"]["url"],
                "changeRcids": sorted(matched),
                "basis": "unverified-source-html-links",
            })

    return {
        "scope": "observed-signals-only",
        "renderReusePolicy": "refetch-all-current-html",
        "discoveryPolicy": "fresh-allpages-and-linked-pages",
        "directDependencyCoverage": "unknown",
        "transitiveDependencyCoverage": "unknown",
        "moduleInvocationCoverage": "unknown",
        "categoryMembershipCoverage": "unknown",
        "recentChangesRetention": "unverified",
        "currentPageCount": len(pages),
        "observedTemplateChangeRcids": sorted(event_ids["template"]),
        "observedModuleChangeRcids": sorted(event_ids["module"]),
        "observedCategoryChangeRcids": sorted(event_ids["category"]),
        "observedCategorizationRcids": sorted(categorization_ids),
        "observedIndexChangeRcids": sorted(index_ids),
        "unverifiedLinkCandidates": candidates,
        "unresolvedPageUrlAssociationCount": sum(
            row.get("interpretation") == "unresolved-url-association"
            for row in page_moves
        ),
        "absentCurrentPageIdCount": len(absent_page_ids),
    }
