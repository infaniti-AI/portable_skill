---
name: posthog-consent-analytics
title: PostHog after a cookie consent choice
summary: Load the PostHog snippet only after the visitor accepts analytics, with person profiles and session replay left off. Use this when product analytics must wait for consent and the page shell is cached.
stack: [nextjs, posthog]
tags: [analytics, consent, privacy]
requires: [content-security-policy]
related_skills: [bootstrap-next-supabase]
status: stable
updated: 2026-10-21
---

# PostHog after a cookie consent choice

The snippet is a client component. It renders nothing until a stored choice says analytics is allowed and a project key exists. Necessary cookies (the auth session) are not gated here. There is no server-side consent record. The choice lives in `localStorage` and survives a full load between the public root layout and the signed-in root layout, which are separate documents.

No call site identifies a user. No code reads `utm_` query parameters. Page views are the snippet's own `capture_pageview`. Do not add a campaign parser.

## Consent record

```ts
export const CONSENT_STORAGE_KEY = "app_cookie_consent"
export const CONSENT_EVENT = "app-cookie-consent-changed"
const CONSENT_MAX_AGE_MS = 1000 * 60 * 60 * 24 * 180

export type ConsentChoice = {
  analytics: boolean
  decidedAt: string
}

export function readConsent(): ConsentChoice | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.localStorage.getItem(CONSENT_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ConsentChoice
    if (Date.now() - new Date(parsed.decidedAt).getTime() > CONSENT_MAX_AGE_MS) {
      window.localStorage.removeItem(CONSENT_STORAGE_KEY)
      return null
    }
    return parsed
  } catch {
    window.localStorage.removeItem(CONSENT_STORAGE_KEY)
    return null
  }
}

export function hasAnalyticsConsent(): boolean {
  return readConsent()?.analytics === true
}

export function storeConsent(analytics: boolean) {
  const choice: ConsentChoice = { analytics, decidedAt: new Date().toISOString() }
  window.localStorage.setItem(CONSENT_STORAGE_KEY, JSON.stringify(choice))
  window.dispatchEvent(new Event(CONSENT_EVENT))
}
```

A choice is valid for 180 days. After that the key is removed and the banner comes back. Corrupt JSON is not a choice. Remove it so the banner can show again.

> NOTE: the reference returns null for corrupt JSON and leaves the key in place. Expiry does remove the key.

`hasAnalyticsConsent` is true only for `analytics === true`. A stored rejection is false, which keeps the snippet unloaded and keeps the banner hidden.

`readConsent` touches `window`. Call it from `useEffect`, never during render (P25). Both client components start in the "nothing stored yet" state and update after mount:

- Banner: `visible` starts `false`. After mount it becomes true only when `readConsent()` is null.
- Snippet: `enabled` starts `false`. After mount it follows `hasAnalyticsConsent`. It also listens for `CONSENT_EVENT`.

The first paint is the same on the server and the client. The banner does not flash for someone who already chose.

## Banner

Show the banner while `visible` is true. Three actions:

- Manage preferences toggles a short explanation. It does not write storage.
- Reject non-essential calls `storeConsent(false)` and hides the banner.
- Accept analytics calls `storeConsent(true)` and hides the banner.

Copy can say that sign-in cookies stay on, that analytics is optional, and that session replay is off. Mount the banner in every root layout that should offer the choice. The reference mounts it in the public document and in the signed-in document.

## Snippet

Render nothing when `enabled` is false or the project key is empty. Otherwise load the library, then init.

The asset host is the API host with `.i.posthog.com` replaced by `-assets.i.posthog.com`. The default API host is `https://us.i.posthog.com`, so the script URL is `https://us-assets.i.posthog.com/static/array.js`. A host that does not contain `.i.posthog.com` is not rewritten. Use `next/script` with `strategy="lazyOnload"`. Run the init script only after `array.js` fires `onLoad`.

```tsx
const apiHost = process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com"
const assetsHost = apiHost.replace(".i.posthog.com", "-assets.i.posthog.com")

function initSnippet(projectKey: string, host: string) {
  const start = () => {
    const client = (window as Window & { posthog?: { init: (key: string, options: object) => void } }).posthog
    if (!client) {
      window.setTimeout(start, 100)
      return
    }
    client.init(projectKey, {
      api_host: host,
      person_profiles: "identified_only",
      autocapture: true,
      capture_pageview: true,
      capture_pageleave: true,
      disable_session_recording: true,
    })
  }
  start()
}
```

`person_profiles: "identified_only"` creates a person profile only after an `identify` call. The app never calls `identify`, so events stay anonymous. Leave it that way unless a later change has a reason to attach a user id, and only after analytics consent.

`disable_session_recording: true` matches the banner. Autocapture, page views, and page leaves are on. Those are the only capture paths. There is no separate event list in the app.

