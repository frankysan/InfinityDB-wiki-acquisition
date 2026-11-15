"""P2.5c operator acceptance across both archive-derived resume workflows."""

from __future__ import annotations

import zipfile

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_provenance import sha256_file
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from test_snapshot_upgrade import _install_history_http, _source
from test_snapshot_update import _build_parent, _remote


@pytest.mark.parametrize("mode", ["history-upgrade", "current-refresh"])
def test_publication_failure_reports_recovery_path_and_replays_without_network(
    tmp_path, monkeypatch, capsys, mode,
):
    import infinity_wiki_acquisition.snapshot_upgrade as upgrade_module
    import infinity_wiki_acquisition.snapshot_update as update_module

    if mode == "history-upgrade":
        parent = _source(tmp_path)
        requested = _install_history_http(monkeypatch)
        target_module = upgrade_module
        mode_args = ["--from-snapshot", str(parent[0]), "--include-history"]
    else:
        parent = _build_parent(tmp_path)
        requested = _remote(monkeypatch, changed=True)
        target_module = update_module
        mode_args = ["--update-from", str(parent[0])]

    original_parent_hashes = tuple(sha256_file(file) for file in parent)
    main_args = [
        *mode_args, "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]),
        "--root", str(tmp_path / "published"),
        "--manifest-dir", str(tmp_path / "child-manifests"),
        "--work-root", str(tmp_path / "work"),
    ]

    original_publisher = target_module._publish_verified_inventory

    def interrupted_publication(*args, **kwargs):
        raise ValueError("synthetic final publication interruption")

    monkeypatch.setattr(
        target_module, "_publish_verified_inventory", interrupted_publication,
    )
    assert cli.main(main_args) == 1
    err = capsys.readouterr().err
    work = next((tmp_path / "work").glob("*.work"))
    assert "synthetic final publication interruption" in err
    assert f"Preserved wiki work -> {work}" in err
    assert work.is_dir()
    assert not list((tmp_path / "published").glob("*.zip"))
    assert not list((tmp_path / "child-manifests").rglob("*.json"))

    monkeypatch.setattr(
        target_module, "_publish_verified_inventory", original_publisher,
    )
    requested.clear()
    assert cli.main([*main_args, "--resume-work", str(work)]) == 0
    assert requested == [], "Completed acquisition must not be repeated"
    assert not work.exists()

    created = list((tmp_path / "published").glob("*.zip"))
    inventories = list((tmp_path / "child-manifests" / "inventories").glob("*.json"))
    manifests = [
        file for file in (tmp_path / "child-manifests").glob("*.json")
        if not file.name.endswith(".inventory.json")
    ]
    assert len(created) == len(inventories) == len(manifests) == 1
    verified = verify_snapshot(created[0], manifests[0], inventories[0])
    assert verified["parent"]["archiveName"] == parent[0].name
    assert verified["parent"]["archiveSha256"] == original_parent_hashes[0]
    assert verified["parent"]["manifestSha256"] == original_parent_hashes[1]
    assert verified["parent"]["inventorySha256"] == original_parent_hashes[2]
    assert tuple(sha256_file(file) for file in parent) == original_parent_hashes
    assert verify_snapshot(*parent)["history"] is False
    assert verified["history"] is (mode == "history-upgrade")

    with zipfile.ZipFile(created[0]) as archive:
        members = archive.namelist()
        assert members
        assert not any(
            entry.startswith((
                ".wiki-", ".wiki/", "local/", "logs/",
            )) for entry in members
        )
        assert not any(
            token in entry
            for entry in members
            for token in (
                "archive-run", "recentchanges", "asset-decisions",
                "history-checkpoint", "crawl-checkpoint",
            )
        )
    if mode == "current-refresh":
        gate = verified["captureMetadata"]["renderReuseGate"]
        assert gate["skippedHtmlBodyCount"] == 0
        assert gate["refetchedPageCount"] == gate["currentPageCount"]


def test_archive_derived_resume_help_explains_both_modes():
    from infinity_wiki_acquisition.cli import parse_args

    history = parse_args([
        "--from-snapshot", "parent.zip", "--include-history",
        "--resume-work", "staging.work",
    ])
    update = parse_args([
        "--update-from", "parent.zip", "--resume-work", "staging.work",
    ])
    assert history.resume_work.name == update.resume_work.name == "staging.work"
    assert history.from_snapshot is not None
    assert update.update_from is not None
