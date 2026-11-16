"""Shared filename/path sanitization for tool scripts.

The goal is to produce filesystem-safe names across Windows, macOS, and Linux
without losing too much readability. The rules are intentionally conservative so
we can still mirror real wiki/site names while avoiding invalid or dangerous
characters on the host OS.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}


def sanitize_path_component(value: str, *, os_name: str | None = None) -> str:
    """Normalize a path component so it is valid on the target OS."""
    os_name = (os_name or sys.platform).lower()
    cleaned = unquote(value).strip().replace("\\", "/")
    cleaned = unicodedata.normalize("NFKD", cleaned)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")

    if os_name.startswith("win"):
        cleaned = re.sub(r'[\x00-\x1f\x7f<>:"|?*]+', "_", cleaned)
        cleaned = cleaned.replace("/", "_")
        cleaned = cleaned.rstrip(". ")
        if not cleaned:
            cleaned = "_"
        if cleaned.split(".", 1)[0].lower() in _WINDOWS_RESERVED:
            cleaned = f"_{cleaned}"
        if len(cleaned) > 240:
            cleaned = cleaned[:240].rstrip(". ")
    else:
        cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
        cleaned = cleaned.replace("/", "_")
        cleaned = cleaned.rstrip(".")
        if not cleaned:
            cleaned = "_"

    return cleaned


def sanitize_filename(name: str, *, os_name: str | None = None) -> str:
    """Return a safe filename while preserving the final extension."""
    path = Path(name)
    stem, suffix = path.stem, path.suffix.lower()
    if not suffix:
        suffix = "." + path.name.split(".")[-1] if "." in path.name else ""

    cleaned = unicodedata.normalize("NFKD", stem)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9._ -]+", "-", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = cleaned.strip("-. ")
    if not cleaned:
        cleaned = "file"

    if (os_name or sys.platform).lower().startswith("win"):
        cleaned = re.sub(r'[<>:"|?*]+', "-", cleaned)
        cleaned = re.sub(r"\s+", "-", cleaned)
        cleaned = re.sub(r"-+", "-", cleaned)
        cleaned = cleaned.strip("-. ")
        if cleaned.lower() in _WINDOWS_RESERVED:
            cleaned = f"_{cleaned}"
        if len(cleaned) > 240:
            cleaned = cleaned[:240].rstrip("-. ")
    else:
        cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
        cleaned = re.sub(r"\s+", "-", cleaned)
        cleaned = re.sub(r"-+", "-", cleaned)
        cleaned = cleaned.strip("-. ")

    if not cleaned:
        cleaned = "file"
    return f"{cleaned}{suffix}"


def _nonstandard_page_case(value: str) -> bool:
    """Whether ASCII title casing could collide with a conventional wiki title.

    Pick one ordinary, capitalized spelling per case-insensitive class; retain
    its readable name. Other spellings get stable source-identity suffixes.
    Preserve backend endpoints. Filename-like suffixes are part of the source
    identity: .JPG and .jpg may name two different File: description pages.
    """
    title = value[:-5] if value.endswith(".html") else value
    if title.casefold() in {"index.php", "api.php", "load.php"}:
        return False
    extension = Path(title).suffix
    # CSS/JS source subpages conventionally use all-lowercase filenames:
    # styles.css is the readable path, Styles.css gets a distinct suffix.
    if ":" not in title and extension.lower() in {".css", ".js"}:
        return title != title.lower()
    # The lowercase extension is our conventional spelling. A case variant
    # must have its own hashed path even when the rest of the title is identical.
    # Apply this to any extension so uncommon file types are not overlooked.
    if extension and extension != extension.lower():
        return True
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", title)
    if extension and re.fullmatch(r"\.[A-Za-z][A-Za-z0-9]*", extension):
        # Do not apply title-case rules to a lowercase file extension.
        words = words[:-1]
    return any(word != word[0].upper() + word[1:].lower() for word in words)


def sanitize_relative_path(
    path: str,
    *,
    os_name: str | None = None,
    disambiguate_unicode: bool = False,
    disambiguate_page_case: bool = False,
    disambiguate_lossy: bool = False,
    disambiguate_asset_case: bool = False,
    reserve_generated_index: bool = False,
) -> str:
    """Normalize a relative path without collapsing distinct page identities.

    Callers choose whether non-ASCII components, noncanonical ASCII title
    capitalization or both receive stable source-identity suffixes.
    Conventional spellings and ordinary asset paths can keep readable names.
    """
    # Split *before* decoding. A URL-encoded slash (%2F) names a distinct
    # source component from a literal / directory separator.
    normalized = path.lstrip("/")
    if not normalized:
        return "index.html"
    parts: list[str] = []
    for encoded in normalized.split("/"):
        if not encoded:
            continue
        part = unquote(encoded)
        safe = sanitize_path_component(part, os_name=os_name)
        if (
            (disambiguate_unicode and not unicodedata.normalize("NFC", part).isascii())
            or (disambiguate_page_case and _nonstandard_page_case(part))
            or (disambiguate_lossy and safe != part)
            or (disambiguate_asset_case and part != part.lower())
            or (reserve_generated_index and part.casefold() == "index.html")
        ):
            # Non-portable punctuation, encoded separators, transliteration
            # and case variants need a stable source-component identity.
            source = unicodedata.normalize("NFC", part)
            marker = "__u_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
            suffix = Path(safe).suffix
            stem = safe[:-len(suffix)] if suffix else safe
            # The ZIP contract limits individual portable components to 240 bytes.
            stem = stem[: 240 - len(marker) - len(suffix)].rstrip(". ")
            safe = f"{stem}{marker}{suffix}"
        parts.append(safe)
    return "/".join(parts) if parts else "index.html"
