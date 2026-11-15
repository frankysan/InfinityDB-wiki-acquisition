"""Opt-in P2.4c.2b portable replay of original current-page HTML.

The companion ZIP is separate from the immutable snapshot ZIP and manifest.
It contains only raw current-page HTTP bodies and a versioned, independently
recomputable index. No reader treats it as a source-side signature, an atomic
remote snapshot, or permission to skip current HTML downloads.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from infinity_wiki_acquisition.snapshot_archive import (
    _archive_info, validate_archive_member_paths,
)
from infinity_wiki_acquisition.snapshot_provenance import sha256_file

FORMAT = "InfinityDB wiki original HTML replay"
VERSION = 1
SCOPE = "current-pages-original-http-bodies-not-reuse-authority"
PRIVATE_DIRECTORY = ".wiki-original-html"
INDEX_MEMBER = "index.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_body_path(work: Path, url: str) -> Path:
    return work / PRIVATE_DIRECTORY / (_sha(url.encode("utf-8")) + ".html")


def write_source_body(work: Path, url: str, body: bytes) -> None:
    """Persist source bytes atomically without overwriting the rewritten page."""
    from infinity_wiki_acquisition.cli import write_bytes

    if not isinstance(body, bytes):
        raise ValueError("Original page content must be bytes")
    write_bytes(source_body_path(work, url), body)


def _links(original: bytes) -> tuple[int, str]:
    from infinity_wiki_acquisition.cli import LinkExtractor

    parser = LinkExtractor()
    parser.feed(original.decode("utf-8", errors="replace"))
    encoded = json.dumps(
        [{"value": link.value, "asset": link.asset} for link in parser.links],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return len(parser.links), _sha(encoded)


def _render(original: bytes, url: str, *, site: Any, language: str) -> bytes:
    from infinity_wiki_acquisition.cli import rewrite_html_links

    return rewrite_html_links(
        original.decode("utf-8", errors="replace"),
        url, language=language, site=site,
    ).encode("utf-8")


def _read_raw(work: Path, url: str, expected_sha: str) -> bytes:
    path = source_body_path(work, url)
    if not path.is_file():
        raise ValueError(f"Missing raw HTML replay input for {url}")
    raw = path.read_bytes()
    if _sha(raw) != expected_sha:
        raise ValueError(f"Original HTML replay hash mismatch for {url}")
    return raw


def create_source_replay_bundle(
    work: Path,
    archive: Path,
    manifest: Path,
    inventory: Path,
    destination: Path,
) -> Path:
    """Publish an opt-in, versioned companion ZIP after full snapshot verification.

    This is not part of the main snapshot contract: old consumers can continue
    to use ZIP/v3 manifest/v1-v2 inventory unchanged. All current pages must
    have retained original bodies; partial bundles are never published.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

    doc = verify_snapshot(archive, manifest, inventory)
    site, language = cli.WIKI_SITES[doc["site"]], doc["language"]
    records = []
    raw_members = []
    with zipfile.ZipFile(archive) as mirrored:
        for member in sorted(
            (row for row in doc["members"] if row["kind"] == "current-page"),
            key=lambda item: item["page"]["url"],
        ):
            url = member["page"]["url"]
            evidence = member.get("renderedResponseVersion")
            if not isinstance(evidence, dict):
                raise ValueError(f"Cannot replay page without source evidence: {url}")
            raw = _read_raw(work, url, evidence["originalResponseSha256"])
            rewritten = _render(raw, url, site=site, language=language)
            if rewritten != mirrored.read(member["path"]):
                raise ValueError(f"HTML replay differs from archived member: {url}")
            count, digest = _links(raw)
            raw_path = "html/" + _sha(url.encode("utf-8")) + ".html"
            records.append({
                "pageUrl": url, "memberPath": member["path"],
                "rawMember": raw_path, "originalSha256": _sha(raw),
                "rewrittenSha256": _sha(rewritten),
                "sourceLinkCount": count, "sourceLinksSha256": digest,
            })
            raw_members.append((raw_path, source_body_path(work, url)))

    index = {
        "format": FORMAT, "formatVersion": VERSION, "scope": SCOPE,
        "site": site.key, "language": language,
        "sourceArchiveName": archive.name,
        "sourceArchiveSha256": sha256_file(archive),
        "sourceManifestSha256": sha256_file(manifest),
        "sourceInventorySha256": sha256_file(inventory),
        "pages": records,
    }
    target = destination / (archive.stem + ".source-replay.zip")
    validate_archive_member_paths([INDEX_MEMBER] + [p for p, _ in raw_members])
    destination.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as output:
            created = True
            output.writestr(
                _archive_info(INDEX_MEMBER),
                (json.dumps(index, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            for path, source in raw_members:
                with source.open("rb") as stream, output.open(
                    _archive_info(path), "w",
                ) as member_out:
                    shutil.copyfileobj(stream, member_out, length=1024 * 1024)
        verify_source_replay_bundle(target, archive, manifest, inventory)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return target


def verify_source_replay_bundle(
    bundle: Path,
    archive: Path,
    manifest: Path,
    inventory: Path,
) -> dict[str, Any]:
    """Independently verify source bytes, discovery links and exact HTML rewrite.

    Reading source URLs from a ZIP never initiates HTTP traffic. The replay
    proof validates local representation, not remote source freshness.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

    doc = verify_snapshot(archive, manifest, inventory)
    site, language = cli.WIKI_SITES[doc["site"]], doc["language"]
    current = {
        member["page"]["url"]: member
        for member in doc["members"] if member["kind"] == "current-page"
    }
    with zipfile.ZipFile(bundle) as raw_zip, zipfile.ZipFile(archive) as mirrored:
        infos = raw_zip.infolist()
        names = [info.filename for info in infos]
        validate_archive_member_paths(names)
        if INDEX_MEMBER not in names:
            raise ValueError("HTML replay bundle lacks index")
        index = json.loads(raw_zip.read(INDEX_MEMBER))
        if (not isinstance(index, dict)
                or set(index) != {
                    "format", "formatVersion", "scope", "site", "language",
                    "sourceArchiveName", "sourceArchiveSha256",
                    "sourceManifestSha256", "sourceInventorySha256", "pages",
                }
                or index["format"] != FORMAT or index["formatVersion"] != VERSION
                or index["scope"] != SCOPE
                or index["site"] != site.key or index["language"] != language
                or index["sourceArchiveName"] != archive.name
                or index["sourceArchiveSha256"] != sha256_file(archive)
                or index["sourceManifestSha256"] != sha256_file(manifest)
                or index["sourceInventorySha256"] != sha256_file(inventory)
                or not isinstance(index["pages"], list)
                or len(index["pages"]) != len(current)):
            raise ValueError("HTML replay bundle identity or coverage mismatch")
        seen = set()
        expected_names = {INDEX_MEMBER}
        for row in index["pages"]:
            if not isinstance(row, dict) or set(row) != {
                "pageUrl", "memberPath", "rawMember",
                "originalSha256", "rewrittenSha256",
                "sourceLinkCount", "sourceLinksSha256",
            }:
                raise ValueError("Invalid HTML replay member description")
            url = row["pageUrl"]
            member = current.get(url)
            if member is None or url in seen or row["memberPath"] != member["path"]:
                raise ValueError("Unrecognized or duplicate HTML replay identity")
            seen.add(url)
            raw_path = "html/" + _sha(url.encode("utf-8")) + ".html"
            if row["rawMember"] != raw_path:
                raise ValueError("HTML replay path does not match source URL")
            expected_names.add(raw_path)
            raw = raw_zip.read(raw_path)
            evidence = member.get("renderedResponseVersion")
            if (not isinstance(evidence, dict)
                    or row["originalSha256"] != _sha(raw)
                    or row["originalSha256"] != evidence["originalResponseSha256"]
                    or row["rewrittenSha256"] != member["sha256"]
                    or _render(raw, url, site=site, language=language)
                    != mirrored.read(member["path"])):
                raise ValueError("HTML source-body or rewrite replay mismatch")
            count, digest = _links(raw)
            if (type(row["sourceLinkCount"]) is not int
                    or row["sourceLinkCount"] != count
                    or row["sourceLinksSha256"] != digest):
                raise ValueError("HTML discovery-link evidence mismatch")
        if set(names) != expected_names or seen != set(current):
            raise ValueError("HTML replay ZIP member set is incomplete")
    return index


def verify_conditional_html_304(
    bundle: Path,
    archive: Path,
    manifest: Path,
    inventory: Path,
    *,
    request_url: str,
    result: bytes,
) -> dict[str, Any]:
    """Bind an HTTP 304 check to independently verified parent source bytes.

    This verifies the entire snapshot/replay pair before inspecting the
    conditional response. The returned result is strictly non-authorizing.
    """
    from infinity_wiki_acquisition import cli
    from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

    verify_source_replay_bundle(bundle, archive, manifest, inventory)
    parent = verify_snapshot(archive, manifest, inventory)
    page = next(
        (row for row in parent["members"]
         if row["kind"] == "current-page" and row["page"]["url"] == request_url),
        None,
    )
    if page is None or not isinstance(page.get("renderedResponseVersion"), dict):
        raise ValueError("No verified HTML response exists for conditional check")
    return validate_conditional_html_304(
        parent_response=page["renderedResponseVersion"],
        request_url=request_url, result=result,
        site=cli.WIKI_SITES[parent["site"]], language=parent["language"],
    )


def validate_conditional_html_304(
    *,
    parent_response: dict[str, Any],
    request_url: str,
    result: bytes,
    site: Any,
    language: str,
) -> dict[str, Any]:
    """Validate a standalone conditional response, *not* authorize HTML reuse.

    Callers must separately verify the parent snapshot and replay bundle.
    Even a valid 304 does not establish source dependency consistency or a
    verified acquisition-wide snapshot time; no crawler uses this to skip.
    """
    from infinity_wiki_acquisition.cli import _is_strong_etag
    from infinity_wiki_acquisition.rendered_response import request_and_rewrite_profile

    validator = parent_response.get("strongEtag")
    if (not _is_strong_etag(validator)
            or parent_response.get("pageUrl") != request_url
            or parent_response.get("httpEffectiveUrl") != request_url
            or parent_response.get("httpStatus") != 200
            or parent_response.get("profile") != request_and_rewrite_profile(
                site=site, language=language,
            )
            or type(result) is bytes
            or getattr(result, "http_status", None) != 304
            or getattr(result, "effective_url", None) != request_url
            or getattr(result, "conditional_etag", None) != validator
            or getattr(result, "etag", None) != validator
            or bytes(result) != b""):
        raise ValueError("Unverified HTML HTTP 304 response")
    return {
        "status": "conditional-response-matches-parent-validator",
        "pageUrl": request_url,
        "etag": validator,
        "reuseAuthorized": False,
        "reason": "dependency-and-source-time-closure-unverified",
    }
