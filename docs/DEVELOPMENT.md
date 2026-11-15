# Development and integration

This guide owns the standalone Wiki/Human Sphere producer's development
workflow. The [README](../README.md) owns the public CLI, site/language
support and consumer handoff; [pyproject.toml](../pyproject.toml) owns
installation; [the CI workflow](../.github/workflows/tests.yml) owns hosted
checks; [CHANGELOG.md](../CHANGELOG.md) owns significant operator outcomes.
Agents start with [AGENTS.md](../AGENTS.md).

## Source acquisition boundary

- Explicitly crawl Infinity Wiki in English or Spanish, or Human Sphere in
  English. Keep source site, language, archive member path, page/revision
  identity, source URL and acquisition time distinct.
- Preserve source page/revision content and referenced-asset provenance;
  distinguish offline-mirror link rewriting from the original HTTP bytes.
  Do not turn a mirrored page into a curated gameplay rule or assume its
  latest revision supersedes an official PDF/Army source. InfinityDB owns source reconciliation,
  content interpretation and subsequent application publication.
- Preserve deterministic `WIKI-en`, `WIKI-es` and `HUMAN-SPHERE` archive
  identities, with optional revision-history variants and a matching v3
  `InfinityDB snapshot provenance` JSON manifest. Exact ZIP SHA-256 and
  logical-content SHA-256 have different purposes; keep both.
- Maintain fail-closed crawl completeness: required pages, revisions or
  assets that fail do not create a finished ZIP/manifest. A partial
  `--resume-work` tree is work evidence, not a published snapshot.
  Optional/missing links must retain their explicit classification.
- Store a private, versioned crawl checkpoint during enumeration (with
  MediaWiki continuation tokens) and crawling (with a pending URL queue).
  Persist atomically at batch/periodic boundaries and on completed discovery.
  Resume must validate site/language and saved paths, avoid repeating committed
  HTML fetches, and preserve the normal complete-archive gate. Retry-only runs
  require a completed-discovery checkpoint. Keep v1 completed checkpoints
  readable, and use full discovery for older work with no checkpoint.
  A separate private history checkpoint preserves `rvcontinue`, page identity,
  enumerated revisions, downloaded oldids and page progress; commit each
  revision API batch and periodic oldid batches atomically. A failed historical
  request blocks publication and resumes from its recorded point, without
  re-enumerating committed batches. Validate history and current-page
  completeness independently. Checkpoint JSON is work metadata, not a ZIP
  member or a published manifest.
- Produce one immutable, versioned snapshot inventory after successful
  ZIP/provenance creation and before removing the work directory. Keep it
  alongside the manifest in an `inventories/` subdirectory, not inside the
  ZIP. Record each ZIP member's content hash and source evidence where
  available. Capture original-response hashes, source HTML relationships
  and final HTTP URLs in private per-page evidence before rewriting links.
  Retain `allpages` numeric IDs where supplied, and preserve HTML ID hints
  independently; never collapse contradictory identities. Canonical
  declarations and meta-refresh hints are not automatically HTTP redirects.
  Template/module page links are not verified render dependencies; mark
  missing identities, unobserved headers and transclusions as unknown.
  The existing v3 manifest and ZIP remain the independent consumer contract;
  if inventory publication fails, retract this run's newly created manifest
  and ZIP and preserve the work directory for recovery.
- Treat MediaWiki redirects, aliases, archive member safety, repeated
  paths, HTML-with-asset extensions, language differences and case-folding
  collisions as source-format concerns. Preserve true source identities
  rather than collapsing pages by similarity.
- Before reusing a completed archive, call `snapshot_verification.verify_snapshot`
  with the explicit ZIP, matching v3 provenance and v1 inventory paths.
  The verifier streams every ZIP member, checks checksums, path portability,
  duplicate/case/directory collisions, manifest/site/history metadata and
  history revision identities. Missing inventory or inconsistent evidence
  blocks reuse, while genuinely unavailable optional source metadata does not.
  Do not infer reuse safety from a successful standalone manifest/inventory
  loader call. It is an offline, read-only check, not a signature or source
  authenticity guarantee; determine artifact trust separately.
