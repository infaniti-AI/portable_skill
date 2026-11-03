---
name: nextjs-seo-geo
title: Next.js public content metadata and discovery
summary: Build canonical metadata, safe structured data, readable articles and publication-based discovery with separate beta indexing controls. Use this when a Next.js public site serves both people and search or AI crawlers.
stack: [nextjs, react, typescript]
tags: [seo, geo, metadata, sitemap, robots, indexing, publication]
requires: []
related_skills: []
status: stable
updated: 2026-11-03
---

# Next.js public content metadata and discovery

Make public content understandable in rendered HTML, then describe the same facts in metadata and discovery endpoints. Keep publication, crawl permission and indexing policy separate. A registered CMS page can still be unpublished; a crawlable page can still carry `noindex`.

## Architecture and data flow

```text
canonical origin + shared social asset + page facts
    -> page metadata + entity URLs
validated published collection
    -> article lookup / metadata / static params
    -> category indexes / selected homepage cards / related CMS links
    -> sitemap / llms article links
article body -> rendered headings / contents / visible FAQ / matching JSON-LD
deployment hostname -> layout robots metadata + response indexing header
```

The publication collection contains only successfully loaded, validated articles. For a Notion source, load `notion-cms-publication` through `search-modules`; its parser, readiness gate and cache policy belong there. This module consumes the resulting records.

The reference uses one collection loader across these consumers. This reduces disagreement about publication; it is not a transaction across requests during a background refresh. Already mounted browser views and responses served during revalidation can still show an older collection.

## Configuration and record shape

| Input | Meaning |
| --- | --- |
| `NEXT_PUBLIC_SITE_URL` | Deployment's configured site origin, used for canonical URLs and hostname classification. Public, never a credential. |
| `SECURITY_HEADERS_ENABLED` | Controls the security-header bundle. It must not disable beta indexing headers. |
| Shared social asset | Stable public URL, width, height and useful alternative text. |
| Published article | Stable slug/path, title, description, author, category, authored update date, optional publication date, body and related paths. |
| Static public paths | Independently available public routes. CMS-backed pages do not belong here. |

The reference trims a trailing slash and falls back to its production origin when the site env is empty. Its beta decision compares the parsed configured hostname with one exact review hostname. It does not inspect each incoming request's Host header. Set the environment correctly before building each deployment; a review alias serving an artifact built with the production origin is not automatically classified as beta.

For an adaptation, validate a configured origin's scheme, credentials, path and query at startup. Decide the fallback deliberately and test aliases separately. A hostname substring check can accept an unrelated domain.

## Canonical and social metadata (P49)

Set `metadataBase` in the public layout. Give each public page its own canonical path, title and description. Keep a home canonical out of the shared layout so sign-in and utility routes cannot inherit it. Auth pages use their own title and robots policy.

Next.js merges metadata shallowly: a child's `openGraph` object replaces the parent's object, including images. Generate each page's social fields together and explicitly include the shared asset. The same image can accompany different page titles and descriptions. Check file-based metadata too, because it has higher priority. These are documented [Next.js metadata rules](https://nextjs.org/docs/app/api-reference/functions/generate-metadata#merging).

This generic helper assumes a validated origin and a site-owned absolute path:

```ts
import type { Metadata } from "next"

export function pageMetadata(
  origin: string,
  page: { path: string; title: string; description: string; article?: boolean },
): Metadata {
  const image = {
    url: new URL("/social.png", origin).href,
    width: 1200,
    height: 630,
    alt: "Public application overview",
  }
  return {
    title: page.title,
    description: page.description,
    alternates: { canonical: page.path },
    openGraph: {
      type: page.article ? "article" : "website",
      title: page.title,
      description: page.description,
      url: new URL(page.path, origin).href,
      images: [image],
    },
    twitter: {
      card: "summary_large_image",
      title: page.title,
      description: page.description,
      images: [image],
    },
  }
}
```

