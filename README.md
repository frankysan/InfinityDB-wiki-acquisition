# Infinity Wiki / Human Sphere acquisition

A standalone, command-line **source acquisition and archival tool** for
[InfinityDB](https://github.com/frankysan/InfinityDB). It downloads supported
MediaWiki pages, linked resources and optionally page-revision history; rewrites
links for offline browsing; and publishes independently verifiable snapshot
artifacts. It does **not** interpret Infinity rules or publish data into the
InfinityDB application.

**Important:** Running `infinity-wiki-snapshot` accesses the selected external
wiki. A normal build, test run or InfinityDB deployment does **not** start a
crawl. Downloaded pages and assets are third-party material, separate from this
repository's source-code license.

## Contents

1. [Requirements and installation](#1-requirements-and-installation)
2. [Choose an acquisition mode](#2-choose-an-acquisition-mode)
3. [Quick start](#3-quick-start)
4. [Complete command-line option reference](#4-complete-command-line-option-reference)
5. [Working with completed snapshots](#5-working-with-completed-snapshots)
6. [Resuming interrupted work](#6-resuming-interrupted-work)
7. [Artifacts, provenance and verification](#7-artifacts-provenance-and-verification)
8. [Source evidence and reuse limitations](#8-source-evidence-and-reuse-limitations)
9. [Additional commands and audit tools](#9-additional-commands-and-audit-tools)
10. [Troubleshooting, compatibility and further documentation](#10-troubleshooting-compatibility-and-further-documentation)

## 1. Requirements and installation

- **Python 3.11 or newer.** Runtime dependencies use only the Python standard
  library. The optional test extra installs pytest.
- **Internet access** for an acquisition or update, with sufficient disk space
  for a complete snapshot and (when selected) its history and source replay.
- **A filesystem supporting hard links** for the no-replace manifest
  publication mechanism. Normal offline verification does not require a crawl
  work directory or network access.

From a checkout of this repository:

```powershell
python -m pip install -e .
infinity-wiki-snapshot --help
```

To install test dependencies and run the fully mocked, offline test suite:

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

All relative paths below resolve from the directory where the command is
started. Choose that directory deliberately or pass explicit output paths.

### Supported sources

| `--site` | `--language` | Source | Default archive prefix |
| --- | --- | --- | --- |
| `infinity-wiki` (default) | `en` (default) | [Infinity Wiki](https://infinitythewiki.com/) | `WIKI-en` |
| `infinity-wiki` | `es` | [Infinity Wiki, Spanish](https://infinitythewiki.com/es/) | `WIKI-es` |
| `human-sphere` | `en` | [Human Sphere](https://www.human-sphere.com/Main_Page) | `HUMAN-SPHERE` |

Human Sphere does **not** support `--language es`. A source's language is
inferred from verified parent artifacts for archive-derived operations;
explicit site/language values must match that parent.

## 2. Choose an acquisition mode

The command has three main modes. Each creates a **new, complete snapshot**
rather than altering an earlier archive.

| Mode | How to select it | What it downloads | Parent required? |
| --- | --- | --- | --- |
| **Fresh acquisition** | No `--from-snapshot` or `--update-from` | Current pages and resources; optionally all revisions with `--include-history` | No |
| **History upgrade** | `--from-snapshot PARENT.zip --include-history` | Revision enumeration and missing oldid HTML; inherits verified current pages/assets without refetching them | Yes: ZIP, manifest, inventory |
| **Current-content update** | `--update-from PARENT.zip` | Fresh page discovery and current HTML; checks/downloads assets; automatically preserves history if the parent has it | Yes: ZIP, manifest, inventory |

The important distinction is **history upgrade versus current-content update**.
A history upgrade preserves the original current HTML exactly as captured;
it does not refresh the wiki's present-day pages. An update refreshes current
pages and incorporates changes while preserving and reconciling previously
archived history where applicable.

You can also **resume** any of the three modes by supplying the preserved
`--resume-work` directory from its interrupted run. See [section 6](#6-resuming-interrupted-work).

## 3. Quick start

### A. Acquire the current Infinity Wiki

```powershell
infinity-wiki-snapshot --site infinity-wiki --language en
```

For Spanish, substitute `--language es`. For Human Sphere, use:

```powershell
infinity-wiki-snapshot --site human-sphere --language en
```

To enumerate **all main-namespace Rules Wiki pages**, including pages not
linked from the home page, explicitly add `--allpages`. Both English and
Spanish Rules Wiki APIs have been probed for main-namespace titles and page
IDs; Human Sphere already uses allpages by default:

```powershell
infinity-wiki-snapshot --site infinity-wiki --language en --allpages
```

Rules Wiki now uses portable `.html` page filenames (for example,
`Foo.html` and `Foo/Bar.html`), eliminating file-versus-directory
collisions. Root indexes remain `index.html` and `es/index.html`.
The fresh Rules Wiki default remains link-based for operator compatibility;
use `--allpages` for main-namespace coverage. The `--allpages`
option is only for **new, fresh** Rules Wiki acquisitions.
It checkpoints MediaWiki's `apcontinue` token and requires positive page
IDs. Resume with `--allpages` as well; a previous link-only work directory
cannot be promoted into full discovery. An `--update-from` run already
forces allpages for either source.

To retain the exact **original current-page HTML** in an additional portable
companion ZIP for independent link-rewrite verification, opt in **from the
start**:

```powershell
infinity-wiki-snapshot --site infinity-wiki --language en --source-replay-dir "local/data/wiki-source-replay"
```

This is optional. It increases storage, does not preserve original HTML for
historical oldid pages, and does **not** enable conditional HTML download
skipping.

### B. Upgrade a completed non-history snapshot to include history

If comprehensive Rules Wiki main-namespace history is the goal,
first acquire a fresh `--allpages` snapshot or perform a verified
`--update-from` refresh of a link-only parent. A history-only upgrade
does **not** discover pages missing from its parent.

First complete the fresh run and identify its ZIP, matching provenance JSON
and inventory JSON (paths described in [section 7](#7-artifacts-provenance-and-verification)).
Then run:

```powershell
infinity-wiki-snapshot `
  --from-snapshot "local/data/wiki/WIKI-en 20261115-180000.zip" `
  --include-history
```

The date in this example is illustrative; use the **actual** filename from
your acquisition. By default, the matching parent JSON paths are found from
the parent ZIP's stem under `--manifest-dir`. If the three parent files are
stored elsewhere, specify them explicitly:

```powershell
infinity-wiki-snapshot `
  --from-snapshot "D:\WikiArchive\parent.zip" `
  --from-manifest "D:\WikiArchive\parent.json" `
  --from-inventory "D:\WikiArchive\parent.inventory.json" `
  --include-history
```

History upgrade also accepts an already history-enabled parent: it refreshes
the revision enumeration and reuses previously archived oldid bytes only when
their IDs, parent IDs, timestamps and page associations match verified parent
evidence. All parent files remain unchanged.

### C. Acquire current pages and full history in one fresh run

```powershell
infinity-wiki-snapshot --site infinity-wiki --language en --include-history
```

History mode downloads all public revisions advertised by the wiki for every
mirrored current page. This can take much longer and require significantly
more storage. It is **not** a date-bounded historical reconstruction:
`all-observed` history may include revisions newer than an inherited current
HTML snapshot.

### D. Refresh a completed archive

```powershell
infinity-wiki-snapshot --update-from "local/data/wiki/WIKI-en 20261115-180000.zip"
```

The updater verifies the parent artifacts before network access, scans
RecentChanges as observation evidence, performs fresh page enumeration,
downloads every current HTML page and processes linked assets. A
history-enabled parent retains/reconciles its oldids automatically.

An older Rules Wiki archive may contain one member classified as `unknown`:
`opensearch_desc__u_b7ab2ebdc1fb.php`. This is the linked MediaWiki
OpenSearch XML description, not a wiki article. The updater now accepts
**only this source-identifiable legacy case** from a fully verified parent:
it discards the old unknown classification and fetches/validates the XML
resource anew. It does not rewrite the parent, reuse the old unknown
bytes, or accept unrelated unknown members.

For a history parent whose former page IDs disappear from current
enumeration, the default is to **stop rather than infer deletion**.
You can explicitly retain those missing pages as historical-only records:

```powershell
infinity-wiki-snapshot `
  --update-from "path/to/history-parent.zip" `
  --allow-historical-only
```

This opt-in can produce a **v2 history index and v2 inventory**, which require
a separately compatible downstream reader. Only use it when preservation of
unresolved former pages is intentional.

## 4. Complete command-line option reference

The following table covers **every** `infinity-wiki-snapshot` argument
implemented in `src/infinity_wiki_acquisition/cli.py`. Options are
case-sensitive. Square-bracketed values in the syntax below denote
placeholders, not literal characters.

### Source, content and acquisition modes

| Option | Default | Purpose and rules |
| --- | --- | --- |
| `--site {infinity-wiki,human-sphere}` | `infinity-wiki` | Select the source wiki. For parent-derived runs it is inferred; an explicitly supplied value must match the parent. |
| `--language {en,es}` | `en` | Select the wiki language. Human Sphere is English-only. For parent-derived runs, source language is inferred unless explicitly provided. |
| `--allpages` | Off for fresh Rules Wiki; Human Sphere already enumerates | Fresh Rules Wiki only. Require complete paginated main-namespace enumeration and positive numeric page IDs before page/asset crawling. A private work receipt binds the selection across resume. Cannot combine with `--from-snapshot` or `--update-from`. |
| `--include-history` | Off | In a fresh run, download every advertised rendered oldid revision. **Required** with `--from-snapshot`. With `--update-from`, it is valid only if the parent already contains history; a history parent is preserved even without the flag. |
| `--from-snapshot PATH` | Not set | Upgrade a verified, complete snapshot to a new history archive; **must** accompany `--include-history`. Works with both non-history and history-enabled parents. Does not fetch current HTML/assets again. |
| `--update-from PATH` | Not set | Produce a new snapshot from a verified parent, refreshing current discovery/pages/assets. If the parent has history, refresh/reconcile its history as well. Cannot combine with `--from-snapshot` or `--retry-failed-only`. |
| `--allow-historical-only` | Off | With `--update-from` and a history-enabled parent, explicitly retain formerly current pages missing from current enumeration. May emit v2 history/index inventory records; never treat absence as verified deletion. |

### Parent artifacts and destinations

| Option | Default | Purpose and rules |
| --- | --- | --- |
| `--from-manifest PATH` | `--manifest-dir/<parent-stem>.json` | Select the matching **parent v3 provenance manifest** for `--from-snapshot` or `--update-from`. Cannot be used alone. |
| `--from-inventory PATH` | `--manifest-dir/inventories/<parent-stem>.inventory.json` | Select the matching **parent v1/v2 inventory**. Cannot be used alone. Missing or contradictory parent evidence fails verification. |
| `--root DIRECTORY` | `local/data/wiki` | Destination for newly published, timestamped snapshot ZIPs. Existing ZIPs are not replaced. |
| `--manifest-dir DIRECTORY` | `local/data/manifests/snapshots` | Destination for matching provenance JSON; inventory JSON goes in its `inventories/` subdirectory. Also determines default parent sidecar lookup locations. |
| `--work-root DIRECTORY` | `local/work/wiki` | Parent directory for newly created `*.work` staging directories. A failed run retains its own staging directory here; a resumed run uses the explicit `--resume-work` path instead. |
| `--source-replay-dir DIRECTORY` | Off | Create a **separate** original-HTML replay companion ZIP from a fresh crawl or `--update-from`. Ineligible with `--from-snapshot` because a history-only upgrade fetches no current HTML. On resume, use the same selection as the original run. |

### Recovery and help

| Option | Default | Purpose and rules |
| --- | --- | --- |
| `--resume-work DIRECTORY` | Not set | Reopen a preserved `*.work` directory. Normal crawls use their crawl/history checkpoints; archive-derived modes additionally verify a run receipt bound to the exact parent ZIP, manifest, inventory and acquisition options. |
| `--retry-failed-only` | Off | **Ordinary crawls only.** With `--resume-work` and a checkpoint with completed discovery, retry recorded failed URLs without restarting discovery. Cannot combine with `--from-snapshot` or `--update-from`. Not a replacement for general resume. |
| `-h`, `--help` | — | Print the installed CLI's actual help text and exit without starting a crawl. |

### Valid combinations at a glance

| Operation | Required | Optional/relevant | Not permitted |
| --- | --- | --- | --- |
| Fresh current snapshot | Source/language are optional | `--allpages` for Rules Wiki, `--source-replay-dir`, destinations | Parent selection flags |
| Fresh with history | `--include-history` | `--allpages` for Rules Wiki, `--source-replay-dir`, destinations | Parent selection flags |
| History upgrade | `--from-snapshot` + `--include-history` | `--from-manifest`, `--from-inventory`, `--resume-work` | `--update-from`, `--source-replay-dir`, `--retry-failed-only` |
| Current-content update | `--update-from` | Parent sidecars, `--source-replay-dir`, `--resume-work`; history-related flags under their constraints | `--from-snapshot`, `--retry-failed-only` |
| Ordinary resume | Same initial mode/site/language + `--resume-work` | `--allpages` and `--include-history` if selected initially | Changing the completed source scope |
| Retry only failed crawl URLs | `--resume-work` + `--retry-failed-only` | Same source selection as the original ordinary crawl | Archive-derived modes; incomplete discovery |

The two parent-derived modes require a fully verified parent **ZIP + v3
provenance manifest + inventory**. A legacy archive that has no inventory
must first go through the [legacy indexing workflow](#legacy-inventory-indexing)
where eligible; a reconstructed inventory cannot supply missing source
identities for an unsafe upgrade.

## 5. Working with completed snapshots

### Portable page layout and an older snapshot parent

**Current Rules Wiki page members** use `.html` suffixes: a page
`/Foo` is `Foo.html` while a subpage `/Foo/Bar` is
`Foo/Bar.html`. The generated roots remain `index.html`,
`es/index.html` and `wiki/index.html`. Upper/lowercase variants,
Unicode, encoded slashes, Windows-reserved names and punctuation that
would otherwise collapse to the same filesystem component receive
stable source-identity suffixes. Binary assets retain their ordinary
extensions, with additional suffixes where needed for unique identities.

**Do not modify an older completed ZIP to rename its members.** For example,
the independently verified `WIKI-en 20261115-223808` source archive
uses the older extensionless layout. To create a fully enumerated
current-content child with corrected filenames, run:

```powershell
infinity-wiki-snapshot `
  --update-from "local/data/wiki/WIKI-en 20261115-223808.zip" `
  --from-manifest "local/data/manifests/snapshots/WIKI-en 20261115-223808.json" `
  --from-inventory "local/data/manifests/snapshots/inventories/WIKI-en 20261115-223808.inventory.json" `
  --source-replay-dir "local/data/wiki-source-replay"
```

This **new acquisition** uses `allpages`, downloads current HTML again,
checks assets and writes a new complete ZIP/v3 manifest/inventory and replay
companion, preserving the original parent artifacts and their SHA-256
lineage. Renaming a mirror path with an unchanged page URL is recorded
as `mirror-path-layout-change`, not an asserted change in wiki identity.
Interrupted **older** `--update-from` work cannot be resumed after this
layout cutover; restart from the same verified parent. New updates can
resume normally. The existing consumer must be checked for assumptions
about extensionless file members before ingesting newly generated archives.

A history-only upgrade of an **older** completed parent does *not*
migrate member paths—it preserves all current HTML bytes and old member
names. For a comprehensive history archive, first acquire/refresh the
parent's current page set with allpages and verify the resulting child,
then upgrade that child to history.

### History upgrades versus current updates

A history upgrade uses the verified parent as a source of unchanged current
HTML/assets and retrieves all visible revisions. It checks previous oldid
identity and association rather than equating matching numeric IDs with
an arbitrary new page. Current content retains its **original acquisition
time**; history acquisition is recorded separately.

A current-content update always rediscovers and refetches current HTML on
its initial pass. A resumed update can retain **already checkpointed current
pages** from the same interrupted run; it never treats an old parent HTML
page as a substitute merely because its latest revision is unchanged.

For assets, the updater may reuse verified parent bytes instead of downloading
the body when **either** of these independent proofs holds:

- The latest MediaWiki `imageinfo` identifies the exact original upload URL,
  timestamp, SHA-1 and size, matching the verified parent bytes.
- A strong HTTP ETag from an exact parent URL and effective URL matches an
  HTTP `304 Not Modified` response to `If-None-Match`. Weak validators,
  redirects and HTTP `200` require fresh download or rejection.

New/changed assets are fetched and checked. Missing or ambiguous version
evidence is **not** approval to reuse the parent. Failed downloads prevent
a finished snapshot.

### Source identity and unavailable former pages

A current page's **source URL, page ID, original HTTP response and mirror
path** are separate identities. Source title aliases, moves and missing
pages are reconciled only when evidence is sufficient. A missing page ID
cannot be assumed deleted: it may have moved namespaces or become
inaccessible.

With `--allow-historical-only`, the updater can retain last-archived
HTML and oldids under explicit v2 `historicalOnlyPages` records. These
records are **not** current content and do not claim fresh revision
enumeration. A previously retired identity reappearing in live discovery
requires separate reinstatement review, not silent promotion.

For the detailed producer/consumer v2 contract, see
[development guidance](docs/DEVELOPMENT.md). **Do not feed v2 history
snapshots to an unverified v1-only downstream consumer.**

## 6. Resuming interrupted work

A required page/asset/revision failure or failed publication leaves the
private work directory available. Once the cause of failure is fixed, use
the **same** mode, source and options with `--resume-work`.

### Ordinary interrupted crawl

For a fresh run started with `--allpages`, repeat **that same flag**
when resuming. A private `.wiki-fresh-discovery.json` receipt rejects
accidental scope changes. The existing link-only work directory remains
compatible with ordinary link-only resume.

```powershell
infinity-wiki-snapshot `
  --site infinity-wiki --language en `
  --resume-work "local/work/wiki/WIKI-en 20261115-180000.work"
```

If discovery was already completed and you specifically want to retry failed
URLs only, add `--retry-failed-only`. Normal resume can continue unfinished
`allpages` pagination, pending URLs or history revision pagination. Retry-only
is not available until the entire discovered URL set was checkpointed.

```powershell
infinity-wiki-snapshot `
  --site infinity-wiki --language en `
  --resume-work "local/work/wiki/WIKI-en 20261115-180000.work" `
  --retry-failed-only
```

### Repair older Rules Wiki work after filename changes

An interrupted fresh Rules Wiki crawl started before the current
`.html` filename layout is **not safe to resume directly**, even if its
only observed issue was a case-only title such as `No_LoF` versus
`No_Lof`. Unicode transliteration, punctuation, asset names and
rewritten links can also have changed. The new private
`.wiki-fresh-discovery.json` receipt binds the precise layout and
link-only/allpages choice. An old work directory lacking that receipt or
recording the former layout is refused rather than silently migrated.

For a fresh, non-history crawl started with `--source-replay-dir`, use the
offline repair tool to **create a new work directory** from its committed
checkpoint and retained original HTML bodies:

```powershell
python -m infinity_wiki_acquisition.repair_crawl_paths `
  --work "local/work/wiki/WIKI-en 20261115-213855.work" `
  --output-work "local/work/wiki/WIKI-en 20261115-213855-portable.work"
```

Then resume from the **new** work directory with the same source/language
and original HTML replay option:

```powershell
infinity-wiki-snapshot `
  --site infinity-wiki --language en `
  --resume-work "local/work/wiki/WIKI-en 20261115-213855-portable.work" `
  --source-replay-dir "local/data/wiki-source-replay"
```

The repair does **not** access the network or modify the old work directory.
It verifies committed saved HTML against its retained source evidence,
reconstructs every saved page's local links in the new `.html` layout,
updates HTTP asset evidence for renamed assets and preserves the original
allpages/link-only selection. **If the old run selected `--allpages`, repeat
that option when resuming the repaired directory.** Only checkpoint-
committed files are migrated; in-flight downloads may repeat during
resume. The old directory remains intact until you explicitly decide
it is no longer needed.
An ordinary crawl without original-HTML replay evidence cannot use this
repair safely; start a new crawl instead of guessing how to repair links.
The tool deliberately refuses history and archive-derived work, which have
additional provenance contracts.

### Interrupted history upgrade

**Completed Rules Wiki recovery (2026-11-16):** The first history
upgrade of `WIKI-en 20261116-075926` stopped at the inherited
`https://infinitythewiki.com/api.php` service endpoint after
saving 2,801 oldids for 709 of 711 current pages. That URL is an
HTTP API service, **not** an ordinary MediaWiki article; the revision
API reported the page title missing. An explicit, source-evidenced
`historyExclusion` with no invented revision allowed a safe resume.
The completed `WIKI-en-history 20261116-090844` independently
verified: **3,734 members, 2,801 revisions, all 711 parent
current pages preserved byte-for-byte, 221 assets and exact parent
lineage**. The actual `_history/index.json` contains the
`api.php.html` entry with zero revisions, query URL, exclusion reason
and response SHA-256. Other missing pages or failed revision requests
are still fatal. Later fresh/current crawls ignore that service URL.

For any **currently interrupted** history-only upgrade, use the
read-only preflight against the *existing* private work directory and
exact parent triple:

```powershell
python -m tools.inspect_history_resume `
  "path/to/parent.zip" `
  "path/to/parent.json" `
  "path/to/parent.inventory.json" `
  --work "path/to/preserved.work"
```

Once the cause has been fixed, resume **the same run**, not a new
acquisition. Do not delete the work directory or use ordinary
`--retry-failed-only`:

```powershell
infinity-wiki-snapshot `
  --from-snapshot "path/to/parent.zip" `
  --from-manifest "path/to/parent.json" `
  --from-inventory "path/to/parent.inventory.json" `
  --include-history `
  --resume-work "path/to/preserved.work"
```

Committed revision files are checked and reused; a noncommitted
in-flight batch may repeat. The original parent ZIP, manifest and
inventory are not changed. **A successful run normally deletes its
private work directory**; the historical
`WIKI-en-history 20261116-081423.work` is not an active recovery
target after successful publication.

### Interrupted current-content update

Only updates started with the **current** filename layout may use their
original `--resume-work` checkpoint. Older update work is bound to its
former layout and will be rejected; start a new `--update-from` from
the same verified immutable parent instead. This is different from
history-only resume, which preserves its parent's existing paths.

```powershell
infinity-wiki-snapshot `
  --update-from "path/to/parent.zip" `
  --from-manifest "path/to/parent.json" `
  --from-inventory "path/to/parent.inventory.json" `
  --resume-work "path/to/preserved.work"
```

Add the original `--allow-historical-only`, `--include-history` or
`--source-replay-dir` options if they were selected when the update began.

Archive-derived work contains a private `.wiki-archive-run.json` receipt
binding the operation and options to the exact parent ZIP/manifest/inventory
SHA-256s. Current updates also retain a bounded RecentChanges scan, crawler
checkpoint, asset-version decisions and (where completed) post-crawl source
observations. History uses a separate `.wiki-history-checkpoint.json` and
verified parent-oldid accounting.

**What resume guarantees:** committed checkpoint pages/assets/oldids are
checked and reused; incorrectly matched or corrupt evidence fails closed;
parent artifacts are not rewritten; publication remains all-or-nothing.
Uncommitted in-flight URL attempts or revision batches **may be repeated**.
Do not interpret resumability as globally exactly-once HTTP requests.

For step-by-step diagnostics and the acceptance test matrix, see
[archive-derived recovery acceptance](docs/ARCHIVE_RECOVERY_ACCEPTANCE.md).

## 7. Artifacts, provenance and verification

### Published files

A successful run normally publishes **three immutable artifacts**:

| Artifact | Example path | Contents |
| --- | --- | --- |
| Snapshot ZIP | `local/data/wiki/WIKI-en 20261115-180000.zip` | Offline-rewritten current pages, downloaded assets, optional historical oldid HTML and history index |
| Provenance v3 JSON | `local/data/manifests/snapshots/WIKI-en 20261115-180000.json` | Acquisition/source identity, exact ZIP SHA-256, logical-content SHA-256 and any parent ZIP input binding |
| Inventory v1 or v2 JSON | `local/data/manifests/snapshots/inventories/WIKI-en 20261115-180000.inventory.json` | Every ZIP member's size/hash/classification, observed source identities and relationships, parent lineage, ignored/unknown source evidence |

The three filenames share their **actual snapshot stem**. With history,
the archive prefix includes `-history`. Supported prefixes are
`WIKI-en`, `WIKI-es` and `HUMAN-SPHERE`.

With `--source-replay-dir`, a separate **fourth optional artifact** named
`<archive stem>.source-replay.zip` preserves the exact original current-page
HTML alongside a versioned index. It is bound to the three published
artifacts by SHA-256. It is not part of the standard snapshot ZIP and is
not produced by a history-only upgrade.

Private `*.work` files (crawl/history checkpoints, HTTP observations,
RecentChanges evidence and run receipts) are **never** ZIP members.
Successful runs remove staging when possible. Failed runs retain it for
diagnosis and recovery. Snapshot output and parent files are never
silently replaced.

### Independently verify a snapshot

Verification is **offline and read-only** and requires all three published
artifacts. From the installed Python environment:

```python
from pathlib import Path
from infinity_wiki_acquisition.snapshot_verification import verify_snapshot

snapshot = verify_snapshot(
    Path("local/data/wiki/WIKI-en 20261115-180000.zip"),
    Path("local/data/manifests/snapshots/WIKI-en 20261115-180000.json"),
    Path("local/data/manifests/snapshots/inventories/WIKI-en 20261115-180000.inventory.json"),
)
print(snapshot["site"], snapshot["language"], snapshot["history"])
```

`verify_snapshot` checks exact archive and logical-content hashes, ZIP
integrity, safe/portable member names, member sizes and SHA-256s, site and
history identity, recorded source evidence, revisions, and parent
relationships. It returns the validated inventory or raises
`SnapshotVerificationError`; it neither fetches from the wiki nor extracts
files. The verifier checks recorded consistency—it does **not** authenticate
the original publisher or guarantee source freshness.

To validate an optional original-source companion:

```python
from pathlib import Path
from infinity_wiki_acquisition.source_replay import verify_source_replay_bundle

report = verify_source_replay_bundle(
    Path("local/data/wiki-source-replay/WIKI-en 20261115-180000.source-replay.zip"),
    Path("local/data/wiki/WIKI-en 20261115-180000.zip"),
    Path("local/data/manifests/snapshots/WIKI-en 20261115-180000.json"),
    Path("local/data/manifests/snapshots/inventories/WIKI-en 20261115-180000.inventory.json"),
)
print(len(report["pages"]))
```

This independently replays original current-page link extraction and HTML
rewriting and compares the results with the verified snapshot bytes.

## 8. Source evidence and reuse limitations

The downloader records source URLs, MediaWiki numeric IDs and title hints,
redirect/effective URLs, source-response hashes, optional latest revision
IDs, template/category observations, HTTP/asset versions, and ignored or
unresolved links. An HTML `Template:` or `Module:` link is **not** by itself
proof of a rendering dependency. Original HTTP body bytes are retained
only in an optional replay companion, not the main snapshot ZIP.

Current-page updates query RecentChanges and check source-time coverage,
but neither allpages enumeration nor fully paginated API responses prove
that a source has retained all earlier activity. Template/module/category
changes, asynchronous rendering and intermediary caches prevent an
unverified assumption that an unchanged page revision or strong ETag means
the rendered HTML is still current.

**Current policy:** the render-reuse gate always keeps
`reuseAuthorized=false` and `skippedHtmlBodyCount=0` for current-page
refreshes. Conditional asset reuse does **not** imply conditional HTML reuse.
A verified HTTP `304` for a rendered page, or a clean offline ETag comparison,
does not grant permission to skip the next page download.

The companion ZIP supports separate read-only differential ETag and source
authority audits described below. A positive HTML reuse gate remains
blocked pending verified site-specific render/source-time guarantees. See
[rendered freshness authority](docs/RENDER_FRESHNESS_AUTHORITY.md).

## 9. Additional commands and audit tools

### Offline snapshot acceptance and parent-lineage audit

Run `tools.verify_snapshot_acceptance` to independently verify the
snapshot ZIP, its matching provenance/inventory, and, when supplied, the
original-HTML replay companion and exact parent artifact hashes.
It compares **source URLs rather than filenames**, so expected path-layout
migrations do not appear as deleted wiki pages.

```powershell
python -m tools.verify_snapshot_acceptance `
  "local/data/wiki/WIKI-en 20261116-075926.zip" `
  "local/data/manifests/snapshots/WIKI-en 20261116-075926.json" `
  "local/data/manifests/snapshots/inventories/WIKI-en 20261116-075926.inventory.json" `
  --replay "local/data/wiki-source-replay/WIKI-en 20261116-075926.source-replay.zip" `
  --parent "local/data/wiki/WIKI-en 20261115-223808.zip" `
           "local/data/manifests/snapshots/WIKI-en 20261115-223808.json" `
           "local/data/manifests/snapshots/inventories/WIKI-en 20261115-223808.inventory.json"
```

This command is read-only and offline. Its JSON result includes member
classification, page IDs, source-URL additions/absences, relocated page
paths, any unresolved member classifications, and the replay/lineage
verification statuses. Do not interpret it as remote source freshness.
The English Rules Wiki update dated **2026-11-16** independently passed
these checks: 932 members, 711 current HTML pages (all replayed), 221
assets, zero unknown members and zero parent page URLs lost. A separate
read-only API comparison found all **650** main-namespace source URLs
present, with zero page-ID mismatches.

### Rules Wiki source-specific policy audit (offline)

For an existing independently verified Rules Wiki current-content
archive, use the P3.5 source-policy auditor to examine source URL and
namespace prefixes, media host distribution, thumbnail candidates,
ignored URL reasons, known service endpoints, and unresolved references.
This is **read-only and offline**:

```powershell
python -m tools.audit_rules_source_policy `
  "local/data/wiki/WIKI-en 20261116-075926.zip" `
  "local/data/manifests/snapshots/WIKI-en 20261116-075926.json" `
  "local/data/manifests/snapshots/inventories/WIKI-en 20261116-075926.inventory.json"
```

The verified English archive contains 209 Corvus Belli-hosted
assets, 11 query-distinct wiki `load.php` resources and one
OpenSearch XML descriptor. The independently verified Spanish archive,
`WIKI-es 20261116-092640`, contains 207 Corvus Belli assets,
eight `/wiki-es/load.php` resources and one Spanish OpenSearch XML.
For a Spanish audit, substitute its stem into all three paths above.

Both current snapshots and their original-HTML replay companions
passed independent verification; neither has a standard MediaWiki
upload thumbnail or an unclassified member. The Spanish archive also
contains eight references to an out-of-scope Corvus Belli
quick-start PDF at `downloads.corvusbelli.com`; these are
external links, not missing required wiki downloads.
No evidence justifies copying Human Sphere's permissive 404,
namespace, thumbnail or alias-host policies. See
[source-policy evidence and decisions](docs/RULES_WIKI_SOURCE_POLICY.md).
The separate Spanish history acquisition is not covered by this
current-content acceptance.

### Rules Wiki allpages coverage audit

Use the read-only API comparator to check an **existing, verified**
link-only snapshot against current main-namespace enumeration.
The `--live` flag is mandatory and explicitly authorizes API requests;
the tool does not fetch current HTML or change the ZIP:

```powershell
python -m tools.audit_rules_allpages `
  "local/data/wiki/WIKI-en 20261115-223808.zip" `
  "local/data/manifests/snapshots/WIKI-en 20261115-223808.json" `
  "local/data/manifests/snapshots/inventories/WIKI-en 20261115-223808.inventory.json" `
  --live
```

Add `--json` to print the complete comparison (all missing URLs and ID
conflicts). The tool compares exact page URLs, not merely normalized titles.
A missing page may have been created after the snapshot; a later API
enumeration does not prove atomic historical coverage. An unlisted archived
page might be a root alias or another namespace.

For a bounded, API-only check of the two deployed language endpoints,
run `python -m tools.probe_rules_allpages`; use `--language en` or
`--language es` to limit the probe to one installation. These scripts
are provided from a repository checkout, not as installed entry points.

To acquire the API-discovered pages in a **new immutable child snapshot**
based on a verified parent, use `--update-from` with the parent triple.
This refreshes all current page bodies and performs full enumeration; it
does not simply merge a list of missing titles into the old ZIP. The
parent archive stays untouched.

### Legacy inventory indexing

For completed **pre-inventory** snapshots with an existing matching **v3**
provenance manifest:

```powershell
infinity-wiki-index-legacy "path/to/old-snapshot.zip" "path/to/old-manifest.json"
```

| Argument | Meaning |
| --- | --- |
| `archive` (required positional) | Completed source ZIP |
| `manifest` (required positional) | Matching v3 provenance JSON |
| `--inventory-dir DIRECTORY` | Optional destination; defaults to `<manifest directory>/inventories` |
| `-h`, `--help` | Show command help |

Indexing is fully offline, produces a new inventory **without** replacing or
modifying the ZIP/manifest, and verifies the resulting triple before
publication. Missing original source URLs, page IDs and other unobserved
metadata stay `unknown`. A legacy-reconstructed inventory may therefore
verify file integrity yet still be unsuitable for a source-identity-sensitive
history upgrade or current update. Earlier provenance versions require a
separate migration, not this command.

The entry point is also available as:

```powershell
python -m infinity_wiki_acquisition.legacy_snapshot_index --help
```

### Read-only rendered-HTML audits

These tools are intended for a **repository checkout** with verified
original-source replay companions. They do not initiate a crawl, modify
snapshots or authorize HTML download reuse.

Compare two completed captures (all eight artifact paths are positional,
with the earlier capture first):

```powershell
python -m tools.audit_render_freshness `
  "OLD-REPLAY.zip" "OLD.zip" "OLD.json" "OLD.inventory.json" `
  "NEW-REPLAY.zip" "NEW.zip" "NEW.json" "NEW.inventory.json"
```

Assess a capture's transport/source-authority gaps, optionally against an
earlier capture:

```powershell
python -m tools.audit_render_authority `
  "NEW-REPLAY.zip" "NEW.zip" "NEW.json" "NEW.inventory.json" `
  --previous "OLD-REPLAY.zip" "OLD.zip" "OLD.json" "OLD.inventory.json"
```

| Audit option | Meaning |
| --- | --- |
| `--previous OLD_REPLAY OLD_ZIP OLD_MANIFEST OLD_INVENTORY` | Optional four-file earlier verified capture for `audit_render_authority`. Omit it to assess the current capture alone. |
| `-h`, `--help` | Show the selected audit command's arguments. |

A detected identical strong ETag for **different original HTML bytes** is
negative evidence against the source validator; no finite number of
matching captures proves future rendered freshness. The authority report
is explicitly non-authorizing.

See [rendered freshness authority](docs/RENDER_FRESHNESS_AUTHORITY.md)
and [development and verification details](docs/DEVELOPMENT.md).

## 10. Troubleshooting, compatibility and further documentation

| Symptom | What to check |
| --- | --- |
| A required URL or revision failed | Check the printed error and preserved `*.work` path. Fix the source/connection issue, then resume with the same mode and options. A failed required source prevents publication. |
| `--retry-failed-only` is refused | It requires `--resume-work` on an **ordinary** crawl whose full discovery checkpoint is complete. Use normal resume otherwise. |
| Parent upgrade/update is rejected before fetching | Pass the actual ZIP, matching v3 manifest and matching inventory; verify the site/language and content identities. Reconstructed legacy inventories may lack required source metadata. |
| `Unclassified member cannot be updated safely` | The known Rules Wiki `opensearch_desc__u_b7ab2ebdc1fb.php` case is handled by the current updater when its verified parent contains the expected source URL/path. Other unidentified members still block updates; inspect their inventory evidence rather than removing them or changing their type by hand. |
| Resume refuses a parent or option mismatch | Use the **exact** parent triple and original mode/options. Do not copy a work directory to a different parent run or invent missing checkpoint evidence. |
| Missing historical page IDs | Do not infer deletion. For history-enabled updates, optionally choose `--allow-historical-only`, understanding the v2 downstream compatibility requirement. |
| Existing output or staging file blocks publication | Publication deliberately avoids overwrites. Inspect competing files and stale temporary evidence before choosing a new output directory or retrying; do not bulk-delete ignored source material. |
| A downloaded asset looks like HTML | HTML challenges, invalid binary signatures and MediaWiki description pages are classified separately. Required invalid resources stop publication; genuine HTML pages are not silently published as binary assets. |
| An archive verifies but content is not current | Snapshot verification proves recorded artifact consistency, **not** source freshness, correct gameplay interpretation, or authoritative rules coverage. |

### Consumer boundary

The handoff to InfinityDB is a portable, complete ZIP with matching
provenance JSON and inventory. Transfer or select the exact artifacts;
the producer's own path recorded in provenance is informational and need not
be recreated on the consumer machine. The consumer must verify the files
against **its actual supported version** before ingestion. In particular,
v2 historical-only records are an explicit compatibility opt-in.

This standalone producer does not depend on the InfinityDB runtime. It does
not automatically edit, import into, build, deploy or publish the downstream
application. Source material is research evidence, not a player-facing rule
authority.

### Further documentation

- [Development and integration](docs/DEVELOPMENT.md) — source contracts,
  verification design, identity rules, asset/revision evidence and testing.
- [Acquisition roadmap](docs/TODO.md) — completed phases and open research
  limitations.
- [Human Sphere / Rules Wiki parity audit](docs/SITE_PARITY_AUDIT.md) —
  source-specific differences, current coverage limitations, portable
  filename safeguards and planned acceptance checks.
- [Archive recovery acceptance](docs/ARCHIVE_RECOVERY_ACCEPTANCE.md) —
  recovery commands, checkpoint guarantees and synthetic acceptance matrix.
- [Rendered-HTML freshness authority](docs/RENDER_FRESHNESS_AUTHORITY.md) —
  HTTP/MediaWiki evidence, negative gate and external conditions.
- [Changelog](CHANGELOG.md) — operator-visible implementation history.
- [Agent instructions](AGENTS.md) — repository-specific contribution rules.

This repository contains acquisition code, not mirrored third-party source
documents or media. Keep downloaded material outside Git and respect its
separate copyright and usage terms.
