# Human Sphere → Rules Wiki acquisition parity review

Reviewed 2026-11-15 against repository `main` at
`02173fca4f89e5fa57ec8e4939c05f8672e6733a`. This is a
**source-policy and implementation audit**, not a live crawl. No source-site
API configuration, access, alias behavior, or asset inventory was
independently confirmed against a running deployment.

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
| **Page/directory collisions** | `page_path_suffix=".html"` separates page `/Foo` from descendants `/Foo/Bar` | Page paths are extensionless: `Foo` and `Foo/Bar` can require a filesystem file and directory with the same name | **High: open**. Fails rather than safely mirroring both. Needs a reviewed deterministic path scheme and explicit migration of completed work/snapshots; copying the Human Sphere suffix blindly would change every Rules Wiki page path |
| **Other path normalization** | Case and Unicode safeguards, but punctuation folding can still collide | Case and Unicode safeguards, but punctuation folding and case-only *asset* names can still collide | **Medium: shared open risk**. Both fail closed; review a path-identity escape scheme only when source cases are observed. Do not infer two distinct URLs are the same page |
| **MediaWiki thumbnail originals** | From a standard same-site `/images/thumb/.../` derivative, also attempts original `/images/.../` upload; records candidate source evidence | The thumbnail-to-original rule is currently enabled only for Human Sphere; Rules Wiki also has a distinct `assets.corvusbelli.net` host | **Conditional**. Enable only for verified same-site standard MediaWiki thumbnails if present; never fabricate originals from external/static asset URLs |
| **Noncontent namespaces** | Ignores `Talk:` and the configured project namespace | Ignores `Infinity:` project namespace, but **not** `Talk:` | **Medium: site-policy review**. Confirm that excluding Rules Wiki talk pages is appropriate for intended source coverage before changing ignored URL classes |
| **Service endpoints** | Ignores paths under `/rest.php/` and `/cdn-cgi/` | No analogous ignored-prefix configuration; `index.php` and `Special:` are already skipped by shared logic | **Conditional**. Inspect en/es service routes, especially separate `/wiki-es/` API endpoints, before deciding what must not be mirrored |
| **Discovered HTTP 404s** | May ignore a 404 for a *non-required linked* page/asset; an allpages-required page remains mandatory | All discovered eligible 404s are failures unless explicitly ignored by another rule | **Source-specific by design** until live evidence shows stale optional links are common; do not copy 404 ignoring to the Rules Wiki unconditionally |
| **Host canonicalization** | Both `human-sphere.com` and `www.human-sphere.com` normalize to one origin | Only `infinitythewiki.com` is recognized as a page host | **Conditional**. Confirm whether `www.`/other aliases actually serve the same installation before adding them |
| **Courtesy pacing** | 0.5 s per source request | No configured delay; common HTTP backoff still retries transient failures | **Operational difference**, not a known bug; revisit based on source limits or real 429/503 observations |
| **Localization / output** | English only, `HUMAN-SPHERE`, page `.html` suffix | Separate en/es wiki/API roots, `WIKI-en`/`WIKI-es`, extensionless pages | **Intentional**. Preserve correct separate source languages, paths, and archive identities |

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
rather than silently changing existing link-only default behavior
while P3.4 portable filename work remains. The original crawl
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

### Portable paths and migration

The case-sensitive Rules Wiki fix and the new Unicode disambiguation
change the deterministic local name of some pages and assets. A
previously written HTML page can contain *old rewritten links* even after
the downloader executable changes. A raw `--resume-work` cannot safely
reclassify the already checkpointed files.

For the previously interrupted **fresh non-history** run with original
`--source-replay-dir` bodies, use
`python -m infinity_wiki_acquisition.repair_crawl_paths --work OLD.work
--output-work NEW.work` before resuming the new work directory. The repair
replays original HTML and updates saved paths/evidence while preserving the
old work. It refuses missing or incompatible replay evidence. History and
parent-derived work need a separate migration contract, not this command.
A completed earlier snapshot is immutable; a history-only upgrade retains
its old current-page paths, rather than retroactively renaming them.

The extra `.html` suffix that protects Human Sphere from `Foo` versus
`Foo/Bar` conflicts cannot be copied into an active Rules Wiki run without
a source-path/rewrite and checkpoint migration. The portable ZIP verifier
remains fail closed in the interim.

## Next steps

1. **P3.3 implemented:** English/Spanish API probes, English
   archive-versus-source coverage comparison and an explicit opt-in
   full-discovery mode are complete. For actual acceptance, run a full
   source update/new `--allpages` snapshot and independently verify its
   artifacts; a full Spanish archive comparison remains unperformed.
2. **High:** Design Rules Wiki page-versus-directory-safe filenames for
   subpage titles without breaking index/language routes or portable ZIP
   hashes. Explicitly test on Windows/Linux, root paths, link rewriting,
   archived history and interrupted raw replay.
3. **Medium:** Study actual Rules Wiki namespace/service endpoints, optional
   404 responses, MediaWiki thumbnail/imageinfo and host aliases from
   **source evidence** before extending any Human Sphere source-specific
   exception. Retain fail-closed source coverage by default.
4. **Lower:** Review path collisions from punctuation folding and asset
   capitalization on **both** sites. Observe real collision cases before
   changing output names globally.
5. **Acceptance:** Run focused offline parity tests, then verify actual
   fresh ZIP/v3 manifest/v1 inventory/source replay, followed by
   a separate history upgrade after intended current-page coverage is
   established. No external crawl or cross-repository deployment is
   part of this audit.

The exact rendered-page freshness/source-time limitations in
[RENDER_FRESHNESS_AUTHORITY.md](RENDER_FRESHNESS_AUTHORITY.md)
apply equally to both sites. Porting path or source-policy fixes must
not enable HTTP 304 rendered-HTML reuse.
