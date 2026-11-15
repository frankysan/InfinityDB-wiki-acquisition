# Archive-derived recovery acceptance (P2.5c)

Status: **P2.5 acceptance completed by offline synthetic-source tests**.
No real Wiki or Human Sphere crawl was performed. The two parent-derived
workflows share a private parent-binding receipt; their source-time and
download checkpoints remain distinct. This document does not authorize
conditional reuse of rendered current-page HTML.

## Operator recovery procedure

1. **Identify the failed command's operation.** History-only upgrades use
   `--from-snapshot ... --include-history`. Current-content refreshes use
   `--update-from ...`; keep `--include-history` if it was explicitly
   selected. Never change operation mid-run.
2. **Retain the exact original parent triple** (ZIP, v3 manifest JSON and
   v1/v2 inventory JSON), and the preserved `*.work` directory. The CLI
   prints `preserved work -> ...` for source failures and
   `Preserved wiki work -> ...` after other post-staging failures,
   including keyboard interruptions. No successful run requires that work
   directory to remain.
3. **Correct the cause of the interruption** (e.g. restore source access,
   disk space or a verification issue), then rerun the same mode with
   `--resume-work`. Keep the original `--allow-historical-only`,
   `--include-history` and `--source-replay-dir` selections where used.
   Resume explicitly refuses parent SHA-256, site/language, mode and option
   mismatches. Ordinary crawl-only `--retry-failed-only` is **not** valid
   for archive-derived recovery.

History upgrade:

```powershell
infinity-wiki-snapshot `
  --from-snapshot "PARENT.zip" `
  --from-manifest "PARENT.json" `
  --from-inventory "PARENT.inventory.json" `
  --include-history `
  --resume-work "PRESERVED.work"
```

Current content update:

```powershell
infinity-wiki-snapshot `
  --update-from "PARENT.zip" `
  --from-manifest "PARENT.json" `
  --from-inventory "PARENT.inventory.json" `
  --resume-work "PRESERVED.work"
```

For a history parent whose current enumeration omits formerly current
page IDs, also supply `--allow-historical-only` **if it was selected at
the start**. Such snapshots use the independently checked v2 inventory.
For opt-in original-HTML replay companions, supply
`--source-replay-dir "SAME_REPLAY_DIRECTORY"` on resume. The replay option
must have been enabled in the first attempt; rewritten HTML cannot recover
missing original HTTP response bytes.

**After completion:** The new ZIP, provenance manifest and inventory must
all exist and independently verify as a full snapshot. Any optional
original-source companion must verify against those exact artifacts.
Parent hashes must remain unchanged. The work directory is normally
deleted only after the complete new output verifies; if cleanup fails,
a surviving work directory is inspection material, not evidence that
publication was incomplete.

## Offline acceptance coverage

| Proof obligation | History-only upgrade | Current-content update |
|---|---|---|
| Independently verify parent ZIP/v3 manifest/v1-v2 inventory | Checked before work reuse | Checked before source scan/resume |
| Parent SHA-256, site/language, mode/options run receipt | Required | Required |
| Resumed source pagination | History page/revision continuation | Allpages continuation and crawl queue |
| Completed saved source requests | Oldid history checkpoint | Pages and assets, with file-hash evidence |
| Source-version reuse counters | Reused parent oldids | HTTP 304, imageinfo SHA-1, parent oldids |
| Post-crawl observations | Rebuilt history index/associations | Page revisions and template/category metadata |
| Historical-only preservation | Existing parent history checked | v2 retired pages and oldid associations retained |
| Fail-closed provenance and no partial outputs | Required | Required |
| Private receipt/checkpoint isolation | Not in snapshot ZIP | Not in snapshot ZIP |
| No newly authorized rendered-HTML skip | Inherited current bytes only, already verified parent | All newly current HTML fetched during original run |

Core acceptance tests:
`tests/test_archive_recovery_acceptance.py`,
`tests/test_archive_resume.py`, `tests/test_update_resume.py`,
`tests/test_snapshot_upgrade.py`, `tests/test_snapshot_update.py`,
`tests/test_history_refresh.py`, `tests/test_historical_only.py`,
`tests/test_asset_conditional_reuse.py` and
`tests/test_mediawiki_upload_reuse.py`.

The consolidated operator tests deliberately interrupt **after**
download and **before** final inventory publication, then verify that
the CLI reports the saved work path, the new run completes with no
repeat completed network requests, parent file hashes are identical,
the final child inventory references that exact parent's hashes, and
private work artifacts are not members of the ZIP.

The lower-level tests also cover missing/altered receipts, incomplete
RecentChanges scan, altered saved HTML or asset bytes, resumed allpages
continuation, failed current-page and asset fetches, history oldid
interruptions, and resumed HTTP/imageinfo evidence accounting. A
noncommitted queue batch may be retried on resume, so **zero duplicate
requests is guaranteed only for work committed in the corresponding
checkpoint**; it is not a global exactly-once network guarantee.

## Important boundaries

- Parent snapshots are immutable. The child is a complete new archive,
  **not** a delta and not a partially accepted history crawl.
- Resuming cannot repair genuinely inconsistent page/oldid associations,
  changed parent identities or corrupted committed work silently. Review
  the source, restore trustworthy work or begin a fresh update.
- RecentChanges retention is not proved complete; observations do not
  make the acquisition an atomic as-of-time source snapshot.
- A full HTTP page fetch may itself return stale server-rendered HTML.
  Site-specific dependency and cache freshness remain externally
  unverified under P2.4c; P2.5 does not turn on HTTP 304 HTML reuse.
- Only synthetic/offline acceptance was run. Live source reliability,
  production-scale I/O and downstream InfinityDB ingestion have not
  been certified by this acceptance step.
