---
name: content-security-policy
title: Content Security Policy alongside cache components
summary: Send a locked-down CSP and the companion security headers from Next.js, with script-src left on unsafe-inline because the cached shell cannot carry a per-request nonce. Use this on a cacheComponents app that talks to Supabase, Stripe, analytics, and object storage.
stack: [nextjs]
tags: [security, csp, headers]
requires: [nextjs-cache-components]
related_skills: [bootstrap-next-supabase]
status: stable
updated: 2026-10-21
---

# Content Security Policy alongside cache components

`headers()` in `next.config.ts` returns one policy for every path. The value is computed when the server process starts, from environment origins. It cannot see a per-request nonce.

**P21.** Cache components prerender one HTML shell and reuse it. A nonce in that shell would have to match a CSP header that changes every request. Those two constraints disagree, so `script-src` keeps `'unsafe-inline'` and there is no nonce. `style-src` also allows `'unsafe-inline'` because the UI uses inline styles. That exception is separate from the nonce problem. Do not add `'unsafe-eval'`. See `nextjs-cache-components`.

The flag `SECURITY_HEADERS_ENABLED` defaults on. The string `"false"` returns an empty header list for local debugging. Any other value, including unset, sends the headers. Changing the flag means restarting the process, because `next.config.ts` reads it at load.

## Headers

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | Directives below, joined with `"; "`. |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), geolocation=(), microphone=()` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload`, and only when `NODE_ENV` is `production` |

`frame-ancestors 'none'` matches the frame denial. HSTS is omitted outside production so an HTTP dev host is not pinned. `upgrade-insecure-requests` is appended to the policy only in production for the same reason.

`Permissions-Policy` disables camera, geolocation, and microphone. A feature that records audio has to allow `microphone` on that page's origin. The shipping policy does not.

## Origins

Take `URL.origin` and drop anything that does not parse. `origin` strips userinfo, path, and query. A Sentry DSN looks like `https://<public key>@<host>/<project>`. The header gets `https://<host>` only. Putting the raw DSN in `connect-src` would publish the key.

If the media base URL is missing, omit that origin. A hardcoded production host in the fallback would point every environment at the wrong site.

> NOTE: the source substitutes a fixed public media host when `NEXT_PUBLIC_MEDIA_BASE_URL` is empty. Omit the origin instead.

The Supabase websocket origin is the https origin with the scheme flipped to `wss:`. Realtime fails closed without it (`supabase-realtime-postgres-changes`).

```ts
function originFromUrl(value?: string | null): string | null {
  if (!value) return null
  try {
    return new URL(value).origin
  } catch {
    return null
  }
}
```

| Env | Directive | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL`, else `SUPABASE_URL` | `connect-src`, `img-src`, `media-src` | Also the `wss:` twin on `connect-src`. |
| `NEXT_PUBLIC_MEDIA_BASE_URL` | `connect-src`, `img-src`, `media-src` | Public object host, no trailing path. |
| `NEXT_PUBLIC_SENTRY_DSN`, else `SENTRY_DSN` | `connect-src` | Origin only. The SDK is bundled, so `script-src` does not load Sentry from a CDN. |
| `NEXT_PUBLIC_POSTHOG_HOST` | `script-src`, `connect-src` | See the PostHog note below. |

Fixed vendor hosts in the shipping policy:

- `script-src`: `https://js.stripe.com`, `https://*.i.posthog.com`, `https://*.posthog.com`
- `connect-src`: `https://*.r2.cloudflarestorage.com`, `https://api.stripe.com`, `https://*.i.posthog.com`, `https://*.posthog.com`
- `img-src`: `https://*.stripe.com`
- `frame-src`: `https://js.stripe.com`, `https://hooks.stripe.com`

The storage API host (`*.r2.cloudflarestorage.com`) is where a browser PUT of a presigned upload goes. It is a different origin from the public media host used for images and video. Both belong in the policy when both are used. `r2-presigned-uploads` depends on this.

> NOTE: the shipping `script-src` and `connect-src` use the PostHog wildcards and do not read `NEXT_PUBLIC_POSTHOG_HOST`. A custom host is blocked until its origin is added. Append `originFromUrl(process.env.NEXT_PUBLIC_POSTHOG_HOST)` next to the wildcards.

## Policy

