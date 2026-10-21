---
name: bootstrap-next-supabase
description: Bootstrap a Next.js app with Supabase magic-link sessions, row-level security for browser reads, short-lived API tokens, cache components, a CSP, scrubbed Sentry, consent-gated PostHog, and versioned legal consent. Use when starting the signed-in shell of a product, before billing, uploads, or background jobs.
---

# Bootstrap a Next.js app on Supabase

This skill orders the signed-in shell. It does not replace the modules. Implement each step from the module, then apply the cross-checks below.

Billing is `add-stripe-billing`. Queues and the daily timer are `background-jobs`. Invite redemption is `invite-code-gate`. Object uploads are `r2-presigned-uploads`. Load those only when that work is in scope.

## Load the modules

Follow the `search-modules` skill. From the catalog root:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" auth rls jwt cache csp sentry posthog consent
```

Open these files in this order even when the ranking differs. For each file, open every name in `requires` and repeat until no new name appears.

1. `supabase-magic-link-auth`
2. `supabase-rls-client-reads`
3. `m2m-jwt-service-auth`
4. `nextjs-cache-components`
5. `content-security-policy`
6. `sentry-nextjs`
7. `posthog-consent-analytics`
8. `versioned-legal-consent`

Follow each module's architecture, env names, schema, setup, failure prevention, and checklist. Stop at a line marked `> NOTE: unverified` and confirm it in current vendor docs before relying on it. Use the target project's table and route names. Keep catalog examples generic (`Item`, `app_user`, `example.com`).

## Build order

Later steps assume the earlier ones exist.

### 1. Session

Implement `supabase-magic-link-auth` first. The browser does not persist the Supabase session. `@supabase/ssr` writes httpOnly cookies. Identity checks call `getUser()`. `getSession()` only copies an access token into a data client.

Set `NEXT_PUBLIC_SUPABASE_URL` and `SUPABASE_URL` to the same project, and the same for the anon keys. A host mismatch breaks the cookie name (`sb-<first-label>-auth-token`). Put `/auth/callback` on the Auth redirect allow list for every origin, including local. Build `emailRedirectTo` from `NEXT_PUBLIC_SITE_URL`.

The callback exchanges the code and redirects to one fixed app path. The marketing page decides "maybe signed in" from a local cookie parse plus a non-httpOnly hint cookie. It does not call Auth. `proxy.ts` calls `getUser()` on the app shell.

The sign-in form receives its initial error from the server. The first client render matches the server HTML. Read `window` only inside the handler that assigns the OAuth URL (P25).

### 2. Database roles

Implement `supabase-rls-client-reads` before any browser `select`. The `authenticated` role selects its own rows. The API login role is `NOBYPASSRLS` with a policy of `USING (TRUE)`, so the API must filter every query itself. Apply the schema grants and the `search_path` the module specifies (P10). Keep `SUPABASE_SERVICE_ROLE_KEY` off every `NEXT_PUBLIC_` name and off the cookie client.

### 3. API token

Implement `m2m-jwt-service-auth` once `getUser()` exists. The Next.js server signs a 60-second ES256 JWT whose `sub` is that user id, in the same request. Django sets `request.user_id` from the verified `sub`. Issuer and audience are shared constants, not env vars.

Because the API role is not isolated by RLS, every handler filters on `request.user_id`. A user id in a JSON body or query must match `sub` (P14). On an auth-exempt path `user_id` stays empty. Reject that case before `UUID()` (P13). The service subject is only for handlers that do not parse `sub` as a UUID.

Never put the private key, the public key, or a minted token in a cache key, a log line, or a `NEXT_PUBLIC_` variable.

### 4. Cache

Turn on `cacheComponents` and the `forever` profile from `nextjs-cache-components`. Cache the public catalog (service subject) and the legal and SEO bodies. Cache per-user recommendation lists with the user id as the argument and a per-user tag. Leave item list, item detail, notes, and message pages as uncached Suspense holes (P18). A missed tag can stick for a year, so every writer that changes a cached value calls `updateTag` or `revalidateTag`.

Do not cache a thrown CMS or API failure, and do not cache an empty stand-in for a failed read (P20). Inside `"use cache"`, rethrow with the helper the module names. Cookie and search-param reads stay in Suspense boundaries.

### 5. CSP

Add `content-security-policy` only after `cacheComponents` is on. The header is computed when the process starts. A per-request nonce cannot match a prerendered shell, so `script-src` keeps `'unsafe-inline'` and no nonce (P21). Leave `'unsafe-eval'` out. `script-src` and `style-src` are the only directives with `'unsafe-inline'`.

Build every env origin with `URL.origin`. The Supabase entry contributes its `https` origin and the `wss` twin. A Sentry DSN contributes `https://<host>` only. Include the PostHog and Stripe hosts the module already lists, plus `originFromUrl(NEXT_PUBLIC_POSTHOG_HOST)` when that host sits outside the wildcards. Omit the media origin when `NEXT_PUBLIC_MEDIA_BASE_URL` is unset. HSTS and `upgrade-insecure-requests` are production-only. `SECURITY_HEADERS_ENABLED=false` is a local empty header list. Restart the process after a header change.

