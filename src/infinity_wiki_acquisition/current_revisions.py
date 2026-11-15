"""Batched, conservative observations of latest MediaWiki page revisions.

This is metadata observed *after* the current HTML crawl, not a version
guarantee for the earlier rendered response. Unknown or contradictory API
identities are represented explicitly and never authorize HTML byte reuse.
Private evidence lives outside published ZIPs.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FORMAT = "InfinityDB wiki current page revision observations"
VERSION = 1
FILENAME = ".wiki-current-revisions.json"
BATCH_SIZE = 40
STATUSES = {
    "observed", "missing-page-id", "identity-unresolved",
    "revision-unavailable", "api-unavailable",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _aware(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Revision timestamp lacks a timezone")
    return result


def _same_title(first: str, second: str) -> bool:
    return first.replace("_", " ").strip().casefold() == second.replace(
        "_", " ",
    ).strip().casefold()


def observe_current_revisions(
    pages: Iterable[Any], page_ids: dict[str, int],
    work: Path, *, site: Any, language: str,
) -> dict[str, Any]:
    """Query latest revisions in bounded page-ID batches.

    Bad API batches do not invalidate a fully captured HTML crawl; they
    become explicit unknown metadata. An absent/mismatched identity is
    never promoted into an authoritative revision claim.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.page_evidence import read_page_evidence

    page_records = list(pages)
    results: dict[str, dict[str, Any]] = {}
    indexed: dict[int, list[Any]] = {}
    for page in page_records:
        page_id = page_ids.get(page.url)
        evidence = read_page_evidence(work, page.url, path=page.path)
        html_at = evidence.get("fetchedAt") if evidence else None
        if html_at is not None:
            _aware(html_at)
        record = {
            "pageUrl": page.url, "memberPath": page.path,
            "pageId": page_id, "status": "missing-page-id",
            "revisionId": None, "revisionTimestamp": None,
            "apiTitle": None, "requestedAt": None, "checkedAt": None,
            "htmlFetchedAt": html_at, "renderCorrespondence": "not-verified",
        }
        results[page.url] = record
        if type(page_id) is int and page_id > 0:
            indexed.setdefault(page_id, []).append(page)

    unique = sorted(indexed)
    for offset in range(0, len(unique), BATCH_SIZE):
        batch = unique[offset:offset + BATCH_SIZE]
        requested_at = _utc_now()
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "prop": "revisions", "pageids": "|".join(map(str, batch)),
            "rvprop": "ids|timestamp", "rvlimit": "1", "rvdir": "older",
        }
        url = f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"
        parsed: dict[int, dict[str, Any]] = {}
        api_ok = False
        try:
            response = json.loads(
                cli.fetch_site_bytes(url, language=language, site=site),
            )
            if (not isinstance(response, dict)
                    or "errors" in response
                    or not isinstance(response.get("query"), dict)
                    or not isinstance(response["query"].get("pages"), list)):
                raise ValueError("MediaWiki revision API response is malformed")
            for item in response["query"]["pages"]:
                if not isinstance(item, dict):
                    raise ValueError("Invalid revision page response")
                page_id = item.get("pageid")
                if (type(page_id) is not int or page_id not in batch
                        or page_id in parsed):
                    raise ValueError("Revision response has unexpected page ID")
                parsed[page_id] = item
            api_ok = True
        except Exception:
            # Failure to obtain optional revision evidence does not change
            # successfully mirrored HTML bytes or the archive publication gate.
            pass
        checked_at = _utc_now()
        for page_id in batch:
            item = parsed.get(page_id) if api_ok else None
            for page in indexed[page_id]:
                record = results[page.url]
                record["requestedAt"] = requested_at
                record["checkedAt"] = checked_at
                if not api_ok:
                    record["status"] = "api-unavailable"
                    continue
                if item is None:
                    record["status"] = "revision-unavailable"
                    continue
                title = item.get("title")
                expected = cli.page_title_from_url(
                    page.url, language=language, site=site,
                )
                if (not isinstance(title, str)
                        or not _same_title(title, expected)):
                    record["status"] = "identity-unresolved"
                    continue
                revisions = item.get("revisions")
                if not isinstance(revisions, list) or len(revisions) != 1:
                    record["status"] = "revision-unavailable"
                    continue
                revision = revisions[0]
                if not isinstance(revision, dict):
                    record["status"] = "revision-unavailable"
                    continue
                revision_id = revision.get("revid")
                stamp = revision.get("timestamp")
                if (type(revision_id) is not int or revision_id <= 0
                        or not isinstance(stamp, str)):
                    record["status"] = "revision-unavailable"
                    continue
                try:
                    _aware(stamp)
                except ValueError:
                    record["status"] = "revision-unavailable"
                    continue
                record.update(
                    status="observed", apiTitle=title,
                    revisionId=revision_id, revisionTimestamp=stamp,
                )
    document = {
        "format": FORMAT, "version": VERSION,
        "site": site.key, "language": language,
        "records": results,
    }
    target = work / FILENAME
    temp = target.with_name(target.name + ".part")
    temp.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return document


