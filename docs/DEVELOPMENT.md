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
  Infinity Wiki now applies the existing deterministic case-variant
  disambiguation to page-title components, and Unicode/NFC-based
  disambiguation to page and asset components, preserving the ordinary
  root and /es/ and /wiki/ routing prefixes. Thus a title such as
  `No_LoF` receives a stable `__u_<sha256-prefix>` path distinct
  from conventional `No_Lof` on Windows and Linux. The full URL,
  page evidence, original HTML, and source identity remain unchanged.
  P3.4 additionally disambiguates case-only assets, punctuation folding,
  URL-encoded separators and portable generated index collisions.
  An interrupted **fresh non-history** crawl created before this
  mapping change must not directly resume stale rewritten paths.
  The offline `python -m infinity_wiki_acquisition.repair_crawl_paths
  --work OLD.work --output-work NEW.work` copies only checkpoint-
  committed files into a separate work directory. It requires the
  original `--source-replay-dir` HTML bodies, recomputes every
  saved page's offline link rewriting, updates matching source
  provenance, asset HTTP evidence, and checkpoint paths, and validates
  portable output paths without editing the original. It also preserves
  the previously chosen link-only/allpages discovery mode and writes
  the current path-layout receipt. The interrupted pending/failure
  queue is retained; up to one uncommitted checkpoint batch may
  repeat. It deliberately rejects history/parent-derived runs and
  refuses to guess missing original HTML. See the README's
  case-collision recovery example.
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
- P3.4 uses the Rules Wiki's deterministic, Windows-portable `.html`
  current-page members (`Foo.html` versus `Foo/Bar.html`) and preserves
  generated `index.html`/`es/index.html`/`wiki/index.html` files.
  Percent-encoded URL separators are split **after**, not before,
  source path segmentation. Sanitization loss, case-only assets,
  nonstandard source capitalization, Unicode and generated-index title
  conflicts receive component-based SHA-256 identity markers. The
  original source request URL, page ID and source representation hashes
  remain independently recorded. Human Sphere's current path policy is
  unchanged; any residual cross-origin or unusual source collision
  continues to fail closed.
  Every fresh Rules Wiki run writes a private
  `.wiki-fresh-discovery.json` version-2 receipt binding site,
  language, allpages/link-only mode and
  `rules-html-escaped-v1` path layout. Old link-only work without a
  receipt and P3.3 version-1 allpages work **cannot** directly resume:
  copy/rewrite committed fresh work using the original-source replay
  migration and then resume the migrated work with the original
  `--allpages` choice. Old archive-derived current-update work is
  rejected by its parent-bound receipt's missing path-layout option;
  start a new `--update-from` against the same verified parent instead.
  New update children refetch all current pages and use current paths,
  count inherited bytes only with exact-path evidence, and label
  same-URL path-only changes `mirror-path-layout-change`. A
  history-only upgrade keeps the old parent's exact path/bytes; this
  is intentional, not a migration. The v3 ZIP provenance and v1/v2
  inventory **formats** are unchanged, but downstream consumers
  cannot assume extensionless Rules Wiki members. Exact downstream
  acceptance and real-source tests remain P3.6.
- P3.3 full Rules Wiki discovery is selectable on **new fresh** runs via
  `--allpages`; link-only remains the default for operator compatibility,
  independently of P3.4's completed portable filename migration.
  Human Sphere already uses paginated `allpages` by default, and
  all `--update-from` runs force it. The
  Rules Wiki English/Spanish main-namespace APIs were probed on
  2026-11-15; both supplied positive page IDs and continuation. The
  independently verified `WIKI-en 20261115-223808` link-only snapshot
  contained 599 current HTML pages; a later observed 650-page English
  main-namespace API enumeration had 112 exact URL absences and 538
  matches. This is observational coverage, **not** an atomic source-time
  proof or an automated archive migration; see
  `docs/SITE_PARITY_AUDIT.md`.
  `--allpages` requires a numeric page ID for each returned main-namespace
  title, rejects reused IDs for different URLs and invalid namespace
  records, and persists the established v2 crawl checkpoint's
  `apcontinue` before fetching pages. The new private
  `.wiki-fresh-discovery.json` receipt binds site, language and
  discovery mode so an interrupted full-scope run cannot silently
  resume as link-only or vice versa. Under P3.4, the version-2 receipt
  also binds the new portable filename layout: older work must use
  explicit offline original-HTML migration rather than directly resume.
  Changing a link-only run to allpages still requires a **new run**. Parent-derived modes do not accept
  `--allpages` because current updates already use forced allpages
  and history-only upgrades do not fetch current pages. The optional
  `tools.audit_rules_allpages --live` compares exact URLs and
  independently observed page IDs to a fully verified snapshot and
  prints an observational report; `tools.probe_rules_allpages`
  performs only one API batch per requested language. Neither command
  fetches page HTML, mutates sources or authorizes reuse.
