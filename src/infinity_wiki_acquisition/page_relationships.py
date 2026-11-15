"""Optional MediaWiki template/category observations for P2.4b.

Query prop=templates|categories by independently enumerated page IDs, retaining
all continuation pages and explicit unknowns. API lists describe state during
observation, not which transclusions generated earlier rendered HTML. No
relationship observation permits HTML reuse.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from infinity_wiki_acquisition.current_revisions import _same_title
from infinity_wiki_acquisition.page_evidence import read_page_evidence

FORMAT = "InfinityDB wiki page relationship observations"
VERSION = 1
FILENAME = ".wiki-page-relationships.json"
BATCH_SIZE = 40
MAX_REQUESTS_PER_BATCH = 200
STATUSES = {"observed", "missing-page-id", "identity-unresolved", "api-unavailable"}
SCOPE = "post-html-mediawiki-api-not-render-bound"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Relationship observation timestamp is not a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid relationship observation timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Relationship timestamp lacks a timezone")
    return parsed


def _items(value: Any, *, category: bool) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Invalid relationship API list")
    items = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Invalid relationship API entry")
        ns, title = entry.get("ns"), entry.get("title")
        if (type(ns) is not int or ns < 0
                or (category and ns != 14)
                or not isinstance(title, str) or not title.strip()):
            raise ValueError("Invalid relationship API namespace/title")
        items.add((ns, title))
    return [{"ns": ns, "title": title} for ns, title in sorted(items)]


def _record(page: Any, page_id: int | None, html_at: str | None) -> dict[str, Any]:
    return {
        "pageUrl": page.url,
        "memberPath": page.path,
        "pageId": page_id,
        "status": "missing-page-id",
        "apiTitle": None,
        "templates": [],
        "categories": [],
        "queryPageIds": [],
        "requestedAt": None,
        "checkedAt": None,
        "htmlFetchedAt": html_at,
        "responseSha256s": [],
        "scope": SCOPE,
        "moduleCoverage": "unknown",
    }


def observe_page_relationships(
    pages: Iterable[Any],
    page_ids: dict[str, int],
    work: Path,
    *,
    site: Any,
    language: str,
) -> dict[str, Any]:
    """Observe paginated lists; errors leave explicitly unknown relationships."""
    from infinity_wiki_acquisition import cli

    records: dict[str, dict[str, Any]] = {}
    indexed: dict[int, list[Any]] = {}
    for page in pages:
        page_id = page_ids.get(page.url)
        evidence = read_page_evidence(work, page.url, path=page.path)
        html_at = evidence.get("fetchedAt") if evidence else None
        if html_at is not None:
            _time(html_at)
        record = _record(page, page_id, html_at)
        records[page.url] = record
        if type(page_id) is int and page_id > 0:
            indexed.setdefault(page_id, []).append(page)

    ids = sorted(indexed)
    for offset in range(0, len(ids), BATCH_SIZE):
        batch = ids[offset:offset + BATCH_SIZE]
        requested = _now()
        hashes: list[str] = []
        aggregate: dict[int, dict[str, Any]] = {}
        continuation: dict[str, str] = {}
        seen_tokens: set[tuple[tuple[str, str], ...]] = set()
        complete = False
        try:
            for _ in range(MAX_REQUESTS_PER_BATCH):
                params = {
                    "action": "query", "format": "json", "formatversion": "2",
                    "prop": "templates|categories",
                    "pageids": "|".join(map(str, batch)),
                    "tllimit": "max", "cllimit": "max",
                    **continuation,
                }
                url = f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"
                payload = cli.fetch_site_bytes(url, language=language, site=site)
                response = json.loads(payload)
                if (not isinstance(response, dict)
                        or "errors" in response or "warnings" in response
                        or not isinstance(response.get("query"), dict)
                        or "redirects" in response["query"]
                        or not isinstance(response["query"].get("pages"), list)):
                    raise ValueError("Unusable MediaWiki relationship API response")
                seen_in_response: set[int] = set()
                for item in response["query"]["pages"]:
                    if not isinstance(item, dict):
                        raise ValueError("Malformed relationship page")
                    page_id = item.get("pageid")
                    if (type(page_id) is not int or page_id not in batch
                            or page_id in seen_in_response or item.get("missing")):
                        raise ValueError("Conflicting relationship page identity")
                    seen_in_response.add(page_id)
                    title = item.get("title")
                    if not isinstance(title, str) or not title.strip():
                        raise ValueError("Relationship page has no title")
                    templates = _items(item.get("templates"), category=False)
                    categories = _items(item.get("categories"), category=True)
                    previous = aggregate.setdefault(page_id, {
                        "title": title, "templates": set(), "categories": set(),
                    })
                    if not _same_title(previous["title"], title):
                        raise ValueError("Relationship API title changed during pagination")
                    previous["templates"].update(
                        (part["ns"], part["title"]) for part in templates
                    )
                    previous["categories"].update(
                        (part["ns"], part["title"]) for part in categories
                    )
                hashes.append(hashlib.sha256(payload).hexdigest())
                token = response.get("continue")
                if token is None:
                    complete = True
                    break
                if (not isinstance(token, dict) or "continue" not in token
                        or set(token) - {"continue", "tlcontinue", "clcontinue"}
                        or not (set(token) & {"tlcontinue", "clcontinue"})
                        or any(not isinstance(value, str) or not value
                               for value in token.values())):
                    raise ValueError("Unusable relationship continuation")
                next_token = tuple(sorted(token.items()))
                if next_token in seen_tokens:
                    raise ValueError("Repeated relationship continuation")
                seen_tokens.add(next_token)
                continuation = token
        except Exception:
            # Optional research observations must never turn partial API
            # results into claims of complete template/category lists.
            complete = False
        checked = _now()
        for page_id in batch:
            for page in indexed[page_id]:
                record = records[page.url]
                record.update(
                    status="api-unavailable",
                    queryPageIds=batch,
                    requestedAt=requested,
                    checkedAt=checked,
                    responseSha256s=hashes,
                )
                if not complete or page_id not in aggregate:
                    continue
                item = aggregate[page_id]
                record["apiTitle"] = item["title"]
                expected = cli.page_title_from_url(
                    page.url, site=site, language=language,
                )
                if not _same_title(expected, item["title"]):
                    record["status"] = "identity-unresolved"
                    continue
                record.update(
                    status="observed",
                    templates=[
                        {"ns": ns, "title": title}
                        for ns, title in sorted(item["templates"])
                    ],
                    categories=[
                        {"ns": ns, "title": title}
                        for ns, title in sorted(item["categories"])
                    ],
                )

    document = {
        "format": FORMAT, "version": VERSION,
        "site": site.key, "language": language, "records": records,
    }
    target = work / FILENAME
    temp = target.with_name(target.name + ".part")
    temp.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return document


def validate_page_relationship(record: Any, *, url: str) -> None:
    """Fail closed on contradictory or promoted optional source claims."""
    if (not isinstance(record, dict) or set(record) != {
        "pageUrl", "memberPath", "pageId", "status", "apiTitle",
        "templates", "categories", "queryPageIds", "requestedAt", "checkedAt",
        "htmlFetchedAt", "responseSha256s", "scope", "moduleCoverage",
    } or record["pageUrl"] != url
            or not isinstance(record["memberPath"], str)
            or not record["memberPath"]
            or record["status"] not in STATUSES
            or record["scope"] != SCOPE
            or record["moduleCoverage"] != "unknown"):
        raise ValueError("Invalid page relationship identity or scope")
    page_id = record["pageId"]
    if page_id is not None and (type(page_id) is not int or page_id <= 0):
        raise ValueError("Invalid relationship page ID")
    for field, ns in (("templates", None), ("categories", 14)):
        items = record[field]
        if not isinstance(items, list):
            raise ValueError("Invalid relationship list")
        keys = []
        for item in items:
            if (not isinstance(item, dict)
                    or set(item) != {"ns", "title"}
                    or type(item["ns"]) is not int or item["ns"] < 0
                    or (ns is not None and item["ns"] != ns)
                    or not isinstance(item["title"], str)
                    or not item["title"].strip()):
                raise ValueError("Invalid relationship namespace/title")
            keys.append((item["ns"], item["title"]))
        if keys != sorted(set(keys)):
            raise ValueError("Relationships must be sorted and distinct")
    requested, checked, fetched = (
        record["requestedAt"], record["checkedAt"], record["htmlFetchedAt"],
    )
    if fetched is not None:
        _time(fetched)
    ids = record["queryPageIds"]
    hashes = record["responseSha256s"]
    if (not isinstance(ids, list) or any(type(i) is not int or i <= 0 for i in ids)
            or ids != sorted(set(ids)) or len(ids) > BATCH_SIZE
            or not isinstance(hashes, list) or len(hashes) > MAX_REQUESTS_PER_BATCH
            or any(not isinstance(item, str) or len(item) != 64
                   or any(ch not in "0123456789abcdef" for ch in item)
                   for item in hashes)):
        raise ValueError("Invalid relationship request evidence")
    if record["status"] == "missing-page-id":
        if (page_id is not None or ids or hashes or requested is not None
                or checked is not None or record["apiTitle"] is not None):
            raise ValueError("Unqueried relationship cannot claim API evidence")
    else:
        if (page_id is None or page_id not in ids or requested is None
                or checked is None or _time(requested) > _time(checked)
                or (fetched is not None and _time(fetched) > _time(requested))):
            raise ValueError("Relationship query lacks valid page/time evidence")
    if record["status"] in {"observed", "identity-unresolved"}:
        if (not hashes or not isinstance(record["apiTitle"], str)
                or not record["apiTitle"].strip()):
            raise ValueError("Incomplete relationship observation")
    elif record["apiTitle"] is not None:
        raise ValueError("Unknown relationship status claimed an API title")
    if record["status"] != "observed" and (
        record["templates"] or record["categories"]
    ):
        raise ValueError("Incomplete relationship query claimed page relationships")


def load_page_relationship_evidence(
    work: Path, *, site: str, language: str,
) -> dict[str, dict[str, Any]] | None:
    path = work / FILENAME
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Unreadable MediaWiki relationship evidence") from exc
    if (not isinstance(doc, dict)
            or set(doc) != {"format", "version", "site", "language", "records"}
            or doc["format"] != FORMAT or doc["version"] != VERSION
            or doc["site"] != site or doc["language"] != language
            or not isinstance(doc["records"], dict)):
        raise ValueError("MediaWiki relationship evidence has invalid scope")
    for url, row in doc["records"].items():
        validate_page_relationship(row, url=url)
    return doc["records"]


def assess_relationship_coverage(members: Iterable[dict[str, Any]]) -> dict[str, Any]:
    pages = [item for item in members if item["kind"] == "current-page"]
    observed = [
        item["pageRelationships"] for item in pages
        if "pageRelationships" in item
    ]
    return {
        "scope": SCOPE,
        "pageCount": len(pages),
        "observedCount": sum(item["status"] == "observed" for item in observed),
        "unknownCount": sum(item["status"] != "observed" for item in observed),
        "templatePageCount": sum(
            bool(item["templates"]) for item in observed
            if item["status"] == "observed"
        ),
        "categorizedPageCount": sum(
            bool(item["categories"]) for item in observed
            if item["status"] == "observed"
        ),
        "renderCorrespondence": "not-verified",
        "directness": "not-established",
        "transitiveClosure": "not-established",
        "moduleDependencies": "unknown",
    }
