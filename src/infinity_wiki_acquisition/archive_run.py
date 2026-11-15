"""P2.5 private parent-bound resume identity for archive-derived runs.

This is acquisition work metadata, not a published snapshot member. The
immutable parent ZIP, provenance and inventory must be verified separately
before loading this receipt. Resume refuses changed parent bytes, options,
site/language or a different operation before network access or writes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_provenance import sha256_file

FILENAME = ".wiki-archive-run.json"
OLDID_REUSE_FILENAME = ".wiki-reused-parent-oldids.jsonl"
FORMAT = "InfinityDB wiki archive-derived run"
VERSION = 1
MODES = frozenset({"history-upgrade", "current-refresh"})


def _binding(
    archive: Path, manifest: Path, inventory: Path,
    *, mode: str, site: str, language: str, options: dict[str, bool],
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError("Invalid archive-derived acquisition mode")
    if (not isinstance(options, dict)
            or not all(type(v) is bool for v in options.values())):
        raise ValueError("Invalid archive-derived acquisition options")
    return {
        "mode": mode,
        "site": site,
        "language": language,
        "parentArchiveName": archive.name,
        "parentArchiveSha256": sha256_file(archive),
        "parentManifestSha256": sha256_file(manifest),
        "parentInventorySha256": sha256_file(inventory),
        "options": options,
    }


def record_parent_oldid_reuse(work: Path, oldid: int, expected_sha: str) -> None:
    """Append only an exact oldid/verified-parent-hash claim to private work."""
    if type(oldid) is not int or oldid <= 0 or len(expected_sha) != 64:
        raise ValueError("Invalid parent oldid reuse identity")
    target = work / OLDID_REUSE_FILENAME
    if target.is_symlink():
        raise ValueError("Unsafe parent oldid accounting")
    # One record per callback; validate the actual saved oldid bytes on resume.
    encoded = json.dumps({
        "oldid": oldid, "sha256": expected_sha,
    }, sort_keys=True).encode() + b"\n"
    with target.open("ab") as output:
        output.write(encoded)


def restored_parent_oldid_reuse(
    work: Path, parent_revisions: dict[int, dict[str, Any]],
) -> set[int]:
    """Recover only logged, byte-for-byte verified parent oldid transfers.

    A log append may precede a file write interrupted before completion.
    Such entries do not count until the corresponding file exists and
    matches the independently verified parent bytes.
    """
    from hashlib import sha256

    path = work / OLDID_REUSE_FILENAME
    if not path.exists():
        return set()
    if path.is_symlink():
        raise ValueError("Unsafe parent oldid accounting")
    try:
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("Truncated parent oldid reuse log")
        records = [json.loads(line) for line in raw.splitlines()]
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Invalid parent oldid reuse log") from exc
    reused = set()
    for item in records:
        if (not isinstance(item, dict) or set(item) != {"oldid", "sha256"}
                or type(item["oldid"]) is not int or item["oldid"] <= 0):
            raise ValueError("Invalid parent oldid reuse log record")
        oldid = item["oldid"]
        prior = parent_revisions.get(oldid)
        if prior is None or item["sha256"] != prior["sha256"]:
            raise ValueError("Parent oldid reuse log contradicts verified parent")
        target = work / prior["path"]
        if target.is_symlink():
            raise ValueError("Unsafe saved parent oldid")
        if target.is_file():
            payload = target.read_bytes()
            if len(payload) != prior["size"] or sha256(payload).hexdigest() != prior["sha256"]:
                raise ValueError("Saved parent oldid does not match verified parent")
            reused.add(oldid)
    return reused


def open_archive_run(
    work: Path, archive: Path, manifest: Path, inventory: Path,
    *,
    mode: str, site: str, language: str, options: dict[str, bool],
    resume: bool,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """Create or verify a private run receipt; never synthesize resume state."""
    if not work.is_dir() or work.is_symlink():
        raise ValueError("Archive-derived work directory is missing or unsafe")
    binding = _binding(
        archive, manifest, inventory, mode=mode, site=site,
        language=language, options=options,
    )
    path = work / FILENAME
    if path.is_symlink():
        raise ValueError("Archive-derived run receipt cannot be a symlink")
    if resume:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError("Missing or invalid archive-derived resume receipt") from exc
        if (not isinstance(document, dict)
                or set(document) != {"format", "version", "startedAt", *binding.keys()}
                or document.get("format") != FORMAT
                or document.get("version") != VERSION
                or any(document.get(key) != value for key, value in binding.items())):
            raise ValueError("Archive-derived resume differs from the verified parent or options")
        try:
            stamp = datetime.fromisoformat(document["startedAt"].replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Invalid archive-derived run timestamp") from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Archive-derived run timestamp must have a timezone")
        return document

    if started_at is None or started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("New archive-derived runs require an aware start time")
    document = {
        "format": FORMAT, "version": VERSION,
        "startedAt": started_at.isoformat(timespec="seconds"),
        **binding,
    }
    encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode()
    try:
        with path.open("xb") as out:
            out.write(encoded)
    except FileExistsError as exc:
        raise ValueError("Archive-derived run receipt already exists; use resume") from exc
    return document
