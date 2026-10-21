---
name: sentry-nextjs
title: Sentry on Next.js with scrubbed events
summary: Initialize Sentry on the browser, Node server, and edge runtime, upload source maps from a Turbopack build, and drop request bodies and user content before send. Use this when a Next.js app reports errors without shipping user-written text, tokens, or cookies.
stack: [nextjs, sentry]
tags: [observability, privacy, errors]
requires: []
related_skills: [bootstrap-next-supabase]
status: stable
updated: 2026-10-21
---

# Sentry on Next.js with scrubbed events

Three init files share one scrubber. The browser bundle can see only the public DSN. Source maps upload at build time with a token that never reaches the client. Events leave with `sendDefaultPii` off, and a `beforeSend` hook still strips bodies, cookies, and long strings because the SDK flag does not cover fields the app attached itself.

The SDK is bundled. `content-security-policy` needs the DSN origin on `connect-src` only. `script-src` does not load Sentry from a CDN.

## Where each runtime starts

| Runtime | File | Loaded by | DSN |
| --- | --- | --- | --- |
| Browser | client init | `instrumentation-client.ts` | `NEXT_PUBLIC_SENTRY_DSN` |
| Node | server init | `instrumentation.ts` when `NEXT_RUNTIME` is `nodejs` | `SENTRY_DSN`, else the public DSN |
| Edge | edge init | `instrumentation.ts` when `NEXT_RUNTIME` is `edge` | public DSN, else `SENTRY_DSN` |

Skip `Sentry.init` when that runtime's DSN is empty. Local work then does not send events.

> NOTE: unverified that `captureException` is a no-op when `init` was skipped. The routes call it with no DSN guard.

`instrumentation.ts` also exports `onRequestError = Sentry.captureRequestError`. The client file exports `onRouterTransitionStart = Sentry.captureRouterTransitionStart` so client navigations are traced by the SDK hook Next calls. Error tracing is still sampled at 0 (below). The hook is wired; the sample rate is what stops traces.

Environment tag:

- Browser: `NEXT_PUBLIC_VERCEL_ENV`, else `NODE_ENV`.
- Server: `VERCEL_ENV`, else `NODE_ENV`.
- Edge: `VERCEL_ENV`, else `NODE_ENV`.

## Init options

Same knob object for every runtime. These knobs live in code.

| Option | Value |
| --- | --- |
| `sendDefaultPii` | `false` |
| `sampleRate` | `0.5` (error events) |
| `tracesSampleRate` | `0` |
| `enableLogs` | `false` |
| `replaysSessionSampleRate` | `0` (browser only) |
| `replaysOnErrorSampleRate` | `0` (browser only) |

Shared `ignoreErrors`: `AbortError`, `The operation was aborted`, `The user aborted a request`, `ResizeObserver loop limit exceeded`, `ResizeObserver loop completed with undelivered notifications.`

Shared `denyUrls`: `/^chrome-extension:\/\//i`, `/^moz-extension:\/\//i`.

`beforeSend` and `beforeBreadcrumb` point at the scrubber. A dropped event returns `null`.

The root error boundary is a client component that calls `captureException` inside `useEffect`. The first render stays the same on the server and the client. It renders its own `<html>` and `<body>` because it replaces the root layout. Read `window` only after mount if a later change needs it (P25).

Call sites pass stable tags, `feature` and `action`. The scrubber's drop list matches on `action` and on the exception text. Keep a tag for a real server failure distinct from the noisy client channels below.

## Scrubber

Walk `extra`, `contexts`, and breadcrumb data. Depth over 4 becomes `"[MaxDepth]"`. A string longer than 240 characters becomes the first 240 characters plus `"... [truncated]"`.

A key is sensitive when its lowercase form contains any list entry (`String.includes`). `authorization` matches `Authorization`. The same rule filters a longer name that merely contains a fragment: `content` matches `contentType`, and `message` matches `messageId`. Name diagnostic fields so they avoid those fragments, or accept that they are removed.

Sensitive fragments: `authorization`, `cookie`, `cookies`, `set-cookie`, `token`, `access_token`, `refresh_token`, `password`, `secret`, `api_key`, `apikey`, `client_secret`, `body`, `payload`, `content`, `message`, `transcript`, `transcription`, `audio`, `query`. Add the product's own document field names. A matching key is replaced with `"[Filtered]"` and is not walked.

On the request, always replace `cookies`, `data`, and `query_string` with `"[Filtered]"`, and strip the query off `url`. Headers go through the same substring check, so `authorization` is filtered and `user-agent` stays.

```ts
const SENSITIVE = new Set([
  "authorization", "cookie", "cookies", "set-cookie", "token",
  "access_token", "refresh_token", "password", "secret",
  "api_key", "apikey", "client_secret", "body", "payload",
  "content", "message", "transcript", "transcription", "audio", "query",
])

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase()
  return [...SENSITIVE].some((item) => normalized.includes(item))
}

export function scrubEvent(event: ErrorEvent, hint?: EventHint): ErrorEvent | null {
  if (shouldDrop(event, hint)) return null
  scrubRequest(event)
  event.extra = scrubValue(event.extra) as ErrorEvent["extra"]
  event.contexts = scrubValue(event.contexts) as ErrorEvent["contexts"]
  event.breadcrumbs = event.breadcrumbs?.map(scrubBreadcrumb)
  return event
}
```

