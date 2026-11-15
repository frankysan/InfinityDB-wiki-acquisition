"""Private HTTP asset observation evidence for reproducible incremental reuse.

Evidence is keyed by the original request URL (not a mirror path) and kept
out of published ZIPs. An HTTP 304 is only a representation validation for a
strong parent ETag, not a new source body or proof of global Wiki consistency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASSET_EVIDENCE_DIRECTORY = ".wiki-asset-evidence"
ASSET_EVIDENCE_FORMAT = "InfinityDB wiki asset HTTP evidence"
ASSET_EVIDENCE_VERSION = 1


def _evidence_path(work: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return work / ASSET_EVIDENCE_DIRECTORY / f"{key}.json"


def build_asset_evidence(
    url: str, path: str, payload: bytes,
) -> dict[str, Any] | None:
    """Record only observed HTTP response metadata bound to exact content."""
    from infinity_wiki_acquisition.cli import _is_strong_etag

    status = getattr(payload, "http_status", None)
    effective = getattr(payload, "effective_url", None)
    if status not in (200, 304) or not isinstance(effective, str):
        return None
    etag = getattr(payload, "etag", None)
    validator = etag if _is_strong_etag(etag) and effective == url else None
    revalidated = status == 304
    if revalidated and (
        validator is None
        or getattr(payload, "conditional_etag", None) != validator
    ):
        raise ValueError("An asset HTTP 304 lacks matching strong ETag evidence")
    modified = getattr(payload, "last_modified", None)
    if not isinstance(modified, str) or not modified.strip():
        modified = None
    return {
        "format": ASSET_EVIDENCE_FORMAT,
        "version": ASSET_EVIDENCE_VERSION,
        "sourceUrl": url,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "responseStatus": status,
        "effectiveUrl": effective,
        "strongEtag": validator,
        "lastModified": modified,
        "conditionalRevalidated": revalidated,
        "observedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_asset_evidence(work: Path, record: dict[str, Any]) -> None:
    if (record.get("format") != ASSET_EVIDENCE_FORMAT
            or record.get("version") != ASSET_EVIDENCE_VERSION
            or not isinstance(record.get("sourceUrl"), str)):
        raise ValueError("Invalid asset evidence record")
    path = _evidence_path(work, record["sourceUrl"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def read_asset_evidence(
    work: Path, url: str, path: str, sha256: str,
) -> dict[str, Any] | None:
    """Return content-bound source version evidence, never infer HTTP facts."""
    evidence = _evidence_path(work, url)
    if not evidence.is_file():
        return None
    try:
        document = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"Unreadable asset evidence for {url}") from exc
    from infinity_wiki_acquisition.cli import _is_strong_etag

    if (not isinstance(document, dict)
            or set(document) != {
                "format", "version", "sourceUrl", "path", "sha256",
                "responseStatus", "effectiveUrl", "strongEtag", "lastModified",
                "conditionalRevalidated", "observedAt",
            }
            or document["format"] != ASSET_EVIDENCE_FORMAT
            or document["version"] != ASSET_EVIDENCE_VERSION
            or document["sourceUrl"] != url
            or document["path"] != path
            or document["sha256"] != sha256
            or document["responseStatus"] not in (200, 304)
            or type(document["responseStatus"]) is not int
            or not isinstance(document["effectiveUrl"], str)
            or not isinstance(document["conditionalRevalidated"], bool)
            or document["conditionalRevalidated"] != (
                document["responseStatus"] == 304
            )
            or (document["strongEtag"] is not None
                and not _is_strong_etag(document["strongEtag"]))
            or (document["lastModified"] is not None
                and not isinstance(document["lastModified"], str))
            or (document["conditionalRevalidated"]
                and (not document["strongEtag"]
                     or document["effectiveUrl"] != url))):
        raise ValueError(f"Asset evidence contradicts archived member: {url}")
    try:
        observed = datetime.fromisoformat(document["observedAt"])
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("Timezone is missing")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid asset observation time for {url}") from exc
    return {
        key: document[key] for key in (
            "sourceUrl", "sha256", "responseStatus", "effectiveUrl",
            "strongEtag", "lastModified", "conditionalRevalidated", "observedAt",
        )
    }
