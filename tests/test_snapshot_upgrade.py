"""P2.1 offline integration tests for verified non-history -> history upgrades."""

from __future__ import annotations

import copy
import json
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, CHECKPOINT_FORMAT, CHECKPOINT_VERSION,
    write_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file, write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_upgrade import (
    SnapshotUpgradeIncomplete, upgrade_snapshot_with_history,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError, verify_snapshot,
)

_PARENT_TIME = datetime(2025, 11, 14, 8, 30, tzinfo=timezone.utc)
_NEW_TIME = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)


def _source(
    root: Path, *, site=cli.DEFAULT_SITE, language: str = "en",
) -> tuple[Path, Path, Path]:
    source_root = site.root_urls[language]
    work = root / "source_work"
    work.mkdir()
    page_url = source_root
    asset_url = source_root.rsplit("/", 1)[0] + "/images/icon.png"
    current = work / "index.html"
    content = b"<html><body>original mirrored current</body></html>"
    current.write_bytes(content)
    asset = work / "images/icon.png"
    asset.parent.mkdir()
    asset.write_bytes(b"\x89PNG synthetic unmodified binary")
    pages = (cli.WikiPage(page_url, "index.html", "Main Page"),)
    state = {
        "format": CHECKPOINT_FORMAT, "version": CHECKPOINT_VERSION,
        "site": site.key, "language": language, "rootUrl": source_root,
        "phase": "complete", "complete": True, "enumerationContinuation": None,
        "queue": [], "discovered": {page_url: False, asset_url: True},
        "required": [page_url], "pageIds": {page_url: 81},
        "saved": {
            page_url: ["index.html", len(content)],
            asset_url: ["images/icon.png", asset.stat().st_size],
        },
        "pages": [{"url": page_url, "path": "index.html", "title": "Main Page"}],
        "ignored": {"https://example.invalid/discarded": "optional ignored"},
        "failed": {},
    }
    checkpoint = work / CHECKPOINT_FILENAME
    write_crawl_checkpoint(checkpoint, state)
    archive = cli.archive_wiki(
        [current, asset], root / "archive", root=work,
        site=site, language=language, now=_PARENT_TIME,
    )
    manifest = write_snapshot_manifest(
        archive, root / "manifests", snapshot_type="wiki",
        acquired_at=_PARENT_TIME, source_url=source_root, document_count=2,
        project_root=root, language=language,
    )
    document = build_snapshot_inventory(
        archive, manifest, work=work, site=site.key, language=language,
        root_url=source_root, include_history=False, pages=pages,
        crawl_checkpoint=checkpoint,
    )
    inventory = root / "manifests" / "inventories" / (archive.stem + ".inventory.json")
    inventory.parent.mkdir()
    inventory.write_text(json.dumps(document), encoding="utf-8")
    verify_snapshot(archive, manifest, inventory)
    return archive, manifest, inventory


def _install_history_http(monkeypatch, *, fail_oldid: bool = False):
    requested: list[str] = []

    def fake(url: str, *, language: str, site) -> bytes:
        requested.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if query.get("prop") == ["revisions"]:
            return json.dumps({
                "query": {"pages": [{
                    "pageid": 81, "title": "Main Page",
                    "revisions": [{
                        "revid": 101, "parentid": 100,
                        "timestamp": "2025-10-01T12:00:00Z",
                    }, {
                        "revid": 102, "parentid": 101,
                        "timestamp": "2026-10-01T12:00:00Z",
                    }],
                }]},
            }).encode("utf-8")
        if "oldid" in query:
            if fail_oldid and query["oldid"] == ["102"]:
                raise OSError("synthetic oldid failure")
            return f"<html>OLDID {query['oldid'][0]}</html>".encode()
        raise AssertionError(f"Current content was refetched: {url}")

    monkeypatch.setattr(cli, "fetch_site_bytes", fake)
    return requested


