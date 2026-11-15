"""Offline copy-and-repair for a fresh Infinity Wiki crawl's old case-colliding paths.

Original work stays untouched. Requires retained original HTML source bodies
(--source-replay-dir), so local links can be reproduced without network access.
History and archive-derived runs require separate migration contracts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


def repair_crawl_paths(work: Path, output_work: Path) -> dict[str, int]:
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.crawl_checkpoint import (
        CHECKPOINT_FILENAME, CHECKPOINT_VERSION, read_crawl_checkpoint,
        write_crawl_checkpoint,
    )
    from infinity_wiki_acquisition.page_evidence import (
        EVIDENCE_DIRECTORY, read_page_evidence, write_page_evidence,
    )
    from infinity_wiki_acquisition.rendered_response import request_and_rewrite_profile
    from infinity_wiki_acquisition.snapshot_archive import validate_archive_member_paths
    from infinity_wiki_acquisition.source_replay import (
        PRIVATE_DIRECTORY, source_body_path,
    )

    if not work.is_dir() or work.is_symlink():
        raise ValueError("Original work directory is absent or unsafe")
    if output_work.exists() or output_work.is_symlink():
        raise ValueError("New work destination already exists; nothing was overwritten")
    if work.resolve() in output_work.resolve().parents:
        raise ValueError("New work directory must be outside the original work")
    if any((work / path).exists() for path in (
        ".wiki-archive-run.json", ".wiki-history-checkpoint.json", "_history",
    )):
        raise ValueError("Only fresh non-history crawls can use this repair")
    try:
        header = json.loads((work / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("No readable original crawl checkpoint") from exc
    if not isinstance(header, dict) or header.get("site") != "infinity-wiki":
        raise ValueError("The original checkpoint is not for Infinity Wiki")
    site = cli.INFINITY_WIKI_SITE
    language = header.get("language")
    if language not in site.languages or header.get("version") != CHECKPOINT_VERSION:
        raise ValueError("Unsupported original checkpoint language or version")
    root_url = cli.canonical_crawl_url(site.root_urls[language], site=site)
    state = read_crawl_checkpoint(
        work / CHECKPOINT_FILENAME, work=work, site=site.key,
        language=language, root_url=root_url, require_complete=False,
    )
    pages = {row["url"]: row for row in state["pages"]}
    new_paths = {
        url: cli.local_relative_path(
            url, site=site, asset=state["discovered"][url],
        )
        for url in state["saved"]
    }
    validate_archive_member_paths(new_paths.values())

    # Require source bodies and verify committed mirrored bytes *before*
    # creating any destination. An uncheckpointed URL will simply be
    # downloaded again by the normal resume queue.
    source_data = {}
    for url, page in pages.items():
        evidence = read_page_evidence(work, url, path=page["path"])
        observed = evidence.get("renderedResponseVersion") if evidence else None
        raw_path = source_body_path(work, url)
        if (not isinstance(observed, dict)
                or not raw_path.is_file() or raw_path.is_symlink()):
            raise ValueError(f"Original HTML replay evidence unavailable: {url}")
        raw = raw_path.read_bytes()
        old_path = work / page["path"]
        if (hashlib.sha256(raw).hexdigest() != evidence["originalSha256"]
                or observed.get("originalResponseSha256") != evidence["originalSha256"]
                or observed.get("memberPath") != page["path"]
                or observed.get("rewrittenMemberSha256")
                != hashlib.sha256(old_path.read_bytes()).hexdigest()):
            raise ValueError(f"Saved HTML or source body is inconsistent: {url}")
        source_data[url] = (raw, evidence)

    output_work.mkdir(parents=True, exist_ok=False)
    for directory in (PRIVATE_DIRECTORY, EVIDENCE_DIRECTORY, ".wiki-asset-evidence"):
        original = work / directory
        if original.exists():
            if not original.is_dir() or original.is_symlink():
                raise ValueError(f"Unsafe source evidence directory: {directory}")
            shutil.copytree(original, output_work / directory)

    renamed = 0
    for url, (old_relative, _size) in state["saved"].items():
        relative = new_paths[url]
        renamed += relative != old_relative
        target = output_work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if url not in pages:
            shutil.copyfile(work / old_relative, target)
        else:
            raw, evidence = source_data[url]
            body = cli.rewrite_html_links(
                raw.decode("utf-8", errors="replace"),
                url, language=language, site=site,
            ).encode("utf-8")
            cli.write_bytes(target, body)
            evidence["path"] = relative
            observed = evidence["renderedResponseVersion"]
            observed["memberPath"] = relative
            observed["rewrittenMemberSha256"] = hashlib.sha256(body).hexdigest()
            observed["profile"] = request_and_rewrite_profile(
                site=site, language=language,
                transport_v2=observed["formatVersion"] == 2,
            )
            write_page_evidence(output_work, url, evidence)
            pages[url]["path"] = relative
        state["saved"][url] = [relative, target.stat().st_size]

    write_crawl_checkpoint(output_work / CHECKPOINT_FILENAME, state)
    read_crawl_checkpoint(
        output_work / CHECKPOINT_FILENAME, work=output_work, site=site.key,
        language=language, root_url=root_url, require_complete=False,
    )
    return {
        "savedFiles": len(state["saved"]),
        "rewrittenPages": len(pages),
        "renamedPaths": renamed,
        "pendingUrls": len(state["queue"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output-work", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = repair_crawl_paths(args.work, args.output_work)
    except (ValueError, OSError) as exc:
        print(f"Portable path repair failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Copied {report['savedFiles']} checkpointed files; "
        f"rewrote {report['rewrittenPages']} HTML pages, "
        f"remapped {report['renamedPaths']} paths. "
        f"Pending checkpoint URLs: {report['pendingUrls']}."
    )
    print(f"Resume with --resume-work \"{args.output_work}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
