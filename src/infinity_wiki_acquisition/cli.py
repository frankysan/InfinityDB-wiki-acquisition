#!/usr/bin/env python3
"""Download one timestamped local mirror snapshot of a configured Infinity MediaWiki site.

The downloader stages the mirror in a local work directory, rewrites local links,
then stores the complete result as a timestamped ZIP archive. Optional history mode
also retains every revision advertised by MediaWiki for each mirrored page. Successful
runs remove their work directory; incomplete runs preserve it for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import datetime
from html import escape as escape_html, unescape as unescape_html
from html.parser import HTMLParser
from pathlib import Path, PurePath
from typing import NamedTuple, TextIO

from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME,
    CHECKPOINT_FORMAT,
    CHECKPOINT_VERSION,
    read_crawl_checkpoint,
    write_crawl_checkpoint,
)
from infinity_wiki_acquisition.history_checkpoint import (
    HISTORY_CHECKPOINT_FILENAME,
    download_history_checkpointed,
)
from infinity_wiki_acquisition.page_evidence import (
    EVIDENCE_FORMAT,
    EVIDENCE_VERSION,
    write_page_evidence,
)
from infinity_wiki_acquisition.path_sanitization import (
    sanitize_path_component as _sanitize_path_component,
)
from infinity_wiki_acquisition.path_sanitization import sanitize_relative_path
from infinity_wiki_acquisition.snapshot_archive import create_timestamped_archive
from infinity_wiki_acquisition.snapshot_inventory import write_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import write_snapshot_manifest

sanitize_path_component = _sanitize_path_component

HISTORY_DIRECTORY = "_history"
HISTORY_INDEX_FORMAT = "InfinityDB wiki revision history"
HISTORY_INDEX_VERSION = 1
FETCH_ATTEMPTS = 5
FETCH_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)
RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0"
ASSET_EXTENSIONS = frozenset(
    {
        ".avif",
        ".css",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".png",
        ".pdf",
        ".rar",
        ".svg",
        ".txt",
        ".webp",
        ".woff",
        ".woff2",
        ".zip",
    }
)


class WikiSite(NamedTuple):
    """Static acquisition contract for one MediaWiki installation."""

    key: str
    label: str
    languages: tuple[str, ...]
    root_urls: dict[str, str]
    api_urls: dict[str, str]
    index_urls: dict[str, str]
    page_hosts: frozenset[str]
    canonical_host: str
    asset_hosts: frozenset[str]
    archive_prefix: str
    archive_language: bool
    project_namespaces: frozenset[str]
    ignored_namespaces: frozenset[str] = frozenset()
    ignored_path_prefixes: tuple[str, ...] = ()
    ignored_discovered_http_statuses: frozenset[int] = frozenset()
    enumerate_pages: bool = False
    request_delay_seconds: float = 0.0
    page_path_suffix: str = ""


INFINITY_WIKI_SITE = WikiSite(
    key="infinity-wiki",
    label="Infinity Wiki",
    languages=("en", "es"),
    root_urls={
        "en": "https://infinitythewiki.com/",
        "es": "https://infinitythewiki.com/es/",
    },
    api_urls={
        "en": "https://infinitythewiki.com/api.php",
        "es": "https://infinitythewiki.com/wiki-es/api.php",
    },
    index_urls={
        "en": "https://infinitythewiki.com/index.php",
        "es": "https://infinitythewiki.com/wiki-es/index.php",
    },
    page_hosts=frozenset({"infinitythewiki.com"}),
    canonical_host="infinitythewiki.com",
    asset_hosts=frozenset({"assets.corvusbelli.net"}),
    archive_prefix="WIKI",
    archive_language=True,
    project_namespaces=frozenset({"infinity"}),
)

HUMAN_SPHERE_SITE = WikiSite(
    key="human-sphere",
    label="Human Sphere",
    languages=("en",),
    root_urls={"en": "https://www.human-sphere.com/Main_Page"},
    api_urls={"en": "https://www.human-sphere.com/api.php"},
    index_urls={"en": "https://www.human-sphere.com/index.php"},
    page_hosts=frozenset({"human-sphere.com", "www.human-sphere.com"}),
    canonical_host="www.human-sphere.com",
    asset_hosts=frozenset(),
    archive_prefix="HUMAN-SPHERE",
    archive_language=False,
    project_namespaces=frozenset({"human sphere"}),
    ignored_namespaces=frozenset({"talk"}),
    ignored_path_prefixes=("/rest.php/", "/cdn-cgi/"),
    ignored_discovered_http_statuses=frozenset({404}),
    enumerate_pages=True,
    request_delay_seconds=0.5,
    page_path_suffix=".html",
)

WIKI_SITES = {
    INFINITY_WIKI_SITE.key: INFINITY_WIKI_SITE,
    HUMAN_SPHERE_SITE.key: HUMAN_SPHERE_SITE,
}
DEFAULT_SITE = INFINITY_WIKI_SITE

# Backwards-compatible public constants used by existing callers/tests.
ROOT_URL = DEFAULT_SITE.root_urls["en"]
SUPPORTED_LANGUAGES = DEFAULT_SITE.languages
LANGUAGE_ROOT_URLS = DEFAULT_SITE.root_urls
LANGUAGE_API_URLS = DEFAULT_SITE.api_urls
LANGUAGE_INDEX_URLS = DEFAULT_SITE.index_urls
LANGUAGE_ACCEPT_HEADERS = {
    "en": "en-US,en;q=0.9",
    "es": "es-ES,es;q=0.9,en;q=0.5",
}
ALLOWED_HOSTS = set(DEFAULT_SITE.page_hosts | DEFAULT_SITE.asset_hosts)


class WikiLink(NamedTuple):
    """One link discovered in an included HTML page."""

    value: str
    asset: bool


class WikiFetchedBytes(bytes):
    """HTTP payload with observed redirect metadata, still usable as bytes."""

    def __new__(
        cls, data: bytes, *, effective_url: str, http_status: int | None,
        etag: str | None = None, last_modified: str | None = None,
        conditional_etag: str | None = None,
    ) -> "WikiFetchedBytes":
        instance = bytes.__new__(cls, data)
        instance.effective_url = effective_url
        instance.http_status = http_status
        instance.etag = etag
        instance.last_modified = last_modified
        instance.conditional_etag = conditional_etag
        return instance


class WikiReference(NamedTuple):
    """Source HTML reference type, separate from crawler page/asset decision."""

    value: str
    asset: bool
    relation: str


class WikiEnumeratedTitle(str):
    """Allpages title with its independent MediaWiki page ID, when supplied."""

    __slots__ = ("page_id",)

    def __new__(cls, title: str, page_id: int | None = None) -> "WikiEnumeratedTitle":
        instance = str.__new__(cls, title)
        instance.page_id = page_id
        return instance


class WikiDownloadFailure(NamedTuple):
    """One required mirror URL that could not be fetched."""

    url: str
    error: str


class WikiIgnoredURL(NamedTuple):
    """One optional site URL excluded from snapshot completeness."""

    url: str
    reason: str


class WikiPage(NamedTuple):
    """One mirrored wiki page with its source identity."""

    url: str
    path: str
    title: str


class WikiRevision(NamedTuple):
    """One MediaWiki revision advertised for a mirrored page."""

    oldid: int
    parentid: int
    timestamp: str


class WikiDownloadResult(NamedTuple):
    """Complete crawl result retained until snapshot publication."""

    files: tuple[Path, ...]
    failures: tuple[WikiDownloadFailure, ...]
    ignored: tuple[WikiIgnoredURL, ...] = ()
    pages: tuple[WikiPage, ...] = ()


class WikiHistoryResult(NamedTuple):
    """Optional historical revision acquisition retained until publication."""

    files: tuple[Path, ...]
    failures: tuple[WikiDownloadFailure, ...]
    revision_count: int


class WikiCrawlProgress(NamedTuple):
    """One progress update emitted before fetching an eligible URL."""

    attempted: int
    saved: int
    failed: int
    queued: int
    url: str
    ignored: int = 0
    error: str | None = None


class WikiHistoryProgress(NamedTuple):
    """One progress update emitted before fetching an old revision."""

    page: int
    pages: int
    saved: int
    failed: int
    url: str


class ConsoleProgressReporter:
    """Render compact crawl progress without flooding interactive terminals."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self._last_length = 0

    def __call__(self, progress: WikiCrawlProgress) -> None:
        if progress.error is not None:
            # Keep long crawls diagnosable without printing thousands of errors.
            if progress.failed <= 10 or progress.failed % 100 == 0:
                if self.stream.isatty() and self._last_length:
                    print(file=self.stream, flush=True)
                    self._last_length = 0
                print(
                    f"Wiki crawl failure {progress.failed}: "
                    f"{progress.url}: {progress.error}",
                    file=sys.stderr,
                    flush=True,
                )
            return
        message = (
            f"Wiki crawl: fetching {progress.attempted} | saved {progress.saved} | "
            f"ignored {progress.ignored} | failed {progress.failed} | "
            f"queued {progress.queued} | {progress.url}"
        )
        if self.stream.isatty():
            width = max(1, shutil.get_terminal_size(fallback=(120, 24)).columns - 1)
            visible = message[:width]
            padding = " " * max(0, self._last_length - len(visible))
            print(f"\r{visible}{padding}", end="", file=self.stream, flush=True)
            self._last_length = len(visible)
            return

        if progress.attempted == 1 or progress.attempted % 25 == 0:
            print(message, file=self.stream, flush=True)

    def finish(self) -> None:
        """Terminate an interactive in-place progress line cleanly."""
        if self.stream.isatty() and self._last_length:
            print(file=self.stream, flush=True)
            self._last_length = 0