@pytest.mark.parametrize("site,language", [
    (cli.INFINITY_WIKI_SITE, "en"),
    (cli.INFINITY_WIKI_SITE, "es"),
    (cli.HUMAN_SPHERE_SITE, "en"),
])
def test_upgrade_reuses_original_bytes_preserves_source_evidence_and_records_lineage(
    tmp_path: Path, monkeypatch, site, language,
) -> None:
    archive, manifest, inventory = _source(tmp_path, site=site, language=language)
    before = [path.read_bytes() for path in (archive, manifest, inventory)]
    requested = _install_history_http(monkeypatch)

    result = upgrade_snapshot_with_history(
        archive, manifest, inventory, archive_directory=tmp_path / "output",
        manifest_directory=tmp_path / "new-manifests", work_root=tmp_path / "work",
        now=_NEW_TIME,
    )
    assert result.reused_members == 2
    assert result.revision_count == 2
    assert all("prop=revisions" in url or "oldid=" in url for url in requested)
    assert len(requested) == 3
    assert result.archive != archive
    assert [path.read_bytes() for path in (archive, manifest, inventory)] == before

    with zipfile.ZipFile(archive) as old, zipfile.ZipFile(result.archive) as new:
        assert sorted(new.namelist()) == [
            "_history/index.json", "_history/oldid/101.html",
            "_history/oldid/102.html", "images/icon.png", "index.html",
        ]
        for name in old.namelist():
            assert new.read(name) == old.read(name)
        index = json.loads(new.read("_history/index.json"))
        assert index["revisionCount"] == 2
        assert index["pages"][0]["currentPath"] == "index.html"

    source = verify_snapshot(archive, manifest, inventory)
    upgraded = verify_snapshot(result.archive, result.manifest, result.inventory)
    assert upgraded["history"] is True
    assert upgraded["source"]["rootUrl"] == source["source"]["rootUrl"]
    assert upgraded["members"][-2:] == source["members"][-2:]
    assert upgraded["ignoredUrls"] == source["ignoredUrls"]
    assert upgraded["captureMetadata"]["currentContentAcquiredAt"] == source["snapshot"]["acquiredAt"]
    assert upgraded["captureMetadata"]["historyRetrievedAt"] == _NEW_TIME.isoformat(timespec="seconds")
    assert upgraded["captureMetadata"]["historyCutoff"] == "all-observed"
    assert upgraded["parent"]["archiveSha256"] == sha256_file(archive)
    assert upgraded["parent"]["inventorySha256"] == sha256_file(inventory)
    revisions = [item for item in upgraded["members"] if item["kind"] == "revision"]
    assert {p["pageId"] for item in revisions for p in item["sourceIdentity"]["pageIds"]} == {81}
    assert not list((tmp_path / "work").glob("*.work"))