```ts
function buildContentSecurityPolicy(isProd: boolean): string {
  const supabase = originFromUrl(process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL)
  const supabaseWs = supabase ? supabase.replace(/^https:/, "wss:") : null
  const media = originFromUrl(process.env.NEXT_PUBLIC_MEDIA_BASE_URL)
  const sentry = originFromUrl(process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN)
  const posthog = originFromUrl(process.env.NEXT_PUBLIC_POSTHOG_HOST)

  const connectSrc = [
    "'self'",
    supabase,
    supabaseWs,
    media,
    "https://*.r2.cloudflarestorage.com",
    "https://api.stripe.com",
    sentry,
    posthog,
    "https://*.i.posthog.com",
    "https://*.posthog.com",
  ].filter(Boolean)

  const imgSrc = ["'self'", "data:", "blob:", media, supabase, "https://*.stripe.com"].filter(Boolean)
  const mediaSrc = ["'self'", "blob:", media, supabase].filter(Boolean)
  const scriptSrc = [
    "'self'",
    "'unsafe-inline'",
    "https://js.stripe.com",
    posthog,
    "https://*.i.posthog.com",
    "https://*.posthog.com",
  ].filter(Boolean)

  const directives = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src ${scriptSrc.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${imgSrc.join(" ")}`,
    "font-src 'self' data:",
    `connect-src ${connectSrc.join(" ")}`,
    "frame-src https://js.stripe.com https://hooks.stripe.com",
    "worker-src 'self' blob:",
    `media-src ${mediaSrc.join(" ")}`,
  ]
  if (isProd) directives.push("upgrade-insecure-requests")
  return directives.join("; ")
}
```

`'unsafe-inline'` is on `script-src` and `style-src` only. There is no `'unsafe-eval'`. `object-src` is `'none'`. `frame-src` is the two Stripe hosts, not `'self'`, so the app cannot be framed and cannot frame arbitrary origins. `worker-src` allows a blob worker. Fonts are same-origin plus `data:`.

`style-src` allows inline styles because the UI relies on them. That exception is independent of the script nonce problem.

Wire it next to `cacheComponents`:

```ts
const securityHeadersEnabled = process.env.SECURITY_HEADERS_ENABLED !== "false"
const isProd = process.env.NODE_ENV === "production"

const securityHeaders = [
  { key: "Content-Security-Policy", value: buildContentSecurityPolicy(isProd) },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=()" },
  ...(isProd
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" }]
    : []),
]

const nextConfig = {
  cacheComponents: true,
  async headers() {
    if (!securityHeadersEnabled) return []
    return [{ source: "/:path*", headers: securityHeaders }]
  },
}
```

A config wrapper (error reporting, for example) must export this object rather than replace `headers()`.

## Failure prevention

- **P21.** Keep `'unsafe-inline'` on `script-src` (the cached shell cannot carry a nonce) and on `style-src` (inline styles). Do not add a nonce or `'unsafe-eval'`. Leave `default-src`, `object-src`, `frame-ancestors`, `frame-src`, and `connect-src` locked to known origins.
- The header is on unless `SECURITY_HEADERS_ENABLED` is the string `false`.
- HSTS and `upgrade-insecure-requests` are production-only.
- Build every env origin with `URL.origin`. A failed parse contributes nothing. A DSN contributes its host, not the key in the userinfo.
- Omit the media origin when `NEXT_PUBLIC_MEDIA_BASE_URL` is unset.
- `connect-src` includes `https` and `wss` for the Supabase project. Realtime and the cookie client's Auth calls both use that host.
- Presigned uploads need the storage API host on `connect-src`. Images served from the public media host need that host on `img-src` and `media-src`.
- Add the origin of `NEXT_PUBLIC_POSTHOG_HOST` when it is not covered by the PostHog wildcards.
- Adding a new browser integration means editing this function. A third-party script that is not listed fails closed, which is the point of the policy.
- `microphone=()` blocks speech capture until the policy is updated on purpose.

## Tests

Assert against the header value, not a live browser, unless a browser is already in the suite.

- With the flag unset, `/:path*` returns `Content-Security-Policy`, `nosniff`, `DENY`, the referrer policy, and the permissions policy.
- `SECURITY_HEADERS_ENABLED=false` returns no security headers.
- `script-src` and `style-src` contain `'unsafe-inline'` and do not contain a nonce. Neither contains `'unsafe-eval'`.
- `connect-src` contains the Supabase `https` origin and the matching `wss` origin.
- A DSN `https://public@ingest.example.com/1` contributes `https://ingest.example.com` and not `public`.
- An unset media URL contributes no media origin.
- `NODE_ENV=production` adds HSTS and `upgrade-insecure-requests`. `NODE_ENV=development` adds neither.
- `frame-src` is only `https://js.stripe.com` and `https://hooks.stripe.com`.
- A custom PostHog origin is present when `NEXT_PUBLIC_POSTHOG_HOST` is set.

## Checklist

- [ ] `cacheComponents` stays on. `script-src` and `style-src` use `'unsafe-inline'` with no nonce and no `'unsafe-eval'` (P21).
- [ ] `SECURITY_HEADERS_ENABLED=false` is the only off switch. Default is on.
- [ ] HSTS `max-age=63072000; includeSubDomains; preload` and `upgrade-insecure-requests` are production-only.
- [ ] Origins come from `URL.origin`. DSN userinfo is not in the header. Missing media URL omits the origin.
- [ ] `connect-src` has Supabase `https` and `wss`, the storage API host, Stripe API, Sentry origin, and PostHog.
- [ ] `img-src` and `media-src` include the public media origin. `frame-src` is Stripe only. `frame-ancestors` is `'none'`.
- [ ] `NEXT_PUBLIC_POSTHOG_HOST` is added when it falls outside the wildcards.
- [ ] `microphone=()` is intentional until a capture feature updates it.
- [ ] The config wrapper still calls this `headers()` function.
