"""Conservative, reproducible source-time/coverage assessment.

This is an audit of *reported* acquisition evidence, never a freshness
certificate. MediaWiki's latest revision timestamp can postdate an earlier
HTML fetch, but a revision ID does not bind rendered HTML to templates.
RecentChanges retention cannot be proven by exhausting pagination.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Source observation timestamp must have a timezone")
    return result


def assess_source_time_coverage(
    members: Iterable[dict[str, Any]],
    *,
    recent_changes: dict[str, Any] | None = None,
    page_moves: list[dict[str, Any]] | None = None,
    absent_page_ids: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize risks without promoting optional metadata to verified freshness.

    Rows with missing revision data are counted as unresolved. A latest
    revision timestamp strictly after the earlier HTML response is a risk
    signal, not a proof of exactly which content was rendered.
    """
    page_count = 0
    observed = 0
    missing_html_time = 0
    unknown_revision = 0
    later_revision = 0
    scan_close = (
        _time(recent_changes["requestedThrough"])
        if recent_changes is not None else None
    )
    html_after_scan = 0
    revision_query_after_scan = 0
    for member in members:
        if member["kind"] != "current-page":
            continue
        page_count += 1
        revision = member.get("currentRevision")
        if revision is None:
            unknown_revision += 1
            missing_html_time += 1
            continue
        fetched = revision["htmlFetchedAt"]
        if fetched is None:
            missing_html_time += 1
        elif scan_close is not None and _time(fetched) > scan_close:
            html_after_scan += 1
        requested = revision["requestedAt"]
        if requested is not None and scan_close is not None and (
            _time(requested) > scan_close
        ):
            revision_query_after_scan += 1
        if revision["status"] != "observed":
            unknown_revision += 1
            continue
        observed += 1
        if fetched is not None and (
            _time(revision["revisionTimestamp"]) > _time(fetched)
        ):
            later_revision += 1

    moves = page_moves or []
    missing = absent_page_ids or []
    return {
        "scope": "observations-only",
        "consistency": "not-verified-atomic",
        "renderDependencies": "unverified",
        "recentChangesRetention": (
            "unverified" if recent_changes is not None else "not-observed"
        ),
        "pageCount": page_count,
        "observedRevisionCount": observed,
        "unknownRevisionCount": unknown_revision,
        "missingHtmlFetchTimeCount": missing_html_time,
        "revisionTimestampAfterHtmlFetchCount": later_revision,
        "htmlFetchedAfterRecentChangesWindowCount": html_after_scan,
        "revisionQueriedAfterRecentChangesWindowCount": revision_query_after_scan,
        "unresolvedPageUrlAssociationCount": sum(
            row.get("interpretation") == "unresolved-url-association"
            for row in moves
        ),
        "unresolvedAbsentPageIdCount": len(missing),
    }