Dynamic article metadata and the page body must resolve the same published lookup. A missing article reaches `notFound()`; catalog registration alone is not a body fallback. Static params also derive from the published set. The reference supplies a synthetic unpublished slug when the set is empty to satisfy its Cache Components build; that slug still returns not-found and never enters discovery.

## Readable document structure

- Let the page shell own the H1/title. Normalize authored H1 lines to H2 outside fenced code; preserve code examples. Use H2 for sections and H3 for their subsections.
- Generate deterministic H2/H3 IDs with Unicode letters/numbers, normalized spacing and duplicate suffixes. Use the same text normalization and duplicate counter for the contents and the rendered heading AST.
- The reference shows a contents list only when there are more than three H2 sections. The threshold is a presentation choice. Test emphasis, links, punctuation, duplicate headings and code fences so each contents link resolves to its rendered target.
- Render breadcrumb labels and `BreadcrumbList` from the same entries. A display label may change while an established URL/category key stays stable.
- Keep FAQ answers in server-rendered HTML. Native `details`/`summary` can collapse them visually without requiring JavaScript to insert the answers.
- Render Markdown through a constrained renderer. Tables need horizontal scrolling within their container; stable images need useful alt text. Do not add raw HTML execution to make an unsupported block appear.

## Structured data from visible facts

Choose types that describe the page: site and organization identities, a collection with its listed items, an article with its actual byline, or a breadcrumb trail. Keep stable `@id` URLs for entities and link the publisher/person consistently. An author profile URL must identify the person rather than an unrelated company account.

Use the visible FAQ as the source of FAQ JSON-LD. Use authored dates for `dateModified` and, only when known, `datePublished`. Do not invent ratings, reviews, offers or biographies to add more schema layers. Structured data should represent the reader's page according to [Google's structured-data policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies).

