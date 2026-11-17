"""Read-only three-request probe of MediaWiki current and historical HTML routes.

Use the downloader's ordinary headers; do not bypass HTTP restrictions, alter
the crawl checkpoint, or accept a different representation without review.

Example: python -m tools.probe_history_access --title 112 --oldid 1237
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from infinity_wiki_acquisition import cli


def inspect_url(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={
        "User-Agent": cli.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": cli._site_accept_language("en"),
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            sample = response.read(2048)
            return {
                "status": response.status,
                "effectiveUrl": response.geturl(),
                "contentType": response.headers.get("Content-Type"),
                "sampleBytes": len(sample),
                "htmlLooking": b"<html" in sample[:512].lower()
                    or b"<!doctype html" in sample[:512].lower(),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "effectiveUrl": exc.geturl(),
            "contentType": exc.headers.get("Content-Type"),
            "sampleBytes": len(exc.read(2048)),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--oldid", required=True, type=int)
    args = parser.parse_args(argv)
    if not args.title or any(c in args.title for c in ("\\r", "\\n", "\\0")):
        parser.error("title must be nonempty and single-line")
    if args.oldid <= 0:
        parser.error("oldid must be positive")

    site = cli.HUMAN_SPHERE_SITE
    current = cli.page_url_from_title(args.title, language="en", site=site)
    old = cli.oldid_url(args.title, args.oldid, language="en", site=site)
    no_redirect = f"{site.index_urls['en']}?{urllib.parse.urlencode({'title': args.title, 'oldid': args.oldid})}"
    checks = []
    for variant, url in (("current", current), ("oldid", old), ("withoutRedirect", no_redirect)):
        if checks:
            time.sleep(max(0.5, site.request_delay_seconds))
        checks.append({"variant": variant, "url": url, **inspect_url(url)})
    print(json.dumps({
        "site": site.key, "title": args.title, "oldid": args.oldid,
        "checks": checks,
        "note": "Diagnosis only; an HTTP 200 alone does not establish revision identity.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
