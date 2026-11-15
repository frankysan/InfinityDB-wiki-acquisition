"""P2.3 archive-derived current Wiki refresh with verified asset-version reuse.

Rendered pages are always fetched and mirrored again. Original MediaWiki
uploads may be reused after matching remote imageinfo SHA-1, size and URL
against the fully verified parent bytes; other assets can use strong HTTP
ETag/304 revalidation. Unknown versions never authorize a transfer skip.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from infinity_wiki_acquisition.crawl_checkpoint import (
    CHECKPOINT_FILENAME, read_crawl_checkpoint,
)
from infinity_wiki_acquisition.snapshot_inventory import build_snapshot_inventory
from infinity_wiki_acquisition.snapshot_provenance import sha256_file, write_snapshot_manifest
from infinity_wiki_acquisition.snapshot_upgrade import _publish_verified_inventory
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot


class SnapshotUpdateIncomplete(ValueError):
    """A required source failed; work remains available for inspection."""

    def __init__(self, work: Path, failures: tuple[Any, ...]) -> None:
        self.work = work
        self.failures = failures
        super().__init__(
            f"Wiki update incomplete: {len(failures)} failed source URL(s); "
            f"preserved work -> {work}"
        )


class SnapshotUpdateResult(NamedTuple):
    archive: Path
    manifest: Path
    inventory: Path
    reused_members: int
    changed_members: int
    new_members: int
    removed_members: int
    moved_pages: int


def _parent_sources(parent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Require a classified source set; avoid interpreting unknown members."""
    if parent["history"]:
        raise ValueError(
            "--update-from currently accepts non-history parents only; "
            "history-preserving updates need explicit reconciliation"
        )
    if parent["captureMetadata"].get("legacyIndexed") is True:
        raise ValueError(
            "--update-from needs original source URL evidence; "
            "a legacy-reconstructed inventory is insufficient"
        )
    known: dict[str, dict[str, Any]] = {}
    for member in parent["members"]:
        if member["kind"] not in {"current-page", "asset"}:
            raise ValueError(
                f"Unclassified member cannot be updated safely: {member['path']}"
            )
        for url in member["sourceUrls"]:
            previous = known.setdefault(url, member)
            if previous is not member:
                raise ValueError(f"Parent URL maps to different members: {url}")
    if not any(member["kind"] == "current-page" for member in parent["members"]):
        raise ValueError("Parent inventory does not identify any current pages")
    return known


