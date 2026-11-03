---
name: nextjs-cache-components
title: Next.js 16 cache components and tagged invalidation
summary: Turn on cacheComponents, store shared and derived reads in the remote cache with tags, and leave cookie-backed mutable reads as Suspense holes. Use this when a Next.js 16 app must prerender a static shell without serving stale per-user detail.
stack: [nextjs]
tags: [cache, rendering, security]
requires: []
related_skills: [bootstrap-next-supabase]
status: stable
updated: 2026-11-03
---

# Next.js 16 cache components and tagged invalidation

Next.js 16 with `cacheComponents: true` prerenders a static shell. Cookie and search-param reads become dynamic holes wrapped in `<Suspense>`. A `"use cache"` directive stores the return value of an async function until a tag is invalidated or the `cacheLife` profile expires. Route segment config `dynamic`, `revalidate`, and `fetchCache` is not the control. Official Next.js 16 docs replace those exports with `"use cache"`, `cacheLife`, and `connection()`.

`React.cache()` is a different tool. It dedupes a call inside one server render. It does not survive the next request.

The app on disk is Next.js 16.3. The handler names below match the current Next.js docs (`use cache` versus `use cache: remote`, `revalidateTag` profile `"max"`, `updateTag`, and `{ expire: 0 }`).

## What may be cached

Three kinds of read may use `"use cache: remote"` plus `cacheLife` and `cacheTag`.

- **Shared published content.** Many requests need the same body. One function, one short tag, no user id. A draft is an intentional miss: return null and filter it out. That absence is cached too, so publishing includes revalidation of the shared tag. Builds, cache misses and expiry can also load current content; a long lifetime is not a manual-only publication lock.
- **Derived lists.** The result is expensive, and it changes when an upstream vector or catalog changes, not on every create or delete of the source item. Pass the user id and every limit that changes the payload as arguments. Put the user id in the tag so one user's write does not expire every user.
- **A public catalog that is identical for every caller.** Fetch it with a service subject inside the cached function. The token is not an argument, so it is not part of the key. See `m2m-jwt-service-auth`.

**P39.** Plain `"use cache"` (no handler) is the in-memory cache of one server process. Official docs: that entry is not shared with the next instance, so a later request often misses and calls the origin again. Shared entries use `"use cache: remote"`. The remote handler is shared across instances. It costs a network lookup. The reference config does not set `cacheHandlers`; it relies on the host's built-in `remote` handler. A host that does not provide one needs `cacheHandlers.remote`.

> NOTE: unverified whether a non-platform host errors or silently falls back when `cacheHandlers.remote` is unset. The directive name in the repo and in the docs is `remote`.

`"use cache: private"` is a different handler. It may read cookies and is scoped to one browser. It does not fill the shared server cache. Use it only when the cached function must see request headers. A derived list keyed by a user id argument stays on `remote`.

These reads stay ordinary request fetches. Do not put them in `"use cache"` or `"use cache: remote"`.

- Item detail. A note can be inserted after the save, and a cached document would hide it (P18).
- The item list. It changes on create and delete.
- Message pages. They churn too fast for a year-long entry.
- A lookup of a job that is still running.
- Any function that calls `cookies()` or `headers()`.

## How to choose a tag and a key

