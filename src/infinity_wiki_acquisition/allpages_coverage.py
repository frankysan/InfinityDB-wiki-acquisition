"""Read-only comparison of a verified wiki snapshot with an allpages listing.

The source listing is observed later than the snapshot; it proves neither
an atomic as-of snapshot nor transitive rendered-template freshness.
No source HTML is downloaded, and input ZIP/manifest/inventory are never
modified. Live enumeration is a separately explicit opt-in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any


def collect_live_main_namespace(*, language: str) -> tuple[Any, ...]:
    """Explicit bounded-memory main-namespace API enumeration, no page fetch."""
    from infinity_wiki_acquisition import cli

    site = cli.INFINITY_WIKI_SITE
    seen_tokens: set[str] = set()
    continuation: str | None = None
    collected = []
    while True:
        batch, next_token = cli.fetch_all_page_title_batch(
            language=language, continuation=continuation, site=site,
        )
        collected.extend(batch)
        if next_token is None:
            return tuple(collected)
        if next_token in seen_tokens:
            raise ValueError("MediaWiki allpages continuation token repeated")
        seen_tokens.add(next_token)
        continuation = next_token
        if len(seen_tokens) > 10000:
            raise ValueError("MediaWiki allpages continuation exceeds safe batch count")


def compare_verified_rules_snapshot(
    archive: Path, manifest: Path, inventory: Path,
    *, enumerated: Iterable[Any],
) -> dict[str, Any]:
    """Independently verify archive and compare exact source URLs and page IDs."""
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.snapshot_provenance import sha256_file
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

    document = verify_snapshot(archive, manifest, inventory)
    if document["site"] != cli.INFINITY_WIKI_SITE.key:
        raise ValueError("This audit requires a verified Rules Wiki snapshot")
    language = document["language"]
    site = cli.INFINITY_WIKI_SITE
    observed: dict[str, tuple[str, int]] = {}
    ids: dict[int, str] = {}
    for raw_title in enumerated:
        title = str(raw_title)
        page_id = getattr(raw_title, "page_id", None)
        if not title or type(page_id) is not int or page_id <= 0:
            raise ValueError("Complete allpages comparison requires titles and numeric page IDs")
        url = cli.canonical_crawl_url(
            cli.page_url_from_title(title, site=site, language=language),
            site=site,
        )
        if url in observed and observed[url] != (title, page_id):
            raise ValueError(f"Conflicting allpages URL identity: {url}")
        if page_id in ids and ids[page_id] != url:
            raise ValueError(f"Conflicting allpages page ID: {page_id}")
        ids[page_id] = url
        observed[url] = (title, page_id)
    if not observed:
        raise ValueError("Allpages comparison requires a nonempty complete API listing")

    archived = {
        item["page"]["url"]: item
        for item in document["members"] if item["kind"] == "current-page"
    }
    missing = [
        {"url": url, "title": title, "pageId": page_id}
        for url, (title, page_id) in sorted(observed.items())
        if url not in archived
    ]
    id_mismatch = [
        {
            "url": url,
            "title": title,
            "apiPageId": page_id,
            "archivedPageId": archived[url]["sourceIdentity"]["pageId"],
        }
        for url, (title, page_id) in sorted(observed.items())
        if url in archived and archived[url]["sourceIdentity"]["pageId"] is not None
        and archived[url]["sourceIdentity"]["pageId"] != page_id
    ]
    matched = set(archived) & set(observed)
    unlisted = sorted(set(archived) - set(observed))
    missing_ids = sum(
        archived[url]["sourceIdentity"]["pageId"] is None
        for url in matched
    )
    return {
        "format": "InfinityDB wiki read-only allpages coverage comparison",
        "version": 1,
        "site": document["site"],
        "language": language,
        "snapshotAcquiredAt": document["snapshot"]["acquiredAt"],
        "enumerationObservedAt": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "snapshotSha256": sha256_file(archive),
        "manifestSha256": sha256_file(manifest),
        "inventorySha256": sha256_file(inventory),
        "mainNamespaceEnumeration": "observed-not-source-atomic",
        "archiveCurrentPageCount": len(archived),
        "apiMainNamespacePageCount": len(observed),
        "apiPagesFoundInSnapshot": len(matched),
        "apiPagesAbsentFromSnapshot": len(missing),
        "apiMissingPages": missing,
        "apiPageIdMismatchCount": len(id_mismatch),
        "apiPageIdMismatches": id_mismatch,
        "matchingPagesWithoutArchivedNumericId": missing_ids,
        "archivePagesNotInApiMainNamespaceCount": len(unlisted),
        "archivePagesNotInApiMainNamespace": unlisted,
        "coverageStatus": (
            "missing-or-conflicting-api-pages"
            if missing or id_mismatch else "no-observed-api-url-gap"
        ),
        "sourceTimeAtomicity": "not-verified",
        "reuseAuthorized": False,
    }