- For pre-inventory completed snapshots, use the explicit
  `legacy_snapshot_index.write_legacy_snapshot_inventory` API or
  `infinity-wiki-index-legacy` command. Require exact v3 provenance,
  match site/language/archive/history identity, and validate the constructed
  v1 inventory against the original ZIP/provenance **before** publishing with
  no-replace semantics. Do not rewrite the ZIP or provenance. Only a valid
  archived history index supplies recoverable current-page and oldid
  identities; unobserved source URLs, assets, original HTTP responses,
  ignored URL lists and page IDs remain unknown. Earlier manifest formats
  need a separately reviewed migration/verification contract, not inference
  from file extensions or archive naming alone.
- For the P2.1 non-history-to-history upgrade, accept only an independently
  verified ZIP/v3 manifest/v1 inventory triple with all original members
  explicitly classified as current pages or assets. Work in a newly created
  staging directory, copying the exact verified members (and their source
  metadata) from the source ZIP; never revisit current page/asset URLs.
  Reuse the existing checkpointed history downloader for oldid enumeration
  and fetching. Publish a new *complete* history ZIP, matching v3 manifest,
  and validated v1 inventory; retain original artifact hashes as lineage in
  the inventory and the parent ZIP as the manifest `inputArtifact`.
  Record distinct parent current-content acquisition, history completion,
  and new archive creation times. `historyCutoff=all-observed` means **all
  revisions advertised during the history enumeration**; no timestamp-limited
  historical subset is supported in P2.1. Unknown member identities and
  partial history fail closed. Archive-derived resume and historical
  as-of-time reconstruction remain outside P2.1 scope; retained failed
  staging is inspection evidence, not an automatic resume authorization.
- P2.2 also accepts a verified history parent with explicitly identified
  current pages/assets and a consistent indexed oldid set. Copy only
  current-page/asset members at staging time; never copy the parent's history
  index as if it were current. Each oldid is reusable only after fresh
  MediaWiki enumeration confirms its ID, parentid, timestamp, and the
  requested page URL's *archived* association. The canonical title returned
  by the API may differ across a stable page URL (move/redirect), but such
  title drift must be represented in a newly generated index, not by
  copying old revision metadata. Different URLs are not automatically
  equivalent aliases: if an archived oldid or a previous page/oldid
  association disappears from fresh enumeration, fail without publishing.
  Reject conflicting oldid identities across two newly enumerated pages.
  New revisions must still be downloaded; reused rendered revision bytes
  are hash-checked against the verified parent inventory.
  Preserve the original current-content acquisition time across history
  generations and separately count inherited oldids in
  `captureMetadata.reusedRevisionCount`. A new timestamped snapshot can
  legitimately have the same deterministic ZIP SHA-256 as its parent when
  its contents have not changed: the archive name and chronology distinguish
  the artifacts, and the v3 `inputArtifact` binds the immediate parent.
  This is not a page-move/current-content refresh mechanism (P2.3) or
  archive-derived checkpoint resume (P2.5).
- The initial P2.3 `--update-from` path accepts a verified, fully classified
  **non-history** parent (v3 manifest and v1 inventory), records a paginated
  RecentChanges scan (retention **unverified**), forces new `allpages`
  enumeration for the source language, and runs an entirely fresh current
  page/asset crawl with normal link rewriting and fail-closed publication.
  Only after fresh bytes are downloaded and SHA-256/size checked against the
  same parent source URL/path may old ZIP bytes be reused in the child.
  This conservative operation **does not skip network transfers**: the
  published parent digest, current page IDs and RecentChanges logs alone
  cannot establish that a rendered page or binary upload remained unchanged.
  Compare enumerated numeric page IDs to identify URL changes; corroborate
  actual moves against RecentChanges move logs where possible, otherwise
  retain unresolved alias/move status. Record old IDs absent from fresh
  enumeration as *unresolved*, not automatic deletion
  claims. Record observed edit/move/delete/upload changes separately and
  refuse to present an exhausted RecentChanges window as proven retention
  coverage. New output retains the existing immutable ZIP/v3 manifest/v1
  inventory contract, explicit `inputArtifact` and parent hashes.
  This was the initial non-history baseline; P2.3c.1 now extends
  updates to verified history parents. Interrupted derived resume and
  template-dependent render freshness remain outside this baseline.