class ConsoleHistoryProgressReporter:
    """Render compact historical-revision progress."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self._last_length = 0

    def __call__(self, progress: WikiHistoryProgress) -> None:
        message = (
            f"Wiki history: page {progress.page}/{progress.pages} | "
            f"saved {progress.saved} | failed {progress.failed} | {progress.url}"
        )
        if self.stream.isatty():
            width = max(1, shutil.get_terminal_size(fallback=(120, 24)).columns - 1)
            visible = message[:width]
            padding = " " * max(0, self._last_length - len(visible))
            print(f"\r{visible}{padding}", end="", file=self.stream, flush=True)
            self._last_length = len(visible)
            return

        if progress.saved == 0 or progress.saved % 100 == 0:
            print(message, file=self.stream, flush=True)

    def finish(self) -> None:
        """Terminate an interactive in-place progress line cleanly."""
        if self.stream.isatty() and self._last_length:
            print(file=self.stream, flush=True)
            self._last_length = 0


def srcset_candidates(value: str) -> tuple[tuple[str, str], ...]:
    """Split MediaWiki's comma-separated image candidates, preserving descriptors."""
    if "data:" in value.casefold():
        return ()  # Data URLs contain commas and are never downloaded.
    candidates: list[tuple[str, str]] = []
    for part in value.split(","):
        parts = part.strip().split(None, 1)
        if parts:
            candidates.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return tuple(candidates)


class LinkExtractor(HTMLParser):
    """Collect page links and explicitly referenced assets from one HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[WikiLink] = []
        self.references: list[WikiReference] = []
        self.redirect_hints: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        rel = (attributes.get("rel") or "").casefold().split()
        if tag == "meta" and (attributes.get("http-equiv") or "").casefold() == "refresh":
            content = attributes.get("content") or ""
            hinted = re.search(r"""\burl\s*=\s*['"]?([^;'"\s]+)""", content, re.I)
            if hinted:
                self.redirect_hints.append(unescape_html(hinted.group(1)))
        # A canonical/license link is a page reference, not a binary download.
        # Stylesheets and preload hints can point to extensionless assets.
        linked_resource = tag.lower() == "link" and (
            "stylesheet" in rel
            or (
                "preload" in rel
                and (attributes.get("as") or "").casefold()
                in {"style", "script", "image", "font"}
            )
        )
        for name, value in attrs:
            if name == "srcset" and value:
                for candidate, _ in srcset_candidates(value):
                    self.links.append(WikiLink(value=candidate, asset=True))
                    self.references.append(
                        WikiReference(value=candidate, asset=True, relation="responsive-image")
                    )
                continue
            if name not in {"href", "src"} or not value:
                continue
            asset = (
                name == "src" or linked_resource or is_asset_url(value)
            ) and not is_mediawiki_namespace_page_url(value)
            self.links.append(WikiLink(value=value, asset=asset))
            if tag == "link" and "canonical" in rel:
                relation = "declared-canonical"
            elif tag == "link" and "stylesheet" in rel:
                relation = "stylesheet"
            elif tag == "link" and "preload" in rel:
                relation = "preload"
            elif tag == "script" and name == "src":
                relation = "script"
            elif tag == "img" and name == "src":
                relation = "embedded-image"
            elif name == "src":
                relation = "embedded-resource"
            else:
                relation = "navigation"
            self.references.append(
                WikiReference(value=value, asset=asset, relation=relation)
            )


