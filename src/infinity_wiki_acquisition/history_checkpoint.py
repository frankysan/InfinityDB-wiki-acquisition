"""Private, resumable MediaWiki history acquisition progress.

Current-page discovery and history acquisition have independent checkpoints.
History records are work metadata only, never members of published snapshots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HISTORY_CHECKPOINT_FILENAME = ".wiki-history-checkpoint.json"
HISTORY_CHECKPOINT_FORMAT = "InfinityDB wiki history checkpoint"
HISTORY_CHECKPOINT_VERSION = 1


def write_history_checkpoint(path: Path, document: dict[str, Any]) -> None:
    """Commit one consistent history progress boundary atomically."""
    encoded = (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f"{path.name}.part")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def read_history_checkpoint(
    path: Path,
    *,
    work: Path,
    site: str,
    language: str,
    pages: list[list[str]],
) -> dict[str, Any]:
    """Validate an existing checkpoint, including every committed revision file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable wiki history checkpoint: {path}") from exc

    if (
        not isinstance(document, dict)
        or document.get("format") != HISTORY_CHECKPOINT_FORMAT
        or document.get("version") != HISTORY_CHECKPOINT_VERSION
        or document.get("site") != site
        or document.get("language") != language
        or document.get("pages") != pages
    ):
        raise ValueError("History checkpoint is incompatible with this site/language/page set")

    index = document.get("pageIndex")
    completed = document.get("completedPages")
    active = document.get("active")
    saved = document.get("saved")
    complete = document.get("complete")
    if not (
        type(index) is int
        and 0 <= index <= len(pages)
        and type(complete) is bool
        and isinstance(completed, list)
        and len(completed) == index
        and isinstance(saved, dict)
        and (active is None or isinstance(active, dict))
        and (not complete or (index == len(pages) and active is None))
        and (index < len(pages) or active is None)
    ):
        raise ValueError("History checkpoint has inconsistent page progress")

    referenced_oldids: set[str] = set()
    for i, entry in enumerate(completed):
        if not (
            isinstance(entry, dict)
            and entry.get("currentUrl") == pages[i][0]
            and entry.get("currentPath") == pages[i][1]
            and isinstance(entry.get("title"), str)
            and isinstance(entry.get("revisions"), list)
        ):
            raise ValueError("History checkpoint has an invalid completed page")
        for record in entry["revisions"]:
            if not (
                isinstance(record, dict)
                and type(record.get("oldid")) is int
                and type(record.get("parentid")) is int
                and isinstance(record.get("timestamp"), str)
                and isinstance(record.get("url"), str)
                and record.get("path") == (
                    f"_history/oldid/{record['oldid']}.html"
                )
            ):
                raise ValueError("History checkpoint has invalid revision index data")
            referenced_oldids.add(str(record["oldid"]))

    if active is not None:
        revisions = active.get("revisions")
        offset = active.get("downloadIndex")
        continuation = active.get("continuation")
        canonical = active.get("canonicalTitle")
        enumeration_done = active.get("enumerationComplete")
        if not (
            active.get("pageUrl") == pages[index][0]
            and (canonical is None or (isinstance(canonical, str) and canonical))
            and (continuation is None or isinstance(continuation, str))
            and type(enumeration_done) is bool
            and isinstance(revisions, list)
            and all(
                isinstance(record, list)
                and len(record) == 3
                and type(record[0]) is int
                and type(record[1]) is int
                and isinstance(record[2], str)
                for record in revisions
            )
            and type(offset) is int
            and 0 <= offset <= len(revisions)
            and (enumeration_done or offset == 0)
            and (not enumeration_done or continuation is None)
        ):
            raise ValueError("History checkpoint has invalid revision enumeration progress")
        if any(str(record[0]) not in saved for record in revisions[:offset]):
            raise ValueError("History checkpoint references uncommitted oldid downloads")

    if complete and referenced_oldids != set(saved):
        raise ValueError("Completed history checkpoint has missing or unindexed revisions")

    for oldid, record in saved.items():
        if not (
            isinstance(oldid, str)
            and oldid.isascii()
            and oldid.isdecimal()
            and isinstance(record, list)
            and len(record) == 2
            and record[0] == f"_history/oldid/{oldid}.html"
            and type(record[1]) is int
            and record[1] >= 0
        ):
            raise ValueError(f"Invalid saved history revision record: {oldid}")
        target = work / record[0]
        if not target.is_file() or target.is_symlink() or target.stat().st_size != record[1]:
            raise ValueError(
                f"Saved history revision missing or changed: {record[0]}; "
                "inspect the work directory before resuming"
            )
    return document



