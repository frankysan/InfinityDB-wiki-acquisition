from __future__ import annotations

import unicodedata
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from infinity_wiki_acquisition.path_sanitization import sanitize_path_component

_ARCHIVE_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = 0o100644 << 16
_ARCHIVE_COMPRESSION = zipfile.ZIP_DEFLATED
_ARCHIVE_COMPRESSLEVEL = 6


def _archive_info(arcname: str) -> zipfile.ZipInfo:
    """Return metadata-normalized ZIP information for one snapshot member."""
    info = zipfile.ZipInfo(arcname, date_time=_ARCHIVE_DATE_TIME)
    info.compress_type = _ARCHIVE_COMPRESSION
    info.create_system = 3
    info.external_attr = _ARCHIVE_MODE
    info.extra = b""
    info.comment = b""
    return info


def _portable_archive_path(arcname: str) -> None:
    """Reject members that cannot be extracted as ordinary files on common hosts."""
    parts = arcname.split("/")
    if any(
        not part
        or part in {".", ".."}
        or len(part.encode("utf-8")) > 240
        or sanitize_path_component(part, os_name="Windows") != part
        for part in parts
    ):
        raise ValueError(f"Non-portable archive path: {arcname!r}")


def _portable_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def validate_archive_member_paths(names: Iterable[str]) -> None:
    """Validate file-only ZIP paths, including collisions on case-insensitive hosts."""
    seen_names: set[str] = set()
    seen_casefolded: dict[str, str] = {}
    directories: set[str] = set()
    directory_names: dict[str, str] = {}
    for name in names:
        _portable_archive_path(name)
        if name in seen_names:
            raise ValueError(f"Duplicate archive path: {name}")
        key = _portable_key(name)
        if previous := seen_casefolded.get(key):
            raise ValueError(f"Case-only archive path collision: {previous!r} and {name!r}")
        if key in directories:
            raise ValueError(f"Archive file/directory collision: {name!r}")
        parts = name.split("/")
        parents = ["/".join(parts[:i]) for i in range(1, len(parts))]
        for parent in parents:
            parent_key = _portable_key(parent)
            if parent_key in seen_casefolded:
                raise ValueError(f"Archive file/directory collision: {name!r}")
            existing = directory_names.get(parent_key)
            if existing is not None and existing != parent:
                raise ValueError(
                    f"Case-only archive directory collision: {existing!r} and {parent!r}"
                )
            directory_names[parent_key] = parent
            directories.add(parent_key)
        seen_names.add(name)
        seen_casefolded[key] = name


def create_timestamped_archive(
    files: Iterable[Path],
    destination: Path,
    *,
    prefix: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Archive exactly ``files`` using deterministic member metadata and ordering."""
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{prefix} {timestamp}.zip"
    sequence = 2
    while archive.exists():
        archive = destination / f"{prefix} {timestamp}-{sequence}.zip"
        sequence += 1

    items: list[tuple[str, Path]] = []
    for path in files:
        arcname = path.relative_to(root).as_posix() if root is not None else path.name
        items.append((arcname, path))
    validate_archive_member_paths(name for name, _ in items)

    created = False
    try:
        with zipfile.ZipFile(
            archive,
            "x",
            compression=_ARCHIVE_COMPRESSION,
            compresslevel=_ARCHIVE_COMPRESSLEVEL,
        ) as output:
            created = True
            for arcname, path in sorted(items):
                output.writestr(
                    _archive_info(arcname),
                    path.read_bytes(),
                    compress_type=_ARCHIVE_COMPRESSION,
                    compresslevel=_ARCHIVE_COMPRESSLEVEL,
                )
    except BaseException:
        # A failed write must not leave a seemingly completed ZIP behind.
        # Never remove a competing pre-existing destination.
        if created:
            archive.unlink(missing_ok=True)
        raise
    return archive