Inject the project key and the host with `JSON.stringify` inside the inline script so a quote in the value cannot break out of the string. The key is public (`NEXT_PUBLIC_POSTHOG_KEY`). It is still not a secret to paste into a log line you do not need.

When consent flips to false, unmounting the script tags leaves a `window.posthog` that is already running. Stop the 100 ms retry. If the global exposes an opt-out method, call it.

> NOTE: unverified method name. The reference only stops rendering. It never calls an opt-out API and it never clears the retry timer. Confirm `opt_out_capturing` on the current snippet. A reload with a stored rejection never loads the script, which is the path the reference actually implements.

## CSP (P21)

The init block is inline. The cached shell cannot carry a per-request nonce, so `script-src` keeps `'unsafe-inline'` and names the script hosts. See `content-security-policy`.

| Need | Host |
| --- | --- |
| `script-src` | asset host (`https://us-assets.i.posthog.com` for the default) |
| `connect-src` | API host (`https://us.i.posthog.com` for the default) |

The shipping policy uses `https://*.i.posthog.com` and `https://*.posthog.com` on both `script-src` and `connect-src`. One label of `*.i.posthog.com` covers `us-assets.i.posthog.com` and `us.i.posthog.com`. It does not read `NEXT_PUBLIC_POSTHOG_HOST`. A host outside those suffixes is blocked until its `URL.origin` is added. The default US cloud host and the EU `*.i.posthog.com` hosts sit inside the wildcards.

## Env

| Name | Role |
| --- | --- |
| `NEXT_PUBLIC_POSTHOG_KEY` | Project key. Empty skips the snippet even after consent |
| `NEXT_PUBLIC_POSTHOG_HOST` | API host. Default `https://us.i.posthog.com` |

No server token. No personal API key in the browser.

## Page views

`capture_pageview: true` records the page view the snippet sends. The repo has no `utm_` reader and no campaign column. Leave that parser out.

The repo has no `posthog.identify`, `group`, or `alias`.

## Setup

1. Create a project. Put the project key in `NEXT_PUBLIC_POSTHOG_KEY`. Set `NEXT_PUBLIC_POSTHOG_HOST` when the project is not on the US cloud.
2. Add the consent helpers and the banner. Start `visible` at false and read storage in an effect.
3. Add the snippet. Start `enabled` at false. Load `array.js` only after consent and a non-empty key.
4. Init with `identified_only`, autocapture, page views, page leaves, and session recording off.
5. Confirm the CSP wildcards cover the asset host and the API host, or append the custom origin (`content-security-policy`, P21).
6. Mount the banner and the snippet in each root layout.

## Failure prevention

- **P21.** Keep the inline init on `'unsafe-inline'`. Allow the asset host on `script-src` and the API host on `connect-src`. A custom host outside `*.i.posthog.com` and `*.posthog.com` needs its own origin.
- **P25.** The banner and the snippet render the same tree on the server and the first client paint. Read `localStorage` after mount.
- Treat a missing key as off, even when the visitor accepted analytics.
- Store a rejection as `analytics: false` so the banner stays dismissed for 180 days.
- Remove expired and corrupt consent keys.
- Leave `person_profiles` at `identified_only` while nothing calls `identify`.
- Leave session recording disabled.
- On a later rejection in the same page, stop the retry. Call the snippet's opt-out only after confirming the method name. The reference relies on a reload.
- Do not parse `utm_` parameters. The app does not.

## Tests

Empty storage: `readConsent()` is null and `hasAnalyticsConsent()` is false. `storeConsent(true)` writes `analytics: true`, a string `decidedAt`, and dispatches `CONSENT_EVENT`. `storeConsent(false)` stays a rejection. A `decidedAt` 181 days ago returns null and removes the key. A `decidedAt` 10 days ago is kept. Corrupt JSON returns null and removes the key.

Snippet: with consent false, or with an empty key, the script tag is absent. With consent true and a key, the script `src` is the asset host plus `/static/array.js`. The init object has `person_profiles: "identified_only"` and `disable_session_recording: true`.

Banner: the first render is not visible. After mount with empty storage it is visible. Accept and reject both hide it and write the matching flag.

## Checklist

- [ ] Consent is `localStorage`, 180 days, with an explicit true or false.
- [ ] Banner and snippet do not read `window` during render.
- [ ] Snippet loads only after acceptance and only when the project key is set.
- [ ] Init uses `identified_only`, autocapture, pageview, pageleave, and recording off.
- [ ] No `identify` call and no UTM parser.
- [ ] CSP allows the asset host and the API host, with `'unsafe-inline'` on `script-src`.
- [ ] A rejection after load stops the retry. A reload with analytics false never loads the script.
- [ ] Both root layouts mount the banner and the snippet.
