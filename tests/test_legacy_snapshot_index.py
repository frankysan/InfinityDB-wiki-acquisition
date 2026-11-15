"""Offline regression tests for conservative pre-inventory snapshot indexing."""

from __future__ import annotations

import copy
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.legacy_snapshot_index import (
    build_legacy_snapshot_inventory,
    main,
    write_legacy_snapshot_inventory,
)
from infinity_wiki_acquisition.snapshot_provenance import (
    SnapshotProvenanceError,
    sha256_file,
    snapshot_content_sha256,
    write_snapshot_manifest,
)
from infinity_wiki_acquisition.snapshot_verification import (
    SnapshotVerificationError,
    verify_snapshot,
)

_ACQUIRED = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)


def _legacy_pair(
    root: Path, *, site=cli.DEFAULT_SITE, language: str = "en",
    history: bool = False,
) -> tuple[Path, Path]:
    source_root = site.root_urls[language]
    work = root / "work"
    work.mkdir(parents=True)
    current = work / "Main.html"
    current.write_bytes(b'<html><link rel="canonical" href="https://unverified.invalid/"></html>')
    asset = work / "images" / "asset.png"
    asset.parent.mkdir()
    asset.write_bytes(b"not-classified-from-extension")
    files = [current, asset]
    if history:
        oldid = work / "_history/oldid/44.html"
        oldid.parent.mkdir(parents=True)
        oldid.write_bytes(b"<html>old revision</html>")
        index = work / "_history/index.json"
        index.write_text(json.dumps({
            "format": cli.HISTORY_INDEX_FORMAT,
            "formatVersion": cli.HISTORY_INDEX_VERSION,
            "revisionCount": 1,
            "pages": [{
                "currentUrl": source_root,
                "currentPath": "Main.html",
                "title": "Main Page",
                "revisions": [{
                    "oldid": 44, "parentid": 43,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "url": cli.oldid_url("Main Page", 44, language=language, site=site),
                    "path": "_history/oldid/44.html",
                }],
            }],
        }), encoding="utf-8")
        files.extend([index, oldid])
    archive = cli.archive_wiki(
        files, root / "archives", root=work, language=language, site=site,
        include_history=history, now=_ACQUIRED,
    )
    manifest = write_snapshot_manifest(
        archive, root / "manifests", snapshot_type="wiki",
        acquired_at=_ACQUIRED, source_url=source_root, document_count=len(files),
        project_root=root, language=language,
    )
    return archive, manifest


def _rebind_manifest(archive: Path, manifest: Path) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["snapshot"]["archive"]["sha256"] = sha256_file(archive)
    try:
        document["snapshot"]["contentSha256"] = snapshot_content_sha256(archive)
    except SnapshotProvenanceError:
        # An invalid name/collision cannot have a valid logical-content digest.
        # Keep the previous digest and assert fail-closed rejection.
        pass
    with zipfile.ZipFile(archive) as source:
        document["snapshot"]["documentCount"] = len(source.infolist())
    manifest.write_text(json.dumps(document), encoding="utf-8")


def _mutate_archive(archive: Path, manifest: Path, mutation) -> None:
    with zipfile.ZipFile(archive) as source:
        items = [(info.filename, source.read(info)) for info in source.infolist()]
    updated = mutation(items)
    with zipfile.ZipFile(archive, "w") as dest:
        for name, data in updated:
            dest.writestr(name, data)
    _rebind_manifest(archive, manifest)