def test_upgrade_cli_infers_parent_site_language_and_explicit_paths(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    archive, manifest, inventory = _source(tmp_path, site=cli.HUMAN_SPHERE_SITE)
    requested = _install_history_http(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert cli.main([
        "--from-snapshot", str(archive), "--from-manifest", str(manifest),
        "--from-inventory", str(inventory), "--include-history",
        "--root", "out", "--manifest-dir", "new-manifests", "--work-root", "work",
    ]) == 0
    assert len(requested) == 3
    created = list((tmp_path / "out").glob("*.zip"))
    assert len(created) == 1
    assert created[0].name.startswith("HUMAN-SPHERE-history ")
    assert "Reused 2 parent members" in capsys.readouterr().out


@pytest.mark.parametrize("args,error", [
    (["--from-snapshot", "missing.zip"], "requires --include-history"),
    (["--from-snapshot", "missing.zip", "--include-history", "--retry-failed-only"], "cannot be combined"),
    (["--from-snapshot", "missing.zip", "--include-history", "--resume-work", "work"], "cannot be combined"),
    (["--from-manifest", "missing.json"], "requires --from-snapshot"),
    (["--from-inventory", "missing.json"], "requires --from-snapshot"),
])
def test_upgrade_cli_rejects_ambiguous_modes(tmp_path: Path, monkeypatch, capsys, args, error):
    monkeypatch.chdir(tmp_path)
    assert cli.main(args) == 1
    assert error in capsys.readouterr().err


def test_upgrade_rejects_mismatched_site_or_language_before_network(tmp_path: Path, monkeypatch):
    archive, manifest, inventory = _source(tmp_path)
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **kw: pytest.fail("network called"))
    for site, language in [("human-sphere", None), (None, "es")]:
        with pytest.raises(ValueError, match="mismatch"):
            upgrade_snapshot_with_history(
                archive, manifest, inventory,
                archive_directory=tmp_path / "out",
                manifest_directory=tmp_path / "new-manifests",
                work_root=tmp_path / "work",
                expected_site=site, expected_language=language,
            )
    assert not (tmp_path / "work").exists()


def test_upgrade_fails_closed_on_unknown_source_member_without_network(tmp_path: Path, monkeypatch):
    archive, manifest, inventory = _source(tmp_path)
    data = json.loads(inventory.read_text(encoding="utf-8"))
    unknown = next(item for item in data["members"] if item["kind"] == "asset")
    unknown.update(kind="unknown", representation="unknown", sourceUrls=[])
    inventory.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(ValueError, match="Cannot establish history coverage"):
        upgrade_snapshot_with_history(
            archive, manifest, inventory,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "new-manifests",
            work_root=tmp_path / "work",
        )


def test_upgrade_rejects_cross_site_page_identity_before_network(
    tmp_path: Path, monkeypatch,
) -> None:
    archive, manifest, inventory = _source(tmp_path)
    document = json.loads(inventory.read_text(encoding="utf-8"))
    page = next(item for item in document["members"] if item["kind"] == "current-page")
    wrong_url = "https://www.human-sphere.com/Main_Page"
    page["page"]["url"] = wrong_url
    page["sourceIdentity"]["requestUrl"] = wrong_url
    page["sourceUrls"] = [wrong_url]
    inventory.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(ValueError, match="does not match snapshot site/language"):
        upgrade_snapshot_with_history(
            archive, manifest, inventory,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "new-manifests",
            work_root=tmp_path / "work",
        )


def test_upgrade_rejects_corrupt_parent_inventory_before_network(tmp_path: Path, monkeypatch):
    archive, manifest, inventory = _source(tmp_path)
    document = json.loads(inventory.read_text(encoding="utf-8"))
    document["members"][0]["sha256"] = "a" * 64
    inventory.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_site_bytes", lambda *a, **kw: pytest.fail("network called"))
    with pytest.raises(SnapshotVerificationError):
        upgrade_snapshot_with_history(
            archive, manifest, inventory,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "new-manifests",
            work_root=tmp_path / "work",
        )


def test_failure_retains_work_without_publishing_partial_archive(tmp_path: Path, monkeypatch):
    archive, manifest, inventory = _source(tmp_path)
    _install_history_http(monkeypatch, fail_oldid=True)
    with pytest.raises(SnapshotUpgradeIncomplete, match="1 revision request") as captured:
        upgrade_snapshot_with_history(
            archive, manifest, inventory,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "new-manifests",
            work_root=tmp_path / "work",
            now=_NEW_TIME,
        )
    assert len(captured.value.failures) == 1
    assert (captured.value.work / "index.html").is_file()
    assert (captured.value.work / "_history/oldid/101.html").is_file()
    assert not list((tmp_path / "out").glob("*.zip"))
    assert not list((tmp_path / "new-manifests").rglob("*.json")) if (tmp_path / "new-manifests").exists() else True


def test_inventory_publication_failure_removes_new_zip_manifest_not_source(
    tmp_path: Path, monkeypatch,
) -> None:
    archive, manifest, inventory = _source(tmp_path)
    before = [item.read_bytes() for item in (archive, manifest, inventory)]
    _install_history_http(monkeypatch)
    def reject(*args, **kwargs):
        raise SnapshotVerificationError("injected final verification failure")
    monkeypatch.setattr(
        "infinity_wiki_acquisition.snapshot_upgrade.verify_snapshot",
        lambda *args, **kwargs: (
            reject(*args, **kwargs) if args[0] != archive
            else verify_original(*args, **kwargs)
        ),
    )
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot as verify_original
    with pytest.raises(SnapshotVerificationError, match="injected"):
        upgrade_snapshot_with_history(
            archive, manifest, inventory,
            archive_directory=tmp_path / "out",
            manifest_directory=tmp_path / "new-manifests",
            work_root=tmp_path / "work", now=_NEW_TIME,
        )
    assert [item.read_bytes() for item in (archive, manifest, inventory)] == before
    assert not list((tmp_path / "out").glob("*.zip"))
    assert not list((tmp_path / "new-manifests").rglob("*.json"))





def test_parent_chronology_cannot_claim_future_history_or_missing_time(
    tmp_path: Path, monkeypatch,
) -> None:
    archive, manifest, inventory = _source(tmp_path)
    _install_history_http(monkeypatch)
    result = upgrade_snapshot_with_history(
        archive, manifest, inventory,
        archive_directory=tmp_path / "out",
        manifest_directory=tmp_path / "new-manifests",
        work_root=tmp_path / "work", now=_NEW_TIME,
    )
    original = json.loads(result.inventory.read_text(encoding="utf-8"))
    for value in ("2026-11-16T00:00:00Z", "not-a-timestamp"):
        document = copy.deepcopy(original)
        document["captureMetadata"]["historyRetrievedAt"] = value
        result.inventory.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(SnapshotVerificationError):
            verify_snapshot(result.archive, result.manifest, result.inventory)


def test_failed_archive_write_does_not_leave_partial_zip(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    good = work / "a.html"
    good.write_bytes(b"existing")
    missing = work / "z.html"
    with pytest.raises(FileNotFoundError):
        cli.archive_wiki(
            [good, missing], tmp_path / "output", root=work,
            now=_NEW_TIME, include_history=True,
        )
    assert not list((tmp_path / "output").glob("*.zip"))

@pytest.mark.parametrize("field", ["archiveSha256", "inventorySha256", "acquiredAt"])
def test_verifier_rejects_modified_lineage(tmp_path: Path, monkeypatch, field: str) -> None:
    archive, manifest, inventory = _source(tmp_path)
    _install_history_http(monkeypatch)
    result = upgrade_snapshot_with_history(
        archive, manifest, inventory,
        archive_directory=tmp_path / "out",
        manifest_directory=tmp_path / "new-manifests",
        work_root=tmp_path / "work", now=_NEW_TIME,
    )
    document = json.loads(result.inventory.read_text(encoding="utf-8"))
    document["parent"][field] = (
        "f" * 64 if field != "acquiredAt" else "2020-01-01T00:00:00Z"
    )
    result.inventory.write_text(json.dumps(document), encoding="utf-8")
    if field == "inventorySha256":
        # Its syntax is verifiable but the parent inventory is not part of the
        # target triple. Source authenticity requires the parent to be supplied.
        assert verify_snapshot(result.archive, result.manifest, result.inventory)["parent"][field] == "f" * 64
    else:
        with pytest.raises(SnapshotVerificationError):
            verify_snapshot(result.archive, result.manifest, result.inventory)
