# P3.5 Rules Wiki source-specific policy evidence

Date: 2026-11-16. Audited the independently verified English
`WIKI-en 20261116-075926` and Spanish
`WIKI-es 20261116-092640` current-content snapshots using the
**read-only, offline** `python -m tools.audit_rules_source_policy`
command. Their ZIPs, provenance/inventories and original HTML replay
companions independently verify. Both independent history children, `WIKI-en-history 20261116-090844`
and `WIKI-es-history 20261116-101038`, have since passed
ZIP/provenance/inventory and exact parent-lineage verification.
The P3.5 source-policy audit examines **current HTML and assets**,
not inferred rules about old rendered revisions.

This is an inspection of captured source URLs, provenance, page references
and ignored URLs. It does **not** assert that the archive contains every
externally referenced resource, that transient HTTP errors could not occur,
that old rendered content is still current, or that English and Spanish
are guaranteed to share configuration beyond what was observed.

## Observed English archive

| Evidence | Count or finding |
| --- | --- |
| Current HTML pages | **711**, all observed HTTP 200 in page evidence |
| Archived assets | **221** |
| Corvus Belli asset-host URLs | **209** from `assets.corvusbelli.net/wiki/` |
| Same-origin asset URLs | **12**: 11 distinct `load.php` query URLs (MediaWiki styles/scripts) and 1 `opensearch_desc.php` XML |
| Standard MediaWiki `/images/thumb/` URLs | **0** |
| Candidate same-site thumbnail → original conversions | **0** |
| Unknown-classified archive members | **0** |
| Source URLs needing another wiki-host alias | **0 observed** |
| Same archive member path shared across source hosts | **0 observed** |
| MediaWiki API service URLs saved as current pages | **1 inherited `api.php` pseudo-page**, already covered by P3.6b |
| Source references explicitly classified ignored | **711**, all pointing to the optional wiki favicon |
| Other unresolved/non-archived source references | **0 recorded in the verified page-reference evidence** |
| Source URLs explicitly ignored during acquisition | **4**: `Infinity:About`, `Infinity:General_disclaimer`, `Infinity:Privacy_policy`, and `favicon.ico` |
| Optional HTTP 404 omissions | **0 recorded** |

The 711 current HTML URLs consist of 650 API-enumerated main-namespace
pages, 58 `Category:` pages, one `Template:` page, the entry root and
the inherited `/api.php` service pseudo-page. **Do not mistake unknown
colon-prefixed page titles such as `Fireteam: Duo`, `ARO: Automatic
Reaction Order` or `Burst: Single Target` for MediaWiki namespaces.**
The audit separates these ordinary titles from recognized
`Category:`/`Template:` namespace prefixes.

Every one of the 711 captured pages includes a reference to the single
archived OpenSearch descriptor and to the intentionally ignored favicon.
The 11 stylesheet/script resource URLs differ by `load.php` query
parameters, so their source identity must not be collapsed to a single
`load.php` asset filename.

## Source-policy decisions

| Human Sphere-specific behavior | English Rules Wiki decision | Evidence/condition |
| --- | --- | --- |
| Follow same-host `/images/thumb/` derivatives to original uploads | **Do not port** | No matching same-site MediaWiki images or thumbnails in the verified English acquisition. External Corvus Belli assets are not MediaWiki uploads; fabricating original URLs would be wrong. |
| Ignore `Talk:` pages | **Leave unchanged** | No `Talk:` page observed. `Category:` pages and ordinary colon-title pages are valuable source content. Exclusion would broaden source loss without evidence. |
| Ignore `/rest.php/` and `/cdn-cgi/` service prefixes | **Leave unchanged** | Neither path observed in recorded source URLs or source references. Exact English `api.php` service exclusion was implemented separately, based on actual checkpoint and inventory evidence. |
| Ignore non-required discovered HTTP 404s | **Keep fail-closed** | No optional 404s in the verified archive. No evidence supports lowering the Rules Wiki completeness gate. |
| Canonicalize `www.` host alias | **Leave unchanged** | Captured pages and assets use the configured canonical wiki host and the explicit external Corvus Belli asset host. A redirect-equivalence test is needed before changing canonical identities. |
| Apply Human Sphere's 0.5 s courtesy delay | **Do not change by assumption** | This completed crawl did not report rate-limit failures. Existing retry/backoff covers transient HTTP 429/5xx errors. Change pacing in response to actual source constraints. |
| Treat all `.php` endpoints as wiki pages | **Already corrected for known exceptions** | `load.php` is an explicitly referenced asset resource; `opensearch_desc.php` is validated XML (P3.5a); `api.php` is an API service rather than a MediaWiki page (P3.6b). Do not broadly ignore all PHP URLs. |
| Treat URL fragments and query parameters as distinct asset identities | **Retain shared logic** | Eleven distinct `load.php` resources are identified by query string. The current inventory distinguishes them and has zero unknown members. |