def _reference_target_type(url: str, *, asset: bool) -> str:
    """Classify a source reference without asserting transclusion."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).casefold()
    for part in path.split("/"):
        prefix, separator, _tail = part.partition(":")
        if not separator:
            continue
        namespace = prefix.replace("_", " ")
        if namespace in {"template", "module", "mediawiki", "category"}:
            return f"{namespace}-page"
        if namespace in {"file", "image"}:
            return "file-description-page"
    return "asset" if asset else "page"


def build_page_evidence(
    *,
    url: str,
    path: str,
    payload: bytes,
    parser: LinkExtractor,
    site: WikiSite,
) -> dict[str, object]:
    """Capture original-HTML claims before link rewriting.

    A hyperlink to a template/module is not proof of transclusion. Canonical
    and meta-refresh hints are not confirmed HTTP redirects or aliases.
    """
    references: list[dict[str, str]] = []
    canonical_hints: set[str] = set()
    redirect_hints: set[str] = set()
    seen: set[tuple[str, str, str, str]] = set()

    def add(value: str, *, asset: bool, relation: str, evidence: str) -> None:
        if value.lower().startswith(("javascript:", "mailto:", "data:")):
            return
        absolute = urllib.parse.urljoin(url, unescape_html(value))
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.scheme not in {"http", "https"}:
            return
        # Keep the exact observed query-bearing URL distinct from the
        # crawler's fetch identity (which strips page query variants).
        observed_url = urllib.parse.urldefrag(absolute).url
        target = canonical_crawl_url(absolute, asset=asset, site=site)
        target_type = _reference_target_type(target, asset=asset)
        if relation == "navigation" and target_type == "page":
            return  # Ordinary navigation is not an acquisition dependency.
        key = (target, relation, evidence, observed_url)
        if key in seen:
            return
        seen.add(key)
        references.append({
            "targetUrl": target,
            "observedUrl": observed_url,
            "relation": relation,
            "targetType": target_type,
            "evidence": evidence,
        })
        if relation == "declared-canonical":
            canonical_hints.add(observed_url)
        elif relation == "meta-refresh":
            redirect_hints.add(observed_url)

    for ref in parser.references:
        add(
            ref.value, asset=ref.asset, relation=ref.relation,
            evidence="source-html",
        )
        if ref.asset and site.key == HUMAN_SPHERE_SITE.key:
            original_url = mediawiki_original_image_url(
                urllib.parse.urljoin(url, ref.value)
            )
            if original_url is not None:
                add(
                    original_url,
                    asset=True,
                    relation="original-upload-candidate",
                    evidence="crawler-derived",
                )
    for hint in parser.redirect_hints:
        add(hint, asset=False, relation="meta-refresh", evidence="source-html")

    markup = payload.decode("utf-8", errors="replace")
    match = re.search(r"""["']wgArticleId["']\s*:\s*(\d+)""", markup)
    markup_id = int(match.group(1)) if match else None
    if markup_id is not None and markup_id <= 0:
        markup_id = None
    return {
        "format": EVIDENCE_FORMAT,
        "version": EVIDENCE_VERSION,
        "url": url,
        "path": path,
        "originalSha256": hashlib.sha256(payload).hexdigest(),
        "htmlPageIdHint": markup_id,
        "httpEffectiveUrl": getattr(payload, "effective_url", None),
        "httpStatus": getattr(payload, "http_status", None),
        "httpRedirectTarget": (
            getattr(payload, "effective_url", None)
            if getattr(payload, "effective_url", None) not in (None, url)
            else None
        ),
        "canonicalHints": sorted(canonical_hints),
        "redirectHints": sorted(redirect_hints),
        "references": sorted(
            references,
            key=lambda item: (
                item["targetUrl"], item["relation"], item["evidence"]
            ),
        ),
    }


def _require_site_language(site: WikiSite, language: str) -> None:
    if language not in site.languages:
        choices = ", ".join(site.languages)
        raise ValueError(
            f"Unsupported {site.label} language {language!r}; expected one of: {choices}"
        )


def _site_accept_language(language: str) -> str:
    return LANGUAGE_ACCEPT_HEADERS.get(language, f"{language},en;q=0.5")


def _site_archive_prefix(
    site: WikiSite,
    *,
    language: str,
    include_history: bool,
) -> str:
    _require_site_language(site, language)
    prefix = site.archive_prefix
    if site.archive_language:
        prefix = f"{prefix}-{language}"
    if include_history:
        prefix = f"{prefix}-history"
    return prefix


def page_title_from_html(
    html_text: str,
    *,
    site: WikiSite = DEFAULT_SITE,
    language: str = "en",
) -> str | None:
    """Extract the MediaWiki source title from a rendered page footer link."""
    _require_site_language(site, language)
    parser = LinkExtractor()
    parser.feed(html_text)
    valid_path = urllib.parse.urlsplit(site.index_urls[language]).path
    for link in reversed(parser.links):
        candidate = urllib.parse.urljoin(site.root_urls[language], link.value)
        parsed = urllib.parse.urlsplit(candidate)
        if (parsed.hostname or "").lower() not in site.page_hosts:
            continue
        if parsed.path != valid_path:
            continue
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("oldid") and query.get("title"):
            return query["title"][0]
    return None


def page_title_from_url(
    url: str,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Derive a MediaWiki title from a language-scoped pretty URL."""
    _require_site_language(site, language)
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).strip("/")
    if site is INFINITY_WIKI_SITE and language == "es" and path.casefold() == "es":
        return "Página principal"
    if (
        site is INFINITY_WIKI_SITE
        and language == "es"
        and path.casefold().startswith("es/")
    ):
        path = path[3:]
    if not path:
        return "Main Page" if language == "en" else "Página principal"
    return path


def revision_query_url(
    title: str,
    *,
    language: str,
    continuation: str | None = None,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Build one MediaWiki API request that enumerates page revisions."""
    _require_site_language(site, language)
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "titles": title,
        "rvprop": "ids|timestamp",
        "rvlimit": "max",
        "rvdir": "newer",
    }
    if continuation is not None:
        params["rvcontinue"] = continuation
    return f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"


def oldid_url(
    title: str,
    oldid: int,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Build the rendered source URL for one exact MediaWiki revision."""
    _require_site_language(site, language)
    query = urllib.parse.urlencode(
        {"title": title, "oldid": str(oldid), "redirect": "no"}
    )
    return f"{site.index_urls[language]}?{query}"


def allpages_query_url(
    *,
    language: str,
    continuation: str | None = None,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Build one MediaWiki request enumerating main-namespace content pages."""
    _require_site_language(site, language)
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "allpages",
        "apnamespace": "0",
        "aplimit": "max",
    }
    if continuation is not None:
        params["apcontinue"] = continuation
    return f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"


def page_url_from_title(
    title: str,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Return a same-site pretty URL for one main-namespace MediaWiki title."""
    _require_site_language(site, language)
    root = urllib.parse.urlsplit(site.root_urls[language])
    base_path = "/"
    if site is INFINITY_WIKI_SITE and language == "es":
        base_path = "/es/"
    title_path = urllib.parse.quote(title.replace(" ", "_"), safe="/:()")
    return urllib.parse.urlunsplit(
        (root.scheme, site.canonical_host, f"{base_path}{title_path}", "", "")
    )


def fetch_page_revision_batch(
    page: WikiPage,
    *,
    language: str,
    continuation: str | None = None,
    site: WikiSite = DEFAULT_SITE,
) -> tuple[str, tuple[WikiRevision, ...], str | None]:
    """Read one MediaWiki revision page and its exact next continuation token."""
    url = revision_query_url(
        page.title, language=language, continuation=continuation, site=site,
    )
    payload = fetch_site_bytes(url, language=language, site=site)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MediaWiki revision response for {page.url}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"Invalid MediaWiki revision response for {page.url}")
    query = document.get("query")
    page_records = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(page_records, list) or len(page_records) != 1:
        raise ValueError(f"MediaWiki revision response did not identify {page.url}")
    page_record = page_records[0]
    if not isinstance(page_record, dict) or "missing" in page_record:
        raise ValueError(f"MediaWiki page not found while reading history: {page.url}")
    title = page_record.get("title")
    if not isinstance(title, str) or not title:
        raise ValueError(f"MediaWiki revision response has no title for {page.url}")

    revisions: list[WikiRevision] = []
    batch = page_record.get("revisions", [])
    if not isinstance(batch, list):
        raise ValueError(f"MediaWiki revision list is invalid for {page.url}")
    for record in batch:
        if not isinstance(record, dict):
            raise ValueError(f"MediaWiki revision entry is invalid for {page.url}")
        oldid = record.get("revid")
        parentid = record.get("parentid")
        timestamp = record.get("timestamp")
        if (
            type(oldid) is not int
            or type(parentid) is not int
            or not isinstance(timestamp, str)
            or not timestamp
        ):
            raise ValueError(f"MediaWiki revision entry is incomplete for {page.url}")
        revisions.append(
            WikiRevision(oldid=oldid, parentid=parentid, timestamp=timestamp)
        )

    continuation_record = document.get("continue")
    if not isinstance(continuation_record, dict):
        return title, tuple(revisions), None
    next_continuation = continuation_record.get("rvcontinue")
    if (
        not isinstance(next_continuation, str)
        or not next_continuation
        or next_continuation == continuation
    ):
        raise ValueError(f"MediaWiki revision continuation is invalid for {page.url}")
    return title, tuple(revisions), next_continuation


def fetch_page_revisions(
    page: WikiPage,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
) -> tuple[str, tuple[WikiRevision, ...]]:
    """Return every public revision advertised for one mirrored page."""
    continuation: str | None = None
    revisions: list[WikiRevision] = []
    canonical_title: str | None = None
    while True:
        title, batch, continuation_next = fetch_page_revision_batch(
            page, language=language, continuation=continuation, site=site,
        )
        if canonical_title is not None and canonical_title != title:
            raise ValueError(f"MediaWiki revision title changed while reading {page.url}")
        canonical_title = title
        revisions.extend(batch)
        continuation = continuation_next
        if continuation is None:
            break
    if canonical_title is None or not revisions:
        raise ValueError(f"MediaWiki returned no revisions for {page.url}")
    return canonical_title, tuple(revisions)

def fetch_all_page_title_batch(
    *,
    language: str,
    continuation: str | None = None,
    site: WikiSite = DEFAULT_SITE,
) -> tuple[tuple[str, ...], str | None]:
    """Fetch one allpages batch and its next continuation token."""
    _require_site_language(site, language)
    url = allpages_query_url(language=language, continuation=continuation, site=site)
    payload = fetch_site_bytes(url, language=language, site=site)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MediaWiki allpages response for {site.label}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"Invalid MediaWiki allpages response for {site.label}")
    query = document.get("query")
    page_records = query.get("allpages") if isinstance(query, dict) else None
    if not isinstance(page_records, list):
        raise ValueError(f"MediaWiki allpages response is invalid for {site.label}")
    titles: list[str] = []
    for record in page_records:
        if not isinstance(record, dict):
            raise ValueError(f"MediaWiki allpages entry is invalid for {site.label}")
        title = record.get("title")
        if not isinstance(title, str) or not title:
            raise ValueError(f"MediaWiki allpages entry has no title for {site.label}")
        page_id = record.get("pageid")
        if page_id is not None and (type(page_id) is not int or page_id <= 0):
            raise ValueError(f"Invalid MediaWiki page ID for {title!r}")
        titles.append(WikiEnumeratedTitle(title, page_id))

    continuation_record = document.get("continue")
    if not isinstance(continuation_record, dict):
        return tuple(titles), None
    next_token = continuation_record.get("apcontinue")
    if not isinstance(next_token, str) or not next_token or next_token == continuation:
        raise ValueError(f"MediaWiki allpages continuation is invalid for {site.label}")
    return tuple(titles), next_token


def fetch_all_page_titles(
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
) -> tuple[str, ...]:
    """Return all main-namespace MediaWiki page titles for a configured site."""
    continuation: str | None = None
    titles: list[str] = []
    while True:
        batch, continuation = fetch_all_page_title_batch(
            language=language, continuation=continuation, site=site,
        )
        titles.extend(batch)
        if continuation is None:
            break
    return tuple(titles)

def _history_index_payload(
    *,
    language: str,
    pages: list[dict[str, object]],
    revision_count: int,
    site: WikiSite = DEFAULT_SITE,
) -> bytes:
    _require_site_language(site, language)
    document = {
        "format": HISTORY_INDEX_FORMAT,
        "formatVersion": HISTORY_INDEX_VERSION,
        "language": language,
        "pages": pages,
        "revisionCount": revision_count,
        "source": {
            "apiUrl": site.api_urls[language],
            "indexUrl": site.index_urls[language],
        },
    }
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def download_wiki_history(
    pages: tuple[WikiPage, ...],
    destination: Path,
    *,
    language: str = "en",
    site: WikiSite = DEFAULT_SITE,
    progress: Callable[[WikiHistoryProgress], None] | None = None,
    checkpoint_file: Path | None = None,
    reuse_oldid: Callable[[WikiPage, str, int, int, str], bytes | None] | None = None,
) -> WikiHistoryResult:
    """Download rendered oldid revisions, checkpointing when requested."""
    _require_site_language(site, language)
    if not pages:
        raise ValueError("Historical wiki acquisition requires at least one mirrored page")
    if checkpoint_file is not None:
        return download_history_checkpointed(
            pages, destination, language=language, site=site,
            progress=progress, checkpoint_file=checkpoint_file,
            reuse_oldid=reuse_oldid,
        )

    files: set[Path] = set()
    failures: list[WikiDownloadFailure] = []
    history_pages: list[dict[str, object]] = []
    saved_oldids: set[int] = set()

    for page_number, page in enumerate(pages, start=1):
        try:
            canonical_title, revisions = fetch_page_revisions(
                page,
                language=language,
                site=site,
            )
        except Exception as exc:  # pragma: no cover - live network failure path.
            failures.append(
                WikiDownloadFailure(
                    url=revision_query_url(page.title, language=language, site=site),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        revision_records: list[dict[str, object]] = []
        for revision in revisions:
            source_url = oldid_url(
                canonical_title,
                revision.oldid,
                language=language,
                site=site,
            )
            if progress is not None:
                progress(
                    WikiHistoryProgress(
                        page=page_number,
                        pages=len(pages),
                        saved=len(saved_oldids),
                        failed=len(failures),
                        url=source_url,
                    )
                )

            relative = f"{HISTORY_DIRECTORY}/oldid/{revision.oldid}.html"
            target = destination / relative
            if revision.oldid not in saved_oldids:
                if target.is_file():
                    files.add(target)
                    saved_oldids.add(revision.oldid)
                else:
                    try:
                        payload = fetch_site_bytes(
                            source_url,
                            language=language,
                            site=site,
                        )
                    except Exception as exc:  # pragma: no cover - live network failure path.
                        failures.append(
                            WikiDownloadFailure(
                                url=source_url, error=f"{type(exc).__name__}: {exc}"
                            )
                        )
                        continue
                    write_bytes(target, payload)
                    files.add(target)
                    saved_oldids.add(revision.oldid)

            revision_records.append(
                {
                    "oldid": revision.oldid,
                    "parentid": revision.parentid,
                    "path": relative,
                    "timestamp": revision.timestamp,
                    "url": source_url,
                }
            )

        history_pages.append(
            {
                "currentPath": page.path,
                "currentUrl": page.url,
                "revisions": revision_records,
                "title": canonical_title,
            }
        )

    index_path = destination / HISTORY_DIRECTORY / "index.json"
    write_bytes(
        index_path,
        _history_index_payload(
            language=language,
            pages=history_pages,
            revision_count=len(saved_oldids),
            site=site,
        ),
    )
    files.add(index_path)
    return WikiHistoryResult(
        files=tuple(
            sorted(files, key=lambda path: mirror_path_sort_key(path, destination))
        ),
        failures=tuple(failures),
        revision_count=len(saved_oldids),
    )


def ignored_url_reason(url: str, *, site: WikiSite = DEFAULT_SITE) -> str | None:
    """Return why an optional site URL does not affect snapshot completeness."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.hostname or "").lower() not in site.page_hosts:
        return None

    path = urllib.parse.unquote(parsed.path or "/")
    folded_path = path.casefold()
    if folded_path == "/favicon.ico":
        return "optional site favicon"
    if any(folded_path.startswith(prefix.casefold()) for prefix in site.ignored_path_prefixes):
        return "optional site service endpoint"

    segments = [segment.replace("_", " ").casefold() for segment in path.split("/") if segment]
    if any(
        any(segment.startswith(f"{namespace}:") for namespace in site.project_namespaces)
        for segment in segments
    ):
        return "MediaWiki project namespace"
    if any(
        any(segment.startswith(f"{namespace}:") for namespace in site.ignored_namespaces)
        for segment in segments
    ):
        return "optional MediaWiki namespace"

    return None


def ignored_fetch_failure_reason(
    exc: BaseException,
    *,
    required: bool,
    site: WikiSite = DEFAULT_SITE,
) -> str | None:
    """Return why a failed discovered URL may be omitted from a complete snapshot."""
    if required or not isinstance(exc, urllib.error.HTTPError):
        return None
    if exc.code in site.ignored_discovered_http_statuses:
        return f"discovered URL returned HTTP {exc.code}"
    return None


def should_skip_url(url: str, *, site: WikiSite = DEFAULT_SITE) -> bool:
    """Return True when a URL should never be mirrored."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed_hosts = site.page_hosts | site.asset_hosts
    if host and host not in allowed_hosts:
        return True

    path = urllib.parse.unquote(parsed.path).casefold()
    if "index.php" in path:
        return True
    segments = [segment for segment in path.split("/") if segment]
    if any(
        segment in {"special", "especial"}
        or segment.startswith("special:")
        or segment.startswith("especial:")
        for segment in segments
    ):
        return True
    if "javascript:" in url.lower() or "mailto:" in url.lower() or "data:" in url.lower():
        return True
    return False


def mediawiki_original_image_url(url: str) -> str | None:
    """Derive the original upload URL from a standard MediaWiki thumbnail URL."""
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(
        r"(/images)/thumb/([0-9a-f])/([0-9a-f]{2})/([^/]+)/[0-9]+px-[^/]+",
        parsed.path,
        flags=re.I,
    )
    if match is None:
        return None
    directory, first, second, filename = match.groups()
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, f"{directory}/{first}/{second}/{filename}", "", "")
    )


def is_mediawiki_namespace_page_url(url: str) -> bool:
    """Whether an apparent asset is a MediaWiki HTML namespace page.

    A wiki title such as Module:Infobox/styles.css is served as an HTML
    description/source page, despite its stylesheet-looking extension.
    Uploaded binary files still live under /images/.
    """
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).casefold()
    if path.startswith("/images/"):
        return False
    page_namespaces = {
        "file", "image", "module", "mediawiki", "template",
        "category", "user", "help", "portal",
    }
    for segment in path.split("/"):
        namespace, separator, _title = segment.partition(":")
        if not separator:
            continue
        normalized = namespace.replace("_", " ")
        # MediaWiki has a corresponding *_talk namespace for most content
        # namespaces. These are HTML pages even if the title ends in .css.
        if normalized.endswith(" talk"):
            normalized = normalized[:-5]
        if normalized in page_namespaces:
            return True
    return False


def is_asset_url(url: str) -> bool:
    """Return whether a URL path is recognizably an asset resource."""
    path = urllib.parse.urlsplit(url).path
    decoded_path = urllib.parse.unquote(path).casefold()
    if decoded_path.startswith("/images/"):
        return True
    if is_mediawiki_namespace_page_url(url):
        return False
    suffix = Path(path).suffix.casefold()
    return suffix in ASSET_EXTENSIONS or Path(path).name.casefold() == "load.php"


def wiki_page_matches_language(
    url: str,
    language: str,
    *,
    site: WikiSite = DEFAULT_SITE,
) -> bool:
    """Return whether a wiki page URL belongs to the selected language tree."""
    _require_site_language(site, language)

    parsed = urllib.parse.urlsplit(url)
    if (parsed.hostname or "").lower() not in site.page_hosts:
        return False

    if site is not INFINITY_WIKI_SITE:
        return True

    path = urllib.parse.unquote(parsed.path or "/").casefold()
    is_spanish = path == "/es" or path.startswith("/es/")
    return is_spanish if language == "es" else not is_spanish


def canonical_crawl_url(
    url: str,
    *,
    asset: bool = False,
    site: WikiSite = DEFAULT_SITE,
) -> str:
    """Return the fetch identity used for one mirrored page or asset.

    Wiki page query/fragment variants map to the same persisted page and are
    collapsed. Asset queries can select different resources, so their query
    strings remain part of the fetch identity. Fragments never affect fetching.
    """
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    netloc = parsed.netloc.lower()
    scheme = parsed.scheme.lower()
    if hostname in site.page_hosts:
        netloc = site.canonical_host
        scheme = urllib.parse.urlsplit(site.root_urls[site.languages[0]]).scheme
    # An allpages title might encode punctuation (%2C), while rendered HTML
    # spells the same punctuation literally. Normalize each URL path segment
    # before deduplication. Split first so encoded slashes remain distinct
    # from real path separators; preserve literal percent signs by quoting.
    path = "/".join(
        urllib.parse.quote(
            urllib.parse.unquote(segment),
            safe=":@!$&'()*+,;=-._~",
        )
        for segment in (parsed.path or "/").split("/")
    )
    return urllib.parse.urlunsplit(
        (
            scheme,
            netloc,
            path,
            parsed.query if asset else "",
            "",
        )
    )


def local_relative_path(
    url: str,
    *,
    asset: bool = False,
    site: WikiSite = DEFAULT_SITE,
    os_name: str | None = None,
) -> str:
    """Map a mirrored page or asset URL to a relative local filesystem path."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    if path == "/":
        normalized = "index.html"
    else:
        normalized = urllib.parse.unquote(path)
        if normalized.startswith("/"):
            normalized = normalized[1:]
        if not normalized or normalized.endswith("/"):
            normalized = f"{normalized}index.html" if normalized else "index.html"
        elif not asset and site.page_path_suffix:
            normalized = f"{normalized}{site.page_path_suffix}"

    if asset and parsed.query:
        query_hash = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:12]
        candidate = Path(normalized)
        if candidate.suffix:
            normalized = str(
                candidate.with_name(
                    f"{candidate.stem}__q_{query_hash}{candidate.suffix}"
                )
            )
        else:
            normalized = f"{normalized}__q_{query_hash}"

    path_os_name = os_name
    if path_os_name is None:
        path_os_name = "Windows"
    return sanitize_relative_path(
        normalized,
        os_name=path_os_name,
        disambiguate_unicode=site.key == HUMAN_SPHERE_SITE.key,
        disambiguate_page_case=site.key == HUMAN_SPHERE_SITE.key and not asset,
    )


def rewrite_html_links(
    html_text: str,
    base_url: str,
    *,
    language: str = "en",
    site: WikiSite = DEFAULT_SITE,
    os_name: str | None = None,
) -> str:
    """Rewrite included pages/assets to local mirror paths for one language."""
    _require_site_language(site, language)
    pattern = re.compile(
        r"""(?P<attr>(?<![\w-])(?:href|src|srcset)\s*=\s*["'])(?P<value>[^"']+)(?P<quote>["'])""",
        re.I,
    )
    source_path = local_relative_path(base_url, site=site, os_name=os_name)
    source_directory = posixpath.dirname(source_path)

    def page_relative(path: str) -> str:
        return posixpath.relpath(path, start=source_directory or ".")

    def rewrite_value(value: str, *, asset: bool) -> str:
        candidate = urllib.parse.urljoin(base_url, value)
        parsed = urllib.parse.urlparse(candidate)
        hostname = (parsed.hostname or "").lower()

        if value.lower().startswith(("javascript:", "mailto:", "data:")):
            return value
        if ignored_url_reason(candidate, site=site) is not None:
            return candidate

        if hostname in site.asset_hosts:
            relative = page_relative(
                local_relative_path(candidate, asset=True, site=site, os_name=os_name)
            )
            if parsed.fragment:
                relative = f"{relative}#{parsed.fragment}"
            return relative

        if hostname in site.page_hosts or not hostname:
            if not asset and not wiki_page_matches_language(
                candidate, language, site=site
            ):
                return candidate
            relative = page_relative(
                local_relative_path(candidate, asset=asset, site=site, os_name=os_name)
            )
            if parsed.fragment:
                relative = f"{relative}#{parsed.fragment}"
            return relative

        return value

    def replace(match: re.Match[str]) -> str:
        value = unescape_html(match.group("value"))
        attr = match.group("attr").lstrip().split("=", 1)[0].strip().casefold()
        if attr == "srcset":
            if not srcset_candidates(value):
                return match.group(0)
            chunks = []
            for part in value.split(","):
                pieces = re.match(r"(\s*)(\S+)(.*)", part, re.S)
                if pieces is None:
                    chunks.append(part)
                else:
                    chunks.append(
                        pieces.group(1)
                        + rewrite_value(pieces.group(2), asset=True)
                        + pieces.group(3)
                    )
            rewritten = ",".join(chunks)
        else:
            asset = (attr == "src" or is_asset_url(value)) and not (
                is_mediawiki_namespace_page_url(value)
            )
            rewritten = rewrite_value(value, asset=asset)
        return f"{match.group('attr')}{escape_html(rewritten, quote=True)}{match.group('quote')}"

    return pattern.sub(replace, html_text)


def _retryable_fetch_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_STATUS
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def _is_strong_etag(value: object) -> bool:
    """Accept only an unambiguous, quoted, non-weak HTTP entity tag."""
    return (
        isinstance(value, str)
        and len(value) >= 3
        and value[0] == '"'
        and value[-1] == '"'
        and all(0x21 <= ord(char) <= 0x7E and char != '"' for char in value[1:-1])
    )


def fetch_bytes(
    url: str, *, language: str = "en",
    if_none_match: str | None = None,
) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "*/*"
            if is_asset_url(url)
            else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": _site_accept_language(language),
        "Connection": "keep-alive",
    }
    if if_none_match is not None:
        if not _is_strong_etag(if_none_match):
            raise ValueError("Only strong HTTP ETags may be used for conditional reuse")
        headers["If-None-Match"] = if_none_match
    request = urllib.request.Request(url, headers=headers)
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                getter = getattr(response, "geturl", None)
                effective = getter() if callable(getter) else url
                status = getattr(response, "status", None)
                response_headers = getattr(response, "headers", None)
                return WikiFetchedBytes(
                    response.read(),
                    effective_url=effective if isinstance(effective, str) else url,
                    http_status=status if type(status) is int else None,
                    etag=response_headers.get("ETag") if response_headers else None,
                    last_modified=(
                        response_headers.get("Last-Modified")
                        if response_headers else None
                    ),
                    conditional_etag=if_none_match,
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and if_none_match is not None:
                response_headers = exc.headers
                return WikiFetchedBytes(
                    b"", effective_url=exc.geturl(),
                    http_status=304,
                    etag=response_headers.get("ETag") if response_headers else None,
                    last_modified=(
                        response_headers.get("Last-Modified")
                        if response_headers else None
                    ),
                    conditional_etag=if_none_match,
                )
            if not _retryable_fetch_error(exc) or attempt >= FETCH_ATTEMPTS - 1:
                raise
            time.sleep(FETCH_RETRY_DELAYS[attempt])
        except Exception as exc:
            if not _retryable_fetch_error(exc) or attempt >= FETCH_ATTEMPTS - 1:
                raise
            time.sleep(FETCH_RETRY_DELAYS[attempt])
    raise AssertionError("fetch retry loop exhausted unexpectedly")


def fetch_site_bytes(
    url: str,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
    if_none_match: str | None = None,
) -> bytes:
    """Fetch one site URL, applying any source-specific courtesy delay."""
    _require_site_language(site, language)
    if site.request_delay_seconds:
        time.sleep(site.request_delay_seconds)
    if if_none_match is not None:
        return fetch_bytes(
            url, language=language, if_none_match=if_none_match,
        )
    return fetch_bytes(url, language=language)


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".part")
    with temp_path.open("wb") as handle:
        handle.write(payload)

    max_attempts = 12
    for attempt in range(max_attempts):
        try:
            temp_path.replace(path)
            return
        except PermissionError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(0.05 * (2**attempt), 0.5))


