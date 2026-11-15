# Wiki acquisition roadmap

This is the maintained backlog for the independent InfinityDB Wiki/Human Sphere acquisition producer. The single developer owns releases. Implement coherent batches with offline tests; do not impose application-side deployment or consumer changes on this repository.

## P0 — Resumable acquisition state (complete)

Goal: Resume interrupted acquisition at any point, without refetching already completed HTML pages or re-enumerating earlier discovery batches. The existing failure-only retry remains supported.

- [x] Complete-queue checkpoint and `--retry-failed-only` (existing implementation): persisted discovered URL identities, files, pages, failures, ignored URLs and fail-closed publication.
- [x] **P0.1** Version checkpoint for acquisition phases (`enumerating`, `crawling`, `complete`) and an ordered pending URL queue. Periodic atomic updates (about every 100 URLs); resume only consistent, site/language-matched state; preserve incomplete work on interruption.
- [x] **P0.2** Persist MediaWiki `allpages` continuation between batches; interrupted enumeration resumes from its token instead of starting again.
- [x] **P0.3** Separate history checkpoint now persists revision-list continuation tokens, per-page progress and oldid downloads, validating saved files and independently blocking incomplete-history publication.
- [x] **P0.4** Offline tests for interrupted enumeration and crawl, bounded replay, reclassified URLs, corrupt/missing files, completed snapshots, and legacy completed-checkpoint compatibility. Legacy work without a checkpoint retains full resume. Historical revision checkpoints are covered by P0.3.
- [x] **P0.5** Update operator documentation and ensure checkpoints never enter published ZIPs.

## P1 — Persistent inventories for completed snapshots

Goal: Allow independently verified reuse of a published ZIP without depending on the ephemeral crawl directory.

- [x] **P1.1** Version 1 immutable sidecar inventories accompany successful snapshots, with site/language, acquisition time and archive hashes, per-member SHA-256, known source identities and historical revisions, and reserved parent lineage.
- [x] **P1.2** Independently retain original and mirrored source hashes/identities, MediaWiki allpages IDs and separate markup ID hints, observed HTTP redirect targets versus unverified canonical/meta-refresh claims, and original-HTML resource/namespace references with explicit archive resolution. Template/module **transclusion is not verified**; actual rendering-dependency analysis remains P2.4.
- [ ] **P1.3** Verify ZIP SHA-256, member integrity and path portability before reuse; keep the existing v3 provenance ZIP/JSON contract unchanged unless a separately verified extension is needed.
- [ ] **P1.4** Index legacy snapshots conservatively where identities can be established. Mark unknowns instead of guessing; old ZIPs and manifests are immutable.
- [ ] **P1.5** Evaluate a private SQLite state store when repeated updates make JSON checkpoints inefficient, without forcing InfinityDB to import the acquisition runtime.

## P2 — Archive-derived acquisition

Goal: Build a new *complete* immutable snapshot while reusing verified content from an earlier one.

- [ ] **P2.1** `--from-snapshot <zip> --include-history`: upgrade non-history snapshots by reusing current pages and assets, fetching revision inventories and missing oldid HTML, then producing a new history ZIP and matching manifest. Record original current-page timestamp separately from history retrieval timestamp; offer history cutoff semantics explicitly.
- [ ] **P2.2** Reuse already downloaded oldid revisions when upgrading from a history archive; handle redirects/aliases and revision-title identity.
- [ ] **P2.3** `--update-from <zip>`: compare MediaWiki page IDs/revision IDs, recent changes (including moves/deletions) and upload hashes/timestamps; retrieve new/changed assets/pages, reuse verified unchanged members, and rewrite links before publishing.
- [ ] **P2.4** Deal with template/module-dependent rendered HTML invalidation and category/index changes. If RecentChanges retention is insufficient or revision metadata cannot establish unchanged content, widen discovery/verification rather than claim completeness.
- [ ] **P2.5** Support interrupted archive-derived runs, incremental history growth, clear source lineage, and repeatable offline tests. Never mutate an earlier archive.

## Invariants

- Required failures or incomplete discovery/history block ZIP/manifest publication.
- Checkpoint/state data remain private work artifacts and are excluded from published ZIPs; permanent inventories are separate sidecars.
- A snapshot is an immutable complete output, not a delta archive; a reused byte still needs traceable source evidence.
- Rewriting HTML for offline links must not be confused with original HTTP response content.
- Scope is producer-only: do not automatically alter, deploy or depend on sibling InfinityDB repositories.