### 6. Errors

Initialize Sentry for the browser, Node, and edge from `sentry-nextjs`. `sendDefaultPii` stays false. The scrubber drops bodies, cookies, tokens, and the noise classes the module lists. Export `withSentryConfig(nextConfig, …)` around the same object that sets `cacheComponents` and `headers()`. A wrapper that replaces that object drops the CSP. `SENTRY_AUTH_TOKEN` is build-time only. Source maps use `useRunAfterProductionCompileHook` because Turbopack skips the Webpack injection. Put the DSN origin on `connect-src`, not the raw DSN.

### 7. Analytics

Add `posthog-consent-analytics` after the CSP. The snippet loads only after a 180-day `localStorage` choice and a non-empty `NEXT_PUBLIC_POSTHOG_KEY`. The banner and the snippet start hidden and read storage after mount (P25). Init uses `person_profiles: "identified_only"`, autocapture and pageviews on, session recording off. Do not call `identify`. Do not parse `utm_` parameters. The inline init depends on `'unsafe-inline'` plus the PostHog asset and API hosts (P21). A same-page rejection unmounts the script. Confirm the snippet's opt-out method before calling it. The module marks that method name unverified.

### 8. Legal versions

Implement `versioned-legal-consent` last. It needs the session callback and the `legal-content` cache. The published version is the later Ready `YYYY-MM-DD` child of the privacy page and the terms page. Other policy pages use the same CMS shape and do not move the version.

The sign-in action requires acceptance, writes the version into an httpOnly cookie (1 day), then starts the magic link or OAuth. The callback posts `terms_privacy` and deletes the cookie. A failed post is logged. Sign-in still completes, and the cookie is still deleted. A later publish shows a dialog at most once per local day. It is not a route gate. A repeat seed must not overwrite a newer in-memory accept. Do not catch the CMS error inside `"use cache"` (P20).

## Cross-checks

These fail only when the modules are combined.

- The id in `sub` is the id from `getUser()` in that request. The browser JWT used for a Supabase read is the session access token, not the M2M JWT. The M2M JWT is the `Authorization` header on the app API.
- Browser tables that the signed-in user may see have a SELECT-own-rows policy. Tables the API writes (consent, and later subscriptions) are not inserted by the anon client.
- Cached functions receive a user id where the entry is per user. They do not receive cookies, the access token, or the M2M token.
- `withSentryConfig` wraps the config object that already contains `cacheComponents` and `headers()`.
- PostHog's inline script and the Auth form both render the same tree on the server and the first client paint.
- The legal cookie is set only after the person accepts, and only the callback consumes it. The dialog reads the stored consent row, not that cookie.

## Finish

Walk the checklist at the end of each module above. Then confirm:

- A magic link opened in the same browser lands on the fixed app path. A protected navigation refreshes through `proxy.ts`. The marketing page makes no Auth call.
- An `authenticated` query returns only that user's rows. An API handler that omitted the `user_id` filter would see every row, so the handler includes the filter. A body user id that differs from `sub` is rejected.
- Item detail HTML is absent from the prerendered shell. Invalidating `legal-content` refreshes the legal body.
- The CSP has `'unsafe-inline'` on `script-src`, no nonce, the Supabase `wss` origin, and a Sentry origin with no userinfo.
- A Sentry test event contains no request body and no cookie header.
- With an empty consent key, the PostHog script tag is absent. After accept, it loads.
- A newer Ready legal date shows the dialog once that local day, and a second sign-in records the new version.