Serialize a native `application/ld+json` script with `<` escaped. Plain `JSON.stringify` alone does not protect an HTML script element from a CMS string containing a closing script tag. [Next.js recommends this escape](https://nextjs.org/docs/app/guides/json-ld):

```ts
export function jsonLdHtml(data: Record<string, unknown>): string {
  return JSON.stringify(data).replace(/</g, "\\u003c")
}
```

Pass that result to the script element's `dangerouslySetInnerHTML`. This protects that serialization boundary; it does not validate schema meaning or authorize raw HTML elsewhere. Validate the rendered data with a schema validator, then inspect its agreement with the visible page. Schema validity does not guarantee a rich result.

## Discovery is a publication projection

Build the sitemap from independently available static public paths plus published CMS paths. Deduplicate by canonical path. A CMS-backed company page is conditional too; putting it in the static list can advertise a 404 after withdrawal.

Use valid editorial update dates for CMS `lastModified`. Leave the field absent on a static page when no meaningful maintained date exists. Do not use `new Date()`, file modification time or the deployment clock as article recency. [Google's sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) ties `lastmod` to significant page changes, not routine rebuilds. Authored legal acceptance versions remain a separate workflow; load `versioned-legal-consent` when needed.

Generate category indexes, selected homepage cards and related CMS links by intersecting their editorial selections with the published collection. Retain only independently available static links outside that collection. The reference's grouped indexes use explicit slug lists: a newly published article may require a group/featured selection edit before appearing in those particular cards. Do not assume every published record appears in every display group.

Generate `/llms.txt` as plain text from public facts and the same published collection. Conditional CMS pages stay conditional there as well. The reference returns `text/plain; charset=utf-8` and requires HTTP cache revalidation; this header does not itself refresh the underlying collection. Generated listing filters do not rewrite inline Markdown, navigation or author-profile links; audit those separately for withdrawn targets.

Treat GEO as discoverable, clear, attributable content. Google states that its AI search features need no special AI text file or schema and that inclusion is not guaranteed. `/llms.txt`, schema and crawler allowances therefore carry no ranking or citation promise. See [Google's AI features guidance](https://developers.google.com/search/docs/appearance/ai-features).

## Crawl and indexing controls (P48)

Keep public sign-in and invitation UI crawlable while setting `robots: { index: false, follow: true }` on those pages. `robots.txt` disallows private application, API and auth-callback paths separately. It is not access control. Session authorization still protects private data.

On beta deployments, apply both root public-layout robots metadata and `X-Robots-Tag: noindex` across all matched routes. Compose that response header independently of the security-header toggle. The reference leaves robots crawlable so supporting crawlers can read `noindex`; it does not replace the beta robots file with a blanket disallow.

For example, this composition uses a reserved example hostname, never a URL substring:

```ts
type Header = { key: string; value: string }

export function indexingHeaders(siteUrl: string, securityHeaders: Header[]): Header[] {
  const beta = new URL(siteUrl).hostname === "beta.example.com"
  return [
    ...securityHeaders,
    ...(beta ? [{ key: "X-Robots-Tag", value: "noindex" }] : []),
  ]
}
```

Pass an empty security-header list when `SECURITY_HEADERS_ENABLED` is false; beta still receives its indexing header. Verify the deployed edge preserves it on HTML and non-HTML routes. Pages blocked by robots cannot expose their `noindex` to Googlebot; the URL may still be discovered from links. See [Google's noindex documentation](https://developers.google.com/search/docs/crawling-indexing/block-indexing).

Specific AI crawler names can have different search, training and user-fetch roles. Check each operator's official policy before adapting the allowlist. Noindex and robots directives do not make a public review environment private or ensure every crawler complies.

## Setup order

1. Configure the origin per deployment and a stable shared social asset; verify hostname classification before building.
2. Define independent static routes and the validated publication loader. Give CMS-backed special pages deterministic canonical paths.
3. Install layout `metadataBase`, entity IDs and defaults without a home canonical. Add per-page metadata including explicit social images.
4. Render article structure, byline, dates, breadcrumbs and FAQ HTML. Add escaped JSON-LD from the same facts.
5. Connect indexes, cards, related links, sitemap and llms to published records. Check curated groups and authored links separately.
6. Add auth noindex, private robots rules and independent beta response headers. Test with security headers both on and off.
7. Build against a controlled CMS fixture, inspect generated output, then review the deployed beta with its actual headers. The offline mock setup belongs to `nextjs-testing`.

## Failure prevention and verification

**P49:** A child metadata object can silently drop the inherited image or keep unrelated social text. Assert title, description, canonical, Open Graph URL/type/images and Twitter title/images on a representative article and static page. Check the final HTML, not just the helper object.

**P48:** A correct CSP toggle must not remove beta noindex. Exercise exact beta hostname, case normalization, trailing slash and port, production, local and lookalike-host cases. Re-import config when testing import-time env flags. Confirm auth UI is crawlable and emits noindex while public production pages remain indexable.

Publication tests should remove an article and a CMS-backed special page, then check lookup/not-found, indexes, related CMS links, sitemap and llms. Static routes must remain. Verify no build-clock dates or synthetic static-param slugs escape into discovery.

Rendering checks should round-trip malicious script strings through JSON parsing, leave only one intended script element, retain FAQ answers without JavaScript, and resolve duplicate/formatted heading anchors. Review narrow-screen tables, images and navigation for overflow. Source unit tests cover shared images, date omission, collection/sitemap boundaries, basic heading duplicates, script escaping, FAQ HTML and hostname classification; the wider rendered-output cases are adaptation acceptance checks.

## Checklist

- [ ] Canonical origin matches the deployment; each public page owns its canonical.
- [ ] Open Graph and Twitter include page text and explicit shared image fields (P49).
- [ ] JSON-LD is escaped and describes visible facts with stable entity IDs.
- [ ] Contents IDs match rendered headings; FAQ answers exist in initial HTML.
- [ ] Published collection drives CMS discovery; curated selections and authored links are reviewed.
- [ ] Dates come from editorial metadata, never the deployment clock.
- [ ] Auth UI exposes noindex; private routes retain authorization.
- [ ] Beta noindex matches the parsed configured hostname and survives disabled security headers (P48).
- [ ] Sitemap and llms exclude unpublished CMS pages; no ranking or citation guarantee is claimed.