- The first P2.3b improvement records optional original asset HTTP
  `sourceVersion` evidence bound to a source URL and archived member SHA-256.
  Unredirected HTTP 200 responses may retain **strong** ETags in private
  `.wiki-asset-evidence/` sidecars and published v1 inventory fields.
  The updater can offer `If-None-Match` only against a verified parent
  asset with that exact URL, member path, previous digest, strong ETag and
  matching effective URL. An HTTP 304 is reusable only with the *same*
  strong ETag, requested effective URL and an empty body. Copy the bytes
  from the verified parent ZIP and record `responseStatus: 304` and
  `conditionalRevalidated: true` against the child asset. A 200 or
  missing/weak/redirected validator never establishes unchanged content:
  download the new bytes and verify their format as usual. Contradictory
  304 evidence blocks reuse, never silently certifies stale bytes.
  Capture cache headers when available on fresh normal crawls too; legacy
  inventory records without HTTP validators remain valid and fall back
  to downloading. Track actual body transfers avoided through
  `conditionalReusedAssetCount` and `sourceVersionSkipping`; always
  fetch and rewrite current-page HTML because unchanged source revision IDs
  cannot prove unchanged template/module-rendered output (P2.4).
  An HTTP 304 validates a representation at request time; it cannot prove
  overall multi-request Wiki consistency, a complete RecentChanges window,
  or the original upload's MediaWiki SHA-1/version timestamp.
- P2.3b also uses the MediaWiki Action API `prop=imageinfo` for eligible
  *original* uploads, not thumbnails or third-party hosts. Request one latest
  imageinfo record with source URL, SHA-1, size and upload timestamp; reject
  missing/mismatched titles, URLs, checksum formats, sizes and timestamps as
  **unknown**, then fall back to a normal download. Imageinfo SHA-1 is a
  MediaWiki-native checksum (often base36), normalized to 40-character hex.
  A verified parent ZIP's actual asset bytes may be reused **without
  downloading a body** only when the source URL/path are the same and
  SHA-1 and size both equal the current imageinfo response. For new/changed
  uploads, retrieve the body unconditionally and compare the fetched SHA-1
  and size to the immediately queried metadata; conflicting observations
  block publication. Record the exact upload title, URL, SHA-1, size,
  uploaded timestamp, inspection timestamp and whether it authorized reuse
  in the optional `uploadVersion` member field. Offline verification also
  hashes each such ZIP member with SHA-1, checks the URL/identity/times,
  and reconciles `uploadSha1ReusedAssetCount` and
  `uploadVersionEvidenceCount` with actual inventory members.
  This is representation evidence *at each request's observation time*,
  not an atomic Wiki-wide snapshot or a substitute for a source signature.
  Combined with verified parent SHA-256 and immutable ZIP contents, it is
  stronger than inferring unchanged images from path or date alone.
  Source revision IDs for current HTML and dependency-aware reuse remain
  open P2.3b/P2.4 work.
