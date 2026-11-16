# Human Sphere → Rules Wiki acquisition parity review

Initial comparison reviewed 2026-11-15 at repository
`02173fca4f89e5fa57ec8e4939c05f8672e6733a`; the report was then
updated for P3.3/P3.4 implementation and P3.5b English source-policy
acceptance. P3.3 independently probed the English/Spanish MediaWiki
APIs, and P3.4 portable-path migration passed offline integration
tests. The real English current and history artifacts were subsequently
published and independently verified on 2026-11-16 (P3.6a/c).
P3.5b inspected the **verified English current archive** without
contacting the source. Spanish source-policy behavior and downstream
consumer compatibility have not yet been accepted.

## Shared protections already in place

Both `infinity-wiki` and `human-sphere` invoke the same
`cli.download_wiki`, `LinkExtractor`, canonical URL handling,
HTML rewriting, HTTP retry/headers, asset-content checks, resumable crawl
and oldid checkpoints, v3 provenance, v1/v2 inventory and independent
verification. They also share the page source-evidence sidecars,
`--source-replay-dir`, history upgrades and parent-bound updates.

These protections **do not** need separate Rules Wiki implementations:

- HTML-with-binary-extension validation, including MediaWiki namespace
  pages such as `Module:Infobox/styles.css` and `File:...` descriptions;
  actual binary assets are separately checked.
- `srcset` parsing and relative link rewriting, including fragments and
  query-distinct binary resources.
- Fail-closed ZIP portability, case/directory collision checks, checkpoint
  resumability and original-body replay verification.
- Exact source-page URL versus HTTP effective URL, markup page-ID hints,
  parent lineage and source-time observations. They are *observations*,
  not evidence of transitive rendering-freshness or atomic source time.

## Comparison matrix

| Area | Human Sphere configuration/behavior | Rules Wiki current behavior | Assessment |
| --- | --- | --- | --- |
| **Fresh discovery** | `enumerate_pages=True`: paginated MediaWiki `allpages` plus outgoing links | Link-only remains the fresh default, with opt-in `--allpages`; `--update-from` also forces it | **P3.3 implemented:** both en/es API endpoints returned positive IDs. English live comparison found 650 main-namespace pages, including 112 URLs absent from the 599-page verified link-only snapshot. `--allpages` supports checkpointed continuation and requires page IDs; a history-only upgrade still cannot discover pages absent from its parent |
| **Case-only titles** | Conventional title name retained; other capitalization gets a stable `__u_<hash>` page filename | Now uses the same policy for page title components, preserving `/`, `/es/` and legacy `/wiki/` routing directories | **Shared/fixed previously**. `No_LoF` and `No_Lof` now have separate portable names |
| **Unicode versus ASCII** | Non-ASCII page and asset names receive stable NFC-based disambiguation instead of colliding with ASCII transliterations | Previously no Unicode disambiguation; **enabled for Rules Wiki page and asset paths in this review** | **Fixed**. E.g. `Ca%C3%B1on` and `Canon` are distinct on Windows/Linux; Unicode normalization is tested |
| **Page/directory collisions** | `page_path_suffix=".html"` separates page `/Foo` from descendants `/Foo/Bar` | Now uses `.html` for ordinary source pages while retaining generated `index.html` | **P3.4 fixed:** `Foo.html` and `Foo/Bar.html` can coexist. Old Rules Wiki snapshots stay unchanged; new updates use the corrected layout with full source HTML refetch and exact parent lineage |
| **Other path normalization** | Case and Unicode safeguards; residual punctuation/asset casing may still fail closed | P3.4 adds component-level URL decoding, punctuation/encoded-separator markers, asset casing, generated-index reservation and host-independent Windows-portable names | **P3.4 fixed for Rules Wiki:** source-identity suffixes distinguish previously lossy URL components; Human Sphere continues its existing fail-closed policy. Cross-origin/local-path and unusual source aliases still require explicit evidence, not silent merging |
| **MediaWiki thumbnail originals** | From a standard same-site `/images/thumb/.../` derivative, also attempts original `/images/.../` upload; records candidate source evidence | Verified English/Spanish current snapshots have **209/207** Corvus Belli-hosted assets respectively and **zero** standard same-site MediaWiki `/images/thumb/` paths | **P3.5b/c evidence:** no thumbnail derivation is justified in either language; it would invent unobserved original URLs |
| **Noncontent namespaces** | Ignores `Talk:` and the configured project namespace | The verified English acquisition ignored three `Infinity:` project pages; it captured 58 `Category:` pages and one `Template:` page; no `Talk:` pages were observed | **P3.5b English evidence:** do not widen ignored namespaces without observed source justification; unrelated colon-bearing titles are ordinary page names |
| **Service endpoints** | Ignores paths under `/rest.php/` and `/cdn-cgi/` | The Rules Wiki OpenSearch XML descriptor is now classified as a resource, not a wiki page; `index.php` and `Special:` remain skipped by shared logic | **P3.5a narrowly fixed:** previously archived `opensearch_desc.php` was the only unclassified member in the verified English parent, and is re-fetched as XML on update. Broader namespace and service-endpoint exclusions still require live evidence, particularly for `/wiki-es/` |
| **Discovered HTTP 404s** | May ignore a 404 for a *non-required linked* page/asset; an allpages-required page remains mandatory | All discovered eligible 404s remain failures; the verified English snapshot has **zero optional 404 omissions** | **P3.5b English evidence:** retain strict completeness; no reason to generalize Human Sphere's optional 404 policy |
| **Host canonicalization** | Both `human-sphere.com` and `www.human-sphere.com` normalize to one origin | All 711 English pages are on `infinitythewiki.com`; 209 external assets use the explicitly approved `assets.corvusbelli.net` host | **P3.5b English evidence:** no additional alias needed by the reviewed archive; do not infer equivalence from hostname similarity |
| **Courtesy pacing** | 0.5 s per source request | No configured delay; common HTTP backoff still retries transient failures | **P3.5b English evidence:** completed acquisition had no reported rate-limit failures; preserve current behavior unless the source requires more pacing |
| **Localization / output** | English only, `HUMAN-SPHERE`, page `.html` suffix | Separate en/es wiki/API roots, `WIKI-en`/`WIKI-es`, and now portable page `.html` members | **Intentional**. Root and language index paths, source URLs, page IDs and archived historical identities remain independent of local member names |

