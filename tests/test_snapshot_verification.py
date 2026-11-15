"""Offline regression checks for the P1.3 snapshot reuse boundary."""

from __future__ import annotations

import copy
import json
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_archive import validate_archive_member_paths
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import (
    sha256_file,
    write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError,
    verify_snapshot,
)

_NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def _fixture(
    tmp_path: Path, *, history: bool = False, known_page: bool = True,
    site=cli.DEFAULT_SITE, language: str = "en",
):
    root_url = site.root_urls[language]
    work = tmp_path / "work"
    work.mkdir()
    current = work / "index.html"
    current.write_bytes(b"<html>current</html>")
    files = [current]
    if history:
        oldid = work / "_history/oldid/44.html"
        oldid.parent.mkdir(parents=True)
        oldid.write_bytes(b"<html>revision</html>")
        index = work / "_history/index.json"
        index.write_text(json.dumps({
            "format": cli.HISTORY_INDEX_FORMAT,
            "formatVersion": cli.HISTORY_INDEX_VERSION,
            "revisionCount": 1,
            "pages": [{
                "currentUrl": root_url,
                "currentPath": "index.html",
                "title": "Main Page",
                "revisions": [{
                    "oldid": 44,
                    "parentid": 43,
                    "timestamp": "2025-12-01T00:00:00Z",
                    "url": cli.oldid_url("Main Page", 44, language="en"),
                    "path": "_history/oldid/44.html",
                }],
            }],
        }), encoding="utf-8")
        files += [index, oldid]
    archive = cli.archive_wiki(
        files, tmp_path / "archives", root=work, now=_NOW, include_history=history,
        language=language, site=site,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests", snapshot_type="wiki",
        acquired_at=_NOW, source_url=root_url, document_count=len(files),
        project_root=tmp_path, language=language,
    )
    pages = (
        (cli.WikiPage(root_url, "index.html", "Main Page"),)
        if known_page else ()
    )
    document = build_snapshot_inventory(
        archive, manifest, work=work, site=site.key, language=language,
        root_url=root_url, include_history=history, pages=pages,
    )
    inventory = tmp_path / "manifests" / "inventories" / f"{archive.stem}.inventory.json"
    inventory.parent.mkdir()
    inventory.write_text(json.dumps(document), encoding="utf-8")
    return archive, manifest, inventory


def _edit_json(path: Path, change):
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def _rebind_zip(archive: Path, manifest: Path, inventory: Path, *, count: int | None = None):
    new_sha = sha256_file(archive)
    _edit_json(manifest, lambda d: (
        d["snapshot"]["archive"].update(sha256=new_sha),
        d["snapshot"].update(**({"documentCount": count} if count is not None else {})),
    ))
    _edit_json(inventory, lambda d: (
        d["snapshot"].update(archiveSha256=new_sha),
        d["snapshot"].update(**({"memberCount": count} if count is not None else {})),
    ))


@pytest.mark.parametrize("history,known_page", [
    (False, False), (False, True), (True, True),
])
def test_accepts_valid_snapshot_and_does_not_mutate_inputs(
    tmp_path: Path, history: bool, known_page: bool,
):
    paths = _fixture(tmp_path, history=history, known_page=known_page)
    before = tuple(path.read_bytes() for path in paths)
    document = verify_snapshot(*paths)
    assert document["history"] is history
    assert document["snapshot"]["memberCount"] == (3 if history else 1)
    assert tuple(path.read_bytes() for path in paths) == before


@pytest.mark.parametrize("site,language", [
    (cli.INFINITY_WIKI_SITE, "es"),
    (cli.HUMAN_SPHERE_SITE, "en"),
])
def test_accepts_other_supported_site_languages(tmp_path: Path, site, language: str):
    paths = _fixture(tmp_path, site=site, language=language)
    document = verify_snapshot(*paths)
    assert (document["site"], document["language"]) == (site.key, language)


def test_unknown_member_may_have_observed_url(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path, known_page=False)
    _edit_json(inventory, lambda d: d["members"][0].update(
        sourceUrls=["https://example.com/unclassified"],
    ))
    assert verify_snapshot(archive, manifest, inventory)["members"][0]["kind"] == "unknown"


def test_detects_archive_byte_corruption(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path)
    with archive.open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(SnapshotVerificationError, match="ZIP SHA-256 mismatch"):
        verify_snapshot(archive, manifest, inventory)


def test_detects_modified_member_even_when_zip_hashes_are_updated(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path)
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("index.html", b"changed")
    _rebind_zip(archive, manifest, inventory)
    with pytest.raises(SnapshotVerificationError, match="Inventory member differs"):
        verify_snapshot(archive, manifest, inventory)


