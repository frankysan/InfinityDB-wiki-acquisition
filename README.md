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
Completed snapshots also receive an inventory in the manifest directory's
`inventories/` subdirectory.
The existing `--root`, `--work-root` and `--manifest-dir` options still
accept `data/wiki`, `data/work/wiki` and `data/manifests/snapshots` respectively.
Paths in the producer checkout
are not automatically available in InfinityDB; transfer selected ZIP/manifest
pairs without rewriting their provenance.

`--include-history` additionally archives MediaWiki revisions; `--resume-work`
uses a preserved incomplete crawl. Partial/failed crawls never publish a ZIP
or manifest. Archive acquisition is optional and never runs during an
InfinityDB build, serve or Docker deployment.

### Resume interrupted crawls and retry failed URLs

Acquisitions now write a private, versioned
`.wiki-crawl-checkpoint.json` file in their `.work` directory. It records the
site/language, discovered URLs, saved file paths and sizes, ignored and failed
URLs, **the ordered pending queue**, and MediaWiki `allpages` continuation.
The checkpoint is written atomically after each enumeration batch, about
every 100 completed URL attempts, and when discovery finishes. On abrupt
termination, up to 100 URL attempts may be replayed from the last checkpoint;
previously committed HTML pages are not refetched.

To continue an interrupted crawl, use the existing command:

```powershell
infinity-wiki-snapshot --site human-sphere --language en --resume-work "local/work/wiki/HUMAN-SPHERE 20261114-170628.work"
```

An existing checkpoint makes normal `--resume-work` continue the saved
enumeration or crawl queue; completed checkpoints can also resume pending
failures. The checkpoint is **never included** in the published ZIP.

For a preserved, checkpointed run with failures:

```powershell
infinity-wiki-snapshot --site human-sphere --language en --resume-work "local/work/wiki/HUMAN-SPHERE 20261114-170628.work" --retry-failed-only
```

This mode skips the initial allpages query and does not refetch already
completed pages or assets. It retries the recorded failures, follows any
new links from successfully recovered HTML pages, and publishes the normal
ZIP/manifest pair **only when no failures remain**. If another failure
occurs, its updated record stays in the same checkpoint for another retry.
Saved HTML links are repaired locally if a retried URL has been reclassified
between asset and page since the earlier run.

Older work directories without a checkpoint cannot use retry-only safely,
because their complete discovered-URL set was not recorded. Run one normal
`--resume-work` first; this re-enumerates and refetches HTML but reuses
valid binary assets and creates checkpoints as it progresses.
A missing or changed *checkpointed* saved file, or a site/language mismatch,
fails closed rather than silently certifying incomplete work.
### Resume historical revisions

With `--include-history`, acquisition also writes a separate
`.wiki-history-checkpoint.json` in the work directory. It records the
current page, the MediaWiki `rvcontinue` token, revision inventories,
previously downloaded oldid files and completed pages. Each API batch
is checkpointed immediately; oldid downloads are checkpointed every 100
revisions and at each completed page. An interruption can replay a small
uncommitted batch while reusing valid oldid files already on disk.

Use the normal resume command **with `--include-history`** to continue
the historical phase:

```powershell
infinity-wiki-snapshot --site human-sphere --language en --include-history --resume-work "local/work/wiki/HUMAN-SPHERE-history RUN.work"
```

A failed revision request is retried from its saved phase/token without
refetching earlier completed revision lists. History checkpoints validate
the exact current-page set and recorded file sizes; a mismatched or corrupt
checkpoint blocks publication. Even if current-page acquisition is complete,
history failures or interruptions do not publish a ZIP or manifest. Both
checkpoint files are private work metadata, excluded from archives.