- P2.5a history-upgrade resume is explicitly enabled for
  `--from-snapshot --include-history --resume-work <work>` and the
  corresponding `upgrade_snapshot_with_history(..., resume_work=...)`.
  Before fetching any revision, verify the entire parent triple again
  and validate a private `.wiki-archive-run.json` receipt (exact
  ZIP/manifest/inventory hashes, source, language, operation and options).
  The receipt is created before first copying parent content and never
  enters the published archive. On resume, existing parent page/asset files
  are checked by actual SHA-256 and size and are not overwritten. The
  existing page-by-page history checkpoint resumes previously committed
  oldid enumeration/downloads, including completed pages and uncommitted
  files. A private append-only oldid reuse log records only actual
  parent oldid copies; on resume, logged oldids count only when their
  saved bytes still match the fully verified parent. Interrupted logging
  with no corresponding file is not a successful reuse. This preserves
  `reusedRevisionCount` across resumed history parents without claiming
  new network downloads as reused. History identity/association
  verification, immutable parent lineage and all-or-nothing publication
  remain unchanged. Current refresh resume is independently implemented
  under P2.5b and shares the parent-bound private receipt protocol.
- P2.5b current-update resume accepts `--update-from --resume-work`
  only with the exact independently verified parent triple and an existing
  `.wiki-archive-run.json` receipt for operation `current-refresh`,
  site, language and options (require-history, historical-only, replay).
  The receipt is committed before the RecentChanges scan. The complete
  scan is saved atomically to `.wiki-update-recentchanges.json` with exact
  parent/child requested observation boundaries; on resume it is parsed
  with bounded IDs, event timestamps and retention explicitly unverified.
  If a crawl checkpoint exists but the scan sidecar is missing or invalid,
  fail before network. The existing P0 crawl checkpoint persists the
  allpages continuation token, fetched URL queue, saved files and failures.
  Resuming validates committed HTML rewrite hashes, original source-HTML
  replay SHA-256 (if selected), and asset bytes before making another
  request; it never treats a recorded size alone as a secure byte identity.
  Each asset fetch writes an atomic private, URL-keyed decision before the
  crawler writes its file: selected response-body SHA-256, path/size,
  optional MediaWiki upload metadata and conditional 304 reuse flag.
  The asset decision is trusted only when the currently saved file hash
  matches, and conditional validation is independently corroborated with
  the private HTTP asset source evidence and the verified parent's strong
  ETag/version. Upload reuse rechecks the saved SHA-1/size and exact
  parent member; saved upload and conditional counts are reconstructed
  from completed records, not transient Python sets.
  Complete `.wiki-current-revisions.json` and
  `.wiki-page-relationships.json` observations are reusable only if
  their exact URL, path, page ID and earlier HTML fetch time still match
  all completed current pages. Missing observations are fetched; stale or
  partially contradictory sidecars fail, rather than being silently
  promoted. History updates retain their already committed oldid
  checkpoints and parent reuse log; v2 historical-only/reinstatement
  rules still apply. Existing archived retired-page and missing-oldid
  files are checked against verified parent bytes on resume rather
  than silently overwritten.
  Private run/scan/asset evidence never enters published ZIPs. Cleanup
  occurs only on success; source interruptions or verification errors
  leave work inspectable with no resulting archive, manifest or inventory.
  Parent snapshots are never modified. This is resumability, not a
  dependency-fresh HTML/atomic source-time guarantee.
