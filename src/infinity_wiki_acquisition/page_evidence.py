"""Private, per-page source evidence captured before offline HTML rewriting.

Evidence is stored outside the snapshot ZIP and indexed by the request URL,
not by a potentially shared/rewritten mirror path. A page can be refetched
without changing evidence belonging to another source identity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

EVIDENCE_DIRECTORY = ".wiki-page-evidence"
EVIDENCE_FORMAT = "InfinityDB wiki page evidence"
EVIDENCE_VERSION = 1


def _evidence_file(work: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return work / EVIDENCE_DIRECTORY / f"{key}.json"


def write_page_evidence(work: Path, url: str, record: dict[str, Any]) -> None:
    """Atomically persist one observed source-page record."""
    if record.get("url") != url or record.get("format") != EVIDENCE_FORMAT:
        raise ValueError("Page evidence does not identify its source URL")
    path = _evidence_file(work, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def read_page_evidence(
    work: Path, url: str, *, path: str,
) -> dict[str, Any] | None:
    """Return explicitly recorded evidence, never reconstruct source from mirror."""
    file = _evidence_file(work, url)
    if not file.exists():
        return None
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable wiki page evidence for {url}") from exc
    if not (
        isinstance(record, dict)
        and record.get("format") == EVIDENCE_FORMAT
        and record.get("version") == EVIDENCE_VERSION
        and record.get("url") == url
        and record.get("path") == path
        and isinstance(record.get("originalSha256"), str)
        and len(record["originalSha256"]) == 64
        and isinstance(record.get("references"), list)
        and all(
            isinstance(ref, dict)
            and isinstance(ref.get("targetUrl"), str)
            and isinstance(ref.get("relation"), str)
            and isinstance(ref.get("targetType"), str)
            and ref.get("evidence") in {"source-html", "crawler-derived"}
            for ref in record["references"]
        )
        and isinstance(record.get("canonicalHints"), list)
        and isinstance(record.get("redirectHints"), list)
    ):
        raise ValueError(f"Invalid wiki page evidence for {url}")
    return record