Breadcrumbs in category `fetch` or `xhr` scrub `data` only. Other breadcrumbs scrub `message` through the length limit (pass a key that is not itself sensitive, or the whole message becomes `"[Filtered]"`) and scrub `data`.

### What gets dropped

Return `null` for expected noise. Keep everything else, including a server stream failure whose action tag is not in the noise set.

- `action` tag `comment_stream` or `api_notification_stream_setup`.
- Exception text containing `notification comment stream` or `eventsource`.
- Card declines: `card_declined`, `card declined`, `your card was declined`.
- `NotAllowedError`, or `permission denied` together with `microphone`, `audio`, `getusermedia`, or `feature` tag `voice_input`.

A unit test that keeps an event tagged `api_process_agent_stream` with message `stream failed` guards the last rule. A broad "stream" filter would hide real proxy bugs.

## Build wrapper

`next.config.ts` exports `withSentryConfig(nextConfig, options)`. The wrapper must keep the inner `headers()` from `content-security-policy`. Replacing the config object drops the CSP.

```ts
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  silent: true,
  widenClientFileUpload: Boolean(process.env.SENTRY_AUTH_TOKEN),
  useRunAfterProductionCompileHook: true,
  webpack: {
    treeshake: { removeDebugLogging: true },
  },
})
```

`useRunAfterProductionCompileHook` is on because the SDK detects Turbopack, which is the Next.js 16 default, and skips Webpack injection. Source maps upload from that compile hook. `widenClientFileUpload` is on only when `SENTRY_AUTH_TOKEN` is set, so a build without a token does not try to upload a wider client set.

The token, org, and project are build-time. They are not `NEXT_PUBLIC_`.

## CSP

Take `new URL(dsn).origin`. A DSN looks like `https://<public key>@<host>/<project>`. The origin is `https://<host>`. The userinfo is the public client key; putting the raw DSN in `connect-src` would publish it a second time. The browser DSN is already public. The header still gets the origin only.

If both DSN vars are empty, omit the origin. See `content-security-policy`.

## Env

| Name | Role |
| --- | --- |
| `NEXT_PUBLIC_SENTRY_DSN` | Browser, and the edge fallback. Public by design |
| `SENTRY_DSN` | Server preferred, edge fallback |
| `SENTRY_AUTH_TOKEN` | Build. Source map upload |
| `SENTRY_ORG` | Build |
| `SENTRY_PROJECT` | Build |
| `NEXT_PUBLIC_VERCEL_ENV` | Browser environment tag |
| `VERCEL_ENV` | Server and edge environment tag |

## Setup

1. Create the project and copy the DSN into `NEXT_PUBLIC_SENTRY_DSN` and `SENTRY_DSN`.
2. Add the three init files, `instrumentation.ts`, and `instrumentation-client.ts`. Leave `init` uncalled when the DSN is empty.
3. Add the scrubber and point `beforeSend` and `beforeBreadcrumb` at it.
4. Wrap `next.config.ts` without dropping `headers()`.
5. Set the auth token in the build environment. Confirm a production build uploads maps and the browser stack traces symbolicate.
6. Put the DSN origin on `connect-src`.
7. Capture from the root error boundary in an effect.

## Failure prevention

- Leave `sendDefaultPii` false, and still scrub. The flag does not remove a body the app put on the event.
- Filter request cookies, body, and query string on every event. Strip the URL query.
- Keep the substring matcher. Avoid field names that contain `content`, `message`, `token`, or `query` unless those fields should disappear from the event.
- Cap strings at 240 characters and depth at 4.
- Drop aborts, extension URLs, card declines, microphone denial, and the known noisy realtime actions. Keep other stream errors.
- Sample errors at 0.5 and traces at 0. Leave session replay at 0.
- Keep the source-map token off the client.
- Put the DSN origin in the CSP. Leave the raw DSN, including the key in the userinfo, out of the header.
- Call `captureException` from an effect in the root error boundary (P25).

## Tests

Build an event with a request URL that has a query, an `authorization` header, cookies, and a body. After scrub, the URL has no query, cookies and body are `"[Filtered]"`, `authorization` is `"[Filtered]"`, and `user-agent` remains. A nested `password` is filtered. A 300-character safe string is 240 characters plus the truncation suffix. An event with no request is returned.

Drop: action `comment_stream`, an `EventSource` exception, `Your card was declined.`, and `NotAllowedError` / `Permission denied`. Keep action `api_process_agent_stream`.

A fetch breadcrumb filters `token` inside `data` and keeps a plain URL. A UI breadcrumb truncates a long message and filters `secret`.

## Checklist

- [ ] Browser, Node, and edge each init with the DSN column above, or skip init when it is empty.
- [ ] `sendDefaultPii` is false. Replay and trace sample rates are 0. Error sample rate is 0.5.
- [ ] `beforeSend` scrubs bodies, cookies, tokens, and long strings, and returns `null` for the drop list.
- [ ] `instrumentation.ts` exports `onRequestError`. The client exports `onRouterTransitionStart`.
- [ ] `withSentryConfig` sets `useRunAfterProductionCompileHook` and does not replace `headers()`.
- [ ] `SENTRY_AUTH_TOKEN` is build-only.
- [ ] CSP `connect-src` has the DSN origin.
- [ ] Root error boundary reports in an effect.
