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

`--include-history` additionally archives MediaWiki revisions; `--resume-work`
uses a preserved incomplete crawl. Partial/failed crawls never publish a ZIP
or manifest. Archive acquisition is optional and never runs during an
InfinityDB build, serve or Docker deployment.

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