Arguments and captured serializable values are part of the cache key. Two `limit` values are two entries even when they share a tag. Pass every runtime input that changes the result as an argument. The key also includes build/deployment and function identity, so a code change or new build can create a new entry; do not assume cached content survives deployment unchanged. See the [official cache-key rules](https://nextjs.org/docs/app/api-reference/directives/use-cache#cache-keys).

| Invalidation set | Tag shape | Who expires it |
| --- | --- | --- |
| One published library | a short stable name, `published-pages` | the secret revalidate route |
| One user's derived lists | a prefix plus the user id, `derived-${userId}` | the server action that knows the user id |

`cacheTag` accepts at most 128 tags, each at most 256 characters. Longer tags are dropped. Use one tag per call. Do not put an email, an access token, or a raw session id in the tag. The user id is already an argument; repeating it in the tag is what lets you expire that user's entries without knowing every limit.

The public revalidate route allowlists only the shared tags. A caller cannot pass `derived-${userId}` or an arbitrary string. Per-user tags are expired from the server action.

## Configuration

`cacheForeverSeconds` is `31536000` (365 days). `stale`, `revalidate`, and `expire` are the same number. Freshness comes from tags, not from the clock. A missed invalidation can stick for a year, so every writer that changes a cached value calls `revalidateTag` or `updateTag`.

```ts
const nextConfig = {
  cacheComponents: true,
  cacheLife: {
    forever: {
      stale: 31_536_000,
      revalidate: 31_536_000,
      expire: 31_536_000,
    },
  },
}
```

Inside the cached function the app calls `cacheLife("forever")`. The TypeScript overload list does not include custom profile names, so that call is annotated. `cacheLife({ stale, revalidate, expire })` with the same three numbers typechecks and matches the profile.

```ts
import { cacheLife, cacheTag } from "next/cache"

const FOREVER = { stale: 31_536_000, revalidate: 31_536_000, expire: 31_536_000 }

export async function cachedPublishedLibrary(): Promise<Page[]> {
  "use cache: remote"
  cacheLife(FOREVER)
  cacheTag("published-pages")
  return loadPublishedPages()
}

export async function cachedDerivedList(userId: string, limit: number): Promise<Item[]> {
  "use cache: remote"
  cacheLife(FOREVER)
  cacheTag(`derived-${userId}`)
  return backendGet(userId, `/api/derived/?limit=${limit}`)
}
```

`backendGet` signs a short-lived service JWT inside the cached function. The cache stores the JSON. The token is not an argument, so it is not part of the cache key.

## Identity stays outside the cache

`"use cache"` and `"use cache: remote"` cannot read `cookies()` or `headers()`. Resolve the user id in the caller, then pass the id in.

```tsx
export async function DerivedLoader() {
  const userId = await getCurrentUserId()
  const items = await cachedDerivedList(userId, 8)
  return <DerivedList items={items} />
}
```

Render that loader under `<Suspense>`. `getCurrentUserId()` touches the session cookie, which is the dynamic hole. The rest of the page can stay in the shell.

A cookie-free data client (`Authorization: Bearer <access token>`) does not call `cookies()`, which is why it can run inside a cache helper without forcing a prerender bailout (`supabase-rls-client-reads`). The token that authorizes it still cannot be a cache argument: Next hashes arguments into the cache key, the entry fragments on every refresh, and the token string lands in the key. Obtain the token with `getSession()` in the same request and use the client from a server action. The running-job lookup does that and stays uncached.

## Choose the failure policy at the cache boundary

`backendGet` throws when the response is not ok. A throw is not stored as a successful empty list. Callers that want an isolated section catch after rethrowing Next's prerender signal:

```ts
import { unstable_rethrow } from "next/navigation"

async function safe<T>(promise: Promise<T>, fallback: T): Promise<T> {
  try {
    return await promise
  } catch (error) {
    unstable_rethrow(error)
    console.error("section load failed:", error)
    return fallback
  }
}
```

The fallback lives in the caller, outside the cache directive. Putting `return []` in the `catch` inside the cached function stores the empty list for the `forever` profile.

Keep generic transport failures observable rather than converting them into cached empty stand-ins. An editorial workflow can deliberately choose a different policy: the reference article collection logs and skips individual unreadable or invalid articles, then successfully caches the remaining subset. Every article failing can therefore replace a populated collection with `[]` (P47). A warm stale entry is not protection against that successful empty replacement. The parsing, publication contract and limits of this choice belong in `notion-cms-publication`.

The article collection rejects a missing `NOTION_API_KEY` in production before per-article isolation; outside production a missing key returns an empty collection. A configured but rejected key can cause every article read to be skipped. CI production builds use a local CMS endpoint (`NOTION_API_BASE_URL`) and synthetic key, exercising the real loader rather than publishing catalog stubs; see `nextjs-testing`. The separate legal loader retains its own version-selection/error policy and returns an empty library without a key. Do not infer that it inherits the article guard or skip behavior.

## Invalidation

`updateTag` is server-action only. It expires the tag and the next read waits for fresh data. The action response also re-renders the current route (read-your-own-writes). `revalidateTag` without a second argument is deprecated and immediately expires data; it does not acquire `updateTag`'s action-only contract. `"max"` is stale-while-revalidate: the next visit that reads the tag can serve the stale value while a fresh fetch runs. `{ expire: 0 }` makes that next read block for fresh data. The invalidation call itself does not rebuild the collection. See [official revalidateTag semantics](https://nextjs.org/docs/app/api-reference/functions/revalidateTag). A cookie write in the same action still re-renders the current route, even when the tag call was `revalidateTag`.

| Call | Where | Effect |
| --- | --- | --- |
| `updateTag(tag)` | Server action | Expires the tag and refreshes the route that called it. |
| `revalidateTag(tag, "max")` | Server action or route handler | Marks the tag stale; subsequent reads can serve stale while rebuilding. Does not itself re-render the current route (P37). |
| `revalidateTag(tag, { expire: 0 })` | Route handler | Immediate expiry for a webhook-style caller. |

**P37.** Use `revalidateTag(tag, "max")` from a write whose screen does not render the cached lists, and from a delete of the page currently on screen. `updateTag` would re-render that page: a deleted item is requested again and 404s, and a save re-renders a layout that does not show the cached lists. Keep `updateTag` for a screen that must show its own write before the response returns. The reference mutations that touch a derived-list tag all use `revalidateTag(..., "max")`. None call `updateTag`.

```ts
import { timingSafeEqual } from "crypto"
import { revalidateTag } from "next/cache"

const ALLOWED = new Set(["published-pages", "policy-library"])

function secretsEqual(provided: string, expected: string): boolean {
  const providedBuf = Buffer.from(provided)
  const expectedBuf = Buffer.from(expected)
  if (providedBuf.length !== expectedBuf.length) {
    timingSafeEqual(expectedBuf, expectedBuf)
    return false
  }
  return timingSafeEqual(providedBuf, expectedBuf)
}

export async function POST(request: Request) {
  const secret = process.env.REVALIDATE_SECRET || ""
  if (!secret) return Response.json({ error: "Revalidate is not configured" }, { status: 503 })

  const header = request.headers.get("authorization")
  const token = header?.startsWith("Bearer ") ? header.slice("Bearer ".length).trim() : ""
  if (!token || !secretsEqual(token, secret)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 })
  }

  let tag = "published-pages"
  try {
    const body = (await request.json()) as { tag?: unknown }
    if (typeof body?.tag === "string" && body.tag.length > 0) tag = body.tag
  } catch {
    // Empty or non-JSON body uses the default shared tag.
  }
  if (!ALLOWED.has(tag)) return Response.json({ error: "Tag not allowed" }, { status: 400 })

  revalidateTag(tag, tag === "published-pages" ? "max" : { expire: 0 })
  return Response.json({ revalidated: true, tag })
}
```

`REVALIDATE_SECRET` unset is 503, not an open endpoint. Equal-length buffers use `timingSafeEqual`; the mismatch branch rejects after a dummy comparison, without claiming constant-time execution of the whole handler. The allowlist is the shared tags only. Article refresh selects `"max"`; the separate legal library selects `{ expire: 0 }`. A 200 acknowledges invalidation, not completed publication or guaranteed fresh content on the next article response.

## Suspense holes

Anything that reads the incoming request has to sit under `<Suspense>` so the parent can stay in the static shell. Official docs name `cookies()`, `headers()`, and `useSearchParams()`.

**P01.** `useSearchParams()` in a page or layout opts that whole segment out of the shell. Call it in a small client child, and wrap that child.

```tsx
function SidebarLive() {
  const itemId = useSearchParams().get("item")
  return <Sidebar itemId={itemId} />
}

export function SidebarFrame() {
  return (
    <Suspense fallback={<Sidebar itemId={null} />}>
      <SidebarLive />
    </Suspense>
  )
}
```

The same boundary wraps a client view whose job is to read `session_id` or a tab query, and a server child that awaits the page `searchParams` promise. The fallback is the shell the prerender can paint.

**P20.** `export const dynamic` is rejected when cache components are on. A helper that catches every error and returns `[]` also swallows the prerender bailout from `cookies()` or `connection()`, and the build fails later with a worse message. Call `unstable_rethrow(error)` before any fallback. In a route handler, `await connection()` is the first line, outside `try/catch`, so the bailout is not logged as a request failure.

```ts
export async function GET(request: Request) {
  await connection()
  try {
    const runId = new URL(request.url).searchParams.get("runId")
    if (!runId) return Response.json({ error: "runId is required" }, { status: 400 })
    return streamRun(runId)
  } catch (error) {
    return Response.json({ error: "stream failed" }, { status: 500 })
  }
}
```

`new Date()` during prerender needs an uncached request read earlier in that component. The shell loader awaits `cookies()` before it formats a timestamp, so the subtree defers to request time instead of baking the build clock into the HTML.

## Client state that outlives the server payload

**P19.** The list is not server-cached. The client component still seeds `useState` from the server props once. With cache components, the router can keep a recently visited segment mounted and show it again without remounting. That instance would keep the previous list and skip the new server payload.

The list loader passes `key={crypto.randomUUID()}` on every server render. A new payload mounts a new instance. A back/forward restore of the same payload keeps its key and its state. Delete bumps a refresh token; an effect on that token refetches the current filter. A filter change refetches. The default filter uses the server page and does not refetch on mount.

Do not also keep a `sessionStorage` snapshot that returns early when the filter key matches. That snapshot survived a create and showed the previous page. If any client snapshot exists, clear it on create, update, and delete before navigating back.

A client section that must stay mounted syncs from props in an effect when the id key changes. `updateTag` refreshes the server props; it does not reset `useState` by itself. `revalidateTag(..., "max")` does not refresh those props on the current route at all (P37).

## Failure prevention

- **P01.** `useSearchParams()` and awaited `searchParams` live in a child under `<Suspense>`.
- **P18.** Item detail, the item list, notes, and message pages are ordinary fetches. Derived-list tags are invalidated when the underlying vectors change. The Realtime path refetches; it does not read a cached detail entry.
- **P19.** No `sessionStorage` list that skips refetch. Fresh server renders get a new client `key`. Delete refetches.
- **P20.** `unstable_rethrow` before a fallback. `connection()` sits outside `try/catch`. No `export const dynamic`.
- **P37.** A write that must not re-render the current route calls `revalidateTag(tag, "max")`. `updateTag` is only for a screen that displays its own write. Deleting the page on screen with `updateTag` refetches that page and 404s.
- **P39.** Shared entries use `"use cache: remote"`, `cacheLife`, and `cacheTag`. Plain `"use cache"` is one process's LRU. Arguments are part of the key, so a limit that changes the payload is an argument, not a literal.
- Successful empty results receive the configured long lifetime. Generic transport errors throw; caller fallbacks remain outside the cache. Article publication explicitly skips per-article failures and can cache partial or empty results (P47).
- Missing production article credentials throw; offline production builds need the CMS mock and synthetic key. Legal loading has a separate policy.
- Cache arguments are user ids and limits. Access tokens and service JWTs stay out of the key.
- `REVALIDATE_SECRET` is required. The route allowlist is only the shared tags. Compare with `timingSafeEqual`.
- Revalidate after editorial changes; builds, cache misses and expiry can also execute the CMS loader.

## Tests

- A shared cached function uses `"use cache: remote"`, calls `cacheTag` with the shared tag, and does not call `cookies()`.
- A per-user function's arguments are the user id and a limit. The tag contains that id. Two limits are two keys.
- A non-ok API response rejects, and a second call is not served the empty fallback from the cache.
- `unstable_rethrow` is invoked for a prerender signal before the fallback returns. Cover the list, detail, and related-record loaders.
- `GET` stream handler calls `connection()` before the `try`.
- Revalidate route: 503 with no secret, 401 on a bad bearer, 400 on a tag outside the allowlist; article tag uses `"max"`, legal tag uses `{ expire: 0 }`. Empty/non-JSON body selects the default shared tag. A 200 does not assert a rebuilt collection.
- Publication boundary: missing production key throws before loading; configured key with mixed/all-failed article reads yields partial/empty success. A cold production CI build reads the mock without bypassing the guard.
- A delete or a save whose screen does not show the cached lists uses `revalidateTag(..., "max")`. A write that stays on a page that renders the new value uses `updateTag`.
- Views that read search params are under `<Suspense>`.
- A list client with a new `key` shows the new server page. A delete refresh token refetches.

## Checklist

- [ ] `cacheComponents: true` and a `forever` profile of 31536000 / 31536000 / 31536000.
- [ ] Shared published content and derived lists use `"use cache: remote"` (P39). Plain `"use cache"` is not the shared store.
- [ ] User id and every limit are arguments. The service JWT is signed inside. No access token in the cache key.
- [ ] Generic transport errors throw; publication's deliberate per-article skip and possible empty replacement are understood (P47). `unstable_rethrow` precedes caller fallbacks (P20).
- [ ] `updateTag` only when the current route must show the write (P37). Secret route selects `"max"` for published articles and `{ expire: 0 }` for the legal library.
- [ ] Route allowlist is the shared tags only. Missing secret is 503.
- [ ] `useSearchParams()` is a Suspense child (P01). `connection()` is outside `try/catch` on request-only routes.
- [ ] `cookies()` (or `connection()`) runs before `new Date()` in a prerendered loader.
- [ ] Item list and detail are uncached (P18). The list client does not restore a `sessionStorage` snapshot (P19).
- [ ] Publishing CMS content revalidates the shared tag; deploy/cache misses can load CMS too. Production CI uses a CMS mock and synthetic key.
