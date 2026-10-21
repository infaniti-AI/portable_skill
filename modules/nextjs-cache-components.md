---
name: nextjs-cache-components
title: Next.js 16 cache components and tagged invalidation
summary: Turn on cacheComponents, cache public and per-user derived data with tags, and leave cookie-backed mutable reads as Suspense holes. Use this when a Next.js 16 app must prerender a static shell without serving stale per-user detail.
stack: [nextjs]
tags: [cache, rendering, security]
requires: []
related_skills: []
status: stable
updated: 2026-10-21
---

# Next.js 16 cache components and tagged invalidation

Next.js 16.3 with `cacheComponents: true` prerenders a static shell. Cookie and search-param reads become dynamic holes wrapped in `<Suspense>`. `"use cache"` stores the return value of a function until a tag is invalidated or the `cacheLife` profile expires. Route segment config `dynamic`, `revalidate`, and `fetchCache` is not the control. Official Next.js 16 docs replace those exports with `"use cache"`, `cacheLife`, and `connection()`.

`React.cache()` is a different tool. It dedupes a call inside one server render. It does not survive the next request.

## What is cached

| Data | Cache | Tag |
| --- | --- | --- |
| Public catalog from the API, using a service subject | `"use cache"` + profile `forever` | `platform-audiences` |
| Per-user recommendation lists | `"use cache"`, argument is the user id | `audience-recommendations-<userId>`, `recommendations-<userId>` |
| Published legal and SEO bodies | `"use cache"` + `forever` | `legal-content`, `seo-content` |
| Item list, item detail, notes, message pages | Uncached request fetch | None |

Detail stays uncached because a note can be inserted after the save, and a cached document would hide it (P18). The list changes on create and delete. Message pages churn too fast to be worth a year-long entry. The code comments state both choices.

The public catalog and the legal/SEO bodies are shared across users. Recommendation entries are keyed by the user id argument plus a per-user tag. They are derived lists, not the mutable item.

## Configuration

`cacheForeverSeconds` is `31536000` (365 days). `stale`, `revalidate`, and `expire` are the same number. Freshness comes from tags, not from the clock. A missed invalidation can stick for a year, so every writer that changes a cached list calls `updateTag` or `revalidateTag`.

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

export async function cachedPublicCatalog(): Promise<CatalogItem[]> {
  "use cache"
  cacheLife(FOREVER)
  cacheTag("platform-audiences")
  return backendGet(getServiceSubject(), "/api/catalog/public/")
}

export async function cachedRecommendations(userId: string): Promise<Recommendation[]> {
  "use cache"
  cacheLife(FOREVER)
  cacheTag(`recommendations-${userId}`)
  return backendGet(userId, "/api/recommendations/?limit=8")
}
```

`backendGet` signs a short-lived service JWT inside the cached function. The cache stores the JSON. The token is not an argument, so it is not part of the cache key. See `m2m-jwt-service-auth`.

## Identity stays outside the cache

`"use cache"` cannot read `cookies()` or `headers()`. Resolve the user id in the caller, then pass the id in.

```tsx
export async function CatalogLoader() {
  const userId = await getCurrentUserId()
  const [catalog, recommended] = await Promise.all([
    cachedPublicCatalog(),
    cachedRecommendations(userId),
  ])
  return <Catalog catalog={catalog} recommended={recommended} />
}
```

Render that loader under `<Suspense>`. `getCurrentUserId()` touches the session cookie, which is the dynamic hole. The rest of the page can stay in the shell.

A cookie-free Supabase client (`Authorization: Bearer <access token>`) does not call `cookies()`, which is why it can run inside a cache helper without forcing a prerender bailout (`supabase-rls-client-reads`). The token that authorizes it still cannot be a `"use cache"` argument: Next hashes arguments into the cache key, the entry fragments on every refresh, and the token string lands in the key. Obtain the token with `getSession()` in the same request and use the client from a server action. The running-job lookup does that and stays uncached.

## Do not cache a failure or an empty stand-in

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

The fallback lives in the caller, outside `"use cache"`. Putting `return []` in the `catch` inside the cached function stores the empty list for the `forever` profile.

For a CMS body, throw on HTTP errors so a blip is not stored. Return null only for an intentional draft, and filter those out. That absence is cached until the tag is invalidated, so publishing a draft includes a revalidate of `seo-content` or `legal-content`. When the CMS key is unset, the cached function may return a catalog stub so CI can prerender. That stub is also cached; the first deploy with a real key needs the tag expired.

`cacheTag` accepts at most 128 tags, each at most 256 characters. Longer tags are dropped. These tags are short and one per call.

## Invalidation

| Call | Where | Effect |
| --- | --- | --- |
| `updateTag(tag)` | Server action | Expires the tag and refreshes the page that is rendering. Read-your-own-writes. |
| `revalidateTag(tag, "max")` | Server action | Expires for the next visit. Does not re-render the current page. |
| `revalidateTag(tag, { expire: 0 })` | Route handler | Immediate expiry for a webhook-style caller. |

Official Next.js 16 docs: `updateTag` is for server actions. `revalidateTag` without a second argument is deprecated. `"max"` is the stale-while-revalidate profile. `{ expire: 0 }` forces the next read to miss.

Use `updateTag` after a write when the user stays on a page that shows the new list. Use `revalidateTag(..., "max")` from the action that deletes the page currently on screen. `updateTag` would refresh that page and 404.

Per-user tags are `audience-recommendations-<userId>` and `recommendations-<userId>`. Expire both when the vectors behind them change. The public HTTP route cannot expire them.

```ts
import { timingSafeEqual } from "crypto"
import { revalidateTag } from "next/cache"

