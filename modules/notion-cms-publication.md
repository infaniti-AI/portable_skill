---
name: notion-cms-publication
title: Notion CMS validation and cached publication
summary: Turn a code-owned catalog and Ready Notion page bodies into one cached published collection with isolated article failures and shared discovery consumers. Use this when editors publish public articles from Notion into a Next.js site.
stack: [nextjs, notion, typescript]
tags: [cms, notion, publication, cache, editorial, markdown]
requires: [nextjs-cache-components]
related_skills: []
status: stable
updated: 2026-11-03
---

# Notion CMS validation and cached publication

Keep one collection of articles that can currently be read and validated. A code catalog defines candidate routes; each CMS page supplies publication status, metadata and body. Non-Ready pages are absent. Read or validation errors remove that article from the next collection while other candidates continue.

This is an explicit publication policy (P47). It permits partial and empty collections. It does not retain a previous good body for each failed article or make a refresh all-or-nothing.

## Architecture and ownership

```text
code catalog: route identity / title / CMS page reference / related paths
    -> sequential page reads
    -> cursor pagination + nested block conversion
    -> exact Ready header + metadata/date + body validation
    -> skip nulls; log and skip individual errors
    -> one remote cached published collection + one tag
    -> lookup / page metadata / indexes / related links / sitemap / llms
```

The reference owns titles, slugs and related paths in code. It computes canonical paths from that identity, including a special top-level CMS page. Editing a Notion page title or adding a child under the CMS parent does not register a new route. A header Slug field is not the routing authority.

Consumers filter or find inside this collection rather than fetching and caching each article separately. There are no retained article snapshots, local Markdown publication sources or preview-switch alternatives. Use `nextjs-seo-geo` for metadata, document rendering and discovery projections.

## Configuration

| Name or setting | Purpose |
| --- | --- |
| `NOTION_API_KEY` | Server-only integration credential with read access to every catalog page and required children. |
| `NOTION_API_BASE_URL` | Server-only trusted Notion-compatible endpoint. Offline tests use a local mock. Never accept this override from a request. |
| `REVALIDATE_SECRET` | Server-side bearer credential for the allowlisted refresh route. |
| `NODE_ENV` | Production mode makes missing CMS credentials an explicit failure before catalog loading. |
| `Notion-Version` | Explicit version header on block reads; the reference pins `2022-06-28`. Revalidate converter compatibility before changing it. |
| Remote cache handler | Provided by the deployment or configured as described in `nextjs-cache-components`. |

Do not send integration keys to the browser or expose them in cache arguments, paths or logs. Restrict the API base override to trusted configuration: requests send the bearer credential there.

The production collection loader throws when `NOTION_API_KEY` is missing. Outside production a missing key returns an empty collection. A lower-level single-page helper can return null without a key; calling it alone does not enforce the production guard. CI production builds need a mock endpoint and a synthetic key, not a title-only fallback or empty-key bypass. See `nextjs-testing` for harness setup.

## Catalog and CMS header schema

Generic application types may look like this; a `path` field here abstracts the reference's deterministic route mapper:

```ts
export type CatalogEntry = {
  slug: string
  path: string
  title: string
  cmsPageId: string
  related: string[]
}

export type ArticleContent = {
  description: string
  author: string
  index: "resource" | "compare" | "about"
  updated: string
  published: string
  body: string
}

export type PublishedArticle = CatalogEntry & ArticleContent
```

Each field below is its own paragraph before the first top-level heading block:

| Header | Contract |
| --- | --- |
| Status | Exact joined plain-text paragraph `Status: Ready for site`, after trimming outer whitespace. Any other text is non-Ready. |
| Description | Nonempty summary used by the page and discovery consumers. |
| Author | Nonempty author identity, maintained by the editor. |
| Index | One recognized category. The reference accepts Resource, Compare or About, normalized to lowercase. |
| Updated | Required valid calendar date in `YYYY-MM-DD` form. |
| Published | Optional original publication date with the same validation. Unknown or invalid becomes absent, never the update date. |

Formatting can span rich-text fragments; the Ready check joins `plain_text`, so bold styling does not change the required text. A substring in body prose, a different case, or a status after the first heading does not pass. Drafts return null before strict body conversion, so unsupported draft content need not become a publication error.

The metadata parser reads only converted header text before the first H1–H3 line. Labels can be bold and are matched case-insensitively; category values are normalized. Required dates round-trip through UTC calendar parsing to reject impossible days. An invalid required update date rejects the article. Optional publication dates remain omitted when unavailable.