### P3.3 observed API coverage and new opt-in full discovery

The original link-based Rules Wiki snapshot `WIKI-en 20261115-223808`
independently verified **820 total members and 599 current HTML pages**,
including its original-source replay companion. On 2026-11-15 a
separate read-only source API comparison observed **650 English
main-namespace pages**, with **538 exact URL matches and 112 absent**
from the earlier link-only snapshot. There were no conflicting known
numeric IDs, but those 538 matched original archived pages lacked
independently enumerated page IDs. **61 archived HTML URLs** were not
listed by the main-namespace API (potentially other namespaces and the
separate home route); they are not automatically false pages.
This later source observation cannot establish the original archive's
as-of-time source completeness. No original snapshot bytes were changed.

Both deployed language endpoints were confirmed with bounded, read-only
API requests: `https://infinitythewiki.com/api.php` (English) and
`https://infinitythewiki.com/wiki-es/api.php` (Spanish) each returned
a first main-namespace batch of 500 titles with positive page IDs and
an `apcontinue` token. The complete English listing was enumerated;
a full Spanish listing has **not** yet been compared with an actual
Spanish archive. See MediaWiki's
[allpages API](https://www.mediawiki.org/wiki/API:Allpages).

New `--allpages` is explicitly opt-in for **fresh Rules Wiki runs**,
rather than silently changing existing link-only default behavior.
P3.4's path-layout migration is implemented independently; changing the
discovery default remains a separate operator compatibility decision. The original crawl
checkpoints retain per-batch `apcontinue`, required source URLs and
numeric page IDs; strict mode requires every allpages title to carry
a valid positive ID and detects conflicting mappings. A private
`.wiki-fresh-discovery.json` receipt prevents an interrupted
full-discovery crawl from resuming as link-only or vice versa.
Tests prove inclusion of an unlinked page, continuation recovery,
English/Spanish source routing, snapshot and replay verification, and
subsequent history upgrade without redownloading parent current HTML.
The standalone `tools.audit_rules_allpages --live` command compares
independently verified snapshot members to current source API identities
without fetching HTML; `tools.probe_rules_allpages` checks one API
batch per requested language.

**Recommended sequence:** preserve the verified link-only parent;
use an explicit `--update-from` (which already forces complete
main-namespace enumeration) or create a new `--allpages` fresh
snapshot, verify all artifacts, and only then call a history upgrade
comprehensive for its observed page scope. Neither method proves
atomic source time or template/module rendering freshness.

### P3.4 portable paths and migration

Rules Wiki now uses `.html` for its current page members. A page
`/Foo` becomes `Foo.html` while `/Foo/Bar` becomes `Foo/Bar.html`;
ordinary generated indexes remain `index.html`/`es/index.html`/
`wiki/index.html`. Component-level escaping gives ambiguous punctuation,
percent-encoded separators, case-only asset names and reserved index
titles deterministic source-identity suffixes, and always uses Windows-
portable filesystem rules even when running on Linux. Human Sphere's
existing `.html` layout remains unchanged.

The new mapping changes every non-root Rules Wiki page's local member
name. Previously saved HTML contains *old rewritten links*: using
`--resume-work` directly against older work would be unsafe.
New fresh Rules Wiki work carries a private version-2 discovery/path-
layout receipt. Old unversioned link crawls and version-1 allpages
work are refused for direct resume until migrated.

For the previously interrupted **fresh non-history** run with original
`--source-replay-dir` bodies, use
`python -m infinity_wiki_acquisition.repair_crawl_paths --work OLD.work
--output-work NEW.work` before resuming the new work directory. The repair
replays original HTML and updates saved paths/evidence while preserving the
old work. It refuses missing or incompatible replay evidence. History and
parent-derived work need a separate migration contract, not this command.
A completed earlier snapshot is immutable; a history-only upgrade retains
its old current-page paths, rather than retroactively renaming them.

The offline repair creates a **separate** new work directory, validates
old checkpoint-committed original response evidence, rewrites every saved
page/link to the current layout, updates page and HTTP asset evidence,
and writes the new mode/layout receipt. Allpages selection is preserved.
If original HTML bodies are unavailable, restart a fresh acquisition
instead of guessing prior links.

A **completed original snapshot is immutable**. For the verified
`WIKI-en 20261115-223808` link-only archive, start a new
`--update-from` against its exact ZIP/v3 manifest/inventory to refetch
current pages, enumerate allpages, and publish a corrected child with exact
parent SHA-256 lineage. Same URL/page ID with a changed local member path
is reported as `mirror-path-layout-change`, not a wiki move.
The old ZIP/manifest/inventory and any history associated with that
parent remain intact. A history-only upgrade of an old parent retains the
old member names because it doesn't fetch or rewrite current HTML.

**Interrupted older archive-derived updates** are bound to their original
path layout. Their old receipt lacks the new boolean layout marker,
so recovery fails closed: restart `--update-from` from the same
verified immutable parent. The fresh-only repair command must not
rewrite archive-derived or history staging. Normal new update resumes
continue through the parent-bound checkpoint contract.

## Next steps

1. **P3.3 and P3.6a/c English accepted:** English/Spanish API probes and
   opt-in full enumeration are implemented. The 2026-11-16 English
   `--update-from` snapshot verified its current-page ZIP, provenance,
   inventory and original-HTML replay; its 650 main-namespace API URLs
   all matched the later source listing. The subsequently resumed
   history upgrade independently verified all 2,801 oldids and its
   parent lineage. Full Spanish archival acceptance remains unperformed.
2. **P3.4 implemented:** Added deterministic portable filenames, versioned
   work-mode/layout receipts, offline fresh-work migration, and immutable
   parent-to-child update tests for English/Spanish routing, old/current
   history, source HTML replay and ZIP verification.
3. **P3.5b/c English and Spanish source-policy audits complete:**
   See [Rules Wiki source-policy evidence](RULES_WIKI_SOURCE_POLICY.md)
   for the independently verified 932-member English and
   1,146-member Spanish current snapshots, their original HTML replay,
   namespace-looking source URLs, asset hosts, `/wiki-es/` service
   routes, and intentionally ignored/external references. No further
   Human Sphere-specific ignore, host, 404, thumbnail, or pacing
   exception was justified by either archive. The subsequently
   published Spanish history child was independently verified under
   P3.6e; the source-policy decision remains unchanged.
4. **Lower:** Rules Wiki punctuation folding and case-only asset paths now
   disambiguate. Retain Human Sphere's current fail-closed safeguards until
   real residual cases justify changes; inspect cross-origin asset-path
   conflicts and true source aliases before extending their policies.
5. **Remaining acceptance:** English and Spanish current-content
   and history archives have all passed independent offline checks.
   Verify downstream InfinityDB consumer compatibility against an
   exact pinned consumer revision. A separate live Spanish allpages
   re-enumeration, if needed for a same-time coverage comparison,
   would be an independent optional check. This source-policy
   audit makes no cross-repository edits, deployments or live
   HTML fetches.

The exact rendered-page freshness/source-time limitations in
[RENDER_FRESHNESS_AUTHORITY.md](RENDER_FRESHNESS_AUTHORITY.md)
apply equally to both sites. Porting path or source-policy fixes must
not enable HTTP 304 rendered-HTML reuse.
