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
- [x] **P1.3** Offline `verify_snapshot(archive, manifest, inventory)` validates matching v3 provenance/v1 inventory, exact ZIP and logical-content hashes, CRC-checked per-member bytes, portable collision-free paths, metadata and history-index consistency. Reuse fails closed; existing consumer ZIP/JSON contracts remain unchanged.
- [x] **P1.4** Explicit offline legacy indexing creates an immutable v1 inventory for a verified pre-inventory Wiki ZIP plus v3 provenance pair; only the archived `_history/index.json` establishes current-page and oldid identities, while other members and unavailable metadata remain `unknown`. Sidecars are verified before no-overwrite publication; old ZIPs/manifests remain unchanged. Earlier provenance versions and unsupported/nonportable snapshots fail closed.
- [x] **P1.5** Evaluated JSON versus incremental SQLite checkpoint costs on synthetic 10,000/75,000-URL and 10,000/50,000-revision workloads. JSON full writes remained modest (~11/74 ms for crawl sizes; ~16/78 ms for history sizes), while SQLite 100-record transactions were ~2.5/2.8 ms. **Decision:** retain the tested JSON checkpoint implementation; no measured end-to-end bottleneck justifies migrating P0 state. Revisit a private, transactional SQLite store during P2 only if measured archive-derived persistence overhead warrants it. Benchmarks, limits and triggers are documented in `docs/DEVELOPMENT.md`; keep the consumer ZIP/provenance/inventory contract independent.

## P2 — Archive-derived acquisition

Goal: Build a new *complete* immutable snapshot while reusing verified content from an earlier one.

- [x] **P2.1** `--from-snapshot <zip> --include-history`: verify a complete non-history ZIP/v3 manifest/v1 inventory triple before network access; require explicit current-page/asset identities; reuse every original member byte-for-byte and preserve its source evidence; enumerate and fetch all available history oldids using the existing checkpointed downloader. Produce a separate complete history ZIP/v3 manifest/v1 inventory with parent artifact hashes and independent current-content, history-completion, and new-archive timestamps. **History cutoff policy:** `all-observed` (every revision advertised during enumeration, including ones newer than the original HTML); date-limited as-of reconstruction is not supported because the current full-history contract cannot safely describe it. Partial history and unknown source identities fail closed. Failed staging remains available; archive-derived resume belongs to P2.5.
- [x] **P2.2** History parents can now serve as `--from-snapshot` inputs. Refresh revision enumeration while reusing previously archived oldid bytes only after matching oldid, parent ID, timestamp and archived current-page association. Multiple aliases sharing a revision are supported; title changes for the same requested URL update the generated history index without forcing a redownload. Missing oldids, conflicting revision identities and vanished page/oldid associations fail closed rather than silently dropping historical coverage. Newly advertised revisions are fetched, parent content stays immutable, and child inventory records reused oldid count and preserves original current HTML acquisition time across generations. Original history-index metadata is never reused verbatim. Current-page moves/updates remain P2.3; interrupted upgrade resume remains P2.5.
- [ ] **P2.3** `--update-from <zip>`: compare MediaWiki page IDs/revision IDs, recent changes (including moves/deletions) and upload hashes/timestamps; retrieve new/changed assets/pages, reuse verified unchanged members, and rewrite links before publishing.
  - [x] **P2.3a — conservative current-content refresh:** A new explicit `--update-from` mode verifies a non-history ZIP/v3 manifest/v1 inventory, scans paginated RecentChanges as *non-authoritative retention evidence*, forces fresh main-namespace allpages enumeration, and performs a complete current HTML/asset recrawl (including fresh link rewrites). It compares verified parent member URLs/paths/SHA-256 against downloaded bytes, copies byte-identical parent members, records URL transitions by page ID (corroborated move vs unresolved alias)
and missing IDs without inferring deletion, and publishes a fresh independently verified complete ZIP/manifest/inventory with immutable parent lineage. Required failures block publication; unit/integration tests are fully offline.
  - [ ] **P2.3b — genuinely incremental network acquisition:** Capture authoritative current page revision IDs plus upload identities, content hashes and version timestamps with a clear source-time consistency contract. Compare against retained parent evidence; download only changed/new pages and assets, but widen discovery/refetch when that evidence is missing or insufficient. Integrate source moves/deletions with aliases and reconcile retained links, including fallback beyond RecentChanges retention. A matching ID alone never proves unchanged rendered HTML; transclusion invalidation belongs to P2.4.
  - [ ] **P2.3c — history-parent current refresh:** Preserve or deliberately regenerate all historical coverage while refreshing current pages, including oldid/page associations for moved and deleted titles. Do not silently drop history. Coordinate with P2.2 and P2.5 archive-derived resume.
- [ ] **P2.4** Deal with template/module-dependent rendered HTML invalidation and category/index changes. If RecentChanges retention is insufficient or revision metadata cannot establish unchanged content, widen discovery/verification rather than claim completeness.
- [ ] **P2.5** Support interrupted archive-derived runs, incremental history growth, clear source lineage, and repeatable offline tests. Never mutate an earlier archive.

## Invariants

- Required failures or incomplete discovery/history block ZIP/manifest publication.
- Checkpoint/state data remain private work artifacts and are excluded from published ZIPs; permanent inventories are separate sidecars.
- A snapshot is an immutable complete output, not a delta archive; a reused byte still needs traceable source evidence.
- Rewriting HTML for offline links must not be confused with original HTTP response content.
- Scope is producer-only: do not automatically alter, deploy or depend on sibling InfinityDB repositories.
