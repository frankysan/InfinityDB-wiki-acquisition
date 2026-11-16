"""P3.5 read-only, source-evidence audit of a verified Rules Wiki snapshot.

No network access or artifact mutation. Counts URL/namespace/asset policies
using the source URLs and reference/ignore evidence in the inventory, not
guessed filenames or page content.
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.parse
from pathlib import Path

from infinity_wiki_acquisition import cli
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot


def _path(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlsplit(url).path)


def observed_page_title_class(url: str) -> str:
    """Classify a recorded URL prefix, never assert a MediaWiki namespace ID."""
    if url in cli.INFINITY_WIKI_SITE.root_urls.values():
        return "entry-root"
    title = _path(url).lstrip("/").rsplit("/", 1)[-1]
    prefix = title.split(":", 1)[0].replace("_", " ").casefold() if ":" in title else None
    known_prefixes = {
        "category", "template", "module", "mediawiki", "file", "image",
        "talk", "user", "user talk", "help", "infinity", "special",
        "categoría", "plantilla", "módulo", "archivo", "usuario", "ayuda",
    }
    if prefix in known_prefixes:
        return f"namespace:{prefix}"
    if prefix is not None:
        return "ordinary-title-containing-colon"
    return "ordinary-title"


def audit(archive: Path, manifest: Path, inventory: Path) -> dict:
    doc = verify_snapshot(archive, manifest, inventory)
    if doc["site"] != "infinity-wiki":
        raise ValueError("Requires a verified Rules Wiki archive")
    pages = [m for m in doc["members"] if m["kind"] == "current-page"]
    assets = [m for m in doc["members"] if m["kind"] == "asset"]
    page_ns = collections.Counter()
    pages_in_ns: dict[str, list[str]] = collections.defaultdict(list)
    service_pages = []
    host_assets = collections.Counter()
    host_asset_sample: dict[str, list[str]] = collections.defaultdict(list)
    asset_classes = collections.Counter()
    asset_class_sample: dict[str, list[str]] = collections.defaultdict(list)
    asset_source_version = collections.Counter()
    same_site_asset_urls = []
    html_status = collections.Counter()
    rest_refs = collections.Counter()
    rest_samples: dict[str, list[str]] = collections.defaultdict(list)
    unresolved_by_reason = collections.Counter()
    unresolved_sample: dict[str, list[str]] = collections.defaultdict(list)
    image_original_candidates = []
    source_url_paths = collections.defaultdict(set)
    for m in pages:
        url = m["page"]["url"]
        ns = observed_page_title_class(url)
        page_ns[ns] += 1
        if len(pages_in_ns[ns]) < 4:
            pages_in_ns[ns].append(url)
        if any(x in url.lower() for x in ("api.php", "rest.php", "/cdn-cgi/", "index.php", "opensearch_desc.php")):
            service_pages.append(url)
        sid = m.get("sourceIdentity", {})
        html_status[str(sid.get("httpStatus"))] += 1
        for ref in m.get("references") or []:
            dest = ref.get("targetUrl", "")
            if not isinstance(dest, str):
                continue
            parsed = urllib.parse.urlsplit(dest)
            path = parsed.path.lower()
            host = (parsed.hostname or "").lower()
            if any(f in path for f in ("/rest.php/", "/cdn-cgi/", "/wiki-es/", "/opensearch_desc.php", "/api.php", "/index.php")):
                group = "wiki-es" if "/wiki-es/" in path else (
                    "rest" if "/rest.php/" in path else
                    "cdn-cgi" if "/cdn-cgi/" in path else
                    "opensearch" if "/opensearch_desc.php" in path else
                    "api" if "/api.php" in path else "index")
                rest_refs[group] += 1
                if len(rest_samples[group]) < 3:
                    rest_samples[group].append(dest)
            if ref.get("resolution") != "archived":
                kind = (ref.get("resolution") or "unresolved") + "|" + (
                    "external" if host not in cli.INFINITY_WIKI_SITE.page_hosts | cli.INFINITY_WIKI_SITE.asset_hosts
                    else "on-site"
                )
                unresolved_by_reason[kind] += 1
                if len(unresolved_sample[kind]) < 4:
                    unresolved_sample[kind].append(dest)
    for m in assets:
        for url in m["sourceUrls"]:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower()
            host_assets[host] += 1
            if host in cli.INFINITY_WIKI_SITE.page_hosts:
                same_site_asset_urls.append(url)
            if len(host_asset_sample[host]) < 4:
                host_asset_sample[host].append(url)
            category = (
                "thumb" if "/images/thumb/" in parsed.path else
                "mediawiki-images" if "/images/" in parsed.path else
                "external-assets" if host not in cli.INFINITY_WIKI_SITE.page_hosts else
                "site-other"
            )
            asset_classes[category] += 1
            if len(asset_class_sample[category]) < 4:
                asset_class_sample[category].append(url)
            if category == "thumb":
                original = cli.mediawiki_original_image_url(url)
                if original is not None:
                    image_original_candidates.append({"thumb": url, "candidate": original})
            source_url_paths[m["path"].lower()].add(host)
        version = m.get("sourceVersion") or {}
        asset_source_version["observed" if version else "not-observed"] += 1
    ignored = doc.get("ignoredUrls") or []
    return {
        "source": {"archive": archive.name, "site": doc["site"], "language": doc["language"]},
        "memberKinds": dict(collections.Counter(m["kind"] for m in doc["members"])),
        "pageNamespaces": dict(page_ns.most_common()),
        "pageNamespaceExamples": dict(sorted(pages_in_ns.items())),
        "namespaceClassificationBasis": (
            "observed-title-prefix-only-not-API-namespace-verified"
        ),
        "pageServiceUrls": service_pages,
        "pageHttpStatus": dict(html_status),
        "assetHosts": dict(host_assets.most_common()),
        "assetHostExamples": dict(host_asset_sample),
        "assetClasses": dict(asset_classes.most_common()),
        "assetClassExamples": dict(asset_class_sample),
        "assetSourceVersionEvidence": dict(asset_source_version),
        "sameSiteAssetUrls": sorted(same_site_asset_urls),
        "thumbOriginalCandidates": image_original_candidates[:20],
        "crossHostSameMemberPaths": {
            path: sorted(hosts) for path, hosts in source_url_paths.items() if len(hosts) > 1
        },
        "referenceServiceCounts": dict(rest_refs),
        "referenceServiceExamples": dict(rest_samples),
        "nonarchivedReferenceCounts": dict(unresolved_by_reason),
        "nonarchivedReferenceExamples": dict(unresolved_sample),
        "ignoredUrls": ignored,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(audit(args.archive, args.manifest, args.inventory), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
