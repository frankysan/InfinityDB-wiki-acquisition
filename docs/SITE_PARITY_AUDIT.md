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
| **Fresh discovery** | `enumerate_pages=True`: paginated MediaWiki `allpages` plus outgoing links | `enumerate_pages=False`: root/link crawl only; `--update-from` separately forces `allpages` | **High: coverage gap**. An orphaned page can be absent from an otherwise successful fresh Rules Wiki archive. Current revision and relationship API observations may also lack numeric allpages IDs. Confirm both en/es API behavior before enabling enumeration; a history-only upgrade does *not* add missing current pages |
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

### Why allpages is the most significant coverage difference

The fresh Rules Wiki crawl seeds only its configured root URL when
`INFINITY_WIKI_SITE.enumerate_pages=False`. The Human Sphere fresh crawl
uses the `list=allpages` API and retains numeric page IDs. The later
`--update-from` workflow independently forces allpages, but
`--from-snapshot --include-history` *only enumerates history for the
parent's existing pages*. A completed link-only snapshot therefore
cannot be described as complete main-namespace coverage without additional
evidence. MediaWiki's
[allpages API](https://www.mediawiki.org/wiki/API:Allpages)
can enumerate a namespace using continuation tokens, subject to each
deployment's actual API availability.

**Recommended sequence:** finish/verify the in-progress current crawl as a
link-reachable snapshot; independently establish working en/es allpages
coverage and page-ID semantics; implement/test an intentional full-discovery
mode or policy; then obtain an allpages-complete current snapshot *before
calling a history-upgraded artifact comprehensive*. Do not change the
scope/mapping of the already-running work in place.

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

1. **High:** Verify Rules Wiki English and Spanish `allpages` access and
   compare observed page IDs/title sets against the fresh link crawl.
   Decide the desired default discovery coverage and provide an explicit
   clean migration/test path. Preserve existing snapshot lineage.
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
