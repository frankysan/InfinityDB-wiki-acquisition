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
### Upgrade completed snapshots with history (P2.1–P2.2)

A completed **non-history** snapshot with its matching v3 provenance JSON and
v1 inventory can be upgraded into a *new* history-enabled snapshot without
re-downloading any current HTML or assets:

```powershell
infinity-wiki-snapshot --from-snapshot "local/data/wiki/WIKI-en 20261114-120000.zip" --include-history
```

The command looks for the parent manifest at
`--manifest-dir/<parent ZIP stem>.json` and its inventory at
`--manifest-dir/inventories/<parent ZIP stem>.inventory.json`.
Use `--from-manifest <path>` and `--from-inventory <path>` for
other locations. The parent site and language are inferred from verified
inventory/provenance; explicit `--site` and `--language` values must match.
New outputs use the normal `--root`, `--manifest-dir` and
`--work-root` destinations, with a `-history` archive identity.

The parent ZIP, manifest and inventory are checked by
`verify_snapshot` **before any network requests**. Every inherited
current-page and asset member is copied byte-for-byte into fresh work.
Original page IDs, source URLs, captured HTTP evidence, mirrored byte hashes,
references and ignored-URL evidence are retained in the new inventory.
New MediaWiki revision lists are always enumerated. If the parent already
contains history, previously archived rendered oldid bytes are reused when
their **revision ID, parent ID, timestamp and previously recorded current-page
association** agree with the fresh API enumeration. Missing oldids are
downloaded. A different canonical title returned for the *same page URL*
does not by itself invalidate a matching oldid; the new history index records
the updated canonical title. This also supports multiple page aliases
referencing the same global oldid without downloading it repeatedly.

If an oldid changes identity, a prior oldid disappears from enumeration, or
a recorded page/oldid association disappears, publication is blocked pending
explicit reconciliation. A matching numeric oldid alone never proves a
previously unseen URL is an alias. Current HTML/assets are never refreshed
by this command; the separate `--update-from` path handles current-content
refreshes (P2.3).

The original files never change. The new v3 manifest records the parent ZIP
as `inputArtifact`, and the v1 inventory records the exact parent ZIP,
manifest and inventory hashes. For history parents, the sidecar additionally
records `parentHistory: true` and `reusedRevisionCount`. The new history
index and revision-to-page metadata always reflect the **fresh** enumeration,
even when rendered revision bytes were reused.

**Identity boundary:** every inherited member must already be classified
as a current page with an explicit source URL/title, or as a known asset.
Unknown members might be missing page identities and block upgrading instead
of silently omitting their history. In a history parent, oldid and history-index
members are handled separately, never mistaken for current pages or assets.
Conservative P1.4 inventories containing
unknown members therefore need independent source-identity review first.

