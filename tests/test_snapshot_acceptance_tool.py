"""Focused offline checks for the reusable snapshot acceptance command."""

from __future__ import annotations

import json

from tools.verify_snapshot_acceptance import assess, main
from test_rules_portable_layout import _legacy_parent


def test_acceptance_verifies_snapshot_and_reports_page_id_coverage(
    tmp_path, capsys,
):
    triple = _legacy_parent(tmp_path)
    report = assess(*triple)
    assert report["snapshotVerification"] == "PASS"
    assert report["memberCount"] == 2
    assert report["kinds"] == {"current-page": 2}
    assert report["unknownMembers"] == []
    assert report["mainNamespaceIdsRecorded"] == 1
    assert report["currentPagesWithoutNumericId"] == 1
    assert report["sourceReplayVerification"] == "not-requested"
    assert main([str(path) for path in triple]) == 0
    assert json.loads(capsys.readouterr().out)["memberCount"] == 2


def test_acceptance_requires_proven_child_lineage_when_parent_is_supplied(
    tmp_path, capsys,
):
    triple = _legacy_parent(tmp_path)
    assert main([
        *[str(path) for path in triple],
        "--parent", *[str(path) for path in triple],
    ]) == 1
    assert "lacks parent lineage" in capsys.readouterr().err
