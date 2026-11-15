# Rendered HTML freshness authority — source investigation

Status: **P2.4c.2b.2b.2, source-contract research and negative authority
assessment completed; positive rendered-HTML reuse blocked.**
Reviewed 2026-11-15. This is **not** an approval for conditional HTML
skipping, a live-source study, or a site administrator attestation.

## What the published contracts establish

1. [RFC 9110, HTTP Semantics, §8.8.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.1)
   defines a *strong validator* as metadata that changes when the selected
   representation data observable in a GET 200 response changes. It also
   distinguishes separately negotiated representations. This is an HTTP
   semantic requirement of a conforming origin; it does **not** independently
   attest that a particular MediaWiki deployment, intermediary, plug-in,
   content transformer, or server response actually implements it.
2. [MediaWiki Manual:Parser cache](https://www.mediawiki.org/wiki/Manual:Parser_cache)
   describes dependencies beyond a page's own source revision (notably
   templates), variants based on parser options, and **asynchronous**
   invalidation for changes to dependencies. It explicitly allows stale
   output to be temporarily served while the fresh rendering is prepared.
   This is evidence against treating equal wiki page revision IDs or
   exhausting RecentChanges as proof that rendering is current.
3. The
   [MediaWiki REL1_35 ParserCache::getETag source](https://doc.wikimedia.org/mediawiki-core/REL1_35/php/ParserCache_8php_source.html)
   constructs a **weak** `W/"..."` parser-cache validator incorporating
   parser output key/options and `getTouched()`. This is historical
   **version-specific implementation evidence**, not a claim about the
   Infinity the Wiki or Human Sphere production servers or all MediaWiki
   response paths. A different strong ETag might originate from a different
   handler or an HTTP intermediary.
4. [MediaWiki Manual:Nginx caching](https://www.mediawiki.org/wiki/Manual:Nginx_caching)
   describes independent proxy caching and asynchronous HTML cache updates;
   [Manual:$wgUseCdn](https://www.mediawiki.org/wiki/Manual:$wgUseCdn)
   describes cache aging and purge configuration. Neither source establishes
   the actual cache topology, expiry, purge timing, or source-render contract
   of the two target sites.

**Consequence:** The current API's source-revision, templates/categories,
RecentChanges, HTTP headers and source-body replay observations do not provide
a source-attested, atomic, complete rendered-page freshness protocol. No
published site-specific contract has been verified for either target site.

The producer may record that HTTP 304 matches a past **selected
representation** when transport validation is supported; that still cannot
mean "current after all template/module/category and site configuration
changes." A full HTTP 200 may also return stale cached output; the existing
full-refetch policy is conservative acquisition of observed bytes, not a
claim of atomic source truth.

## Verified local evidence and how to use it

- `verify_snapshot` checks ZIP/provenance/inventory association and refuses
  false render-reuse counts.
- `verify_source_replay_bundle` checks pre-rewrite response bytes, complete
  navigation input and deterministic mirror transformation.
- `prepare_html_transport_probes` checks a fully verified parent capture
  and identifies exact transport-variant candidates. Neither it nor a
  successful `verify_html_transport_response` authorizes HTML reuse.
- `compare_rendered_captures` checks **two** complete source-replay
  captures and can detect equal strong ETags attached to **different**
  original HTML, including unchanged page revision IDs. It is a finite
  negative test, not certification of future correctness.
- `assess_html_source_authority` recomputes the above from verified artifact
  paths and produces a separate **v1 non-authorizing readiness report**.
  It enumerates per-page unknown transport, source-time, dependency, change
  retention and site-specific validator gaps. With two captures, a confirmed
  same-ETag/different-body collision blocks the entire source's validator
  assumption. Neither a zero-collision audit nor an eligible transport
  probe can remove its fixed source-side blockers.

Read-only command, without source requests or artifact mutation:

```sh
python -m tools.audit_render_authority REPLAY.zip SNAPSHOT.zip MANIFEST.json INVENTORY.json
python -m tools.audit_render_authority REPLAY.zip SNAPSHOT.zip MANIFEST.json INVENTORY.json \
  --previous OLD_REPLAY.zip OLD_SNAPSHOT.zip OLD_MANIFEST.json OLD_INVENTORY.json
```

The first four paths describe the newer capture. The optional previous
tuple must be a complete, independently verifiable earlier capture.
Output is JSON with the exact current capture SHA-256 fingerprints and
`reuseAuthorized=false`, `skippedHtmlBodyCount=0`.

## Requirements before positive rendered-HTML reuse

The following are concrete review/implementation requirements, **not**
conditions that a user can satisfy by manually selecting an unchecked
"approved" field:

1. **Target host and response path:** Identify the actual production
   MediaWiki/core/extension/skin versions, URL handler, intermediaries and
   cache topology for **each** site and representation profile. Pin the
   behavior to deployed source/configuration or obtain the source operator's
   verifiable signed/versioned capability contract. Public documentation
   about generic MediaWiki is insufficient.
2. **Rendered representation identity:** Establish that the exact strong
   validator (or stronger version/hash) changes on every observable
   HTML-output change for that selected URL/variant. Verify rules for
   template/module/Scribunto transclusion, categories/index changes,
   redirects, site messages, skin and configuration updates, dynamic
   extensions, response encodings, authenticated/anonymous variants.
   A server that marks a non-strong validator strong must fail eligibility.
3. **Time-bound freshness:** Establish when dependent changes are reflected
   in served output, how asynchronous jobs and proxy purges are tracked,
   and what bounded horizon is guaranteed for a source-atomic checkpoint.
   Exhausted RecentChanges pagination does not prove its retention horizon.
   Explicitly define what happens if the source lacks this capability.
4. **Preflight and continuation:** Bind all observations to the exact
   parent ZIP/manifest/inventory/replay hashes, numeric page ID, URL and
   transport variant before first conditional request. Full fresh
   enumeration/discovery, source-side page moves, source-time boundaries,
   interrupted/resumed checkpoints, history-only/oldid and v2
   historical-only associations must remain validated.
5. **Positive verifier:** Add a *new* independently checked protocol
   version with per-page actual 304 evidence, validated dependency/source
   time, complete current page association, replay/rewriter parity and
   correct `skippedHtmlBodyCount`. On any missing, stale, weak,
   inconsistent or contradictory item, fetch the full body and retain
   uncertainty rather than silently reusing bytes.

If this source-specific evidence remains unavailable, P2.4c.2b.2b.2 is
**blocked**, and the correct operational policy is to keep full current-page
HTML fetching and pursue P2.5 resumability / efficiency independently.
A verified HTTP 304 never upgrades source freshness by itself.