- P2.3b.3 captures **post-crawl current-page revision observations** with
  bounded MediaWiki `prop=revisions` requests (`pageids` batches of 40,
  `rvprop=ids|timestamp`, `rvlimit=1`, newest first). Only current pages
  linked to numeric allpages page IDs qualify. For each observation, preserve
  page URL and local member path, independent MediaWiki page ID, the returned
  title, latest revision ID and timestamp, API request/check times and the
  earlier HTML fetch time (when recorded). Missing IDs, API errors, title
  disagreements and missing revision data are represented as explicit
  unknown statuses, **never** guessed or used to skip rendered HTML.
  Store private `.wiki-current-revisions.json` under the work directory,
  then expose optional per-member `currentRevision` and
  `captureMetadata.currentRevisionEvidence` (same v1 inventory format).
  The verifier checks observation times, ID/title/path matches, explicit
  unknowns and counts. `renderCorrespondence: not-verified` is mandatory:
  these API lookups happen *after* HTML rendering and do not prove which
  source version or transclusions generated the earlier response.
  On `--update-from`, compare parent/child latest source revision IDs by
  stable page ID and report same/changed/unknown/no-baseline states with
  `htmlRefetched: true`. Keep prior move/missing-page IDs separate from
  this comparison; an absent ID is not proof of deletion. P2.4 owns
  transclusion/dependency invalidation.
- P2.3b.4 reconciles *observable* source-time risks without claiming a
  point-in-time wiki snapshot. When current-revision metadata is available,
  attach optional `captureMetadata.sourceTimeCoverage` to the v1 inventory
  and independently recompute it during `verify_snapshot`. This compact
  assessment counts current pages, observed/unknown revision identities,
  unavailable original HTML timestamps and latest revision timestamps later
  than the earlier HTML fetch. It also counts HTML/revision observations
  **after** the requested RecentChanges window, and on archive updates,
  unresolved URL association changes and missing parent page IDs.
  `scope: observations-only`, `consistency: not-verified-atomic`,
  `renderDependencies: unverified` and `recentChangesRetention: unverified`
  (or `not-observed`) are mandatory. A later source revision timestamp is
  a risk signal, not a proof of exactly which revision or transclusions
  produced earlier HTML, and can reflect clock resolution/skew. Matching
  source revision IDs across archives cannot prove unchanged rendering.
  RecentChanges pagination can finish while its retention horizon remains
  unknown; its requested window may close before later pages or revision
  metadata are fetched, so it cannot certify edits during the crawl.
  A missing enumerated page ID or an uncorroborated URL change cannot be
  promoted to a verified deletion or move. Stronger freshness guarantees
  would need a source-side render/dependency identity, a verified change-log
  horizon and explicit observation/retry consistency rules, not just another
  latest-revision query. Until P2.4 addresses transclusion/category
  invalidation, all rendered HTML continues to be refetched and snapshots
  remain evidence collected across multiple requests rather than an atomic
  remote state. Existing ZIP/v3 manifest/older v1 inventories remain valid.
  P2.4a now publishes optional `captureMetadata.renderInvalidation` on updates.
  It classifies observed Template/Module/Category namespace changes,
  categorization and index signals, and matches changed namespace titles
  against *unverified* source-HTML page links. Link matches do not establish
  direct transclusion; module/Scribunto, transitive dependencies and actual
  category membership remain unknown, as does RecentChanges retention.
  The offline verifier recomputes every audit field from recorded events,
  current-page references, moves and absent IDs and rejects stronger claims.
  Keep fresh allpages/linked discovery and full HTML fetching, including
  when no change events or link candidates are found. Both historical-only
  v2 members and archived oldids remain outside the current-page audit.
  P2.4b must investigate additional paginated MediaWiki API relationships
  before any future P2.4c HTML-skip decision.

