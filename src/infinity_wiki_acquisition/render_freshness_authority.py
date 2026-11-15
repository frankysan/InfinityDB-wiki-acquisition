"""Read-only source-side rendering authority assessment.

The existing snapshot and replay verifiers establish archive provenance, not
whether the remote site can serve a dependency-fresh rendered page. Generic
HTTP or MediaWiki documentation cannot substitute for a site-specific,
versioned source-side validation contract. This gate is intentionally
non-authorizing until that contract exists and is independently verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FORMAT = "InfinityDB wiki rendered source authority assessment"
VERSION = 1

# Fixed requirements, not self-reported source claims. A future separately
# versioned positive gate will need to verify each contract externally.
SOURCE_BLOCKERS = (
    "source-specific-strong-render-validator-semantics-unverified",
    "asynchronous-template-module-and-category-invalidation-unbounded",
    "source-render-time-and-dependency-closure-unverified",
    "recentchanges-retention-horizon-unverified",
    "intermediary-cache-coherence-unverified",
)


def assess_html_source_authority(
    bundle: Path, archive: Path, manifest: Path, inventory: Path,
    *,
    previous: tuple[Path, Path, Path, Path] | None = None,
) -> dict[str, Any]:
    """Verify artifacts, expose blocked proof obligations and real collisions.

    The optional previous tuple is (replay, ZIP, manifest, inventory).
    A collision in any comparable HTML pair is a site-wide negative signal.
    A zero-collision audit is not affirmative evidence of correct ETags.
    """
    from infinity_wiki_acquisition.html_transport import (
        prepare_html_transport_probes,
    )
    from infinity_wiki_acquisition.render_freshness_audit import (
        compare_rendered_captures,
    )
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

    transport = prepare_html_transport_probes(bundle, archive, manifest, inventory)
    current = verify_snapshot(archive, manifest, inventory)
    audit = None
    if previous is not None:
        if len(previous) != 4:
            raise ValueError("Previous capture requires exactly four verified artifacts")
        audit = compare_rendered_captures(
            *previous, bundle, archive, manifest, inventory,
        )
        if (audit["current"]["archiveSha256"] != transport["sourceArchiveSha256"]
                or audit["current"]["manifestSha256"]
                != transport["sourceManifestSha256"]
                or audit["current"]["inventorySha256"]
                != transport["sourceInventorySha256"]
                or audit["current"]["sourceReplaySha256"]
                != transport["sourceReplaySha256"]):
            raise ValueError("Previous/current authority evidence identity mismatch")

    collisions = set()
    if audit is not None:
        collisions = {
            item["pageUrl"] for item in audit["pageObservations"]
            if item["assessment"] == "equal-etag-different-source-body"
        }

    members = {
        item["page"]["url"]: item
        for item in current["members"] if item["kind"] == "current-page"
    }
    current_gate = current["captureMetadata"].get("renderReuseGate")
    existing = (
        {row["pageUrl"]: row for row in current_gate["decisions"]}
        if isinstance(current_gate, dict) else {}
    )
    observations = []
    for probe in transport["decisions"]:
        url = probe["pageUrl"]
        member = members[url]
        blockers = set(SOURCE_BLOCKERS)
        blockers.update(probe["unmetTransportConditions"])
        if url in collisions:
            blockers.add("observed-same-etag-different-html")
        if collisions:
            blockers.add("source-site-validator-collision-detected")
        if current_gate is None:
            blockers.add("current-snapshot-update-invalidation-audit-unavailable")
        revision = member.get("currentRevision")
        if not isinstance(revision, dict) or revision.get("status") != "observed":
            blockers.add("current-page-revision-identity-unverified")
        relationships = member.get("pageRelationships")
        if not isinstance(relationships, dict) or relationships.get("status") != "observed":
            blockers.add("page-template-category-observation-incomplete")
        observations.append({
            "pageUrl": url,
            "memberPath": probe["memberPath"],
            "transportProbeStatus": probe["status"],
            "sourceValidatorCollisionObserved": url in collisions,
            "invalidationSignals": (
                existing[url]["observedRiskSignals"] if url in existing else []
            ),
            "unmetAuthorityConditions": sorted(blockers),
            "decision": "refetch-required",
            "reuseAuthorized": False,
        })
    return {
        "format": FORMAT, "version": VERSION,
        "scope": "source-contract-not-verified-no-render-reuse",
        "site": current["site"], "language": current["language"],
        "sourceArchiveSha256": transport["sourceArchiveSha256"],
        "sourceManifestSha256": transport["sourceManifestSha256"],
        "sourceInventorySha256": transport["sourceInventorySha256"],
        "sourceReplaySha256": transport["sourceReplaySha256"],
        "sourceContract": "not-established-for-this-deployment",
        "renderInvalidation": "not-verified-fresh",
        "recentChangesRetention": "unverified",
        "sourceTimeAtomicity": "not-verified",
        "differentialAudit": (
            {
                "previous": audit["previous"],
                "current": audit["current"],
                "lineage": audit["lineage"],
                "equalStrongEtagDifferentBodyCount": audit[
                    "equalStrongEtagDifferentBodyCount"
                ],
            } if audit is not None else None
        ),
        "observedValidatorCollisionCount": len(collisions),
        "currentPageCount": len(observations),
        "refetchRequiredPageCount": len(observations),
        "skippedHtmlBodyCount": 0,
        "reuseAuthorized": False,
        "decisions": observations,
    }
