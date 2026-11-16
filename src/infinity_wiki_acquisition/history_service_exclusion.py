"""Narrow, explicit MediaWiki API service/no-revision exception.

Old Rules Wiki archives recorded the /api.php service endpoint as a
"current-page" HTML member. Preserve those verified current bytes during
history-only upgrades, but never invent a MediaWiki revision for the
endpoint. Only a *missing* revision-API page response authorizes this
record, not a network error or missing generic source page.
"""

from __future__ import annotations

import re
from typing import Any

NO_HISTORY_API_ENDPOINT = "mediawiki-api-endpoint-not-a-wiki-page"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def eligible_service_page(page: Any, *, site: Any, language: str) -> bool:
    from infinity_wiki_acquisition import cli

    if site.key != cli.INFINITY_WIKI_SITE.key:
        return False
    endpoint = site.api_urls.get(language)
    if not isinstance(endpoint, str):
        return False
    return (
        page.url == endpoint
        and page.title.casefold() == "api.php"
        and page.path == cli.local_relative_path(endpoint, site=site)
    )


def approved_parent_service_member(member: dict[str, Any], *, site: Any,
                                   language: str) -> bool:
    from infinity_wiki_acquisition.cli import WikiPage

    if member.get("kind") != "current-page":
        return False
    page = member.get("page")
    identity = member.get("sourceIdentity")
    if not isinstance(page, dict) or not isinstance(identity, dict):
        return False
    if (
        identity.get("pageId") is not None
        or identity.get("htmlPageIdHint") is not None
        or identity.get("requestUrl") != page.get("url")
        or member.get("sourceUrls") != [page.get("url")]
    ):
        return False
    return eligible_service_page(
        WikiPage(page["url"], member["path"], page["title"]),
        site=site, language=language,
    )


def validate_history_exclusion(
    record: dict[str, Any], *, member: dict[str, Any],
    site: Any, language: str,
) -> None:
    from infinity_wiki_acquisition import cli

    evidence = record.get("historyExclusion")
    if (not isinstance(evidence, dict)
            or set(evidence) != {"reason", "queryUrl", "responseSha256"}
            or evidence["reason"] != NO_HISTORY_API_ENDPOINT
            or evidence["queryUrl"] != cli.revision_query_url(
                member["page"]["title"], language=language, site=site,
            )
            or not isinstance(evidence["responseSha256"], str)
            or _SHA256.fullmatch(evidence["responseSha256"]) is None
            or record.get("revisions") != []
            or record.get("currentUrl") != member["page"]["url"]
            or record.get("currentPath") != member["path"]
            or record.get("title") != member["page"]["title"]
            or not approved_parent_service_member(
                member, site=site, language=language,
            )):
        raise ValueError(
            "History exclusion is not justified by a known verified API service"
        )