def mirror_path_sort_key(path: PurePath, destination: PurePath) -> str:
    """Return a platform-neutral sort key for persisted mirror paths."""
    return path.relative_to(destination).as_posix()


def _migrate_blocking_legacy_page_paths(
    target: Path,
    destination: Path,
    *,
    site: WikiSite,
) -> None:
    """Move legacy extensionless page files that block a new descendant path."""
    if not site.page_path_suffix:
        return

    parents: list[Path] = []
    candidate = target.parent
    while candidate != destination:
        parents.append(candidate)
        if candidate.parent == candidate:
            raise ValueError(f"Mirror target is outside destination: {target}")
        candidate = candidate.parent

    for blocker in reversed(parents):
        if not blocker.is_file():
            continue
        migrated = blocker.with_name(f"{blocker.name}{site.page_path_suffix}")
        if migrated.exists():
            blocker.unlink()
        else:
            blocker.replace(migrated)


def validate_asset_bytes(url: str, payload: bytes) -> None:
    """Refuse HTML challenges and mislabeled image/document payloads."""
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.casefold()
    header = payload[:1024].lstrip().lower()
    if (
        header.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
        or (b"<html" in header and b"</html" in payload[-1024:].lower())
    ):
        raise ValueError("HTML response received for an asset URL")
    signatures = {
        ".png": payload.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": payload.startswith(b"\xff\xd8\xff"),
        ".jpeg": payload.startswith(b"\xff\xd8\xff"),
        ".gif": payload.startswith((b"GIF87a", b"GIF89a")),
        ".webp": payload.startswith(b"RIFF") and payload[8:12] == b"WEBP",
        ".avif": payload[4:8] == b"ftyp" and b"avif" in payload[8:32],
        ".pdf": b"%PDF-" in payload[:1024],
        ".zip": payload.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")),
        ".rar": payload.startswith(b"Rar!\x1a\x07"),
        ".ico": payload.startswith(b"\x00\x00\x01\x00"),
        ".woff": payload.startswith(b"wOFF"),
        ".woff2": payload.startswith(b"wOF2"),
    }
    if suffix in signatures and not signatures[suffix]:
        raise ValueError(f"Invalid {suffix} file signature")