- P2.3c.1 allows `--update-from` to refresh a verified history parent.
  Keep the current page/asset recrawl, imageinfo/ETag reuse and private
  checkpoint behavior unchanged, but run the checkpointed history
  downloader against the **fresh current-page set** before publication.
  Only accept oldid reuse after parent oldid/parentid/timestamp agree with
  newly enumerated history and a verified old-to-new page association.
  Map previous/current URLs using stable numeric MediaWiki page IDs
  observed in the original and fresh allpages evidence. A changed title
  alone does not invalidate history. Several old alias URLs may converge
  on one current page ID: retain each mapping in
  `captureMetadata.historyRefresh.pageMappings`, with new oldid
  association metadata rebuilt from the fresh index. Require all
  originally archived oldids and page/oldid associations to survive the
  mapping; refuse conflicting identities or missing oldids. The v1
  inventory also records parent/fresh oldid counts, reused oldid count
  and `historyCutoff: all-observed`, cross-checked by the independent
  verifier. The previous complete ZIP/manifest/inventory remain immutable.
  If an earlier page's numeric ID cannot be found in fresh discovery,
  refuse publication by default: absence is not verified deletion.
  Opt-in historical-only preservation is separately versioned (P2.3c.2).
  Failed staging remains available; restarting it is deferred to P2.5.
- P2.3c.2 introduces **only for explicit `--allow-historical-only`**
  a new history index `formatVersion: 2` and inventory `formatVersion: 2`
  when missing historical page IDs must be retained. The existing v1
  formats remain untouched and supported by the same offline verifier.
  The opt-in command is valid only with `--update-from` and a verified
  history-enabled parent. Previous current-page HTML, whose original
  ID is absent from refreshed allpages enumeration, is stored as an
  immutable `historical-page` under
  `_history/retired/<pageId>/<SHA-256 URL prefix>.html`, with original
  URL, prior member path/hash, last known current title, archived
  canonical history title and first observed acquisition timestamp.
  Such a page **is not current and is not confirmed deleted**.
  `_history/index.json.historicalOnlyPages` contains the exact oldid
  records still tied to that former page. Existing parent oldids are
  retained from verified ZIP bytes, and all previous page/oldid
  associations must be present under either freshly enumerated
  `pages` or the explicitly retained former pages. The v2
  `historyCoverage` and per-page `historyScope: last-archived-only`
  make the absence of current enumeration explicit; live page history
  remains all-observed. The independent verifier enforces page-ID
  absence evidence, v2 member kinds, source URLs, byte hashes, oldid
  identity and the index/inventory version pairing. Repeating a v2
  update retains historical-only bytes and oldids. If such an ID
  reappears in allpages, require explicit reinstatement review rather
  than reclassifying it silently.
  **Consumer boundary:** Inspected InfinityDB `dev/v1.1.0`
  `0c853e74abf0956fd5d571920189f72a5db4c01c` read-only.
  Its `tools/research/audit_1_0_reference_inputs.py` history index
  reader iterates `pages` but not `historicalOnlyPages`; therefore
  producer v2 verification does *not* grant downstream acceptance.
  Keep v2 opt-in with a CLI compatibility warning until separately
  authorized, pinned consumer tests cover historical-only references.
  Archive-derived resume remains P2.5.

The consumer receives a portable **complete ZIP plus its matching JSON**,
not the producer checkout, a crawl work directory or an application
import. InfinityDB verifies that pair offline; the manifest's original
producer path is informational. When changing provenance/archive schemas,
maintain backward compatibility where supported and test a pinned
consumer reader without adding an `infinity_db` runtime dependency.
No claim of source-content completeness or gameplay accuracy follows
merely from a successful crawl.

## Checkpoint storage decision (P1.5, 2026-11-15)

**Keep the existing JSON checkpoints for P0/P1.** A bounded synthetic
microbenchmark on the Windows development checkout compared the current
atomic JSON writers with a minimal SQLite table, updating 100 rows per
transaction (`journal_mode=DELETE`, `synchronous=FULL`).
The median of three checkpoint writes was:

| Synthetic workload | Full JSON rewrite | SQLite 100-row update |
| --- | ---: | ---: |
| 10,000 discovered URLs (8,000 initially saved) | 11.0 ms | 2.5 ms |
| 75,000 discovered URLs (60,000 initially saved) | 74.1 ms | 2.8 ms |