The current `--retry-failed-only` switch applies to the current-page crawl
checkpoint; history resumes automatically under `--include-history`.
History upgrades from existing completed ZIPs remain planned in
`docs/TODO.md`.


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
have image-file extensions; the actual uploads remain binary assets.
Likewise, MediaWiki source pages such as `Module:Infobox/styles.css`
and discussion pages such as `Module_talk:Infobox/styles.css` are saved as
HTML (with `.html` mirror suffixes), not as stylesheet assets. This includes
corresponding talk namespaces for supported MediaWiki namespaces. Genuine CSS
resources under `resources/` or `load.php` remain assets.
During a crawl, the first ten
failures and every hundredth subsequent failure are printed immediately to
stderr, so operators can diagnose problems without completing the crawl.

Equivalent MediaWiki URLs (for example, an encoded `%2C` and a literal
comma in a page title) are canonicalized before scheduling downloads. This
avoids treating alternate URL spellings for one page as filename collisions,
while leaving distinct page-title identities separate.

New archive member paths are Windows/macOS/Linux-portable regardless of the
crawl host. For Human Sphere, non-ASCII names and HTML page titles with
nonstandard capitalization receive a stable `__u_<hash>` source-identity
suffix. This keeps `Aibot` distinct from `Aibōt`, `Haetae` distinct
from `HaeTae`, and MediaWiki file-description titles ending in `.JPG`
distinct from those ending in `.jpg`, while preserving conventional ASCII
page filenames and already mirrored asset paths for efficient resumes. Other unsafe filenames,
remaining normalization collisions, and file/directory conflicts fail before
publication. Existing ZIPs are immutable and **not**
repaired in place: produce a new snapshot and matching manifest if you need
the enhanced image coverage or portable member validation.

## Snapshot inventory (format v1)

Every successful acquisition now additionally writes a separate immutable
`<archive stem>.inventory.json` beneath the selected manifest directory's
`inventories/` subdirectory. It records the site, language, history mode,
original root URL, snapshot acquisition time, exact archive and logical-content
hashes, and a sorted record for **every** ZIP member (path, byte size and
SHA-256). Current-page entries record known source URLs and page titles;
historical oldids record oldid, parent revision, revision timestamp and
associated source pages. Generated history indexes are identified separately.
Ignored optional URLs are retained as evidence. `parent: null` reserves a
lineage field for future archive-derived snapshots.

The inventory distinguishes **offline-rewritten current HTML** from downloaded
assets, rendered historical revisions and generated indexes. The original
source response body is not embedded in the ZIP; its SHA-256 is retained
separately from the rewritten member SHA-256 when captured. Individual files
without established source identities remain `unknown`, not inferred from
filenames. Inventories are excluded from ZIPs and never overwrite a previous
inventory.

### Source identity and relationships

A new crawl records source-page observations before rewriting links, in
private `.wiki-page-evidence/` files inside its work directory. The inventory
retains the original request URL and page title independently from the offline
mirror path, and records numeric MediaWiki page IDs when the `allpages` API
supplies them. An HTML `wgArticleId` value is an **independent hint**;
disagreements with an API ID are explicitly flagged rather than resolved by
guessing. Legacy work without this evidence is marked `not-captured`.

When an HTTP response redirects, its actual final URL and status are recorded
where available, without silently merging two source identities. Canonical
`<link>` declarations and HTML meta-refresh targets are retained separately
as **unverified claims**, including their original query-bearing URLs.

Original HTML references to images, stylesheets, scripts and MediaWiki
`Template:` / `Module:` / `File:` / `Category:` pages are recorded with their
evidence type and whether their target was archived, ignored or unresolved.
Derived original-upload candidates are distinguished from directly observed
links. A navigation link to a template or module **does not establish that
it was transcluded**: actual template/module rendering dependencies remain
explicitly unknown. HTTP response *headers* other than the observed final
URL/status are not yet retained. This information enables future incremental
checks without claiming dependency completeness.

The inventory is producer-side metadata for future incremental acquisition,
not a replacement for the v3 provenance manifest. ZIP and provenance remain
the same consumer contract; P1.3–P1.4 in `docs/TODO.md` cover stronger
cross-artifact verification and legacy indexing.

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
