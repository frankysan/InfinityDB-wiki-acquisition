# Changelog

All notable user- or operator-relevant changes to Infinity Wiki / Human Sphere
acquisition are documented here. Each version begins with a concise **Player
summary**, followed by fuller acquisition and operator detail.

This repository was split from InfinityDB on **2026-11-11**. Version **0.1**
(`0.1.0` in the Python package metadata) covers the standalone repository from
that split onward. Sections explicitly marked **before split** describe inherited
work in InfinityDB and retain its original version numbers and release dates;
they are not releases of this repository. Historical entries were reconstructed
from the downloader and shared archive/provenance Git history and the
[InfinityDB changelog](https://github.com/frankysan/InfinityDB/blob/main/docs/CHANGELOG.md).

## [0.1] - Unreleased

### Player summary

- No player-facing changes.

### Added

- **Acquisition:** Added the independently installable
  `infinity-wiki-acquisition` package and `infinity-wiki-snapshot` command for
  English and Spanish Infinity Wiki snapshots and English-only Human Sphere
  snapshots. The standalone package retains optional revision-history acquisition,
  resumable work and incomplete-crawl publication safeguards from InfinityDB.

### Changed

- **Acquisition:** New snapshots now default to `local/data/wiki/`,
  provenance to `local/data/manifests/snapshots/`, and crawl scratch to
  `local/work/wiki/`. Explicit legacy destinations remain supported.
- **Acquisition:** Separated source acquisition from the InfinityDB application.
  The package requires Python 3.11 or newer, uses only the standard library at
  runtime and has no `infinity_db` import dependency. Acquisition remains an
  explicit operator action; ordinary InfinityDB builds, serving and Docker
  deployment do not install or run this package.
- **Acquisition:** Preserved the existing archive identities and v3
  `InfinityDB snapshot provenance` contract during extraction. The repository
  contains acquisition code, not downloaded source documents, game data or
  graphical assets; third-party materials remain outside its source license.
- **Acquisition:** Retained the portable ZIP/JSON handoff defined in InfinityDB
  before extraction. The consumer verifies the exact archive SHA-256 and logical
  `snapshot.contentSha256` offline; the producer-relative path in the manifest is
  informational.

### Added

- **Acquisition:** Added P2.4c.2b.2b.2a non-authorizing
  source-rendering authority assessments backed by independently verified
  snapshot/replay artifacts and optional differential comparison. A confirmed
  strong-ETag collision becomes a source-wide blocker. The reviewed
  HTTP/MediaWiki contract documents external requirements before any
  conditional HTML reuse; snapshots and full-fetch behavior are unchanged.
- **Acquisition:** Added P2.4c.2b.2b.1 offline differential rendered-HTML
  freshness auditing across independently verified full snapshots and raw
  source-replay companion ZIPs. Reports exact source-byte changes, strong
  ETag mismatches/collisions, same-page-revision render drift and explicit
  identity/variant unknowns, with source artifact hashes and immutable
  non-reuse status. Finite comparisons cannot certify future rendered
  freshness, and HTML body refetch remains mandatory.
- **Acquisition:** Added P2.4c.2b.2a versioned HTML HTTP variant
  observations, including explicit identity encoding, Vary, content language,
  cache-control, and stricter source URL/validator conditions. Independently
  checked original-source replay and parent snapshot evidence now support
  non-authorizing transport probe planning and strict 304 assessment.
  Unverified render dependencies/source-time still prohibit all HTML
  download skipping; older v1 response evidence remains supported.
- **Acquisition:** Added opt-in P2.4c.2b.1 original-current-HTML replay
  companion ZIPs (`--source-replay-dir`) for fresh/updated snapshots.
  Versioned companion evidence binds raw source bytes, complete source-link
  discovery and deterministic rewriting to the verified ZIP/v3 manifest
  and v1/v2 inventory without altering those contracts. Strict standalone
  HTTP 304 checks require matching verified parent bytes, URL, request
  profile and strong ETag but do not authorize rendered HTML reuse.
  Full current-HTML refetch and conservative invalidation remain unchanged.
- **Acquisition:** Added P2.4c.2a per-current-page HTTP representation and
  HTML transformation provenance in optional inventory fields: observed
  status/URL, ETag/Last-Modified, source/rewritten hashes, configured request
  variant and implementation fingerprint. Strong validators are only recorded
  for exact-URL HTTP 200 responses; independent ZIP/member checks reject
  misattributed or forged reuse claims. Original HTML is not retained
  by existing archives, so no download skipping is enabled.
- **Acquisition:** Added P2.4c.1 independently checked rendered-HTML reuse
  rejection decisions to updated snapshot inventories. Each current page
  carries explicit missing proofs and observed invalidation risks, including
  unchanged revision IDs, templates/modules and category/index activity.
  Current HTML body fetching remains mandatory and the reported skip count
  is zero; old v1/v2 inventories remain supported. A history-only upgrade
  preserves the prior gate as inherited evidence, and the offline verifier
  rejects forged eligibility, skip counts and omitted pages.
- **Acquisition:** Added P2.4b MediaWiki template/category relationship
  observations to refreshed snapshots. Paginated, bounded API requests retain
  per-page namespace/title lists, numeric page IDs, observation windows and
  response digests; unavailable or contradictory results stay explicitly
  unknown. Independently checked optional inventory fields preserve existing
  ZIP/history contracts. API-observed render/category candidates are separate
  from HTML navigation links, and cannot authorize HTML reuse. Subsequent
  history-only upgrades preserve previously verified relationship records
  and explicitly nest the parent update audit rather than mislabel it
  as refreshed history-time evidence.
- **Acquisition:** Added P2.4a rendered-HTML invalidation signals to
  refreshed snapshot inventories. The independent verifier checks
  Template/Module/Category and categorization/index event identities and
  unverified source-link candidates against published source evidence.
  Unknown or indirect dependencies and unproven RecentChanges retention
  never permit HTML reuse; current HTML and discovery stay conservative.
  Optional v1/v2 metadata does not alter the history/ZIP/provenance contracts.

- **Acquisition:** Added opt-in P2.3c.2 historical-only retention
  (`--update-from --allow-historical-only`). If a previously archived
  page ID is absent from fresh discovery, a new v2 history index and
  inventory preserve the old HTML under `_history/retired/` and its
  original oldid/title/source associations without presenting that
  page as current or asserting verified deletion. Fresh current pages
  still use all-observed histories; former pages explicitly carry
  last-archived-only coverage. Previous v1 archives remain unchanged,
  v2-to-v2 updates keep retired pages, and reappearance requires
  deliberate identity review. Offline verification checks the new
  index/member/absence relationships. Existing downstream readers
  may ignore historical-only references; v2 publication is opt-in
  and warns that consumer acceptance remains separate.
- **Acquisition:** Added P2.3c.1 history-preserving `--update-from`
  updates. History-enabled parents now trigger fresh page/asset
  acquisition and complete revision re-enumeration. Oldid bytes are
  reused only after matching historical revision identities and stable
  MediaWiki page-ID mappings; confirmed moves and aliases that converge
  onto one current page are represented in the new index and parent
  lineage. Missing historical pages, revision identities or
  associations block publication rather than silently stripping
  history. Historical-only/deleted-page publication remains a
  separately tracked schema/consumer decision.
- **Acquisition:** Completed P2.3b.4 source-time and coverage reconciliation
  with optional, independently verified `sourceTimeCoverage` in v1
  inventories. Summaries count unknown current revisions, missing HTML
  observation times, revision timestamps later than earlier HTML fetches,
  events observed after the RecentChanges query cutoff, and unresolved
  page moves/missing parent IDs. The evidence explicitly disclaims an
  atomic source snapshot, complete RecentChanges retention and verified
  template/module dependencies; existing archives remain compatible.
  Rendered HTML continues to be refetched rather than reused on a
  revision-ID match.
- **Acquisition:** Added P2.3b.3 batched current-page revision observations
  after the HTML crawl. New snapshots record latest source revision IDs,
  timestamps, MediaWiki page ID/title, API query windows and original HTML
  fetch times when available. Unknown/mismatched API identities remain
  explicit; parent-to-child updates record changed/unchanged/missing revision
  baselines without ever skipping rendered HTML or claiming transclusion
  freshness. Optional v1 inventory fields and their coverage/comparison
  assertions are independently checked, preserving older archives.
- **Acquisition:** Extended P2.3b with MediaWiki original-upload version
  verification using the Action API's latest `prop=imageinfo` SHA-1,
  size, timestamp and exact URL. An unchanged original upload can now reuse
  bytes from an independently verified parent ZIP without requesting the
  binary body, even when no HTTP ETag exists. Changed uploads are fetched
  and checked against freshly observed imageinfo hashes; ambiguous metadata,
  thumbnails and external assets never authorize reuse. Optional per-member
  `uploadVersion` evidence and reuse counts are checked against actual
  ZIP bytes by the offline snapshot verifier. Rendered HTML and historical
  dependencies remain conservative; current-page revision tracking is next.
- **Acquisition:** Added P2.3b.1 asset-level HTTP version evidence and
  conditional byte reuse to `--update-from`. HTTP 200 asset responses can
  retain strong ETag, Last-Modified and effective URL in private work
  evidence and an optional member-bound v1 inventory `sourceVersion`.
  Later updates send `If-None-Match` for a matching verified parent asset
  and accept old ZIP bytes only after an exact, strong HTTP 304 validation.
  The verifier checks member hashes, validator evidence and conditional
  reuse counts; normal source fetches remain compatible with older inventories.
  Rendered HTML is still refetched, while MediaWiki upload SHA-1/version
  and current-revision metadata collection remain pending P2.3b.
- **Acquisition:** Added the initial conservative P2.3
  `infinity-wiki-snapshot --update-from <zip>` workflow for verified
  non-history snapshot parents. It forces fresh MediaWiki allpages discovery,
  records paginated RecentChanges as retention-unverified evidence, and
  reacquires current pages/assets while rewriting their offline links.
  Identical content is verified against source URL/path and SHA-256 before
  copying prior ZIP bytes; new, changed and removed member counts and
  page-ID URL transitions (distinguishing observed moves from unresolved
  aliases) are recorded in a new verified v1 inventory with parent lineage. Earlier artifacts remain immutable. Network-level skipping
  based on authoritative revision/upload version evidence and history-parent
  refreshes remain follow-up work, not falsely claimed optimizations.
- **Acquisition:** Extended `--from-snapshot --include-history` to verified
  history parents (P2.2). Previously archived oldid HTML is reused only
  after a fresh MediaWiki revision enumeration confirms its immutable
  revision identity and established page association. Shared revisions
  across aliases are reused once, while changed canonical titles are
  represented by regenerated history metadata. Missing historical coverage,
  changed revision identities or lost alias associations block publication.
  The new inventory records the count of reused oldids, maintains the
  original current-content acquisition timestamp across upgrades, and
  preserves parent hashes without altering the v3 ZIP/manifest contract.
- **Acquisition:** Completed P2.1 archive-derived history upgrade via
  `infinity-wiki-snapshot --from-snapshot <zip> --include-history`. Verified
  existing non-history ZIP/provenance/inventory triples can now be reused
  byte-for-byte without downloading current pages or assets again. The new
  history archive preserves inherited source evidence, independently acquires
  revision lists and oldid HTML, and records parent artifact hashes and separate
  current-content/history retrieval times in its v1 inventory. Existing v3
  provenance remains compatible and records the parent ZIP as `inputArtifact`.
  Unknown source identities and incomplete historical acquisition block
  publication; P2.1 history scope is explicitly `all-observed`, not a
  date-bounded reconstruction.
- **Acquisition:** Added an explicit offline legacy snapshot indexer
  (`infinity-wiki-index-legacy`) for completed pre-inventory Wiki ZIPs with
  matching v3 provenance. It reconstructs v1 inventory hashes and only
  independently archived history-index page/revision identities, preserving
  unknown source metadata instead of guessing from filenames or HTML.
  Newly constructed sidecars pass full cross-artifact verification before
  exclusive publication; existing ZIPs, manifests and inventories are unchanged.
- **Acquisition:** A read-only `verify_snapshot(archive, manifest, inventory)`
  interface now verifies completed Wiki snapshots before reuse, rejecting ZIP
  hash/CRC/member tampering, unsafe or colliding archive paths, contradictory
  site/history/provenance metadata, and inconsistent revision-index identities.
  No existing ZIP or v3 provenance fields changed; older snapshots still need
  a verified inventory before reuse.
- **Acquisition:** Snapshot inventories now carry independently observed
  source-page identities and offline-mirrored file identities. The crawler
  persists private per-page original-HTML evidence, retains MediaWiki
  `allpages` page IDs where provided, preserves markup ID disagreements,
  observes actual HTTP response redirect targets, and distinguishes unverified
  canonical/meta-refresh hints. References to images, stylesheets,
  templates/modules and related namespace pages include evidence and target
  resolution status without asserting transclusion; original source response
  SHA-256 remains distinct from rewritten member SHA-256.
- **Acquisition:** Immutable version 1 snapshot inventories are now published
  alongside successful wiki ZIP/provenance outputs (under the selected
  manifest directory's `inventories/` subfolder). They enumerate every ZIP
  member with a SHA-256 digest, source URL evidence where known, mirrored
  current-page identities, historical revision identities and reserved
  source-lineage metadata. Unavailable response headers, omitted page IDs and
  unknown source identities are explicitly distinguished from verified facts.
  Inventory publication failures preserve work and retract this run's
  ZIP/manifest; the v3 downstream handoff format is unchanged.
- **Acquisition:** Separate private historical-revision checkpoints now retain
  per-page `rvcontinue` tokens, revision inventories, completed oldid downloads
  and page progress. `--include-history --resume-work` resumes failed or
  interrupted revision acquisition without repeating previously committed
  enumeration batches or oldid network requests. Incomplete history still
  blocks ZIP/manifest publication; checkpoints never enter the archive.
- **Acquisition:** Version 2 private crawl checkpoints capture enumeration
  continuation, pending URL queue and previously completed work. An interrupted
  `--resume-work` run now picks up from its last atomic checkpoint (per API
  enumeration batch and about every 100 processed URLs), instead of re-fetching
  every HTML page. Completed version 1 checkpoints remain compatible.
- **Acquisition:** `--retry-failed-only --resume-work` retries failed URLs
  and newly discovered descendants without restarting discovery. Checkpoint
  validation rejects missing/changed files and wrong site/language; checkpoint
  data never enters published ZIPs. Legacy work without a checkpoint retains
  the full-resume fallback.

### Fixed

- **Acquisition:** Collect and rewrite MediaWiki `srcset` candidates, including
  responsive `<source>` images, with links relative to the containing page
  so images in nested pages resolve offline. Human Sphere standard thumbnail URLs now also
  enqueue the original upload path when present, without silently inventing
  success for missing source files.
- **Acquisition:** Reject HTML error pages and mismatched common binary file
  signatures instead of archiving corrupted image/document/download assets;
  resume re-fetches invalid previously staged assets. MediaWiki `File:` and
  `Image:` description pages are now correctly classified as HTML even when
  their titles end in `.JPG`, `.png` or another image extension. HTML
  `<link rel="canonical">` and license metadata now resolve as page links
  rather than causing spurious binary-asset failures. MediaWiki namespace
  source pages such as `Module:Infobox/styles.css`, including corresponding
  talk namespaces (for example `Module_talk:Infobox/styles.css`), are likewise
  mirrored as HTML instead of failing asset validation; genuine CSS/JS assets
  are unaffected.
- **Acquisition:** Canonicalize percent-encoded versus literal URL punctuation
  before queue deduplication, fixing false collisions between links such as
  `Arslan%2C_Security_Chief` and `Arslan,_Security_Chief` without merging
  genuinely different titles, case variants, or asset query parameters.
- **Acquisition:** Normalize newly mirrored paths independently of the host OS,
  prevent distinct source URLs from silently sharing a local filename, reject
  unsafe ZIP members and file/directory collisions, and protect reserved Windows
  device names (including names with extensions). Human Sphere paths containing
  non-ASCII characters now receive deterministic disambiguation suffixes so
  names like `Aibot` and `Aibōt` can coexist. Nonstandard ASCII casing on
  Human Sphere HTML pages likewise gets a stable suffix, so `Haetae` and
  `HaeTae` remain separate. Case variants of file-description extensions
  (such as `File:Fusilier_Old5.JPG` and `File:Fusilier_Old5.jpg`) also
  remain distinct without renaming already-downloaded binary assets.

- **Acquisition:** Prevented concurrent Wiki/Human Sphere acquisitions from
  clobbering a previously published v3 snapshot manifest or another run's
  `.json.tmp` staging. Exact-byte identical manifest reruns remain valid,
  while conflicting identities and symlink outputs are rejected. The
  site/language/archive format and legacy output locations are unchanged.
- **Acquisition:** Protected a competing crawl's identical manifest from
  acquisition rollback by requiring newly created provenance. Direct manifest
  writes remain idempotent, with byte-exact comparison including line endings.

### Upgrade notes

- **Acquisition:** Replace the former InfinityDB invocation
  `python -m tools.download_wiki_snapshot` with `infinity-wiki-snapshot` after
  installing this package. Site, language, history, resume and output-directory
  options remain available. Relative output paths resolve from the working
  directory where the command runs.
- **Acquisition:** Transfer the ZIP and matching JSON manifest together. Archived
  pages remain source research evidence, not authoritative current gameplay
  rules. Provenance compatibility is checked against the consuming InfinityDB
  version rather than an application dependency in this package.

## InfinityDB [1.1.0] - Unreleased (before split)

This acquisition work landed in InfinityDB on 2026-11-11 before the extraction.
The heading retains InfinityDB's pending release target.

### Player summary

- No player-facing acquisition changes.

### Changed

- **Acquisition:** Defined the portable handoff for acquisition in a separate
  repository or on another machine: a complete immutable source ZIP and matching
  v3 provenance JSON, verified offline by the consumer. Producer checkout paths
  are not required, and missing or mismatched provenance blocks acceptance.
  Normal InfinityDB builds, serving and Docker packaging remain independent of
  source downloaders and external acquisition checkouts.

## InfinityDB [0.10.0] - 2026-10-07 (before split)

### Player summary

- No player-facing acquisition changes.

### Added

- **Acquisition:** Added English-only Human Sphere research snapshots through
  `--site human-sphere`, including MediaWiki enumeration of orphaned content
  pages, linked assets, optional revision history and resumable downloads.
  Human Sphere archives and work directories have identities separate from
  Infinity Wiki snapshots. Missing required pages block publication; Talk and
  service pages and stale discovered 404s remain diagnostics.

### Fixed

- **Acquisition:** Preserved downloadable PDF, RAR, TXT and ZIP files and
  MediaWiki upload-tree resources as assets rather than treating them as HTML
  pages. Downloaded files retain their extensions, and page paths do not collide
  with descendant asset directories.
- **Acquisition:** Added bounded retries when Windows temporarily locks a
  downloaded file during replacement, preventing transient file locks from
  immediately aborting acquisition.

## InfinityDB [0.9.0] - 2026-09-29 (before split)

### Player summary

- No player-facing acquisition changes.

### Added

- **Acquisition:** Added opt-in `--include-history` snapshots containing every
  public revision advertised by MediaWiki for each mirrored page. History
  archives retain raw rendered `oldid` pages and an index linking revision IDs,
  parent IDs, timestamps and source URLs to current pages. English and Spanish
  use their respective MediaWiki installations, and history archives have
  distinct names from current-page snapshots.
- **Acquisition:** Added `--resume-work` to reuse a preserved incomplete crawl
  directory. Previously downloaded revision files are reused, while current
  pages are crawled again; incomplete revision acquisition blocks ZIP and
  manifest publication and preserves work for another attempt.

### Fixed

- **Acquisition:** Added bounded retries with backoff for transient HTTP and
  connection failures. Permanent HTTP errors still fail without repeated
  requests.

## InfinityDB [0.7.0] - 2026-09-25 (before split)

### Player summary

- No player-facing acquisition changes.

### Changed

- **Acquisition:** Made archives reproducible for identical member paths and
  bytes by normalizing ZIP metadata and member ordering. Added a logical content
  hash independent of ZIP container metadata alongside the exact archive hash,
  with verification of both when reading the archive/manifest pair. Legacy
  manifests remain readable.

### Fixed

- **Acquisition:** Rejected duplicate and case-only conflicting archive paths
  instead of creating ambiguous snapshots across operating systems. Content
  verification also rejects unsafe or non-normalized member paths.

## InfinityDB [0.6.0] - 2026-09-19 (before split)

### Player summary

- No player-facing acquisition changes.

### Added

- **Acquisition:** Added generated provenance manifests recording the source
  URL, acquisition time, document count and exact archive SHA-256. Kept generated
  acquisition metadata separate from maintained research notes.
- **Acquisition:** Added separate English and Spanish crawls with language-scoped
  pages, shared referenced assets and language-specific archive names
  (`WIKI-en` and `WIKI-es`). Added crawl progress and separate diagnostics for
  ignored optional site URLs and required download failures.

### Changed

- **Acquisition:** Standardized timestamped ZIP snapshots with relative member
  paths and preserved incomplete work for inspection. Required download failures
  prevent ZIP and provenance publication; successful runs remove their staging
  directory.

### Fixed

- **Acquisition:** Canonicalized page URLs so query and fragment variants do not
  trigger duplicate page downloads, while retaining distinct queried assets.
  Optional site links remain external rather than becoming broken local links.

## InfinityDB [0.5.1a] - 2026-09-15 (before split)

### Player summary

- No player-facing acquisition changes.

### Added

- **Acquisition:** Added the original Infinity Wiki downloader, mirroring linked
  pages and referenced assets, rewriting local HTML links for offline browsing
  and archiving the result as a timestamped ZIP.

### Fixed

- **Acquisition:** Improved cross-platform filename and path sanitization for
  mirrored Wiki pages and assets, including Windows filename restrictions and
  local asset URLs.