def download_history_checkpointed(
    pages: tuple[Any, ...],
    destination: Path,
    *,
    language: str,
    site: Any,
    progress: Any,
    checkpoint_file: Path,
) -> Any:
    """Resume revision-list pagination and oldid downloads from private state.

    Page-list batches and completed page transitions are committed immediately;
    oldid downloads commit after each 100 processed revisions. On restart,
    already-present files from an uncommitted batch can be reused.
    """
    from infinity_wiki_acquisition import cli

    identity = [[page.url, page.path, page.title] for page in pages]
    if checkpoint_file.is_file():
        state = read_history_checkpoint(
            checkpoint_file,
            work=destination,
            site=site.key,
            language=language,
            pages=identity,
        )
    else:
        state = {
            "format": HISTORY_CHECKPOINT_FORMAT,
            "version": HISTORY_CHECKPOINT_VERSION,
            "site": site.key,
            "language": language,
            "pages": identity,
            "pageIndex": 0,
            "completedPages": [],
            "active": None,
            "saved": {},
            "complete": False,
        }
        write_history_checkpoint(checkpoint_file, state)

    saved: dict[str, list[str | int]] = state["saved"]
    completed_pages: list[dict[str, object]] = state["completedPages"]
    failures: list[Any] = []

    def store() -> None:
        write_history_checkpoint(checkpoint_file, state)

    def result() -> Any:
        files = {destination / record[0] for record in saved.values()}
        index_file = destination / cli.HISTORY_DIRECTORY / "index.json"
        if index_file.is_file():
            files.add(index_file)
        return cli.WikiHistoryResult(
            files=tuple(
                sorted(files, key=lambda file: cli.mirror_path_sort_key(file, destination))
            ),
            failures=tuple(failures),
            revision_count=len(saved),
        )

    for index in range(state["pageIndex"], len(pages)):
        page = pages[index]
        active = state["active"]
        if active is None:
            active = {
                "pageUrl": page.url,
                "canonicalTitle": None,
                "continuation": None,
                "enumerationComplete": False,
                "revisions": [],
                "downloadIndex": 0,
            }
            state["active"] = active
            store()

        # Do not reissue a committed revision API batch after an oldid failure.
        while not active["enumerationComplete"]:
            continuation = active["continuation"]
            try:
                title, batch, next_continuation = cli.fetch_page_revision_batch(
                    page, language=language, continuation=continuation, site=site,
                )
                if active["canonicalTitle"] not in (None, title):
                    raise ValueError(
                        f"MediaWiki revision title changed while reading {page.url}"
                    )
                known_ids = {record[0] for record in active["revisions"]}
                if len({revision.oldid for revision in batch}) != len(batch) or any(
                    revision.oldid in known_ids for revision in batch
                ):
                    raise ValueError(f"Duplicate revision ID while reading {page.url}")
            except Exception as exc:
                failures.append(
                    cli.WikiDownloadFailure(
                        url=cli.revision_query_url(
                            page.title, language=language,
                            continuation=continuation, site=site,
                        ),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                return result()
            active["canonicalTitle"] = title
            active["revisions"].extend(
                [[rev.oldid, rev.parentid, rev.timestamp] for rev in batch]
            )
            active["continuation"] = next_continuation
            active["enumerationComplete"] = next_continuation is None
            store()

        if not active["revisions"] or not active["canonicalTitle"]:
            failures.append(
                cli.WikiDownloadFailure(
                    url=cli.revision_query_url(
                        page.title, language=language, site=site,
                    ),
                    error=f"ValueError: MediaWiki returned no revisions for {page.url}",
                )
            )
            return result()

        revision_records: list[dict[str, object]] = []
        revisions: list[list[int | str]] = active["revisions"]
        for offset, (oldid, parentid, timestamp) in enumerate(revisions):
            source_url = cli.oldid_url(
                active["canonicalTitle"], oldid, language=language, site=site,
            )
            relative = f"{cli.HISTORY_DIRECTORY}/oldid/{oldid}.html"
            target = destination / relative
            if offset < active["downloadIndex"]:
                if str(oldid) not in saved:
                    raise ValueError(
                        f"Committed oldid missing from history checkpoint: {oldid}"
                    )
            else:
                if progress is not None:
                    progress(
                        cli.WikiHistoryProgress(
                            page=index + 1,
                            pages=len(pages),
                            saved=len(saved),
                            failed=len(failures),
                            url=source_url,
                        )
                    )
                if str(oldid) not in saved:
                    if not target.is_file():
                        try:
                            payload = cli.fetch_site_bytes(
                                source_url, language=language, site=site,
                            )
                        except Exception as exc:
                            failures.append(
                                cli.WikiDownloadFailure(
                                    url=source_url,
                                    error=f"{type(exc).__name__}: {exc}",
                                )
                            )
                            return result()
                        cli.write_bytes(target, payload)
                    saved[str(oldid)] = [relative, target.stat().st_size]
                active["downloadIndex"] = offset + 1
                if active["downloadIndex"] % 100 == 0:
                    store()

            revision_records.append(
                {
                    "oldid": oldid,
                    "parentid": parentid,
                    "path": relative,
                    "timestamp": timestamp,
                    "url": source_url,
                }
            )

        completed_pages.append(
            {
                "currentPath": page.path,
                "currentUrl": page.url,
                "revisions": revision_records,
                "title": active["canonicalTitle"],
            }
        )
        state["pageIndex"] = index + 1
        state["active"] = None
        store()

    index_path = destination / cli.HISTORY_DIRECTORY / "index.json"
    cli.write_bytes(
        index_path,
        cli._history_index_payload(
            language=language,
            pages=completed_pages,
            revision_count=len(saved),
            site=site,
        ),
    )
    state["complete"] = True
    store()
    return result()
