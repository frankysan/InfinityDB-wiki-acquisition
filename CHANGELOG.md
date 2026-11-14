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
  rather than causing spurious binary-asset failures.
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
  `HaeTae` remain separate, without renaming already-downloaded assets.

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
