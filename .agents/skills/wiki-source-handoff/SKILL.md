---
name: wiki-source-handoff
description: "Domain-specific procedure for wiki and human sphere source handoff in InfinityDB-wiki-acquisition; use for source, provenance and cross-repository contract changes."
---

# Wiki and Human Sphere source handoff

## Use this workflow when

- Changing site crawling, URLs, page/revision/asset discovery, resume behavior or ZIP provenance.
- Reconciling a Wiki/Human Sphere producer change against an InfinityDB research consumer.

## Procedure

1. Read [development guidance](../../../docs/DEVELOPMENT.md) and the [README](../../../README.md); identify site, language, history mode and the exact archived revision/member at issue.
2. Preserve page/asset content and provenance, redirect/alias distinctions, revision identifiers, timestamps, source URLs and page-versus-asset classifications. Distinguish rewritten offline mirror links from original fetched bytes; neither is automatically verified current rules.
3. Define completeness before publication: required failed downloads block ZIP/manifest; optional URLs and stale 404 observations remain separately classified. Resume uses an existing incomplete work set without treating it as published.
4. Keep deterministic safe archive member paths, site-specific ZIP identities and the v3 exact-archive/logical-content provenance pair intact. Reject duplicates and unsafe aliases.
5. Test success, interruption/resume, missing required revision/asset, duplicate path, archive changes and unsupported site/language using mocked HTTP and temporary directories.
6. For a changed handoff, pin the exact consuming InfinityDB revision and verify its offline provenance reader. Do not require the producer checkout at application build time.
7. Record source site/language/revision, producer SHA, input/output hashes, actual tests and any remaining consumer check. Update maintained README/guide/changelog owners as needed.

Do not run a live crawl, republish source HTML/artwork or update application rules without separate authorization.