const ALLOWED = new Set(["platform-audiences", "seo-content", "legal-content"])

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

  let tag = "platform-audiences"
  try {
    const body = (await request.json()) as { tag?: unknown }
    if (typeof body?.tag === "string" && body.tag.length > 0) tag = body.tag
  } catch {
    // Empty or non-JSON body uses the default public tag.
  }
  if (!ALLOWED.has(tag)) return Response.json({ error: "Tag not allowed" }, { status: 400 })

  revalidateTag(tag, { expire: 0 })
  return Response.json({ revalidated: true, tag })
}
```

`REVALIDATE_SECRET` unset is 503, not an open endpoint. The compare is constant-time, including the length mismatch. The allowlist is the three public tags. A caller cannot pass `recommendations-<userId>` or an arbitrary string.

## Suspense holes

Anything that reads the incoming request has to sit under `<Suspense>` so the parent can stay in the static shell. Official docs name `cookies()`, `headers()`, and `useSearchParams()`.

**P01.** `useSearchParams()` in a page or layout opts that whole segment out of the shell. Call it in a small client child, and wrap that child.

```tsx
function SidebarLive() {
  const audienceId = useSearchParams().get("audience")
  return <Sidebar audienceId={audienceId} />
}

export function SidebarFrame() {
  return (
    <Suspense fallback={<Sidebar audienceId={null} />}>
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

A client section that must stay mounted syncs from props in an effect when the id key changes. `updateTag` refreshes the server props; it does not reset `useState` by itself.

## Failure prevention

- **P01.** `useSearchParams()` and awaited `searchParams` live in a child under `<Suspense>`.
- **P18.** Item detail, the item list, notes, and message pages are ordinary fetches. Cached recommendation tags are invalidated when the underlying vectors change. The Realtime path refetches; it does not read a cached detail entry.
- **P19.** No `sessionStorage` list that skips refetch. Fresh server renders get a new client `key`. Delete refetches.
- **P20.** `unstable_rethrow` before a fallback. `connection()` sits outside `try/catch`. No `export const dynamic`.
- Empty results inside `"use cache"` are stored for a year. Throw on upstream HTTP errors. Isolate fallbacks in the caller.
- Cache arguments are user ids and limits. Access tokens and service JWTs stay out of the key.
- `REVALIDATE_SECRET` is required. The route allowlist is only the public tags. Compare with `timingSafeEqual`.
- A CMS draft that later becomes ready is invisible until `seo-content` or `legal-content` is revalidated.

## Tests

- A cached public function calls `cacheTag` with the public tag and does not call `cookies()`.
- A cached per-user function's arguments are the user id and a limit, and the tag contains that id.
- A non-ok API response rejects, and a second call is not served the empty fallback from the cache.
- `unstable_rethrow` is invoked for a prerender signal before the fallback returns. Cover the list, detail, and goal loaders.
- `GET` stream handler calls `connection()` before the `try`.
- Revalidate route: 503 with no secret, 401 on a bad bearer, 400 on a tag outside the allowlist, 200 and `revalidateTag(tag, { expire: 0 })` for each allowed tag, default tag on an empty body.
- Delete uses `revalidateTag(..., "max")` when the current page should not refresh. A write that stays on the page uses `updateTag`.
- Sidebar, subscription, and insights views that read search params are under `<Suspense>`.
- A list client with a new `key` shows the new server page. A delete refresh token refetches.

## Checklist

- [ ] `cacheComponents: true` and a `forever` profile of 31536000 / 31536000 / 31536000.
- [ ] `"use cache"` only on the public catalog, per-user recommendation lists, and legal/SEO bodies.
- [ ] User id is resolved outside the cache. The service JWT is signed inside. No access token in the cache key.
- [ ] Upstream HTTP errors throw inside the cache. `unstable_rethrow` precedes every fallback (P20).
- [ ] `updateTag` for read-your-own-writes. `revalidateTag(tag, "max")` when the current page was just deleted. `revalidateTag(tag, { expire: 0 })` on the secret route.
- [ ] Route allowlist is `platform-audiences`, `seo-content`, `legal-content`. Missing secret is 503.
- [ ] `useSearchParams()` is a Suspense child (P01). `connection()` is outside `try/catch` on request-only routes.
- [ ] `cookies()` (or `connection()`) runs before `new Date()` in a prerendered loader.
- [ ] Item list and detail are uncached (P18). The list client does not restore a `sessionStorage` snapshot (P19).
- [ ] Publishing CMS content revalidates `seo-content` or `legal-content`.