@pytest.mark.parametrize("change,message", [
    (lambda d: d["snapshot"].update(archiveSha256="0" * 64), "ZIP hashes differ"),
    (lambda d: d["snapshot"].update(contentSha256="0" * 64), "Logical content hashes differ"),
    (lambda d: d["snapshot"].update(acquiredAt="2020-01-01T00:00:00+00:00"), "Acquisition times"),
    (lambda d: d.update(language="es"), "Source site/language/root URL"),
    (lambda d: d.update(history=True), "Archive name contradicts"),
    (lambda d: d["members"][0].update(size=999), "Inventory member differs"),
    (lambda d: d["members"][0].update(sha256="f" * 64), "Inventory member differs"),
    (lambda d: d["members"][0]["page"].update(url="https://example.com/"), "Current-page URL"),
    (lambda d: d["members"][0]["renderedIdentity"].update(sha256="f" * 64), "Rendered identity"),
    (lambda d: d["members"][0]["sourceIdentity"].update(pageIdConsistency="consistent"),
     "Contradictory page ID evidence"),
])
def test_rejects_contradictory_inventory(
    tmp_path: Path, change, message: str,
):
    archive, manifest, inventory = _fixture(tmp_path)
    _edit_json(inventory, change)
    with pytest.raises(SnapshotVerificationError, match=message):
        verify_snapshot(archive, manifest, inventory)


@pytest.mark.parametrize("extra", [
    ["index.html"],            # Duplicate ZIP entry
    ["INDEX.html"],            # Case-only file collision
    ["index.html/part"],       # File/directory collision
    ["../outside"],            # Traversal
    ["CON.txt"],               # Windows reserved path
    ["trailing. "],            # Windows stripped suffix
    ["A/one.css", "a/two.css"], # Directory case collision
])
def test_rejects_unsafe_or_colliding_zip_members(
    tmp_path: Path, extra: list[str],
):
    archive, manifest, inventory = _fixture(tmp_path, known_page=False)
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("index.html", b"old")
        for name in extra:
            target.writestr(name, b"extra")
    _rebind_zip(archive, manifest, inventory, count=1 + len(extra))
    # Supply an otherwise well-formed distinct inventory for the changed ZIP.
    # ZIP member validation must happen before set/hash matching.
    def add_members(document):
        original = document["members"][0]
        document["members"] = [
            {**copy.deepcopy(original), "path": f"placeholder-{i}.txt"}
            for i in range(1 + len(extra))
        ]
    _edit_json(inventory, add_members)
    with pytest.raises(SnapshotVerificationError, match=(
        "Duplicate archive path|collision|Non-portable archive path"
    )):
        verify_snapshot(archive, manifest, inventory)


def test_shared_archive_validation_rejects_backslash_member():
    with pytest.raises(ValueError, match="Non-portable archive path"):
        validate_archive_member_paths(["a\\b.html"])


def test_rejects_directory_and_symlink_zip_entries(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path)
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("index.html", b"old")
        target.writestr("directory/", b"")
    _rebind_zip(archive, manifest, inventory, count=2)
    _edit_json(inventory, lambda d: d["members"].append({
        **copy.deepcopy(d["members"][0]), "path": "z.txt",
    }))
    with pytest.raises(SnapshotVerificationError, match="Directory members"):
        verify_snapshot(archive, manifest, inventory)

    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("index.html", b"old")
        link = zipfile.ZipInfo("symlink.txt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(link, b"index.html")
    _rebind_zip(archive, manifest, inventory)
    with pytest.raises(SnapshotVerificationError, match="Non-regular ZIP member"):
        verify_snapshot(archive, manifest, inventory)


def test_rejects_missing_inventory(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path, history=True)
    inventory.unlink()
    with pytest.raises(SnapshotVerificationError, match="Unreadable snapshot inventory"):
        verify_snapshot(archive, manifest, inventory)


def test_rejects_missing_history_member(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path, history=True)
    with zipfile.ZipFile(archive) as old_zip:
        retained = [(info.filename, old_zip.read(info)) for info in old_zip.infolist()
                    if info.filename != "_history/oldid/44.html"]
    with zipfile.ZipFile(archive, "w") as new_zip:
        for name, data in retained:
            new_zip.writestr(name, data)
    _rebind_zip(archive, manifest, inventory, count=2)
    _edit_json(inventory, lambda d: d.update(members=[
        member for member in d["members"] if member["kind"] != "revision"
    ]))
    with pytest.raises(SnapshotVerificationError, match="History index references absent"):
        verify_snapshot(archive, manifest, inventory)


def test_rejects_history_index_without_current_member(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path, history=True)
    with zipfile.ZipFile(archive) as old_zip:
        records = [(info.filename, old_zip.read(info)) for info in old_zip.infolist()]
    with zipfile.ZipFile(archive, "w") as new_zip:
        for name, data in records:
            if name == "_history/index.json":
                index = json.loads(data)
                index["pages"][0]["currentPath"] = "missing.html"
                data = json.dumps(index).encode("utf-8")
            new_zip.writestr(name, data)
    _rebind_zip(archive, manifest, inventory)
    with pytest.raises(SnapshotVerificationError, match="absent current-page member"):
        verify_snapshot(archive, manifest, inventory)


def test_detects_history_metadata_tampering(tmp_path: Path):
    archive, manifest, inventory = _fixture(tmp_path, history=True)
    _edit_json(inventory, lambda d: next(
        member for member in d["members"] if member["kind"] == "revision"
    )["revision"].update(parentid=999))
    with pytest.raises(SnapshotVerificationError, match="Revision identity differs"):
        verify_snapshot(archive, manifest, inventory)
