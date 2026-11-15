"""Offline P2.3b.4 source-time and coverage assessments.

A revision timestamp later than an HTML response is a risk indicator, not
a proof of which revision rendered the response. The source-wide snapshot
is never certified atomic by these optional metadata observations.
"""

from __future__ import annotations

import json

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)
from infinity_wiki_acquisition.source_time_coverage import (
    assess_source_time_coverage,
)
from test_current_revisions import _revision_api
from test_snapshot_update import _build_parent, _remote, _update


def _page(url, *, status="observed", html="2026-11-15T16:00:00Z",
          requested="2026-11-15T16:05:00Z",
          revision="2026-11-15T16:02:00Z"):
    observed = status == "observed"
    return {
        "kind": "current-page",
        "currentRevision": {
            "status": status,
            "htmlFetchedAt": html,
            "requestedAt": requested,
            "revisionTimestamp": revision if observed else None,
        },
        "page": {"url": url},
    }


def test_concurrent_revision_timestamp_and_recentchanges_window_gaps():
    changes = {
        "requestedThrough": "2026-11-15T15:59:00Z",
        "retentionCoverage": "unverified",
    }
    members = [
        _page("https://test.invalid/Page_A"),
        _page("https://test.invalid/Page_B", revision="2026-11-15T15:00:00Z"),
        _page("https://test.invalid/Page_C", status="api-unavailable", html=None),
    ]
    audit = assess_source_time_coverage(
        members,
        recent_changes=changes,
        page_moves=[
            {"interpretation": "unresolved-url-association"},
            {"interpretation": "move-corroborated-by-recentchanges"},
        ],
        absent_page_ids=[{"pageId": 3}],
    )
    assert audit == {
        "scope": "observations-only",
        "consistency": "not-verified-atomic",
        "renderDependencies": "unverified",
        "recentChangesRetention": "unverified",
        "pageCount": 3,
        "observedRevisionCount": 2,
        "unknownRevisionCount": 1,
        "missingHtmlFetchTimeCount": 1,
        "revisionTimestampAfterHtmlFetchCount": 1,
        "htmlFetchedAfterRecentChangesWindowCount": 2,
        "revisionQueriedAfterRecentChangesWindowCount": 3,
        "unresolvedPageUrlAssociationCount": 1,
        "unresolvedAbsentPageIdCount": 1,
    }


def test_unobserved_revision_and_no_recentchanges_stay_unknown():
    pages = [
        {"kind": "current-page", "page": {"url": "a"}},
        _page("b", status="identity-unresolved", html=None, requested=None),
        {"kind": "asset"},
    ]
    audit = assess_source_time_coverage(pages)
    assert audit["pageCount"] == 2
    assert audit["observedRevisionCount"] == 0
    assert audit["unknownRevisionCount"] == 2
    assert audit["missingHtmlFetchTimeCount"] == 2
    assert audit["recentChangesRetention"] == "not-observed"
    assert audit["revisionTimestampAfterHtmlFetchCount"] == 0
    assert audit["consistency"] == "not-verified-atomic"


def test_snapshot_update_exposes_audited_concurrency_risks_and_verifier_checks(
    tmp_path, monkeypatch,
):
    parent = _build_parent(tmp_path)
    _remote(monkeypatch, changed=False)
    _revision_api(monkeypatch)
    child = _update(parent, tmp_path)
    inventory = verify_snapshot(
        child.archive, child.manifest, child.inventory,
    )
    audit = inventory["captureMetadata"]["sourceTimeCoverage"]
    assert audit["scope"] == "observations-only"
    assert audit["consistency"] == "not-verified-atomic"
    assert audit["renderDependencies"] == "unverified"
    assert audit["recentChangesRetention"] == "unverified"
    assert audit["pageCount"] == 3
    assert audit["observedRevisionCount"] == 3
    # RecentChanges is requested through the update's start time.
    # No claim can be made about the later crawl/API observation window.
    assert audit["revisionQueriedAfterRecentChangesWindowCount"] == 3

    original = json.loads(child.inventory.read_text(encoding="utf-8"))
    for tamper in ("atomic", "retention", "count", "dependencies"):
        changed = json.loads(json.dumps(original))
        modified = changed["captureMetadata"]["sourceTimeCoverage"]
        if tamper == "atomic":
            modified["consistency"] = "verified-atomic"
        elif tamper == "retention":
            modified["recentChangesRetention"] = "complete"
        elif tamper == "count":
            modified["observedRevisionCount"] = 0
        else:
            modified["renderDependencies"] = "verified"
        child.inventory.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(SnapshotVerificationError, match="Source-time coverage"):
            verify_snapshot(child.archive, child.manifest, child.inventory)


def test_snapshot_without_new_coverage_fields_remains_valid(tmp_path, monkeypatch):
    parent = _build_parent(tmp_path)
    checked = verify_snapshot(*parent)
    assert "sourceTimeCoverage" not in checked["captureMetadata"]
