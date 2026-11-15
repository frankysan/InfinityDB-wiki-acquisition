"""Source-authority review is verified, source-specific and always fail-closed."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from infinity_wiki_acquisition.render_freshness_authority import (
    SOURCE_BLOCKERS, assess_html_source_authority,
)
from test_render_freshness_audit import _updated_capture
from test_source_replay import _updated
from test_snapshot_update import NEW_TIME, ROOT, ROOT_NEW, SITE, LANG


def _args(result):
    return (
        result.source_replay, result.archive, result.manifest, result.inventory,
    )


def test_parent_with_eligible_transport_still_has_no_site_authority(
    tmp_path, monkeypatch,
):
    parent = _updated_capture(tmp_path / "capture", monkeypatch)
    report = assess_html_source_authority(*_args(parent))
    assert report["site"] == SITE.key and report["language"] == LANG
    assert report["sourceContract"] == "not-established-for-this-deployment"
    assert report["currentPageCount"] == report["refetchRequiredPageCount"] == 3
    assert report["skippedHtmlBodyCount"] == 0
    assert report["reuseAuthorized"] is False
    assert report["observedValidatorCollisionCount"] == 0
    assert report["differentialAudit"] is None
    row = next(item for item in report["decisions"] if item["pageUrl"] == ROOT)
    assert row["transportProbeStatus"] == "transport-probe-candidate"
    assert row["decision"] == "refetch-required"
    assert set(SOURCE_BLOCKERS) <= set(row["unmetAuthorityConditions"])
    assert row["reuseAuthorized"] is False


def test_colliding_etag_different_html_is_a_source_wide_negative_signal(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    changed = ROOT_NEW.replace(
        b"</body>", b"<p>new template rendering</p></body>",
    )
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        root_body=changed, when=NEW_TIME + timedelta(days=1),
    )
    report = assess_html_source_authority(
        *_args(second), previous=_args(first),
    )
    assert report["observedValidatorCollisionCount"] == 1
    assert report["differentialAudit"]["lineage"] == "direct-parent"
    assert report["differentialAudit"]["equalStrongEtagDifferentBodyCount"] == 1
    root = next(row for row in report["decisions"] if row["pageUrl"] == ROOT)
    assert root["sourceValidatorCollisionObserved"] is True
    assert "observed-same-etag-different-html" in root["unmetAuthorityConditions"]
    assert all(
        "source-site-validator-collision-detected" in row["unmetAuthorityConditions"]
        for row in report["decisions"]
    )
    assert report["reuseAuthorized"] is False
    assert report["skippedHtmlBodyCount"] == 0


def test_changed_etag_without_collision_does_not_promote_site_to_authoritative(
    tmp_path, monkeypatch,
):
    first = _updated_capture(tmp_path / "first", monkeypatch)
    second = _updated_capture(
        tmp_path / "second", monkeypatch,
        parent=(first.archive, first.manifest, first.inventory),
        root_body=ROOT_NEW + b"<!-- changed source -->",
        etag='"changed"', when=NEW_TIME + timedelta(days=1),
    )
    report = assess_html_source_authority(
        *_args(second), previous=_args(first),
    )
    assert report["observedValidatorCollisionCount"] == 0
    assert report["differentialAudit"]["equalStrongEtagDifferentBodyCount"] == 0
    assert report["reuseAuthorized"] is False
    assert all(row["decision"] == "refetch-required" for row in report["decisions"])


def test_unknown_metadata_blocks_even_transport_preflight(tmp_path, monkeypatch):
    parent, _ = _updated(tmp_path, monkeypatch, strong=True)
    report = assess_html_source_authority(*_args(parent))
    assert report["currentPageCount"] == 3
    assert all(
        row["transportProbeStatus"] == "full-html-fetch-required"
        for row in report["decisions"]
    )
    assert all(
        "versioned-transport-profile-unavailable" in row["unmetAuthorityConditions"]
        for row in report["decisions"]
    )


def test_unverifiable_source_replay_does_not_produce_an_authority_assessment(
    tmp_path, monkeypatch,
):
    parent = _updated_capture(tmp_path / "capture", monkeypatch)
    with pytest.raises((ValueError, OSError)):
        assess_html_source_authority(
            parent.source_replay, parent.archive, parent.manifest,
            tmp_path / "invented-inventory.json",
        )


def test_previous_bundle_must_be_a_complete_verified_tuple(
    tmp_path, monkeypatch,
):
    parent = _updated_capture(tmp_path / "capture", monkeypatch)
    with pytest.raises(ValueError, match="exactly four"):
        assess_html_source_authority(*_args(parent), previous=(parent.archive,))


def test_v2_historical_only_pages_cannot_become_current_reuse_candidates(
    tmp_path, monkeypatch,
):
    from test_historical_only import _retired_update
    from test_snapshot_update import DELETED

    previous, updated = _retired_update(
        tmp_path, monkeypatch,
        source_replay_directory=tmp_path / "v2-source-replay",
    )
    result = assess_html_source_authority(*_args(updated))
    assert result["currentPageCount"] == 3
    assert DELETED not in {item["pageUrl"] for item in result["decisions"]}
    assert result["reuseAuthorized"] is False


def test_cli_emits_json_and_refuses_nonexistent_artifacts(
    tmp_path, monkeypatch, capsys,
):
    from tools.audit_render_authority import main

    parent = _updated_capture(tmp_path / "capture", monkeypatch)
    capsys.readouterr()
    assert main([str(item) for item in _args(parent)]) == 0
    actual = json.loads(capsys.readouterr().out)
    assert actual == assess_html_source_authority(*_args(parent))
    assert actual["reuseAuthorized"] is False
    assert main([str(item) for item in _args(parent)[:3]]
                + ["not-a-file.json"]) == 1
    assert "ERROR:" in capsys.readouterr().err