def _rewrite_reclassified_page_references(
    *,
    destination: Path,
    url: str,
    old_asset: bool,
    new_asset: bool,
    site: WikiSite,
    pages: dict[str, WikiPage],
    saved_records: dict[str, list[str | int]],
) -> None:
    """Repair saved HTML links when a failed URL changes page/asset classification.

    A completed crawl checkpoint contains rewritten local HTML, not source
    HTML. When a previous failed URL is reclassified after a code fix, update
    the old local reference in saved HTML without re-fetching those pages.
    """
    old_relative = local_relative_path(url, asset=old_asset, site=site)
    new_relative = local_relative_path(url, asset=new_asset, site=site)
    if old_relative == new_relative:
        return

    pattern = re.compile(
        r"""(?P<prefix>(?<![\w-])(?:href|src|srcset)\s*=\s*["'])(?P<value>[^"']+)(?P<quote>["'])""",
        re.I,
    )
    for page in pages.values():
        target = destination / page.path
        if not target.is_file():
            raise ValueError(f"Saved HTML missing during link reconciliation: {page.path}")
        base = posixpath.dirname(page.path) or "."
        old_link = escape_html(posixpath.relpath(old_relative, base), quote=True)
        new_link = escape_html(posixpath.relpath(new_relative, base), quote=True)
        old_token = re.compile(
            r"(?<![^\s,])" + re.escape(old_link) + r"(?=$|[#,\s])"
        )

        def change(match: re.Match[str]) -> str:
            value = old_token.sub(lambda _: new_link, match.group("value"))
            return match.group("prefix") + value + match.group("quote")

        text = target.read_text(encoding="utf-8")
        updated = pattern.sub(change, text)
        if updated != text:
            payload = updated.encode("utf-8")
            write_bytes(target, payload)
            saved_records[page.url] = [page.path, len(payload)]


