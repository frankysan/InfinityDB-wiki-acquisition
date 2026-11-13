# Development and integration

This guide owns the standalone Wiki/Human Sphere producer's development
workflow. The [README](../README.md) owns the public CLI, site/language
support and consumer handoff; [pyproject.toml](../pyproject.toml) owns
installation; [the CI workflow](../.github/workflows/tests.yml) owns hosted
checks; [CHANGELOG.md](../CHANGELOG.md) owns significant operator outcomes.
Agents start with [AGENTS.md](../AGENTS.md).

## Source acquisition boundary

- Explicitly crawl Infinity Wiki in English or Spanish, or Human Sphere in
  English. Keep source site, language, archive member path, page/revision
  identity, source URL and acquisition time distinct.
- Preserve source page/revision content and referenced-asset provenance;
  distinguish offline-mirror link rewriting from the original HTTP bytes.
  Do not turn a mirrored page into a curated gameplay rule or assume its
  latest revision supersedes an official PDF/Army source. InfinityDB owns source reconciliation,
  content interpretation and subsequent application publication.
- Preserve deterministic `WIKI-en`, `WIKI-es` and `HUMAN-SPHERE` archive
  identities, with optional revision-history variants and a matching v3
  `InfinityDB snapshot provenance` JSON manifest. Exact ZIP SHA-256 and
  logical-content SHA-256 have different purposes; keep both.
- Maintain fail-closed crawl completeness: required pages, revisions or
  assets that fail do not create a finished ZIP/manifest. A partial
  `--resume-work` tree is work evidence, not a published snapshot.
  Optional/missing links must retain their explicit classification.
- Treat MediaWiki redirects, aliases, archive member safety, repeated
  paths, HTML-with-asset extensions, language differences and case-folding
  collisions as source-format concerns. Preserve true source identities
  rather than collapsing pages by similarity.

The consumer receives a portable **complete ZIP plus its matching JSON**,
not the producer checkout, a crawl work directory or an application
import. InfinityDB verifies that pair offline; the manifest's original
producer path is informational. When changing provenance/archive schemas,
maintain backward compatibility where supported and test a pinned
consumer reader without adding an `infinity_db` runtime dependency.
No claim of source-content completeness or gameplay accuracy follows
merely from a successful crawl.

## Development checks

Python 3.11+ and standard-library runtime only. Use the repository
virtual environment:

```powershell
python -m pip install -e ".[test]"
python -m pip check
python -m pytest -q
```

The test suite is offline, using mocked HTTP and temporary directories,
with no live crawl, private Wiki archives, sibling repositories or third-party
data required. Test success and failure/resume boundaries, deterministic
archive bytes/member paths, manifest integrity and all supported
site/language variants. CI runs the same installed package on Windows,
Ubuntu and macOS at Python 3.11. Report any unavailable host or skipped
coverage instead of claiming hosted or application acceptance.

A networked `infinity-wiki-snapshot` run and `--include-history` selection
are explicit operator actions. The current relative output and preserved
resume-work defaults remain supported. Use categorized ignored `local/`
paths for **new** research, validation, transfer and scratch with site,
language, acquisition revision/run ID and lifetime in the path. Do not
implicitly change documented legacy output paths, overwrite prior
snapshots, or delete incomplete crawl data that remains necessary for
recovery. Third-party source HTML, images and documents remain outside
the MIT code-license grant and out of Git.

## Changes and retirement

Update the README for CLI/output changes, this guide for development
policy, tests for executable invariants, and the changelog for substantial
operator outcomes. Do not copy InfinityDB's browser, database, release
gate or internal test queue into this producer.

For a bounded source audit or one-use recovery tool, record its
owner/question, source revision, input/output hashes and completion
criterion. Reconcile unresolved findings into maintained documentation;
retain unique source evidence where justified, then retire redundant
tools/reports and historical-only tests together. Never infer that a
test-only dependency makes an obsolete acquisition path permanent.
Immutable original archives and privately retained crawl evidence require
their own verified backup/provenance review before deletion.

Every cross-repository handoff states producer SHA, site/language/history
scope, ZIP/manifest hashes and version, consuming InfinityDB revision,
checks actually executed and any unverified cutover. Do not automatically
edit, fetch, push or deploy a sibling repository.