- P2.5c operator acceptance is recorded in
  `docs/ARCHIVE_RECOVERY_ACCEPTANCE.md`. The consolidated synthetic
  CLI tests interrupt both archive-derived modes at publication, verify
  that the exact preserved `*.work` path is printed, and resume without
  re-requesting fully checkpointed work. Both final ZIP/v3
  manifest/v1-v2 inventory sets independently verify, including exact
  parent SHA-256 lineage and unchanged parent bytes; private work
  receipts and evidence never enter a snapshot ZIP. The post-staging
  exception handler attaches a Python 3.11 exception note containing
  the recoverable work path for errors/interrupts that are not already
  `SnapshotUpdateIncomplete` or `SnapshotUpgradeIncomplete`; the
  archive-derived CLI handlers print the note alongside the reason.
  If an error occurs **before** a valid work directory exists, no
  recoverable path is asserted. Only committed checkpoint work is
  protected against duplicate network requests; an interrupted batch
  may be replayed, and no live source consistency is claimed.
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
  P2.4b now observes `prop=templates|categories` using numeric current
  page IDs in batches of 40, with `tllimit=cllimit=max`, complete MediaWiki
  continuation tokens, loop/resource bounds and exact API response digests.
  It stores private `.wiki-page-relationships.json` before inventory creation;
  optional inventory-v1/v2 `pageRelationships` records carry page URL/path/ID,
  returned API title, namespace/title lists, query start/end, earlier HTML
  observation time and response SHA-256s. API errors, malformed continuation,
  page-ID/title disagreement, redirects, unsupported namespace data or an
  exhausted request budget result in **unknown**, without partial lists.
  API title matching handles underscores and case without treating a redirect
  or shared numeric page ID as proof that different URLs are aliases.
  The verifier checks each current page record, snapshot page identity,
  observation chronology, exact list shape/order, status, non-claims, and
  `pageRelationshipEvidence` aggregate counts; old inventories remain valid.
  API response digests are provenance observations, not authenticity proofs
  or a substitute for retaining/verifying raw remote responses externally.

  The `prop=templates` API lists pages reported transcluded; it does not
  establish **direct versus indirect** dependency or module/Scribunto call
  closure for a specific earlier rendered response. `prop=categories`
  observes membership at API request time, not guaranteed membership when
  earlier HTML was fetched or later during a Wiki update. The P2.4a
  invalidation audit adds separately labeled post-HTML API candidate matches;
  these are not equivalent to HTML source-navigation link candidates or
  proved render invalidation. `sourceTimeCoverage.renderDependencies`
  remains `unverified`; fresh discovery, HTML refetch and link rewriting
  remain mandatory under P2.4c. If a verified update parent is subsequently
  upgraded with `--from-snapshot --include-history`, preserve the copied
  current page relationship records and `pageRelationshipEvidence`, and
  keep the parent's update-mode audit/accounting in the explicit
  `inheritedCurrentUpdateEvidence` metadata field. Do not relabel it as
  a new current-source update or alter observation timestamps. The
  independent verifier checks that the inherited relationship coverage
  matches those carried-forward current pages. No new relationship API
  request is made during the history-only upgrade.

  P2.4c.1 adds a deterministic post-crawl `renderReuseGate` to current
  update inventories. It consumes the already-checked
  `currentRevisionComparisons`, `sourceTimeCoverage`,
  `renderInvalidation` and `recentChanges` evidence plus the complete
  current-page member set. It labels every page `refetch-required`,
  lists unmet proof requirements and independently observed risk signals,
  and explicitly records `reuseAuthorized: false` and zero skipped HTML
  bodies. Even `same-source-revision` with no observed changes is
  insufficient: the original rendered representation is not validated,
  source time is non-atomic, transitive/module closures and category/index
  consistency are unverified, RecentChanges retention is unknown, and the
  archived bytes are already rewritten. No code path uses the audit as a
  positive reuse grant. `verify_snapshot` recomputes the exact per-page
  list and counts; future fast paths must be separately versioned and
  cannot simply flip this report to `reuseAuthorized: true`.

  On `--from-snapshot --include-history` of a verified update parent, the
  gate remains under `inheritedCurrentUpdateEvidence` and is checked
  against the retained current page members. V2 historical-only pages and
  oldids are outside the current-page decision set. Existing v1/v2
  inventories lacking this optional field continue to verify. Do not
  infer fresh HTML acquisition from history-only upgrades. P2.4c.2
  remains open: require independently validated rendered-response identity
  (including URL/representation/headers), a bounded source-time and
  dependency/category/index contract, and byte-equivalent or replayable
  offline rewrites before authorizing a skipped body.

  P2.4c.2a now captures source-response and configured rewrite-profile
  observations in the optional per-current-page `renderedResponseVersion`.
  It records original-body and mirrored-member SHA-256, original request
  URL/path/time, HTTP status/effective URL/ETag/Last-Modified and the exact
  configured Accept/Accept-Language/User-Agent values. The implementation
  fingerprints the installed `cli.py` and `path_sanitization.py` bytes
  and Python major/minor version, with memoized source hashes for large
  crawls. Only an observed HTTP 200 at the unchanged request URL and an
  actual strong ETag produces `strongEtag`; missing/weak/redirect metadata
  is retained as weaker source observations, not promoted. A 304 returned
  during an unconditional HTML fetch fails closed. The producer binds the
  original byte hash to its fetched HTML before rewrite and the mirrored
  hash to the ZIP member. `verify_snapshot` independently checks the
  member binding, status, strong ETag semantics, profile structure,
  page-ID member URL/path and matching HTML timestamps in revision and
  API relationship observations. The raw HTTP body is *not* archived,
  so its claimed hash is not independently replayable from the output.
  Fingerprints are observational, not signatures or proof that an external
  source returned particular bytes. Historic current-page members retain
  their original acquisition evidence; historical-only pages/oldids do
  not acquire new current-response claims.

  P2.4c.2b.1 adds opt-in `--source-replay-dir` to normal fresh and
  update acquisitions. The crawler atomically stores each exact pre-rewrite
  current-page HTTP body in private `.wiki-original-html/<SHA-256 URL>.html`,
  never in the normal mirror file set or existing ZIP. On success, after
  independent verification of the ZIP, v3 manifest and v1/v2 inventory,
  `create_source_replay_bundle` generates a separate versioned companion
  ZIP. Its `index.json` binds those three artifact hashes, original URL,
  raw HTML SHA-256, rewritten member identity/hash and complete raw
  LinkExtractor source-link count/digest, including ordinary navigation
  that the primary inventory intentionally excludes. The independent
  `verify_source_replay_bundle` checks every raw ZIP member, recomputes
  full source-link extraction, reruns the exact HTML rewrite, and compares
  bytes to the independently validated snapshot ZIP members. It does not
  use network access or grant rendered freshness. Raw source bodies are
  streamed into the companion to avoid retaining the entire crawl in RAM.
  Failed or missing original body evidence refuses a partial companion and
  retracts that run's new published snapshot artifacts. Preserve work on
  failure; successful work cleanup does not discard the independent
  companion. Retired/historical-only members and oldids are out of scope.
  Since `--from-snapshot --include-history` does not fetch current pages,
  it cannot create a new companion from earlier archived rewritten HTML.

  `verify_conditional_html_304` is a separate *non-authorizing* offline
  check: it first verifies the parent archive, inventory, manifest and raw
  replay companion, then requires an exact matching URL, strong ETag, GET
  request+rewrite profile, HTTP 304 status/effective URL, empty body and
  corresponding conditional request/response ETags. Any difference fails.
  Its result explicitly sets `reuseAuthorized=false` because independent
  rendered-dependency/source-time consistency and full variant coverage
  are still missing. No crawler supplies `If-None-Match` for current HTML.
  A separately versioned positive gate is required for actual skips
  (P2.4c.2b.2). Old archives remain compatible with the original required
  three-file verification; companion validation is opt-in and independent.

  P2.4c.2b.2a adds HTTP transport-variant observation without changing
  crawler reuse. `fetch_bytes` now sends an explicit
  `Accept-Encoding: identity` request and retains response `Vary`,
  `Content-Encoding`, `Content-Language`, `Cache-Control` and whether
  headers were actually observed (including on conditional HTTP 304).
  `rendered_response.capture_rendered_response` writes optional
  `renderedResponseVersion=2` with a separately validated
  `transportVariant` only if bounded response headers were obtained.
  It preserves v1 records when header metadata is missing/unusable, and
  the offline verifier accepts both. The version 2 request/rewrite profile
  explicitly includes `Accept-Encoding: identity`; prior version 1
  request profiles remain supported and are ineligible for the new
  transport preflight. The version 2 variant lists only known supported
  Vary fields (Accept, Accept-Language, User-Agent, Accept-Encoding), and
  rejects wildcard/unsupported Vary, compressed content, content-language
  mismatch, no-store, unknown/weak ETag and request/effective URL changes.
  These are transport-only checks; they are not a source-side proof of
  HTML freshness or dependency closure.

  `prepare_html_transport_probes` first independently verifies the
  complete parent ZIP, v3 manifest, inventory v1/v2 and raw source-replay
  companion. It then compares each current page's exact stored request
  profile with the installed implementation, and records a versioned,
  deterministic list of transport candidates and blocking conditions.
  `verify_html_transport_response` is the safe parent-verified entrypoint
  for evaluating a mock/live conditional result. It rejects mismatched
  URLs, ETags, redirects, 304 Vary/encoding/language conflicts, missing
  observed headers and nonempty 304 bodies. A full 200 response requires
  normal source-body processing; even a matching 304 returns
  `reuseAuthorized: false` and `fetchBodyRequired: true`. The older
  `verify_conditional_html_304` remains non-authorizing and compatible
  with historical v1 metadata. Never allow the producer to skip HTML
  merely because a transport probe appears eligible; the independent
  render/source-time positive authority and checkpoint integration
  remain open in P2.4c.2b.2b.

  P2.4c.2b.2b.1 adds the read-only differential audit
  `render_freshness_audit.compare_rendered_captures`. Its eight arguments
  are two complete (replay ZIP, snapshot ZIP, v3 manifest, v1/v2 inventory)
  sets, with the earlier capture first. Before comparing it verifies all
  eight inputs offline through the existing independent snapshot/replay
  validators and rejects different site/language, backward acquisition
  timestamps or backward per-page HTML observation times. Each exact-URL,
  stable-numeric-ID current-page pair is compared using independently
  verified original HTML hashes. Comparison of strong ETags is allowed only
  if both responses are exact-URL HTTP 200, version-2 explicitly supported
  transport variants and identical request/rewrite profiles. The audit
  records an unchanged ETag alongside changed original HTML as a
  counterexample to safe validation, a same-page-revision render drift,
  changed ETags with identical/different bytes, unknown/incomparable
  variants, as well as newly enumerated and absent (not confirmed deleted)
  pages. Oldids and historical-only entries are not counted as current
  HTML. Output includes exact artifact hashes, direct-parent lineage
  indication, page counts and always `reuseAuthorized: false`. All
  comparisons are observations of *two historical acquisitions*: a
  lack of ETag collisions is not proof of future correctness or
  transitive render dependency/RecentChanges coverage. The positive gate
  remains blocked until the source provides an authoritative mechanism
  binding rendered HTTP versions to complete template/module/category/index
  outputs across the actual fetch time window, with full offline
  preflight/checkpoint/conditional fallback verification.

  P2.4c.2b.2b.2a investigated the authoritative HTTP and MediaWiki
  semantics; see `docs/RENDER_FRESHNESS_AUTHORITY.md` for linked RFC
  9110 sections and source/manual documentation. Generic MediaWiki
  parser-cache invalidation is asynchronous; REL1_35 generated a weak
  parser-cache ETag, and neither behavior is evidence of the specific
  remote sites' actual HTTP/extension/CDN configuration. No target-site
  implementation/configuration contract, complete dependency graph or
  bounded source-time horizon was verified. A positive skip gate is
  externally blocked, not inferred from an unbroken test run.

  `render_freshness_authority.assess_html_source_authority` consumes
  the four complete existing snapshot/replay paths and optionally a
  previous four-path tuple. It checks full parent provenance, exact
  transport variants, and optionally verifies two complete captures
  through the differential audit. Its separate v1 report fingerprints
  the current artifacts, exposes page-specific transport and revision/
  relationship gaps and fixed source-side missing obligations. Any
  verified equal-ETag/different-source-body finding becomes both a
  page-specific and source-wide blocking condition. The entrypoint
  always sets `reuseAuthorized: false`, `skippedHtmlBodyCount: 0`
  and every decision to `refetch-required`, even when transport
  probes and two snapshot bodies match. The read-only command is
  `python -m tools.audit_render_authority REPLAY ZIP MANIFEST INVENTORY
  [--previous OLD_REPLAY OLD_ZIP OLD_MANIFEST OLD_INVENTORY]`.
  This is optional independent analysis and does not alter the existing
  ZIP/v3 manifest/inventory v1-v2 or grant any crawler permissions.

  The next positive cutover is blocked on actual deployment-specific
  evidence (site/server/skin/extension/proxy implementation, validator
  binding, jobs/purges/retention, and source-time guarantees). Until that
  exists, keep P2.4c open and use the independently validated P2.5
  resumability without enabling unverified conditional HTML reuse.

  Stronger freshness assurance still requires a source-side render/dependency
  identity, verified change horizon and coherent source-time observations.

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
  Failed staging remains available for parent-bound resume using P2.5b.
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
  Archive-derived recovery is implemented and independently covered by P2.5.

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
