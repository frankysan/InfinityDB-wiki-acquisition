"""P2.5a archive-derived history upgrade recovery with bound private work."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.archive_run import FILENAME, open_archive_run
from infinity_wiki_acquisition.snapshot_provenance import sha256_file
from infinity_wiki_acquisition.snapshot_upgrade import (
    SnapshotUpgradeIncomplete, upgrade_snapshot_with_history,
)
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot
from test_snapshot_upgrade import (
    _NEW_TIME, _install_history_http, _source,
)


def _upgrade(parent, root: Path, **kwargs):
    return upgrade_snapshot_with_history(
        *parent, archive_directory=root / "output",
        manifest_directory=root / "manifests",
        work_root=root / "work",
        now=_NEW_TIME + timedelta(minutes=1),
        **kwargs,
    )


def test_resume_failed_history_upgrade_skips_completed_oldid_and_enumeration(
    tmp_path: Path, monkeypatch,
):
    parent = _source(tmp_path)
    original_bytes = [file.read_bytes() for file in parent]
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as captured:
        _upgrade(parent, tmp_path)
    work = captured.value.work
    assert (work / FILENAME).is_file()
    assert (work / "_history/oldid/101.html").is_file()
    assert not (work / "_history/oldid/102.html").exists()
    receipt = json.loads((work / FILENAME).read_text(encoding="utf-8"))
    assert receipt["mode"] == "history-upgrade"
    assert receipt["parentArchiveSha256"] == sha256_file(parent[0])
    assert receipt["parentManifestSha256"] == sha256_file(parent[1])
    assert receipt["parentInventorySha256"] == sha256_file(parent[2])

    calls = _install_history_http(monkeypatch)
    result = _upgrade(parent, tmp_path, resume_work=work)
    assert len(calls) == 1
    assert "oldid=102" in calls[0]
    assert result.revision_count == 2
    assert result.reused_members == 2
    assert verify_snapshot(result.archive, result.manifest, result.inventory)["history"]
    assert [file.read_bytes() for file in parent] == original_bytes
    assert not work.exists()


def test_resume_bound_parent_rejects_other_valid_parent_before_network(
    tmp_path: Path, monkeypatch,
):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    prior = _source(a)
    other = _source(b, site=cli.HUMAN_SPHERE_SITE)
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as captured:
        _upgrade(prior, a)
    work = captured.value.work
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("network must not be accessed"),
    )
    with pytest.raises(ValueError, match="site mismatch|another site|resume differs"):
        _upgrade(other, a, resume_work=work)
    assert work.exists()


def test_resume_refuses_tampered_parent_work_bytes_without_refetch(
    tmp_path: Path, monkeypatch,
):
    parent = _source(tmp_path)
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as captured:
        _upgrade(parent, tmp_path)
    work = captured.value.work
    member = work / "index.html"
    content = member.read_bytes()
    member.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("network must not be accessed"),
    )
    with pytest.raises(ValueError, match="Existing parent work member differs"):
        _upgrade(parent, tmp_path, resume_work=work)
    assert not list((tmp_path / "output").glob("*.zip"))


def test_resumed_upgrades_reject_invalid_parent_receipt_before_network(
    tmp_path: Path, monkeypatch,
):
    parent = _source(tmp_path)
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as captured:
        _upgrade(parent, tmp_path)
    work = captured.value.work
    receipt = work / FILENAME
    record = json.loads(receipt.read_text())
    record["parentInventorySha256"] = "0" * 64
    receipt.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("network must not be accessed"),
    )
    with pytest.raises(ValueError, match="resume differs"):
        _upgrade(parent, tmp_path, resume_work=work)


def test_resume_rejects_missing_receipt_and_mismatched_options(
    tmp_path: Path, monkeypatch,
):
    parent = _source(tmp_path)
    work = tmp_path / "unrelated"
    work.mkdir()
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("network must not be accessed"),
    )
    with pytest.raises(ValueError, match="Missing or invalid"):
        _upgrade(parent, tmp_path, resume_work=work)

    open_archive_run(
        work, *parent, mode="current-refresh",
        site=cli.DEFAULT_SITE.key, language="en",
        options={"allowHistoricalOnly": False}, resume=False, started_at=_NEW_TIME,
    )
    with pytest.raises(ValueError, match="resume differs"):
        _upgrade(parent, tmp_path, resume_work=work)


def test_cli_supports_history_upgrade_resume_path(tmp_path: Path, monkeypatch, capsys):
    parent = _source(tmp_path)
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete) as captured:
        _upgrade(parent, tmp_path)
    work = captured.value.work
    _install_history_http(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main([
        "--from-snapshot", str(parent[0]), "--from-manifest", str(parent[1]),
        "--from-inventory", str(parent[2]), "--include-history",
        "--resume-work", str(work), "--root", "other-archive",
        "--manifest-dir", "other-manifests",
    ]) == 0
    assert len(list((tmp_path / "other-archive").glob("*.zip"))) == 1
    assert "Reused 2 parent members" in capsys.readouterr().out


def test_history_parent_reuse_accounting_survives_interrupted_upgrade(
    tmp_path: Path, monkeypatch,
):
    from test_history_refresh import (
        PARENT_IDS, _history_parent, _history_source,
    )

    parent = _history_parent(tmp_path, monkeypatch)
    _history_source(monkeypatch, fail_existing=PARENT_IDS.values())

    class InterruptAfterOne:
        calls = 0

        def __call__(self, item):
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _upgrade(parent, tmp_path, progress=InterruptAfterOne())
    work = next((tmp_path / "work").glob("*.work"))
    log = work / ".wiki-reused-parent-oldids.jsonl"
    assert log.is_file()
    assert len(log.read_text().splitlines()) == 1

    requests = _history_source(
        monkeypatch, fail_existing=PARENT_IDS.values(),
    )
    result = _upgrade(parent, tmp_path, resume_work=work)
    assert result.reused_revisions == len(PARENT_IDS)
    assert result.revision_count == len(PARENT_IDS)
    assert not any(category == "oldid-body" for category, _ in requests)
    verified = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert verified["captureMetadata"]["reusedRevisionCount"] == len(PARENT_IDS)


def test_resume_refuses_tampered_reused_parent_oldid(
    tmp_path: Path, monkeypatch,
):
    from test_history_refresh import (
        PARENT_IDS, _history_parent, _history_source,
    )

    parent = _history_parent(tmp_path, monkeypatch)
    _history_source(monkeypatch, fail_existing=PARENT_IDS.values())

    class InterruptAfterOne:
        calls = 0

        def __call__(self, item):
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _upgrade(parent, tmp_path, progress=InterruptAfterOne())
    work = next((tmp_path / "work").glob("*.work"))
    oldid_file = next((work / "_history/oldid").glob("*.html"))
    content = oldid_file.read_bytes()
    oldid_file.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    monkeypatch.setattr(
        cli, "fetch_site_bytes",
        lambda *a, **kw: pytest.fail("network must not be accessed"),
    )
    with pytest.raises(ValueError, match="Saved parent oldid does not match"):
        _upgrade(parent, tmp_path, resume_work=work)


def test_history_upgrade_without_resume_still_works(tmp_path: Path, monkeypatch):
    parent = _source(tmp_path)
    _install_history_http(monkeypatch)
    updated = _upgrade(parent, tmp_path)
    assert updated.reused_members == 2
    assert updated.revision_count == 2