**P3.5 English conclusion:** no additional source-behavior code change
is justified. Maintain the current fail-closed policies and existing
approved hosts. This conclusion is based on the verified *current* archive;
it does not automatically generalize to historical source snapshots or
Spanish.

## Observed Spanish archive

The verified `WIKI-es 20261116-092640` current snapshot has
**1,146 members:** 930 source-backed current HTML pages and 216
assets. The separate source-replay bundle independently reproduced
**all 930** original HTML responses, and the inventory has **zero
unknown members**. There are 870 current pages with independently
recorded main-namespace allpages numeric IDs and 60 other pages
without those IDs; those 60 consist of 58 `Categoría:`
prefixes, one `Plantilla:` prefix and the separate entry root.
Localized page-title prefixes here are *URL observations*, not
an API-backed namespace classification.

| Evidence | Spanish archive finding |
| --- | --- |
| Current HTML pages | **930**, all recorded with observed HTTP 200 |
| Corvus Belli asset-host URLs | **207** from `assets.corvusbelli.net` |
| Same-origin assets | **9**: 8 query-distinct `/wiki-es/load.php` styles/scripts plus one `/wiki-es/opensearch_desc.php` XML descriptor |
| Standard MediaWiki `/images/thumb/` source URLs | **0** |
| Verified same-site thumbnail-to-original candidates | **0** |
| Unclassified archive members | **0** |
| Observed page URLs from an alternate wiki-host alias | **0** |
| Explicitly ignored URLs | **5**: three `/es/Infinity:` project pages, the optional favicon, and the exact `/wiki-es/api.php` service endpoint |
| Ignored page references | **930**, pointing to the favicon |
| Non-archived external references | **8**, each pointing to `https://downloads.corvusbelli.com/infinity/rules/qsr-n5-es.pdf` |
| Other unresolved on-site eligible source references | **0 recorded** |

The external Spanish quick-start PDF reference is an intentional
**host-policy boundary**, not a failed download or omitted required
wiki page. Do not expand `asset_hosts` to
`downloads.corvusbelli.com` merely to mirror an externally linked
rules document: this acquisition archives the selected MediaWiki
source and its authorized asset host, not every external PDF.
A future explicit external-document capture policy would need a
separately reviewed scope and provenance contract.

Unlike the original English link-only parent, the new Spanish
snapshot already excludes the `/wiki-es/api.php` endpoint; it
does not contain an inherited `api.php` pseudo-page.
The Spanish MediaWiki `allpages` API was independently probed
earlier and returned numeric page IDs and continuation; the full
live Spanish 870-page listing has **not** been separately rechecked
after publication. That is not necessary to verify the snapshot's
internal integrity but remains a distinct live source-coverage
observation.

**P3.5 Spanish decision:** existing Spanish-specific `/wiki-es/`
OpenSearch and ResourceLoader handling works. No new 404,
namespace, host alias, thumbnail or pacing exception is justified
by the verified Spanish archive. Both P3.5b (English) and P3.5c
(Spanish) source-policy evidence reviews are complete. The
Spanish history upgrade subsequently passed independent acceptance
under P3.6e: 2,655 oldid revisions, 930 retained current pages,
216 assets and one generated history index, with zero unknown
members and exact parent lineage. Downstream InfinityDB consumer
acceptance remains a separate workflow.

## Repeatable read-only command

```powershell
python -m tools.audit_rules_source_policy `
  "local/data/wiki/WIKI-en 20261116-075926.zip" `
  "local/data/manifests/snapshots/WIKI-en 20261116-075926.json" `
  "local/data/manifests/snapshots/inventories/WIKI-en 20261116-075926.inventory.json"
```

For Spanish, substitute `WIKI-es 20261116-092640` in all three
paths above. The script requires a fully verified archive triple
and prints observed title-prefix classes, asset hosts/classes,
ignored URLs and nonarchived reference evidence. Namespace labels
are recognized URL-prefix categories only, not independent
MediaWiki namespace-ID verification. No source site is contacted
and no archive is changed.