JSON checkpoint construction added approximately 0.6/6.4 ms respectively.
For complete synthetic history state, full JSON rewrites took 16.2 ms with
10,000 revisions and 78.0 ms with 50,000 revisions. The 75,000-URL
benchmark's JSON file was ~14.6 MB and the SQLite file ~13.0 MB;
the latter has a deliberately **incomplete** schema and the sizes are not
directly comparable. JSON was measured via the existing writers including
serialization, atomic replacement and disk I/O; SQLite used a preloaded,
indexed table with one transaction and excluded the initial migration
(~172 ms at 75,000 rows), data mapping, source-file validation and
checkpoint contract checks. No live crawl was timed. These measurements
are directional and are **not** proof of an equivalent end-to-end SQLite
implementation or cross-platform performance.

The JSON checkpoint frequency (each enumeration batch and approximately
100 completed URL attempts; history at API batches, 100 oldids or each page)
makes the measured write cost small compared with expected network work.
There is no measured crawl-level bottleneck requiring a storage migration.
Changing only the on-disk encoding would not help: the caller still assembles
a complete state snapshot, and all saved paths still require validation when
resuming. Atomic JSON replacement and v1/v2 crawl compatibility are already
covered by tests.

**Revisit under P2**, rather than introduce two competing state authorities:
profile a genuine large archive-derived run if persistence consumes a material
fraction of its duration (for example over 5%), commits become much more
frequent, or lineage/change-set updates require expensive whole-state rewrites.
If SQLite is justified, use it only for private producer work state, commit
coherent URL/history batches transactionally, retain fail-closed resume/file
validation, and provide a tested one-time checkpoint migration. Do not import
the application runtime, store SQLite in published ZIPs, or change the
v3 provenance/v1 inventory consumer contract merely to change work state.

## Development checks

Python 3.11+ and standard-library runtime only. Use the repository
virtual environment:

```powershell
python -m pip install -e ".[test]"
python -m pip check
python -m pytest -q
```

The equivalent bounded runner `python -m tools.run_offline_tests` accepts zero
arguments for the full offline pytest suite or explicit `tests/test_*.py` files
for focused validation. It also supports managed script-job execution without
shell access, crawls or additional runtime dependencies.

The test suite is offline, using mocked HTTP and temporary directories,
with no live crawl, private Wiki archives, sibling repositories or third-party
data required. Test success and failure/resume boundaries, deterministic
archive bytes/member paths, manifest integrity and all supported
site/language variants. CI runs the same installed package on Windows,
Ubuntu and macOS at Python 3.11. Report any unavailable host or skipped
coverage instead of claiming hosted or application acceptance.

A networked `infinity-wiki-snapshot` run and `--include-history` selection
are explicit operator actions. The default relative output now uses
`local/data/wiki/`, `local/data/manifests/snapshots/` and `local/work/wiki/`;
legacy output and resume-work locations remain available via explicit CLI
arguments. Use categorized ignored `local/` paths for new research and
validation. Do not overwrite prior snapshots or delete incomplete crawl
data needed for recovery. Third-party source HTML, images and documents remain outside
the MIT code-license grant and out of Git.

## Changes and retirement

Update the README for CLI/output changes, this guide for development
policy, tests for executable invariants, and the changelog for substantial
operator outcomes. Do not copy InfinityDB's browser, database, release
gate or internal test queue into this producer.

For a bounded source audit or one-use recovery tool, record its
owner/question, source revision, input/output hashes and completion
criterion. Reconcile unresolved findings into maintained documentation;
retain unique source evidence where justified, then retire redundant
tools/reports and historical-only tests together. Never infer that a
test-only dependency makes an obsolete acquisition path permanent.
Immutable original archives and privately retained crawl evidence require
their own verified backup/provenance review before deletion.

Every cross-repository handoff states producer SHA, site/language/history
scope, ZIP/manifest hashes and version, consuming InfinityDB revision,
checks actually executed and any unverified cutover. Do not automatically
edit, fetch, push or deploy a sibling repository.
