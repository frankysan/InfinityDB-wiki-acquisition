# Infinity Wiki / Human Sphere acquisition

Standalone **source acquisition** for InfinityDB research. This repository
contains no downloaded source documents, game data or graphical assets.
Raw third-party materials remain outside its source license.

## Offline development

The code requires Python 3.11 or newer and uses only the standard library.
Install locally with `python -m pip install -e ".[test]"`, then run
`python -m pytest -q`. The tests mock network fetches and require no
local Wiki/Army/PDF archives.

## Explicit acquisition

Run from the desired working directory:

```powershell
infinity-wiki-snapshot --site infinity-wiki --language en
infinity-wiki-snapshot --site infinity-wiki --language es
infinity-wiki-snapshot --site human-sphere --language en
```

The default ZIP directory is `local/data/wiki/`, the matching manifest
directory is `local/data/manifests/snapshots/`, and incomplete crawl work
is kept under `local/work/wiki/`, relative to the working directory.
The existing `--root`, `--work-root` and `--manifest-dir` options still
accept `data/wiki`, `data/work/wiki` and `data/manifests/snapshots` respectively.
Paths in the producer checkout
are not automatically available in InfinityDB; transfer selected ZIP/manifest
pairs without rewriting their provenance.

`--include-history` additionally archives MediaWiki revisions; `--resume-work`
uses a preserved incomplete crawl. Partial/failed crawls never publish a ZIP
or manifest. Archive acquisition is optional and never runs during an
InfinityDB build, serve or Docker deployment.

MediaWiki `srcset` candidates (including `<picture><source>`) are downloaded
and rewritten to paths relative to the containing HTML page, including
pages within subdirectories. For Human Sphere, recognized MediaWiki
`/images/thumb/...` URLs also enqueue the corresponding original upload
when available; a source-side HTTP 404 remains explicitly classified as an
ignored discovered URL. Asset downloads preserve the response bytes exactly,
but HTML challenges and invalid signatures for common images, archives and
documents are rejected instead of being published as binary files. Metadata
links such as `rel="canonical"` and `rel="license"` remain HTML page links
rather than being downloaded as assets; stylesheets/preload resources remain
assets. Resuming an incomplete crawl re-fetches invalid existing assets. MediaWiki `File:` and
`Image:` URLs are treated as HTML description pages even when their titles
have image-file extensions; the actual uploads remain binary assets. During a crawl, the first ten
failures and every hundredth subsequent failure are printed immediately to
stderr, so operators can diagnose problems without completing the crawl.

New archive member paths are Windows/macOS/Linux-portable regardless of the
crawl host. For Human Sphere, non-ASCII names and HTML page titles with
nonstandard capitalization receive a stable `__u_<hash>` source-identity
suffix. This keeps `Aibot` distinct from `Aibōt`, and `Haetae` distinct
from `HaeTae`, while preserving conventional ASCII page filenames and all
previously mirrored asset paths for efficient resumes. Other unsafe filenames,
remaining normalization collisions, and file/directory conflicts fail before
publication. Existing ZIPs are immutable and **not**
repaired in place: produce a new snapshot and matching manifest if you need
the enhanced image coverage or portable member validation.

## Consumer contract

A complete acquisition exports an immutable `WIKI-en ...zip`,
`WIKI-es ...zip` or `HUMAN-SPHERE ...zip` and a matching v3
`InfinityDB snapshot provenance` JSON manifest. Copy the ZIP and JSON
to the consumer machine. Exact archive SHA-256 and logical
`snapshot.contentSha256` must match; the producer-relative path in the
manifest is informational. InfinityDB's snapshot-provenance reader verifies
the pair offline. Source ZIP members remain source evidence, not
authoritative current gameplay rules.

Manifest publication now stages a new canonical JSON document exclusively
and installs it with a same-directory no-replace hard link. An existing
manifest is accepted only if its bytes are identical; different provenance,
symlink targets or stale `.json.tmp` staging fail instead of overwriting
another crawl's evidence. Inspect interrupted staging before cleanup and
use a filesystem supporting hard links. Source archive format, site and
language selection, legacy CLI destinations and resume-work behavior are
unchanged.

Each acquisition requires a newly created manifest, even if a competing crawl
publishes identical bytes, so rollback cannot delete another run's evidence.
Direct `write_snapshot_manifest` calls remain idempotent by default. Equality
uses exact UTF-8 bytes, including line endings. Copying an archive to another
local layout does not require rewriting its manifest: the consumer explicitly
selects the archive and never resolves the producer's recorded path.

The Python provenance contract is copied from InfinityDB's current
`snapshot_provenance` implementation at extraction time, so conformance
is established against the consuming InfinityDB version rather than
requiring an `infinity_db` runtime import.

This project was extracted from the InfinityDB Wiki/Human Sphere downloader
under restructuring phase P5.4. The standalone test suite and cross-platform
GitHub Actions verify the packaged acquisition behavior. InfinityDB reads the
resulting archive and manifest offline; it does not import or install this
package for ordinary builds or serving. The original in-tree downloader remains
until its separate consumer/documentation cutover is complete.
