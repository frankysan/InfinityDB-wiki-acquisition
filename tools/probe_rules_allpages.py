"""Read-only bounded probe of the English/Spanish Rules Wiki allpages APIs.

Only the first main-namespace allpages batch is requested per language.
No page HTML or assets are downloaded, and no files are written.
"""

from __future__ import annotations

import argparse
import sys

from infinity_wiki_acquisition import cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--language", choices=("en", "es"), action="append",
        help="Language(s) to probe; default probes both English and Spanish",
    )
    args = parser.parse_args(argv)
    selected = tuple(dict.fromkeys(args.language or ("en", "es")))
    for language in selected:
        try:
            pages, token = cli.fetch_all_page_title_batch(
                language=language, site=cli.INFINITY_WIKI_SITE,
            )
            if not pages or any(
                type(getattr(page, "page_id", None)) is not int
                or page.page_id <= 0
                for page in pages
            ):
                raise ValueError("API batch omitted titles or positive page IDs")
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"{language}: allpages probe FAILED: {exc}", file=sys.stderr)
            return 1
        print(
            f"{language}: PASS — {len(pages)} main-namespace titles with "
            f"numeric page IDs; continuation: {'present' if token else 'none'}; "
            f"API: {cli.INFINITY_WIKI_SITE.api_urls[language]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