@pytest.mark.parametrize("site,language", [
    (cli.INFINITY_WIKI_SITE, "en"),
    (cli.INFINITY_WIKI_SITE, "es"),
    (cli.HUMAN_SPHERE_SITE, "en"),
])
def test_current_only_archive_marks_unknowns_and_preserves_sources(
    tmp_path: Path, site, language: str,
) -> None:
    archive, manifest = _legacy_pair(tmp_path, site=site, language=language)
    original = (archive.read_bytes(), manifest.read_bytes())
    document = build_legacy_snapshot_inventory(archive, manifest)
    assert document["site"] == site.key
    assert document["language"] == language
    assert document["history"] is False
    assert document["captureMetadata"]["legacyIndexed"] is True
    assert document["captureMetadata"]["ignoredUrlsAvailable"] is False
    assert document["ignoredUrls"] == []
    assert all(member["kind"] == "unknown" and member["sourceUrls"] == []
               for member in document["members"])
    output = write_legacy_snapshot_inventory(archive, manifest)
    assert output == manifest.parent / "inventories" / f"{archive.stem}.inventory.json"
    assert verify_snapshot(archive, manifest, output) == document
    assert (archive.read_bytes(), manifest.read_bytes()) == original


@pytest.mark.parametrize("site", [cli.INFINITY_WIKI_SITE, cli.HUMAN_SPHERE_SITE])
def test_history_rebuilds_only_explicit_index_identities(tmp_path: Path, site) -> None:
    archive, manifest = _legacy_pair(tmp_path, site=site, history=True)
    original = (archive.read_bytes(), manifest.read_bytes())
    inventory = write_legacy_snapshot_inventory(archive, manifest)
    document = verify_snapshot(archive, manifest, inventory)
    members = {member["path"]: member for member in document["members"]}
    page = members["Main.html"]
    assert page["kind"] == "current-page"
    assert page["sourceUrls"] == [site.root_urls["en"]]
    assert page["sourceIdentity"]["pageId"] is None
    assert page["sourceIdentity"]["htmlPageIdHint"] is None
    assert page["sourceIdentity"]["canonicalDeclarations"] is None
    assert page["renderedIdentity"]["originalResponseSha256"] is None
    assert page["references"] is None
    assert members["images/asset.png"]["kind"] == "unknown"
    assert members["_history/index.json"]["kind"] == "generated-index"
    revision = members["_history/oldid/44.html"]
    assert revision["kind"] == "revision"
    assert revision["revision"]["oldid"] == 44
    assert revision["revision"]["parentid"] == 43
    assert revision["sourceIdentity"]["pageIds"] == [
        {"url": site.root_urls["en"], "pageId": None},
    ]
    assert (archive.read_bytes(), manifest.read_bytes()) == original