def download_wiki(
    base_url: str,
    destination: Path,
    *,
    language: str = "en",
    site: WikiSite = DEFAULT_SITE,
    progress: Callable[[WikiCrawlProgress], None] | None = None,
    checkpoint_file: Path | None = None,
    retry_failed_only: bool = False,
    force_allpages: bool = False,
    asset_fetch: Callable[[str], bytes] | None = None,
) -> WikiDownloadResult:
    """Crawl the source or retry recorded failures from a completed discovery.

    Explicit archive updates force main-namespace enumeration even for sites
    where a normal link crawl starts only from the root.
    """
    _require_site_language(site, language)
    if not wiki_page_matches_language(base_url, language, site=site):
        raise ValueError(f"Wiki root {base_url!r} does not match language {language!r}")

    canonical_base_url = canonical_crawl_url(base_url, site=site)
    if retry_failed_only and checkpoint_file is None:
        raise ValueError("--retry-failed-only requires a crawl checkpoint")

    existing_checkpoint = checkpoint_file is not None and checkpoint_file.is_file()
    if existing_checkpoint or retry_failed_only:
        state = read_crawl_checkpoint(
            checkpoint_file,
            work=destination,
            site=site.key,
            language=language,
            root_url=canonical_base_url,
            require_complete=retry_failed_only,
        )
        phase = state.get("phase", "complete")
        enumeration_continuation: str | None = state.get("enumerationContinuation")
        discovered_assets = dict(state["discovered"])
        page_ids: dict[str, int] = dict(state.get("pageIds", {}))
        required_urls = set(state["required"])
        saved_records: dict[str, list[str | int]] = dict(state["saved"])
        saved: set[Path] = {
            destination / record[0] for record in saved_records.values()
        }
        pages: dict[str, WikiPage] = {
            entry["url"]: WikiPage(entry["url"], entry["path"], entry["title"])
            for entry in state["pages"]
        }
        ignored: dict[str, WikiIgnoredURL] = {
            url: WikiIgnoredURL(url, reason) for url, reason in state["ignored"].items()
        }
        target_urls: dict[str, str] = {
            record[0].casefold(): url for url, record in saved_records.items()
        }
        if len(target_urls) != len(saved):
            raise ValueError("Crawl checkpoint contains colliding saved paths")
        queue: deque[tuple[str, bool]] = deque(
            (url, asset) for url, asset in state.get("queue", [])
        )
        # Continue the pending queue first, then retry failures observed
        # before an interruption. Completed checkpoints have an empty queue.
        pending = {url for url, _asset in queue}
        queue.extend(
            (url, discovered_assets[url])
            for url in state["failed"]
            if url not in pending
        )
    else:
        phase = "enumerating" if checkpoint_file and (site.enumerate_pages or force_allpages) else "crawling"
        enumeration_continuation = None
        initial_urls = [canonical_base_url]
        if (site.enumerate_pages or force_allpages) and checkpoint_file is None:
            try:
                titles = fetch_all_page_titles(language=language, site=site)
            except Exception as exc:  # pragma: no cover - live network failure path.
                return WikiDownloadResult(
                    files=(),
                    failures=(
                        WikiDownloadFailure(
                            url=allpages_query_url(language=language, site=site),
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                    ),
                )
            initial_urls.extend(
                canonical_crawl_url(
                    page_url_from_title(title, language=language, site=site),
                    site=site,
                )
                for title in titles
            )
        required_urls = set(initial_urls)
        discovered_assets = dict.fromkeys(initial_urls, False)
        page_ids = {}
        queue = deque((url, False) for url in discovered_assets)
        saved_records = {}
        saved = set()
        pages = {}
        ignored = {}
        target_urls = {}

    discovered = set(discovered_assets)
    failures: list[WikiDownloadFailure] = []
    attempted = 0

    def save_checkpoint() -> None:
        if checkpoint_file is None:
            return
        write_crawl_checkpoint(
            checkpoint_file,
            {
                "format": CHECKPOINT_FORMAT,
                "version": CHECKPOINT_VERSION,
                "phase": phase,
                "complete": phase == "complete",
                "site": site.key,
                "language": language,
                "rootUrl": canonical_base_url,
                "enumerationContinuation": enumeration_continuation,
                "queue": [[url, asset] for url, asset in queue],
                "discovered": discovered_assets,
                "pageIds": page_ids,
                "required": sorted(required_urls),
                "saved": saved_records,
                "pages": [
                    {"url": page.url, "path": page.path, "title": page.title}
                    for page in sorted(pages.values(), key=lambda page: page.path)
                ],
                "ignored": {url: record.reason for url, record in ignored.items()},
                "failed": {failure.url: failure.error for failure in failures},
            },
        )

    if phase == "enumerating":
        # Persist after each MediaWiki API batch. A stopped enumeration
        # restarts from its last committed apcontinue token.
        save_checkpoint()
        while True:
            try:
                titles, next_continuation = fetch_all_page_title_batch(
                    language=language,
                    continuation=enumeration_continuation,
                    site=site,
                )
            except Exception as exc:
                return WikiDownloadResult(
                    files=tuple(sorted(saved, key=lambda file: file.as_posix())),
                    failures=(
                        WikiDownloadFailure(
                            url=allpages_query_url(
                                language=language,
                                continuation=enumeration_continuation,
                                site=site,
                            ),
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                    ),
                )
            for title in titles:
                url = canonical_crawl_url(
                    page_url_from_title(title, language=language, site=site),
                    site=site,
                )
                required_urls.add(url)
                page_id = getattr(title, "page_id", None)
                if page_id is not None:
                    old_id = page_ids.get(url)
                    if old_id is not None and old_id != page_id:
                        raise ValueError(f"Conflicting enumerated page ID for {url}")
                    page_ids[url] = page_id
                if url not in discovered:
                    discovered.add(url)
                    discovered_assets[url] = False
                    queue.append((url, False))
            enumeration_continuation = next_continuation
            if enumeration_continuation is None:
                phase = "crawling"
            save_checkpoint()
            if phase == "crawling":
                break

    if queue:
        phase = "crawling"

    def record_failure(url: str, error: str) -> None:
        failures.append(WikiDownloadFailure(url=url, error=error))
        if progress is not None:
            progress(
                WikiCrawlProgress(
                    attempted=attempted,
                    saved=len(saved),
                    failed=len(failures),
                    queued=len(queue),
                    url=url,
                    ignored=len(ignored),
                    error=error,
                )
            )

    processed = 0
    while queue:
        # Write only between completed URL attempts. If terminated mid-URL,
        # replay from the last durable queue state (at most 100 attempts).
        if checkpoint_file is not None and processed % 100 == 0:
            save_checkpoint()
        url, asset = queue.popleft()
        processed += 1

        ignore_reason = ignored_url_reason(url, site=site)
        if ignore_reason is not None:
            ignored[url] = WikiIgnoredURL(url=url, reason=ignore_reason)
            continue
        if should_skip_url(url, site=site):
            continue

        # Re-evaluate formerly failed assets after code fixes. A MediaWiki
        # namespace page can turn out to be HTML despite a .css/.png suffix.
        old_asset = asset
        if existing_checkpoint:
            # Account for improved URL classification in either direction,
            # while retaining asset hints from stylesheet/source tags.
            asset = (asset or is_asset_url(url)) and not (
                is_mediawiki_namespace_page_url(url)
            )
        discovered_assets[url] = asset
        relative = local_relative_path(url, asset=asset, site=site)
        target = destination / relative
        portable_key = relative.casefold()
        previous_url = target_urls.get(portable_key)
        if previous_url is not None and previous_url != url:
            record_failure(
                url,
                f"Portable mirror path collision with {previous_url}: {relative}",
            )
            continue
        target_urls[portable_key] = url
        if asset and target.is_file():
            try:
                validate_asset_bytes(url, target.read_bytes())
            except ValueError:
                pass  # A previous interrupted crawl may contain an HTML challenge.
            else:
                saved.add(target)
                saved_records[url] = [relative, target.stat().st_size]
                continue

        attempted += 1
        if progress is not None:
            progress(
                WikiCrawlProgress(
                    attempted=attempted,
                    saved=len(saved),
                    failed=len(failures),
                    queued=len(queue),
                    url=url,
                    ignored=len(ignored),
                )
            )

        try:
            payload = (
                asset_fetch(url) if asset and asset_fetch is not None
                else fetch_site_bytes(url, language=language, site=site)
            )
        except Exception as exc:  # pragma: no cover - network failures are expected in real use.
            failure_ignore_reason = ignored_fetch_failure_reason(
                exc,
                required=url in required_urls,
                site=site,
            )
            if failure_ignore_reason is not None:
                ignored[url] = WikiIgnoredURL(url=url, reason=failure_ignore_reason)
                continue
            record_failure(url, f"{type(exc).__name__}: {exc}")
            continue

        if asset:
            try:
                validate_asset_bytes(url, payload)
            except ValueError as exc:
                record_failure(url, f"{type(exc).__name__}: {exc}")
                continue

        _migrate_blocking_legacy_page_paths(target, destination, site=site)
        write_bytes(target, payload)
        saved.add(target)
        saved_records[url] = [relative, target.stat().st_size]
        if asset and checkpoint_file is not None:
            from infinity_wiki_acquisition.asset_evidence import (
                build_asset_evidence, write_asset_evidence,
            )

            observed = build_asset_evidence(url, relative, payload)
            if observed is not None:
                write_asset_evidence(destination, observed)

        if existing_checkpoint and asset and old_asset != asset:
            _rewrite_reclassified_page_references(
                destination=destination,
                url=url,
                old_asset=old_asset,
                new_asset=asset,
                site=site,
                pages=pages,
                saved_records=saved_records,
            )
            # Rewriting previously saved HTML changes recorded file sizes.
            # Commit the entire consistent state before another interruption.
            save_checkpoint()
        if asset:
            continue

        if site.page_path_suffix:
            legacy_site = site._replace(page_path_suffix="")
            legacy_relative = local_relative_path(url, site=legacy_site)
            legacy_target = destination / legacy_relative
            if legacy_target != target and legacy_target.is_file():
                legacy_target.unlink()

        text = payload.decode("utf-8", errors="replace")
        if not any(token in text.lower() for token in ("<html", "<body", "href=", "src=")):
            continue

        pages[url] = WikiPage(
            url=url,
            path=relative,
            title=page_title_from_html(text, site=site, language=language)
            or page_title_from_url(url, language=language, site=site),
        )
        rewritten_text = rewrite_html_links(text, url, language=language, site=site)
        write_bytes(target, rewritten_text.encode("utf-8"))
        saved_records[url] = [relative, target.stat().st_size]

        if existing_checkpoint and old_asset != asset:
            _rewrite_reclassified_page_references(
                destination=destination,
                url=url,
                old_asset=old_asset,
                new_asset=asset,
                site=site,
                pages=pages,
                saved_records=saved_records,
            )

        parser = LinkExtractor()
        parser.feed(text)
        if checkpoint_file is not None:
            evidence = build_page_evidence(
                url=url,
                path=relative,
                payload=payload,
                parser=parser,
                site=site,
            )
            write_page_evidence(destination, url, evidence)
        for link in parser.links:
            absolute = urllib.parse.urljoin(url, link.value)
            candidates = [(absolute, link.asset)]
            if link.asset and site is HUMAN_SPHERE_SITE:
                original_image = mediawiki_original_image_url(absolute)
                if original_image is not None:
                    candidates.append((original_image, True))
            for source, asset_link in candidates:
                candidate = canonical_crawl_url(source, asset=asset_link, site=site)
                if candidate in discovered:
                    continue
                discovered.add(candidate)
                discovered_assets[candidate] = asset_link

                ignore_reason = ignored_url_reason(candidate, site=site)
                if ignore_reason is not None:
                    ignored[candidate] = WikiIgnoredURL(
                        url=candidate,
                        reason=ignore_reason,
                    )
                    continue
                if should_skip_url(candidate, site=site):
                    continue
                if not asset_link and not wiki_page_matches_language(
                    candidate,
                    language,
                    site=site,
                ):
                    continue
                queue.append((candidate, asset_link))

        if existing_checkpoint and old_asset != asset:
            save_checkpoint()

    phase = "complete"
    enumeration_continuation = None
    save_checkpoint()

    return WikiDownloadResult(
        files=tuple(
            sorted(saved, key=lambda path: mirror_path_sort_key(path, destination))
        ),
        failures=tuple(failures),
        ignored=tuple(ignored[url] for url in sorted(ignored)),
        pages=tuple(sorted(pages.values(), key=lambda page: page.path)),
    )


def create_wiki_work_directory(
    root: Path,
    *,
    language: str,
    site: WikiSite = DEFAULT_SITE,
    now: datetime | None = None,
    include_history: bool = False,
) -> Path:
    """Create a collision-safe work directory retained when acquisition fails."""
    _require_site_language(site, language)

    root.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    prefix = _site_archive_prefix(
        site,
        language=language,
        include_history=include_history,
    )
    candidate = root / f"{prefix} {timestamp}.work"
    counter = 2
    while candidate.exists():
        candidate = root / f"{prefix} {timestamp}-{counter}.work"
        counter += 1
    candidate.mkdir()
    return candidate


def archive_wiki(
    files: list[Path],
    destination: Path,
    *,
    root: Path,
    language: str = "en",
    site: WikiSite = DEFAULT_SITE,
    now: datetime | None = None,
    include_history: bool = False,
) -> Path:
    """Store one language-labeled wiki snapshot in a timestamped ZIP file."""
    _require_site_language(site, language)
    return create_timestamped_archive(
        files,
        destination,
        prefix=_site_archive_prefix(
            site,
            language=language,
            include_history=include_history,
        ),
        root=root,
        now=now,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site",
        choices=tuple(WIKI_SITES),
        default=None,
        help=(
            "MediaWiki site to mirror (default: infinity-wiki; "
            "human-sphere is currently English-only)"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("local/data/wiki"),
        help="Directory used to store timestamped wiki ZIP snapshots (default: local/data/wiki)",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path("local/work/wiki"),
        help=(
            "Directory used for temporary wiki crawl work; incomplete runs are "
            "preserved here (default: local/work/wiki)"
        ),
    )
    parser.add_argument(
        "--language",
        choices=SUPPORTED_LANGUAGES,
        default=None,
        help="Wiki language to mirror where supported (default: en; inferred for --from-snapshot)",
    )
    parser.add_argument(
        "--include-history",
        action="store_true",
        help=(
            "Also archive every rendered MediaWiki oldid revision for each mirrored "
            "page; this can be substantially larger and slower than a normal snapshot"
        ),
    )
    parser.add_argument(
        "--from-snapshot",
        type=Path,
        help=(
            "Upgrade a verified completed non-history ZIP to a new history "
            "snapshot; requires --include-history and its matching v3 manifest "
            "and v1 inventory; never downloads current pages again"
        ),
    )
    parser.add_argument(
        "--update-from",
        type=Path,
        help=(
            "Conservatively refresh a verified non-history snapshot: full "
            "main-namespace discovery and HTTP fetches, then compare and "
            "reuse byte-identical members; not yet network-incremental"
        ),
    )
    parser.add_argument(
        "--from-manifest",
        type=Path,
        help="Parent provenance JSON for --from-snapshot/--update-from",
    )
    parser.add_argument(
        "--from-inventory",
        type=Path,
        help=(
            "Parent inventory JSON for --from-snapshot/--update-from; default under --manifest-dir/inventories/"
            "<parent ZIP stem>.inventory.json)"
        ),
    )
    parser.add_argument(
        "--resume-work",
        type=Path,
        help=(
            "Reuse a preserved wiki work directory from an incomplete run; existing "
            "oldid files are not downloaded again"
        ),
    )
    parser.add_argument(
        "--retry-failed-only",
        action="store_true",
        help=(
            "With --resume-work, retry only failures from a completed crawl "
            "checkpoint, preserving previously saved pages and discovered URLs"
        ),
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("local/data/manifests/snapshots"),
        help="Generated snapshot manifest directory (default: local/data/manifests/snapshots)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.update_from is not None:
        if (args.from_snapshot is not None or args.include_history
                or args.retry_failed_only or args.resume_work is not None):
            print(
                "ERROR: --update-from cannot be combined with --from-snapshot, "
                "--include-history, --retry-failed-only or --resume-work",
                file=sys.stderr,
            )
            return 1
        from infinity_wiki_acquisition.snapshot_update import (
            SnapshotUpdateIncomplete, update_snapshot,
        )

        parent_zip = args.update_from
        parent_manifest = (
            args.from_manifest or args.manifest_dir / f"{parent_zip.stem}.json"
        )
        parent_inventory = (
            args.from_inventory
            or args.manifest_dir / "inventories" / f"{parent_zip.stem}.inventory.json"
        )
        try:
            updated = update_snapshot(
                parent_zip, parent_manifest, parent_inventory,
                archive_directory=args.root,
                manifest_directory=args.manifest_dir,
                work_root=args.work_root,
                expected_site=args.site, expected_language=args.language,
            )
        except SnapshotUpdateIncomplete as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            for failure in exc.failures:
                print(f"  {failure.url}: {failure.error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print(
                "Snapshot update interrupted; preserved work for inspection.",
                file=sys.stderr,
            )
            return 130
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(
            f"Updated Wiki snapshot (pages refetched; assets verified or downloaded): "
            f"{updated.reused_members} matching parent members, "
            f"{updated.changed_members} changed, {updated.new_members} new, "
            f"{updated.removed_members} removed, "
            f"{updated.moved_pages} page ID URL changes -> {updated.archive}"
        )
        print(f"Snapshot provenance -> {updated.manifest}")
        print(f"Snapshot inventory -> {updated.inventory}")
        return 0
    if args.from_snapshot is not None:
        if not args.include_history or args.retry_failed_only or args.resume_work is not None:
            print(
                "ERROR: --from-snapshot requires --include-history and cannot "
                "be combined with --retry-failed-only/--resume-work",
                file=sys.stderr,
            )
            return 1
        from infinity_wiki_acquisition.snapshot_upgrade import (
            SnapshotUpgradeIncomplete, upgrade_snapshot_with_history,
        )

        parent_zip = args.from_snapshot
        parent_manifest = (
            args.from_manifest or args.manifest_dir / f"{parent_zip.stem}.json"
        )
        parent_inventory = (
            args.from_inventory
            or args.manifest_dir / "inventories" / f"{parent_zip.stem}.inventory.json"
        )
        try:
            upgraded = upgrade_snapshot_with_history(
                parent_zip, parent_manifest, parent_inventory,
                archive_directory=args.root,
                manifest_directory=args.manifest_dir,
                work_root=args.work_root,
                expected_site=args.site, expected_language=args.language,
            )
        except SnapshotUpgradeIncomplete as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            for failure in exc.failures:
                print(f"  {failure.url}: {failure.error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("History upgrade interrupted; preserved work for inspection.", file=sys.stderr)
            return 130
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(
            f"Reused {upgraded.reused_members} parent members "
            f"({upgraded.reused_revisions} oldid revisions), archived "
            f"{upgraded.revision_count} history revisions -> {upgraded.archive}"
        )
        print(f"Snapshot provenance -> {upgraded.manifest}")
        print(f"Snapshot inventory -> {upgraded.inventory}")
        return 0
    if args.from_manifest is not None or args.from_inventory is not None:
        print(
            "ERROR: --from-manifest/--from-inventory requires "
            "--from-snapshot or --update-from",
            file=sys.stderr,
        )
        return 1
    site = WIKI_SITES[args.site or DEFAULT_SITE.key]
    args.language = args.language or "en"
    try:
        _require_site_language(site, args.language)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.retry_failed_only and args.resume_work is None:
        print("ERROR: --retry-failed-only requires --resume-work", file=sys.stderr)
        return 1
    snapshot_root = (Path.cwd() / args.root).resolve()
    work_root = (Path.cwd() / args.work_root).resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    archive: Path | None = None
    manifest: Path | None = None
    inventory: Path | None = None
    staging_path: Path | None = None
    history_result: WikiHistoryResult | None = None
    progress = ConsoleProgressReporter()
    root_url = site.root_urls[args.language]

    try:
        started_at = datetime.now().astimezone()
        if args.resume_work is not None:
            resume_path = (Path.cwd() / args.resume_work).resolve()
            if not resume_path.is_dir():
                raise ValueError(f"Wiki resume work directory does not exist: {resume_path}")
            staging_path = resume_path
        else:
            staging_path = create_wiki_work_directory(
                work_root,
                language=args.language,
                site=site,
                now=started_at,
                include_history=args.include_history,
            )
        assert staging_path is not None
        try:
            result = download_wiki(
                root_url,
                staging_path,
                language=args.language,
                site=site,
                progress=progress,
                checkpoint_file=staging_path / CHECKPOINT_FILENAME,
                retry_failed_only=args.retry_failed_only,
            )
        finally:
            progress.finish()

        if result.failures:
            print(
                f"Wiki snapshot incomplete: {len(result.failures)} required "
                "eligible URL(s) failed.",
                file=sys.stderr,
            )
            if result.ignored:
                print(
                    f"Ignored {len(result.ignored)} optional site URL(s).",
                    file=sys.stderr,
                )
            for failure in result.failures:
                print(f"  {failure.url}: {failure.error}", file=sys.stderr)
            print(f"Preserved wiki work -> {staging_path}", file=sys.stderr)
            return 1

        files = list(result.files)
        if not files:
            print("No wiki pages were downloaded.", file=sys.stderr)
            print(f"Preserved wiki work -> {staging_path}", file=sys.stderr)
            return 1

        if args.include_history:
            history_progress = ConsoleHistoryProgressReporter()
            try:
                history_result = download_wiki_history(
                    result.pages,
                    staging_path,
                    language=args.language,
                    site=site,
                    progress=history_progress,
                    checkpoint_file=staging_path / HISTORY_CHECKPOINT_FILENAME,
                )
            finally:
                history_progress.finish()
            if history_result.failures:
                print(
                    f"Wiki history incomplete: {len(history_result.failures)} required "
                    "revision request(s) failed.",
                    file=sys.stderr,
                )
                for failure in history_result.failures:
                    print(f"  {failure.url}: {failure.error}", file=sys.stderr)
                print(f"Preserved wiki work -> {staging_path}", file=sys.stderr)
                return 1
            files.extend(history_result.files)

        acquired_at = datetime.now().astimezone()
        archive = archive_wiki(
            files,
            snapshot_root,
            root=staging_path,
            language=args.language,
            site=site,
            now=acquired_at,
            include_history=args.include_history,
        )
        manifest = write_snapshot_manifest(
            archive,
            args.manifest_dir,
            snapshot_type="wiki",
            acquired_at=acquired_at,
            source_url=root_url,
            document_count=len(files),
            project_root=Path.cwd(),
            language=args.language,
            require_new=True,
        )
        inventory = write_snapshot_inventory(
            archive,
            manifest,
            Path(args.manifest_dir) / "inventories",
            work=staging_path,
            site=site.key,
            language=args.language,
            root_url=root_url,
            include_history=args.include_history,
            pages=result.pages,
            crawl_checkpoint=staging_path / CHECKPOINT_FILENAME,
        )
    except KeyboardInterrupt:
        if inventory is not None:
            inventory.unlink(missing_ok=True)
        if manifest is not None:
            manifest.unlink(missing_ok=True)
        if archive is not None:
            archive.unlink(missing_ok=True)
        print("Wiki acquisition interrupted; latest acquisition checkpoints preserved.", file=sys.stderr)
        if staging_path is not None and staging_path.exists():
            print(f"Preserved wiki work -> {staging_path}", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        if inventory is not None:
            inventory.unlink(missing_ok=True)
        if manifest is not None:
            manifest.unlink(missing_ok=True)
        if archive is not None:
            archive.unlink(missing_ok=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        if staging_path is not None and staging_path.exists():
            print(f"Preserved wiki work -> {staging_path}", file=sys.stderr)
        return 1

    assert staging_path is not None
    try:
        shutil.rmtree(staging_path)
    except OSError as exc:
        print(f"WARNING: could not remove wiki work directory {staging_path}: {exc}")

    if history_result is not None:
        print(
            f"Downloaded {len(result.files)} current wiki files + "
            f"{history_result.revision_count} historical revisions -> {archive}"
        )
    else:
        print(f"Downloaded {len(files)} wiki files -> {archive}")
    if result.ignored:
        print(f"Ignored {len(result.ignored)} optional site URL(s).")
    print(f"Snapshot provenance -> {manifest}")
    print(f"Snapshot inventory -> {inventory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