The body starts at the first H1–H3 line; no heading means no body. The reference rejects bodies whose trimmed non-heading remainder has fewer than 80 characters before normalization. That is a placeholder guard, not a quality, correctness or prose-only check. Markdown markup and non-prose content can contribute to that length; editors still review the rendered result.

## Read the entire supported body

1. Fetch `/blocks/{id}/children` with the server credential, explicit version and `page_size=100`.
2. Append results and continue with `start_cursor` while `has_more` and `next_cursor` indicate another page. Apply pagination to every nested read, not only the page root.
3. Inspect the top-level header for Ready before strict conversion.
4. Convert supported blocks and recursively retrieve `has_children` content. A nested fetch failure rejects that article; do not publish just its first page or parent summary.
5. Parse/validate metadata and substantive body, then normalize heading levels and established internal links outside fenced code.

The [Notion block-children API](https://developers.notion.com/reference/get-block-children) returns one level at a time, supports cursors and requires content-read capability. Inaccessible page references can return 404 as well as missing pages; check integration sharing before treating an ID as deleted.

The reference supports:

- Paragraphs, H1–H3, bulleted/numbered items, quotes, code and dividers. Rich text preserves links, bold, italic and inline code; links to known site origins become relative paths.
- Callouts as quoted text and toggles as H3 text followed by their children. List children are indented. These are Markdown approximations, not exact Notion visual replicas.
- Tables by separately fetching their child rows, escaping pipes/newlines, padding uneven widths and treating the first row as the Markdown header.
- External images with captions mapped to alt text. Notion-hosted file images reject the article because their signed URLs expire. [Notion documents their temporary URLs](https://developers.notion.com/reference/file-object); long-lived published HTML needs stable public assets.

Strict conversion rejects unrecognized published block types instead of silently omitting them. Child-page blocks and standalone table-row blocks are intentional exceptions; child pages are not inlined. Table conversion consumes row blocks. This is a bounded supported format, not a universal Notion renderer.

The source does not implement a general link/asset validator, lossless rich-text serialization, request retry/backoff, total fetch deadline, recursion budget or malformed-cursor guard. Sequential reads limit concurrency to one but do not guarantee a per-second rate limit. For larger catalogs, add bounded pagination/depth/time controls and Retry-After handling deliberately and test those additions; do not claim they already exist. Keep source-body and credential text out of diagnostic logs.

Legal pages share low-level reads but use a separate, less strict conversion/version-selection path. Preserve their authored versions and rendering. Do not apply article heading normalization or this skip policy to legal acceptance implicitly.

## One collection with explicit failure isolation (P47)

The following generic builder assumes `read` enforces the Ready/header/body contract. Its catch is the publication decision, not a generic transport helper returning an empty stand-in:

```ts
export async function loadEntries(
  catalog: CatalogEntry[],
  read: (entry: CatalogEntry) => Promise<ArticleContent | null>,
  report: (slug: string, error: unknown) => void,
): Promise<PublishedArticle[]> {
  const published: PublishedArticle[] = []
  for (const entry of catalog) {
    try {
      const content = await read(entry)
      if (content) published.push({ ...entry, ...content })
    } catch (error) {
      report(entry.slug, error)
    }
  }
  return published
}
```

Install a non-throwing reporting function. The reference logs each failure and continues sequentially in catalog order. A non-Ready null is silently excluded; an HTTP error, unsupported Ready block or invalid required field is logged and excluded. Even all failed reads produce a successful `[]` from this builder. A bad key that causes every request to fail is different from an absent key: the former can yield empty after per-article catches, while the latter throws at the production guard.

Wrap the entire build in one `"use cache: remote"` function. Inside that boundary set one shared `cacheTag` and the long-lived profile; assert production configuration before calling the builder. The reference uses its configured `forever` profile, with freshness managed primarily by invalidation. Lookups/category filters outside the boundary just read the collection. Cache mechanics and request-data constraints are in the required `nextjs-cache-components` module.

| Event | Result when the new collection is consumed |
| --- | --- |
| Editor changes a cached article in Notion | No immediate promise of visibility; a cache miss, refresh or new deployment may load it. |
| Article becomes non-Ready | It is absent after a successful rebuild observes that status. |
| One article fails validation or HTTP read | Other successes remain; this article is absent from the new collection. |
| Every article is skipped | Empty collection is a valid cache replacement. Independently static routes remain. |
| Production key is absent | Collection load throws before article isolation. No placeholder articles are synthesized. |

For an absent record, page lookup returns null and the page calls `notFound()`. Generated article indexes, related CMS links, sitemap and llms use the same successful subset. CMS-backed special pages must also be conditional. Curated display groups can select only some published records; inline authored links and static navigation require their own review.

Do not promise per-article old-content retention. A warm stale collection can remain visible during revalidation, but once a partial or empty rebuild completes successfully it can replace that value. Observability should distinguish expected drafts from read/validation failures and record candidate/success counts without logging page bodies.

## Refresh and deployment workflow

Authenticate a POST refresh route with `REVALIDATE_SECRET`, compare the bearer securely and allowlist only intended shared tags. Missing secret returns 503, unauthorized bearer 401 and an unsupported tag 400. An empty/non-JSON body selects the configured default tag in the reference.

For the article collection, use `revalidateTag("published-pages", "max")` with your actual generic tag. For a separate legal library that requires blocking freshness, use its separate allowlisted tag and `{ expire: 0 }`. The reference makes this choice by tag; do not apply one expiry mode to both accidentally.

With `"max"`, invalidation marks stale; a subsequent read can serve the previous collection and start rebuilding in the background. The POST's success means invalidation was accepted, not that all articles were validated or that the next response is fresh. `{ expire: 0 }` makes the next read block for fresh data. See the [official revalidateTag behavior](https://nextjs.org/docs/app/api-reference/functions/revalidateTag).

A manual refresh is not the only CMS read trigger. Build-time static params, page metadata, prerendering, runtime cache misses and expiry can execute the loader. Code/build changes can create different cache entries; do not treat a long lifetime as a deployment-independent publication lock. No CMS webhook-driven refresh is implemented by this reference.

Review in this order:

1. Register candidate routes in code and share the corresponding pages with the integration. Check supported blocks and the header before marking Ready.
2. Build/deploy the review environment with correct origin, CMS configuration and beta indexing controls. A new build may already load current CMS content.
3. POST the allowlisted article tag to that environment's refresh route using its server secret. Trigger a content request, wait for rebuilding to complete and reload.
4. Compare expected successes with article pages, category indexes, sitemap and llms. Inspect logs for missing articles; edit Notion and refresh again. An accepted refresh is not editorial acceptance.
5. Validate both a cold collection and a warm refresh. Confirm that empty replacement is acceptable for this workflow rather than silently adding snapshots or all-or-nothing behavior.

## Verification

Source unit tests cover draft-before-strict-conversion, missing metadata, invalid dates, heading normalization, placeholders, unsupported blocks, isolated and all-failed collections, missing production key, discovery projections and article/legal invalidation routing. Additional acceptance cases for an adaptation:

- Ready status split across rich-text spans succeeds; case variants, body-only status and status after the first heading stay unpublished.
- Multiple root pages and nested children preserve order; a nested failure rejects only that article. Exercise cursor termination and your chosen pagination/depth bounds.
- Tables, uneven rows, escaped pipes, nested lists/callouts/toggles and stable external images render meaningfully. Reject expiring file images and unknown Ready blocks.
- Leap-day validation, optional missing/invalid publication date, metadata-only content and heading-only content behave as specified. Code-fenced headings stay unchanged.
- A mixed catalog preserves success order and logs failures without bodies/credentials; zero successes is empty, not a throw or retained old snapshot.
- Missing production key throws before any page fetch. A configured but rejected credential follows the per-article error policy; nonproduction missing key is empty.
- A warm `"max"` refresh may first serve old data, then expose the rebuilt partial/empty set. Test this under the deployed cache handler; a mocked `cacheTag` cannot prove cache behavior.
- Production build uses the real converter against a local CMS fixture with a synthetic key, with no live CMS credentials or calls. Mock coverage limits belong in `nextjs-testing`.

## Checklist

- [ ] Catalog owns route identity/title; CMS owns status, metadata and body.
- [ ] Exact Ready header precedes the first heading; required fields/dates/body validate.
- [ ] Root and nested reads paginate; unsupported Ready blocks and expiring images reject the article.
- [ ] Production key guard sits outside per-article catches; no title-only CI fallback.
- [ ] One remote collection/tag; nulls and individual failures are skipped (P47).
- [ ] Empty replacement and lack of retained per-article snapshots are understood.
- [ ] Generated CMS consumers use the successful subset; authored links are reviewed separately.
- [ ] Article refresh uses stale-while-revalidate; legal expiry remains separately selected.
- [ ] Builds and cache misses can read CMS; dev review follows refresh completion.
- [ ] Logs expose missing candidates without page bodies or secrets; offline builds exercise the converter.