def test_immutable_publishing_and_explicit_command(tmp_path: Path, capsys) -> None:
    archive, manifest = _legacy_pair(tmp_path)
    destination = tmp_path / "elsewhere"
    assert main([str(archive), str(manifest), "--inventory-dir", str(destination)]) == 0
    path = destination / f"{archive.stem}.inventory.json"
    assert path.is_file()
    assert "Verified legacy snapshot inventory" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(SnapshotVerificationError, match="already exists"):
        write_legacy_snapshot_inventory(archive, manifest, destination)
    assert main([str(archive), str(manifest), "--inventory-dir", str(destination)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert not list(destination.glob("*.tmp"))


def test_bad_zip_hash_never_publishes_sidecar(tmp_path: Path) -> None:
    archive, manifest = _legacy_pair(tmp_path)
    with archive.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(SnapshotVerificationError, match="SHA-256 mismatch"):
        write_legacy_snapshot_inventory(archive, manifest)
    assert not (manifest.parent / "inventories").exists()


@pytest.mark.parametrize("names", [
    ["Main.html", "main.html"],
    ["../outside.html"],
    ["CON.txt"],
    ["A/file.html", "a/other.html"],
])
def test_rejects_nonportable_or_colliding_members_even_with_matching_manifest(
    tmp_path: Path, names: list[str],
) -> None:
    archive, manifest = _legacy_pair(tmp_path)
    _mutate_archive(
        archive, manifest, lambda _: [(name, b"data") for name in names],
    )
    with pytest.raises(SnapshotVerificationError, match=(
        "collision|Non-portable|normalized relative POSIX path"
    )):
        build_legacy_snapshot_inventory(archive, manifest)


def test_unsupported_or_inconsistent_manifest(tmp_path: Path) -> None:
    archive, manifest = _legacy_pair(tmp_path)
    original = json.loads(manifest.read_text(encoding="utf-8"))
    wrong = copy.deepcopy(original)
    wrong["source"]["url"] = "https://wrong.invalid/"
    manifest.write_text(json.dumps(wrong), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="unsupported or ambiguous"):
        build_legacy_snapshot_inventory(archive, manifest)

    wrong = copy.deepcopy(original)
    wrong["formatVersion"] = 2
    manifest.write_text(json.dumps(wrong), encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="requires Wiki provenance v3"):
        build_legacy_snapshot_inventory(archive, manifest)


@pytest.mark.parametrize("index_change,expected", [
    (lambda d: d["pages"][0].update(currentPath="absent.html"), "absent current-page"),
    (lambda d: d.update(revisionCount=2), "revision count"),
    (lambda d: (
        d["pages"][0]["revisions"].append({
            **d["pages"][0]["revisions"][0], "oldid": 45,
            "path": "_history/oldid/45.html",
        }),
        d.update(revisionCount=2),
    ), "absent oldid"),
])
def test_rejects_inconsistent_history_evidence(
    tmp_path: Path, index_change, expected: str,
) -> None:
    archive, manifest = _legacy_pair(tmp_path, history=True)
    def mutate(items):
        result = []
        for name, data in items:
            if name == "_history/index.json":
                document = json.loads(data)
                index_change(document)
                data = json.dumps(document).encode("utf-8")
            result.append((name, data))
        return result
    _mutate_archive(archive, manifest, mutate)
    with pytest.raises(SnapshotVerificationError, match=expected):
        build_legacy_snapshot_inventory(archive, manifest)


def test_rejects_unindexed_oldid(tmp_path: Path) -> None:
    archive, manifest = _legacy_pair(tmp_path, history=True)
    _mutate_archive(
        archive, manifest,
        lambda items: items + [("_history/oldid/999.html", b"unindexed")],
    )
    with pytest.raises(SnapshotVerificationError, match="unindexed oldid"):
        build_legacy_snapshot_inventory(archive, manifest)


def test_refuses_conflicting_current_page_identifications(tmp_path: Path) -> None:
    archive, manifest = _legacy_pair(tmp_path, history=True)
    def mutate(items):
        result = []
        for name, data in items:
            if name == "_history/index.json":
                doc = json.loads(data)
                another = copy.deepcopy(doc["pages"][0])
                another["currentUrl"] = "https://wrong.invalid/"
                doc["pages"].append(another)
                data = json.dumps(doc).encode("utf-8")
            result.append((name, data))
        return result
    _mutate_archive(archive, manifest, mutate)
    with pytest.raises(SnapshotVerificationError, match="Conflicting current-page"):
        build_legacy_snapshot_inventory(archive, manifest)


def test_history_missing_index_fails_closed(tmp_path: Path) -> None:
    archive, manifest = _legacy_pair(tmp_path, history=True)
    _mutate_archive(
        archive, manifest, lambda items: [
            (name, data) for name, data in items if name != "_history/index.json"
        ],
    )
    with pytest.raises(SnapshotVerificationError, match=(
        "Unindexed history|ZIP name contradicts"
    )):
        build_legacy_snapshot_inventory(archive, manifest)


def test_failed_final_verification_does_not_publish(
    tmp_path: Path, monkeypatch,
) -> None:
    archive, manifest = _legacy_pair(tmp_path)
    def reject(*args):
        raise SnapshotVerificationError("injected verification failure")
    monkeypatch.setattr(
        "infinity_wiki_acquisition.legacy_snapshot_index.verify_snapshot", reject,
    )
    with pytest.raises(SnapshotVerificationError, match="injected"):
        write_legacy_snapshot_inventory(archive, manifest)
    assert not list((manifest.parent / "inventories").glob("*.json"))
    assert not list((manifest.parent / "inventories").glob("*.tmp"))