def scan_recent_changes(
    site: Any, language: str, *, since: datetime, until: datetime,
    max_records: int = 100000,
) -> dict[str, Any]:
    """Enumerate RecentChanges as evidence, not a proof of retention coverage.

    Even an exhausted continuation is not proof that the source retains all
    changes back to the requested start. Never use this log to skip content
    fetching or to declare a page deleted without independent discovery.
    """
    from infinity_wiki_acquisition import cli

    if (since.tzinfo is None or until.tzinfo is None
            or since.utcoffset() is None or until.utcoffset() is None
            or since > until):
        raise ValueError("RecentChanges window must have ordered aware timestamps")
    start = until.astimezone(timezone.utc).isoformat(timespec="seconds")
    end = since.astimezone(timezone.utc).isoformat(timespec="seconds")
    token: str | None = None
    seen_tokens: set[str] = set()
    events: list[dict[str, Any]] = []
    ids: set[int] = set()
    while True:
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "list": "recentchanges", "rcprop": "title|ids|timestamp|loginfo|flags",
            "rcstart": start, "rcend": end,
            "rcdir": "older", "rclimit": "max",
        }
        if token is not None:
            params["rccontinue"] = token
        url = f"{site.api_urls[language]}?{urllib.parse.urlencode(params)}"
        payload = cli.fetch_site_bytes(url, language=language, site=site)
        try:
            response = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid MediaWiki RecentChanges JSON") from exc
        if not isinstance(response, dict) or "errors" in response:
            raise ValueError("MediaWiki RecentChanges request failed")
        query = response.get("query")
        batch = query.get("recentchanges") if isinstance(query, dict) else None
        if not isinstance(batch, list):
            raise ValueError("MediaWiki RecentChanges response is incomplete")
        for change in batch:
            if not isinstance(change, dict):
                raise ValueError("Invalid MediaWiki RecentChanges record")
            rcid = change.get("rcid")
            title = change.get("title")
            changed_at = change.get("timestamp")
            kind = change.get("type")
            if (type(rcid) is not int or rcid <= 0 or rcid in ids
                    or not isinstance(title, str)
                    or not title or not isinstance(changed_at, str)
                    or kind not in {"edit", "new", "log", "categorize", "external"}):
                raise ValueError("Invalid or duplicate RecentChanges identity")
            try:
                event_time = datetime.fromisoformat(changed_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Invalid RecentChanges event timestamp") from exc
            if (event_time.tzinfo is None or event_time.utcoffset() is None
                    or not since <= event_time <= until):
                raise ValueError("RecentChanges record is outside the requested window")
            ids.add(rcid)
            event: dict[str, Any] = {
                "rcid": rcid, "type": kind,
                "title": title, "timestamp": changed_at,
            }
            for key in ("pageid", "revid", "old_revid"):
                value = change.get(key)
                if value is not None:
                    if type(value) is not int or value < 0:
                        raise ValueError(f"Invalid RecentChanges {key}")
                    event[key] = value
            if kind == "log":
                logtype = change.get("logtype")
                if not isinstance(logtype, str) or not logtype:
                    raise ValueError("Invalid RecentChanges log type")
                event["logtype"] = logtype
                action = change.get("logaction")
                if isinstance(action, str):
                    event["logaction"] = action
                parameters = change.get("logparams")
                if isinstance(parameters, dict):
                    target_title = parameters.get("target_title")
                    if isinstance(target_title, str) and target_title:
                        event["targetTitle"] = target_title
            events.append(event)
            if len(events) > max_records:
                raise ValueError(
                    f"RecentChanges exceeds {max_records} records; "
                    "cannot retain a bounded audit trail"
                )
        cont = response.get("continue")
        if cont is None:
            break
        next_token = cont.get("rccontinue") if isinstance(cont, dict) else None
        if (not isinstance(next_token, str) or not next_token
                or next_token in seen_tokens):
            raise ValueError("Invalid MediaWiki RecentChanges continuation")
        seen_tokens.add(next_token)
        token = next_token
    return {
        "requestedFrom": end,
        "requestedThrough": start,
        "retentionCoverage": "unverified",
        "observedCount": len(events),
        "events": events,
    }


def _page_changes(
    parent: dict[str, Any], updated: dict[str, Any],
    recent_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare allpages IDs; annotate moves only when a log corroborates them."""
    def id_map(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
        ids: dict[int, dict[str, Any]] = {}
        for member in document["members"]:
            if member["kind"] != "current-page":
                continue
            page_id = member["sourceIdentity"].get("pageId")
            if page_id is None:
                continue
            if page_id in ids and ids[page_id]["page"]["url"] != member["page"]["url"]:
                raise ValueError(f"Multiple current pages claim MediaWiki ID {page_id}")
            ids[page_id] = member
        return ids

    before = id_map(parent)
    after = id_map(updated)
    moved = []
    disappeared = []
    for page_id, member in sorted(before.items()):
        current = after.get(page_id)
        if current is None:
            disappeared.append({
                "pageId": page_id,
                "oldUrl": member["page"]["url"],
                "oldPath": member["path"],
                "status": "absent-from-current-enumeration",
            })
        elif (current["page"]["url"], current["path"]) != (
            member["page"]["url"], member["path"],
        ):
            def normalize(title: str) -> str:
                return title.replace("_", " ").casefold()

            corroborated = any(
                event.get("type") == "log"
                and event.get("logtype") == "move"
                and event.get("pageid") == page_id
                and normalize(event.get("title", "")) == normalize(member["page"]["title"])
                and normalize(event.get("targetTitle", "")) == normalize(current["page"]["title"])
                for event in recent_events
            )
            moved.append({
                "pageId": page_id,
                "oldUrl": member["page"]["url"],
                "newUrl": current["page"]["url"],
                "oldPath": member["path"],
                "newPath": current["path"],
                "interpretation": (
                    "move-corroborated-by-recentchanges"
                    if corroborated else "unresolved-url-association"
                ),
            })
    return moved, disappeared


def update_snapshot(
    source_archive: Path,
    source_manifest: Path,
    source_inventory: Path,
    *,
    archive_directory: Path,
    manifest_directory: Path,
    work_root: Path,
    now: datetime | None = None,
    expected_site: str | None = None,
    expected_language: str | None = None,
    progress: Any = None,
) -> SnapshotUpdateResult:
    """Refresh current content and publish a new independently verified snapshot.

    Fresh allpages and current HTML fetching always run. Only original uploads
    with independently matching MediaWiki SHA-1/size/URL, or assets with
    current strong HTTP 304 validation, can skip a binary response body.
    No HTML content is skipped based on its page revision identity.
    """
    from infinity_wiki_acquisition import cli

    parent = verify_snapshot(source_archive, source_manifest, source_inventory)
    previous_by_url = _parent_sources(parent)
    site = cli.WIKI_SITES[parent["site"]]
    language = parent["language"]
    for member in parent["members"]:
        if (member["kind"] == "current-page"
                and not cli.wiki_page_matches_language(
                    member["page"]["url"], language, site=site,
                )):
            raise ValueError(
                f"Parent source page is outside the selected site/language: {member['path']}"
            )
    if expected_site is not None and expected_site != site.key:
        raise ValueError(f"Source snapshot site mismatch: {expected_site}")
    if expected_language is not None and expected_language != language:
        raise ValueError(f"Source snapshot language mismatch: {expected_language}")
    acquired_at = now or datetime.now().astimezone()
    parent_at = datetime.fromisoformat(parent["snapshot"]["acquiredAt"])
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None or acquired_at < parent_at:
        raise ValueError("Updated snapshot time must be aware and not precede its parent")

    changes = scan_recent_changes(
        site, language, since=parent_at, until=acquired_at,
    )
    work = cli.create_wiki_work_directory(
        work_root, site=site, language=language, now=acquired_at,
        include_history=False,
    )
    archive: Path | None = None
    manifest: Path | None = None
    inventory: Path | None = None
    try:
        if progress is None:
            progress = cli.ConsoleProgressReporter()
        conditionally_reused_urls: set[str] = set()
        upload_sha1_reused_urls: set[str] = set()
        upload_versions: dict[str, dict[str, Any]] = {}
        from infinity_wiki_acquisition.mediawiki_uploads import read_current_upload

        with zipfile.ZipFile(source_archive) as source_zip:
            def fetch_asset(url: str) -> bytes:
                previous = previous_by_url.get(url)
                version = previous.get("sourceVersion") if previous else None
                same_parent_source = (
                    previous is not None
                    and previous["kind"] == "asset"
                    and previous["sourceUrls"] == [url]
                    and previous["path"] == cli.local_relative_path(
                        url, asset=True, site=site,
                    )
                )
                # Only direct original upload URLs with an exact same URL
                # returned by MediaWiki imageinfo qualify. Thumbnails,
                # external CDNs and missing API versions fall back safely.
                upload = read_current_upload(url, site=site, language=language)
                if upload is not None and same_parent_source:
                    prior_bytes = source_zip.read(previous["path"])
                    if (len(prior_bytes) == upload["size"]
                            and hashlib.sha1(prior_bytes).hexdigest()
                            == upload["sha1Hex"]):
                        upload_versions[url] = {**upload, "usedForReuse": True}
                        upload_sha1_reused_urls.add(url)
                        return prior_bytes

                def checked_body(payload: bytes) -> bytes:
                    if upload is not None:
                        if (len(payload) != upload["size"]
                                or hashlib.sha1(payload).hexdigest()
                                != upload["sha1Hex"]):
                            raise ValueError(
                                f"MediaWiki upload changed between imageinfo "
                                f"and body acquisition: {url}"
                            )
                        upload_versions[url] = {
                            **upload, "usedForReuse": False,
                        }
                    return payload

                # If the new MediaWiki upload identity is definitely different
                # from archived bytes, a stale HTTP 304 must not override it.
                if upload is not None:
                    return checked_body(
                        cli.fetch_site_bytes(url, language=language, site=site),
                    )

                # A strong ETag applies to one exact request URL/representation.
                # Never infer an unchanged asset from filenames, weak ETags,
                # timestamps, or a redirect to another effective URL.
                if (
                    same_parent_source
                    and isinstance(version, dict)
                    and version.get("sourceUrl") == url
                    and version.get("effectiveUrl") == url
                    and version.get("sha256") == previous["sha256"]
                    and version.get("responseStatus") in (200, 304)
                    and cli._is_strong_etag(version.get("strongEtag"))
                ):
                    validator = version["strongEtag"]
                    checked = cli.fetch_site_bytes(
                        url, language=language, site=site,
                        if_none_match=validator,
                    )
                    if getattr(checked, "http_status", None) == 304:
                        if (
                            getattr(checked, "etag", None) != validator
                            or getattr(checked, "conditional_etag", None) != validator
                            or getattr(checked, "effective_url", None) != url
                            or len(checked) != 0
                        ):
                            raise ValueError(
                                f"Conditional asset response lacks matching "
                                f"strong ETag or stable URL: {url}"
                            )
                        conditionally_reused_urls.add(url)
                        return cli.WikiFetchedBytes(
                            source_zip.read(previous["path"]),
                            effective_url=url, http_status=304,
                            etag=validator,
                            last_modified=getattr(
                                checked, "last_modified", None,
                            ),
                            conditional_etag=validator,
                        )
                    # A 200 means the server did not validate the old bytes,
                    # even if the ETag text happens to be unchanged.
                    return checked
                return cli.fetch_site_bytes(
                    url, language=language, site=site,
                )

            try:
                fresh = cli.download_wiki(
                    site.root_urls[language], work,
                    site=site, language=language,
                    checkpoint_file=work / CHECKPOINT_FILENAME,
                    progress=progress, force_allpages=True,
                    asset_fetch=fetch_asset,
                )
            finally:
                if hasattr(progress, "finish"):
                    progress.finish()
        if fresh.failures:
            raise SnapshotUpdateIncomplete(work, fresh.failures)
        if not fresh.files or not fresh.pages:
            raise ValueError("Current source discovery did not produce a complete page set")

        state = read_crawl_checkpoint(
            work / CHECKPOINT_FILENAME, work=work, site=site.key,
            language=language, root_url=cli.canonical_crawl_url(
                site.root_urls[language], site=site,
            ),
        )
        if state["failed"]:
            raise ValueError("Updated crawl contains unresolved failures")
        if (any(
            item["kind"] == "current-page"
            and item["sourceIdentity"].get("pageId") is not None
            for item in parent["members"]
        ) and not state["pageIds"]):
            raise ValueError(
                "MediaWiki allpages did not provide page IDs for an identity-based update"
            )
        # Every member is now backed by freshly fetched bytes OR an explicit
        # remote version validation against verified parent ZIP content.
        # Recopy only matching members to maintain exact parent bytes.
        reused_paths: set[str] = set()
        with zipfile.ZipFile(source_archive) as previous_zip:
            for url, saved in state["saved"].items():
                old = previous_by_url.get(url)
                if old is None or old["path"] != saved[0]:
                    continue
                target = work.joinpath(*saved[0].split("/"))
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != old["sha256"] or target.stat().st_size != old["size"]:
                    continue
                if saved[0] not in reused_paths:
                    # This copies from the immutable parent rather than
                    # relying on a filename or cached filesystem artifact.
                    target.write_bytes(previous_zip.read(old["path"]))
                    reused_paths.add(saved[0])

        files = list(fresh.files)
        archive = cli.archive_wiki(
            files, archive_directory, root=work, site=site,
            language=language, now=acquired_at,
        )
        manifest = write_snapshot_manifest(
            archive, manifest_directory, snapshot_type="wiki",
            acquired_at=acquired_at, source_url=site.root_urls[language],
            document_count=len(files), project_root=Path.cwd(),
            language=language, input_artifact=source_archive, require_new=True,
        )
        updated = build_snapshot_inventory(
            archive, manifest, work=work, site=site.key,
            language=language, root_url=site.root_urls[language],
            include_history=False, pages=fresh.pages,
            crawl_checkpoint=work / CHECKPOINT_FILENAME,
        )
        saved_uploads: set[str] = set()
        for member in updated["members"]:
            urls = member["sourceUrls"]
            if member["kind"] != "asset" or len(urls) != 1:
                continue
            upload = upload_versions.get(urls[0])
            if upload is None:
                continue
            contents = work.joinpath(*member["path"].split("/")).read_bytes()
            if (len(contents) != upload["size"]
                    or hashlib.sha1(contents).hexdigest() != upload["sha1Hex"]):
                raise ValueError(
                    f"Uploaded asset identity is inconsistent with published "
                    f"bytes: {member['path']}"
                )
            member["uploadVersion"] = upload
            if upload["usedForReuse"]:
                saved_uploads.add(urls[0])
        if saved_uploads != upload_sha1_reused_urls:
            raise ValueError("MediaWiki upload reuse has inconsistent evidence")
        old_members_by_path = {
            member["path"]: member for member in parent["members"]
        }
        old_paths = set(old_members_by_path)
        new_paths = {item["path"] for item in updated["members"]}
        matched = reused_paths & new_paths
        changed = (new_paths & old_paths) - matched
        added = new_paths - old_paths
        removed = old_paths - new_paths
        moved, absent = _page_changes(parent, updated, changes["events"])
        validated_assets = {
            member["sourceVersion"]["sourceUrl"]
            for member in updated["members"]
            if member["kind"] == "asset"
            and member.get("sourceVersion", {}).get("conditionalRevalidated")
        }
        if validated_assets != conditionally_reused_urls:
            raise ValueError(
                "Conditional asset reuse has inconsistent response evidence"
            )
        updated["captureMetadata"] = {
            **updated["captureMetadata"],
            "updateMode": "verified-asset-version-refresh",
            "sourceVersionSkipping": bool(validated_assets or saved_uploads),
            "conditionalReusedAssetCount": len(validated_assets),
            "uploadSha1ReusedAssetCount": len(saved_uploads),
            "uploadVersionEvidenceCount": len(upload_versions),
            "pageRevisionComparison": "not-safe-for-rendered-html",
            "assetVersionComparison": "strong-etag-304-or-imageinfo-sha1-or-body-hash",
            "matchedParentMemberCount": len(matched),
            "changedMemberCount": len(changed),
            "newMemberCount": len(added),
            "removedMemberCount": len(removed),
            "pageMovesById": moved,
            "parentPageIdsAbsentFromEnumeration": absent,
            "recentChanges": changes,
        }
        updated["parent"] = {
            "archiveName": source_archive.name,
            "archiveSha256": parent["snapshot"]["archiveSha256"],
            "contentSha256": parent["snapshot"]["contentSha256"],
            "manifestSha256": sha256_file(source_manifest),
            "inventorySha256": sha256_file(source_inventory),
            "acquiredAt": parent["snapshot"]["acquiredAt"],
            "reusedMemberCount": len(matched),
        }
        inventory = _publish_verified_inventory(
            updated, archive, manifest, manifest_directory / "inventories",
        )
    except BaseException:
        if inventory is not None:
            inventory.unlink(missing_ok=True)
        if manifest is not None:
            manifest.unlink(missing_ok=True)
        if archive is not None:
            archive.unlink(missing_ok=True)
        raise

    try:
        shutil.rmtree(work)
    except OSError:
        pass
    return SnapshotUpdateResult(
        archive, manifest, inventory, len(matched), len(changed),
        len(added), len(removed), len(moved),
    )