**Time and cutoff semantics:** the original current HTML retains its
*original acquisition time* across repeated history upgrades (rather than
inheriting the most recent history archive's timestamp). The new inventory
independently records `currentContentAcquiredAt` and `historyRetrievedAt`;
the new manifest's
`acquiredAt` is when the *new history snapshot* is built. The history scope is
`all-observed`: fetch all revisions the source API advertises during the
upgrade, **including revisions newer than the inherited current HTML**.
There is no historical date cutoff or promise of an exact as-of-time corpus
in P2.1/P2.2. Date-bounded historical reconstructions need separately designed
source/coverage semantics; the command does not mislabel a partial history as
complete.

On a required revision failure, no ZIP/manifest/inventory is published.
The staging directory and history checkpoint are preserved for inspection,
but `--resume-work` and `--retry-failed-only` cannot be combined with
`--from-snapshot` yet; archive-derived resume is scheduled for P2.5.

### Refresh a completed current-content snapshot (P2.3a–b)

A verified ZIP with matching v3 provenance and v1 inventory can seed
a separate current-content refresh. If the parent includes historical
oldids, the update automatically retains history:

```powershell
infinity-wiki-snapshot --update-from "local/data/wiki/HUMAN-SPHERE 20261001-120000.zip"
```

As with `--from-snapshot`, the default parent JSON paths are resolved below
`--manifest-dir`; `--from-manifest` and `--from-inventory` override those
inputs. The source site/language are inferred and explicit mismatches fail
before any network requests. `--update-from` cannot be combined with
`--from-snapshot`, `--resume-work`, or `--retry-failed-only`.
The optional `--include-history` is accepted
only when the parent already contains history; without it, history is
preserved automatically whenever present. Adding history to a
non-history archive still uses `--from-snapshot --include-history`.

**Page HTML remains a conservative full refresh; binary asset bodies
can be reused with fresh version evidence.** The producer first verifies
the parent artifacts, requests an auditable MediaWiki RecentChanges window,
and performs **fresh, paginated allpages enumeration**, even for sites
that normally start with link discovery. It refetches every current HTML
page and rewrites links from the newly retrieved source response.

The asset reuse decision has two independent, fail-closed paths:

- **MediaWiki original uploads:** For an original `/images/` URL on the
  source Wiki (not a thumbnail), the updater requests the latest
  `prop=imageinfo` record. It requires the reported upload URL to
  match the *exact* requested URL, a valid upload timestamp, and SHA-1
  plus size matching the bytes from the independently verified parent ZIP.
  Only then can it reuse the original member without requesting the
  upload body. Changed uploads are downloaded and checked against the
  newly observed file version. Unavailable, ambiguous, or malformed
  imageinfo falls back to ordinary HTTP download.
- **Strong HTTP ETag:** For assets without usable upload evidence,
  a verified parent may supply a strong ETag captured from a previous
  unredirected HTTP response for that precise URL. The updater sends
  `If-None-Match` and accepts parent bytes **only** when the response
  is HTTP 304 with the identical strong ETag, effective URL, and no body.
  Weak/missing validators, redirects, or an HTTP 200 response never
  authorize a conditional skip. Contradictory 304 evidence blocks reuse.

HTTP observations are captured in private `.wiki-asset-evidence/`
work files and published as optional v1 inventory `sourceVersion`
evidence, bound to source URL and member SHA-256. MediaWiki upload
identities are independently published as optional `uploadVersion`
records (original URL, upload title, SHA-1, size, timestamp, observation
time and whether its bytes were reused). Every upload SHA-1 is also
recomputed from the published ZIP contents by `verify_snapshot`.
The inventory reports HTTP-304 and upload-SHA-1 reuse counts separately.
Existing archives without either version source remain compatible; a
missing proof simply requires a body download. Filename matches,
RecentChanges coverage, unchanged page IDs and `Last-Modified` alone
are never reasons to skip. Required failures block publication and
preserve work.

A new complete ZIP, matching v3 manifest (including parent
`inputArtifact`), and independently verified v1 inventory are published
without modifying the previous artifacts. The inventory retains the parent
ZIP/manifest/inventory hashes, member-change counts, URL-association changes for stable numeric
MediaWiki IDs (distinguishing RecentChanges-corroborated moves from
unresolved aliases), parent IDs missing from the fresh
enumeration, and the observed RecentChanges events. A missing ID is
**not** unconditionally labeled a deletion; it might instead reflect a
namespace change, visibility restriction or ambiguous source state.
RecentChanges continuation is checked, but
`retentionCoverage: unverified` is mandatory: even a paginated complete
response cannot prove how far the Wiki retains older changes.

**Current-page source revisions are now recorded, not used to skip
HTML.** After fetching current pages, the producer sends bounded
MediaWiki revision queries (up to 40 numeric page IDs per request).
Where the returned page identity matches, the v1 inventory optionally
records the latest source revision ID/timestamp, page ID/title, API
observation window and the earlier HTML fetch time. Missing page IDs,
unavailable revisions, API failures and title disagreements remain
explicitly unknown. The private `.wiki-current-revisions.json` work
file is never published inside the ZIP.

On an update, `currentRevisionComparisons` records changed, unchanged
or uncomparable source revision IDs by stable page ID, while
`htmlRefetched: true` and `renderCorrespondence: not-verified` make
the limit explicit. An unchanged revision does **not** establish that
rendered HTML stayed unchanged: templates, modules, categories and other
dependencies may have changed (P2.4). These batched observations occur
*after* HTML downloads, not atomically with them. The original-upload
imageinfo and HTTP-validator paths can avoid large asset response bodies,
but still make API/conditional requests.

**Source-time and coverage audit (P2.3b.4):** New snapshots with
current-page revision observations include a `sourceTimeCoverage` summary
in their inventory. It counts missing revision/HTML timing evidence,
latest revision timestamps later than the earlier HTML fetch, and page
fetches or revision checks outside the already queried RecentChanges
window. Updates also count unresolved move/alias associations and
previously known page IDs absent from fresh enumeration. These counts
are recomputed by `verify_snapshot`; consumers cannot silently promote
them into a stronger status. The explicit limits are
`consistency: not-verified-atomic`,
`renderDependencies: unverified`, and
`recentChangesRetention: unverified` (or `not-observed`).
These are **risk indicators, not source-time correctness guarantees**:
RecentChanges may omit old activity and closes at the initially requested
cutoff; the latest revision metadata is checked later than the HTML body.
Therefore pages remain fully fetched, and no unchanged revision ID
certifies the previously rendered content. Future dependency-aware
verification (P2.4) is required before making stronger claims.

### Refresh a history-enabled snapshot (P2.3c.1)

`--update-from` now also accepts a complete history-enabled parent. It
performs the normal fresh allpages/current-page/asset acquisition, then
re-enumerates revisions for the **new** current-page set and rebuilds the
history index. Previously downloaded oldid bytes are reused only when
their revision ID, parent revision ID and timestamp match a newly
enumerated revision, and the old page association maps to the new URL
through a stable numeric MediaWiki page ID. A renamed canonical title,
a corroborated move or several prior URLs converging onto one identity
do not by themselves force historical bytes to be downloaded again.
New revisions are fetched and included; all prior oldids and archived
page/oldid associations must still be accounted for after remapping.

The new inventory records old and new page URLs with the matching page
ID, the number of reused and newly enumerated revisions and the full
parent artifact lineage. History-index metadata is always rebuilt, not
blindly copied, and current HTML is still refreshed.

### Opt-in retention of missing historical pages (P2.3c.2)

By default, if a historical page ID is missing from fresh MediaWiki
`allpages` enumeration, `--update-from` still blocks publication rather
than silently discarding its old HTML or oldids. A *missing* ID is **not
proof of deletion**; the source page might have moved outside the
enumerated namespace or become inaccessible.

To explicitly retain such pages, use:

```powershell
infinity-wiki-snapshot --update-from "path/to/history-parent.zip" --allow-historical-only
```

This requires a verified history-enabled parent. The resulting archive
uses **history index formatVersion 2 and inventory formatVersion 2** only
when historical-only pages exist. All fresh current pages remain in
`_history/index.json.pages` and are re-enumerated. Formerly current HTML
is moved to `_history/retired/<pageId>/<URL-hash>.html` and classified as
`kind: historical-page`, never `current-page`. Each
`historicalOnlyPages` entry retains a page ID, original URL and member
path, last known current title, historical canonical title, byte hash,
original acquisition time and the previously archived oldid associations.
Their explicit `status: absent-from-current-enumeration` and
`historyScope: last-archived-only` do **not** claim those pages are
currently published or that their revisions were freshly enumerated.
The top-level `historyCoverage:
current-all-observed-former-last-archived` identifies the mixed
coverage. A deleted-page log entry, if observed, remains separately
audited RecentChanges evidence rather than an automatic deletion verdict.

The producer's offline verifier checks all v2 member hashes, original
page IDs, index/inventory relationships, oldid links, and explicit
absence mappings. Successive v2 updates preserve earlier retired pages;
if a retired ID reappears in fresh discovery, publication stops pending
explicit reinstatement review. The old archive remains immutable.

**Consumer compatibility:** v1 index/inventory snapshots remain unchanged;
the v2 extension is an explicit opt-in, not a silent schema upgrade.
At the inspected InfinityDB `dev/v1.1.0` checkout
`0c853e74abf0956fd5d571920189f72a5db4c01c`, the research audit
reader at `tools/research/audit_1_0_reference_inputs.py` iterates
`pages` only, so it will not include `historicalOnlyPages` in oldid
lookup. Do not ingest v2 snapshots into downstream consumers until they
are independently reviewed and validated for the extension. The CLI
prints this warning on v2 publication. No sibling repository is changed
automatically. Archive-derived work resume remains P2.5.

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
Snapshot-derived history upgrades are supported as described above; resuming
an *interrupted archive-derived* upgrade remains planned in `docs/TODO.md`.


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
the same consumer contract; P1.4 in `docs/TODO.md` covers legacy indexing.

### Verify a completed snapshot before reuse

The P1.3 offline verification boundary requires all three published artifacts:
the ZIP, its matching v3 provenance JSON and its v1 inventory sidecar.
From an installed Python environment:

```python
from pathlib import Path
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

verified_inventory = verify_snapshot(
    Path("local/data/wiki/WIKI-en 20261115-120000.zip"),
    Path("local/data/manifests/snapshots/WIKI-en 20261115-120000.json"),
    Path("local/data/manifests/snapshots/inventories/WIKI-en 20261115-120000.inventory.json"),
)
```

The call returns the checked inventory or raises `SnapshotVerificationError`.
It verifies the exact ZIP and logical-content hashes, streams every file to
validate ZIP integrity/size/SHA-256, rejects unsafe or colliding member paths,
and checks inventory, provenance, site, language, history and recorded revision
relationships for contradictions. Verification never extracts or edits input
files and needs no work directory or network access. Unknown source evidence
that was never captured is allowed; contradictory recorded evidence is not.
Older snapshots without inventories need conservative indexing before this
API can certify reuse. Neither checksums nor sidecars authenticate who supplied
an otherwise self-consistent set of files; establish artifact trust separately.

### Index a completed legacy snapshot (P1.4)

For a previously completed Wiki/Human Sphere snapshot that predates inventories,
use the explicit **offline** command with the original ZIP and matching **v3**
provenance JSON:

```powershell
infinity-wiki-index-legacy "local/data/wiki/WIKI-en 20261015-110000.zip" "local/data/manifests/snapshots/WIKI-en 20261015-110000.json"
```

The command creates `<archive stem>.inventory.json` in the manifest
directory's `inventories/` subdirectory; `--inventory-dir <directory>`
selects another sidecar directory. The same entry point is available as
`python -m infinity_wiki_acquisition.legacy_snapshot_index`. Indexing
validates the exact ZIP hash, logical-content hash and member integrity,
then checks the generated inventory with `verify_snapshot` **before**
publishing it. Nothing is extracted or downloaded. ZIPs and manifests are
never rewritten, and existing inventories or staging files are never replaced.

Without a trustworthy original source mapping, a member name such as
`Main_Page.html`, `File:Something.jpg.html` or `images/photo.png`
**cannot** establish a page URL or asset origin. Those members remain
`unknown`, even if their contents resemble HTML or images. For history
archives, the explicitly recorded `_history/index.json` supplies current-page
URLs, titles, and oldid revision references. Page IDs, original HTTP body
hashes, redirects, referenced resources and unobserved response headers
remain unknown. The sidecar records `captureMetadata.legacyIndexed: true`
and `ignoredUrlsAvailable: false`; an empty `ignoredUrls` list in a
reconstructed inventory does not prove no URLs were ignored.

Indexing requires v3 Wiki provenance, recognized site/language and an
internally consistent, portable archive with a matching history mode.
Earlier manifest versions, contradictory history indexes, missing members,
unsafe paths and damaged ZIPs are rejected rather than silently upgraded.
This creates verifiable *file evidence*, not proof that an old crawl covered
every source URL or that its contents remain current.


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
