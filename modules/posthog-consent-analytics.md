---
name: posthog-consent-analytics
title: PostHog after a cookie consent choice
summary: Gate the PostHog snippet, identity, and custom events on analytics consent; queue consented calls until initialization and reset identity on logout. Use this when a cached Next.js shell needs anonymous public visits and identified signed-in usage with session replay off.
stack: [nextjs, posthog]
tags: [analytics, consent, privacy]
requires: [content-security-policy]
related_skills: [bootstrap-next-supabase]
status: stable
updated: 2026-11-03
---

# PostHog after a cookie consent choice

The snippet is a client component. It renders nothing until a stored choice says analytics is allowed and a project key exists. Necessary cookies (the auth session) are not gated here. There is no server-side consent record. The choice lives in `localStorage` and survives a full load between the public root layout and the signed-in root layout, which are separate documents.

A client wrapper handles `identify`, custom `capture`, and logout `reset`. It checks consent before accepting calls and SDK readiness before sending them. A session component supplies the stable auth subject after mount. Automatic page views remain enabled; there is no application `utm_` parser.

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
      loaded: () => window.dispatchEvent(new Event("app-analytics-ready")),
    })
  }
  start()
}
```

`person_profiles: "identified_only"` allows anonymous public events and identified events after a person profile is created. The signed-in component now calls `identify` after consent and initialization. `identified_only` does not disable identification. See [PostHog event identity](https://posthog.com/docs/data/anonymous-vs-identified-events).

`disable_session_recording: true` keeps replay off. Autocapture, page views, and page leaves remain on alongside custom events. The `loaded` callback announces readiness; the wrapper and the session component must subscribe to the same event name.

Inject the project key and the host with `JSON.stringify` inside the inline script so a quote in the value cannot break out of the string. The key is public (`NEXT_PUBLIC_POSTHOG_KEY`). It is still not a secret to paste into a log line you do not need.

When consent flips to false, unmounting script tags leaves an initialized SDK running. The wrapper rejects new calls and checks consent before flushing, but it cannot stop SDK autocapture by itself. An implementation supporting withdrawal without a reload must cancel initialization timers, discard pending jobs, and call `opt_out_capturing()`; reacceptance uses `opt_in_capturing()`. These APIs and the `loaded` callback are documented in the [PostHog JavaScript SDK](https://posthog.com/docs/libraries/js).

> NOTE: the reference unmounts the snippet but does not opt out the already-running SDK, cancel its initialization timer, or clear pending jobs on withdrawal. A reload with rejection stored never loads the snippet. Do not describe live withdrawal as fully implemented.

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

## Identity, readiness, and custom events (P44)

Data flow: consent choice -> script/init -> readiness notification -> queued wrapper calls -> SDK. Independently, session state -> identity effect -> the same wrapper. Logout clears the wrapper queue before resetting SDK identity.

| Wrapper operation | Input shape | Gate and effect |
| --- | --- | --- |
| `identifyUser` | Stable auth id, optional email person property | Drop an empty id or absent consent; queue until ready; call `identify(id)` or `identify(id, properties)` |
| `capture` | Event name plus optional scalar properties | Same consent/readiness gates; SDK failures never break the user action |
| `resetAnalytics` | No event payload | Clear pending calls even before initialization; attempt SDK `reset` regardless of current consent |

The readiness gate in the reference checks `window.posthog.__loaded` and flushes on an application readiness event emitted by `loaded`. `__loaded` is an SDK internal flag, not an application contract. An adaptation can track readiness in its own module from the public `loaded` callback instead. Listen before initialization and handle the already-ready case.

Queued calls are browser-memory jobs, not a durable event log. No consent means drop immediately, never queue for future acceptance. With consent but no ready client, enqueue in arrival order. At flush, recheck consent, drain once, and isolate exceptions per job. A failed analytics call is swallowed; it is not retried. A page close loses pending calls. The reference has no queue size/age bound and leaves jobs pending if consent is withdrawn before readiness; bound/discard them when adapting this mechanism.

Mount the identity effect inside the session provider. Run it when the auth id or optional email changes, and again on consent acceptance or readiness. Remove its listeners on cleanup. Repeated callbacks can identify the same user again; do not promise exactly-once delivery or that every earlier capture was already identified. If event attribution requires identity first, establish it before releasing that session's captures.

Use the auth subject as the distinct id; optional email is a person property, never the sole identity key. No `group` or `alias` path is implemented. Generic call sites use the wrapper:

```ts
identifyUser(session.userId) // called by the session effect after mount
capture("item_saved", { analyzed: false }) // after confirmed save success
capture("upgrade_clicked", { surface: "editor" }) // at the user action
// At logout, before submitting the sign-out action:
resetAnalytics()
```

Attach reset to every logout/account-switch path. The reference clears pending jobs and attempts SDK reset from the logout form's submit handler. That does not prove that session expiry or every future sign-out path resets identity. [PostHog documents stable identity and logout reset](https://posthog.com/docs/libraries/js); reset does not erase the application's separate consent record.

Keep event properties to coarse enums, booleans, and counts. The wrapper's property type is `Record<string, string | number | boolean | null>`; it is not a content scrubber. Call sites still choose what to send. A public CTA listener can capture public pathname, coarse placement, and intent after consent. It must omit query strings, referrer paths, private text, and private detail URLs; do not reuse public pathname capture across authenticated routes. Emit completed-action events after success rather than on every render. These are reusable event rules, not a product event catalog.

## Setup

1. Create a project. Put the project key in `NEXT_PUBLIC_POSTHOG_KEY`. Set `NEXT_PUBLIC_POSTHOG_HOST` when the project is not on the US cloud.
2. Add the consent helpers and the banner. Start `visible` at false and read storage in an effect.
3. Add the snippet. Start `enabled` at false. Load `array.js` only after consent and a non-empty key.
4. Init with `identified_only`, autocapture, page views, page leaves, and session recording off.
5. Confirm the CSP wildcards cover the asset host and the API host, or append the custom origin (`content-security-policy`, P21).
6. Mount the banner and snippet in each root layout. Mount identity inside the authenticated session provider, register readiness/consent listeners, and reset pending work plus SDK identity on logout.
7. Route explicit events through the gated wrapper; add coarse properties at the action outcome. Exercise delayed readiness, rejection, and logout before enabling the project key.

## Failure prevention

- **P21.** Keep the inline init on `'unsafe-inline'`. Allow the asset host on `script-src` and the API host on `connect-src`. A custom host outside `*.i.posthog.com` and `*.posthog.com` needs its own origin.
- **P25.** The banner and the snippet render the same tree on the server and the first client paint. Read `localStorage` after mount.
- Treat a missing key as off, even when the visitor accepted analytics.
- Store a rejection as `analytics: false` so the banner stays dismissed for 180 days.
- Remove expired and corrupt consent keys.
- **P44.** `identified_only` permits identified profiles. Gate stable-id identification and custom events on consent/readiness; clear queued identity and events before resetting on logout. Analytics exceptions cannot fail the product action.
- Leave session recording disabled.
- On same-page withdrawal, stop initialization timers, clear pending work, and opt out the SDK. Wrapper gating alone does not stop automatic capture. The reference has this limitation.
- Queueing provides no durable delivery or exact ordering against automatic page views. Bound queued jobs and discard stale jobs across account changes.
- Do not parse `utm_` parameters. The app does not.

## Tests

Empty storage: `readConsent()` is null and `hasAnalyticsConsent()` is false. `storeConsent(true)` writes `analytics: true`, a string `decidedAt`, and dispatches `CONSENT_EVENT`. `storeConsent(false)` stays a rejection. A `decidedAt` 181 days ago returns null and removes the key. A `decidedAt` 10 days ago is kept. Corrupt JSON returns null and removes the key.

Snippet: with consent false, or with an empty key, the script tag is absent. With consent true and a key, the script `src` is the asset host plus `/static/array.js`. The init object has `person_profiles: "identified_only"` and `disable_session_recording: true`.

Banner: the first render is not visible. After mount with empty storage it is visible. Accept and reject both hide it and write the matching flag.

Wrapper checks already in the reference unit suite: consent off drops capture; loaded + consent sends scalar properties; delayed initialization queues and the ready event flushes; identify uses a stable id with or without the optional email property; reset attempts SDK reset on logout.

Additional acceptance cases for an adaptation (not claims about existing coverage): repeated ready events do not replay drained jobs; logout before readiness drops queued identity/captures; server calls and empty ids do nothing; one SDK exception does not block the next job; consent withdrawn before ready never flushes; withdrawal after load stops automatic capture through SDK opt-out. Exercise account A logout then account B login to ensure no queued A event crosses the identity boundary. The public CTA test should assert only coarse properties, and an unconsented click sends nothing.

## Checklist

- [ ] Consent is `localStorage`, 180 days, with an explicit true or false.
- [ ] Banner and snippet do not read `window` during render.
- [ ] Snippet loads only after acceptance and only when the project key is set.
- [ ] Init uses `identified_only`, autocapture, pageview, pageleave, and recording off.
- [ ] Stable auth id is identified only after consent/readiness; optional properties stay separate. No UTM parser.
- [ ] Delayed calls flush once; logout clears pending identity/events and resets the SDK (P44).
- [ ] Public CTA properties omit query/referrer/private content; custom captures cannot interrupt actions.
- [ ] CSP allows the asset host and the API host, with `'unsafe-inline'` on `script-src`.
- [ ] Live withdrawal cancels timers, clears queued jobs, and opts out SDK capture; record the reference gap if adapting later. A reload with analytics false never loads the script.
- [ ] Both root layouts mount the banner and the snippet.