def load_current_revision_evidence(
    work: Path, *, site: str, language: str,
) -> dict[str, dict[str, Any]] | None:
    """Read and validate private metadata; fail closed on altered evidence."""
    path = work / FILENAME
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Unreadable current revision observations") from exc
    if (not isinstance(document, dict)
            or set(document) != {"format", "version", "site", "language", "records"}
            or document["format"] != FORMAT or document["version"] != VERSION
            or document["site"] != site or document["language"] != language
            or not isinstance(document["records"], dict)):
        raise ValueError("Current revision observations have incompatible scope")
    for url, record in document["records"].items():
        validate_current_revision_record(record, url=url)
    return document["records"]


def validate_current_revision_record(record: Any, *, url: str) -> None:
    if (not isinstance(record, dict)
            or set(record) != {
                "pageUrl", "memberPath", "pageId", "status", "revisionId",
                "revisionTimestamp", "apiTitle", "requestedAt", "checkedAt",
                "htmlFetchedAt", "renderCorrespondence",
            }
            or record["pageUrl"] != url
            or not isinstance(record["memberPath"], str)
            or not record["memberPath"]
            or record["status"] not in STATUSES
            or record["renderCorrespondence"] != "not-verified"):
        raise ValueError("Invalid current revision evidence identity")
    page_id = record["pageId"]
    if page_id is not None and (type(page_id) is not int or page_id <= 0):
        raise ValueError("Invalid current revision page ID")
    for name in ("requestedAt", "checkedAt", "htmlFetchedAt"):
        value = record[name]
        if value is not None:
            if not isinstance(value, str):
                raise ValueError("Invalid current revision observation time")
            _aware(value)
    if record["requestedAt"] is None:
        if record["checkedAt"] is not None:
            raise ValueError("Inconsistent current revision query times")
    elif (record["checkedAt"] is None
          or _aware(record["requestedAt"]) > _aware(record["checkedAt"])):
        raise ValueError("Out-of-order current revision observation times")
    if (record["htmlFetchedAt"] is not None
            and record["requestedAt"] is not None
            and _aware(record["htmlFetchedAt"]) > _aware(record["checkedAt"])):
        raise ValueError("Page fetch occurred after revision query completion")
    if record["status"] == "observed":
        if (page_id is None
                or type(record["revisionId"]) is not int
                or record["revisionId"] <= 0
                or not isinstance(record["revisionTimestamp"], str)
                or not isinstance(record["apiTitle"], str)
                or not record["apiTitle"]
                or record["requestedAt"] is None):
            raise ValueError("Incomplete observed current revision identity")
        _aware(record["revisionTimestamp"])
    elif any(record[name] is not None for name in (
        "revisionId", "revisionTimestamp", "apiTitle",
    )):
        raise ValueError("Unknown current revision cannot claim an identity")
    if record["status"] == "missing-page-id" and page_id is not None:
        raise ValueError("Missing page-ID status contradicts observed ID")
    if record["status"] != "missing-page-id" and page_id is None:
        raise ValueError("Current revision status lacks enumerated page ID")
