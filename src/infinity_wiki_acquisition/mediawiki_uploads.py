"""Conservative MediaWiki original-upload identities from API imageinfo.

Only an exact original media URL can be compared against a MediaWiki file
record. Thumbnails, redirects, unrecognized hosts, and API uncertainty do
not authorize byte reuse. SHA-1 is the native MediaWiki upload checksum;
we additionally bind it to the verified parent member's SHA-256 and size.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any


def _sha1_hex(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value.lower()
    if not re.fullmatch(r"[0-9a-z]{1,31}", value):
        return None
    try:
        integer = int(value, 36)
    except ValueError:
        return None
    if not 0 <= integer < 2**160:
        return None
    return f"{integer:040x}"


def read_current_upload(
    url: str, *, language: str, site: Any,
) -> dict[str, Any] | None:
    """Return a strictly matched latest upload identity, else unknown.

    Network/API errors deliberately yield no reusable evidence: the caller
    must fall back to a normal asset download instead of assuming an old
    upload is unchanged.
    """
    from infinity_wiki_acquisition import cli

    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() not in site.page_hosts
        or not parsed.path.startswith("/images/")
        or parsed.path.startswith("/images/thumb/")
        or parsed.query
        or parsed.fragment
    ):
        return None

    filename = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    if not filename or "/" in filename or "\\" in filename:
        return None
    title = "File:" + filename.replace("_", " ")
    params = {
        "action": "query", "format": "json", "formatversion": "2",
        "prop": "imageinfo", "titles": title,
        "iiprop": "url|sha1|size|timestamp", "iilimit": "1",
    }
    request = (
        f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"
    )
    try:
        document = json.loads(
            cli.fetch_site_bytes(request, language=language, site=site)
        )
    except (OSError, ValueError, UnicodeError, RuntimeError):
        return None
    if not isinstance(document, dict) or "errors" in document:
        return None
    query = document.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) != 1:
        return None
    page = pages[0]
    if not isinstance(page, dict):
        return None
    actual_title = page.get("title")
    if (
        not isinstance(actual_title, str)
        or actual_title.replace("_", " ").casefold() != title.casefold()
    ):
        return None
    imageinfo = page.get("imageinfo")
    if not isinstance(imageinfo, list) or len(imageinfo) != 1:
        return None
    image = imageinfo[0]
    if not isinstance(image, dict):
        return None
    checksum = _sha1_hex(image.get("sha1"))
    size = image.get("size")
    uploaded = image.get("timestamp")
    remote_url = image.get("url")
    if (
        checksum is None
        or type(size) is not int or size <= 0
        or remote_url != url
        or not isinstance(uploaded, str)
    ):
        return None
    try:
        timestamp = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return None
    except ValueError:
        return None
    return {
        "sourceUrl": url,
        "apiTitle": actual_title,
        "remoteUrl": remote_url,
        "sha1Hex": checksum,
        "size": size,
        "uploadTimestamp": uploaded,
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "usedForReuse": False,
        "evidence": "mediawiki-imageinfo",
    }
