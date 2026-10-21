---
name: add-stripe-billing
description: Add hosted Stripe Checkout, signed webhooks, refund cancellation, and server-side plan entitlements to an app that already has a signed-in user. Use when a paid subscription must update a local row and feature caps must follow that row rather than the Checkout redirect.
---

# Add Stripe billing

This skill orders subscription billing. It does not replace the modules. The browser never collects a card and never decides the tier.

The signed-in user comes from `supabase-magic-link-auth`. If that session is missing, stop and run `bootstrap-next-supabase` first. `stripe-checkout` already lists that module in `requires`.

## Load the modules

Follow the `search-modules` skill. From the catalog root:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" stripe checkout webhook entitlement
```

Open these files in this order. For each file, open every name in `requires` and repeat until no new name appears.

1. `stripe-checkout`
2. `stripe-webhooks-refunds`
3. `subscription-entitlements`

Also open `content-security-policy` when the app already sends a CSP. The shipping policy already names `https://js.stripe.com`, `https://api.stripe.com`, `https://hooks.stripe.com`, and `https://*.stripe.com` on the directives the module lists. Hosted Checkout does not load Stripe.js and does not read `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`. Keep those hosts so the portal and any Stripe frame still match the policy.

Follow each module's architecture, env names, schema, setup, failure prevention, and checklist. Stop at a line marked `> NOTE: unverified` and confirm it in current vendor docs. Managed Payments is one of those points: the module records the Stripe clover changelog marking it a public preview that turns on automatic tax after the terms are accepted. Accept the terms before sending `managed_payments.enabled`, or leave the flag off until they are accepted.

## Build order

### 1. Price and table

Create the Product and two recurring Prices in the same Stripe mode as `STRIPE_SECRET_KEY`. Test and live objects do not overlap. Put the price ids in `NEXT_PUBLIC_STRIPE_MONTHLY_PRICE_ID` and `NEXT_PUBLIC_STRIPE_YEARLY_PRICE_ID`.

Create `app_subscriptions` with the columns in `stripe-checkout` before the first session. Unique on `stripe_subscription_id`. The browser policy is SELECT-own-rows (`supabase-rls-client-reads`). The API writes the row. The anon client does not insert it.

Pin the Node SDK and the API HTTP client to `2025-08-27.basil`. Construct `new Stripe(secret)` inside the server action from the key read at call time. The API client sends the same `Stripe-Version` and does not sit next to the Stripe Python SDK.

### 2. Checkout session

`createCheckoutSession` calls `getUser()` and refuses when this user is already entitled. It searches or creates a Customer, then creates a `subscription` mode session. Set `userId` on the session metadata, on `subscription_data.metadata`, and as `client_reference_id` (P16). Subscription webhook events only carry the subscription metadata.

Send either `discounts` or `allow_promotion_codes`, never both (P16). The intro code applies only when the cycle is monthly, `STRIPE_INTRO_PROMOTION_CODE` lists an active code, and the local row count is zero, including canceled rows. A failed count is unknown, not zero: skip the intro code. If the code is missing or inactive, open Checkout with `allow_promotion_codes` instead of failing the action.

`success_url` and `cancel_url` use `NEXT_PUBLIC_SITE_URL` and the billing route. An existing Stripe `active` or `trialing` subscription opens the billing portal instead of a second session. Enable invoice history, payment-method updates, and cancel-at-period-end in the Dashboard. This flow does not refund.

### 3. Return path and webhooks

Deploy the webhook and the sync route from `stripe-webhooks-refunds` before enabling the pay button. The return handler loads the session, treats a session owned by someone else as `not_found`, and POSTs the subscription id to the sync route. The redirect is not entitlement.

The webhook path is exempt from the user JWT and from the user rate limit. The sync route is not. Verify the HMAC on the raw body, check every `v1` signature, and keep the absolute tolerance at 300 seconds. An empty `STRIPE_WEBHOOK_SECRET` returns 500. The secret is per endpoint. A CLI forwarding secret is not the deployed secret.

Subscribe the seven event types the module lists, in the same Stripe mode as the secret key. Claim the event key before writing and release it when the handler fails. A Redis outage fails open. A duplicate event returns 200 without a second write. Invoice events update an existing row. Subscription events insert. Read `current_period_start` and `current_period_end` from the first subscription item. On sync, ignore client-supplied status and reject a metadata `userId` that is not the JWT `sub`.

A refund cancels immediately only when the reason is `requested_by_customer` and the refund status is `succeeded` (P15). Resolve the payment through `/invoice_payments`. Portal cancellation arrives as `customer.subscription.updated` with `cancel_at_period_end` and does not call delete.

Mappers and shared types live outside `"use server"`. Client components import them with `import type` (P17).

### 4. Entitlements

Access is the row, from `subscription-entitlements`. Paid means `status` is `active` or `trialing` and `current_period_end` is still ahead. `cancel_at_period_end` keeps access until that end. `past_due` is free even when the period end is in the future. A lookup error returns the free tier.

Put the same caps in server settings and in client config. Paid uncapped counters are `null` on the client and infinity on the server. The status route returns tier and usage. It does not return the caps. Gate the server commands the module names. The client counters are a display.

After every subscription upsert, delete, past-due mark, or invoice activation, recompute allowance. Paid restores plan-paused records. Free freezes extras down to the free cap. `get_user_subscription_tier` does not freeze by itself. A period that elapses with no Stripe event stays unfrozen until the billing page reconciles. Command checks already see the free tier because they compare `current_period_end` to now.

The yearly support-refund window is 14 UTC calendar days, inclusive, from the period-start date. The cancel button opens the portal. It does not call the refund API. A support refund with reason `requested_by_customer` is what ends the period immediately.

## Flags

Leave `BETA_TESTING` and `NEXT_PUBLIC_BETA_TESTING` unset when real Checkout should decide the tier. The API flag returns the paid tier before the row lookup and accepts `1`, `true`, `yes`, and `on`. The public flag is the exact string `true`. That string overwrites the client status payload to paid after the fetch, and it is the same string `invite-code-gate` uses to gate the shell. `NEXT_PUBLIC_BETA_TESTING=1` leaves the gate off and leaves the client tier on the fetched row. Set both flags for the same intent, or leave both unset. One side on makes the shell, the client tier, and the API tier disagree.

## Cross-checks

- Checkout, the webhook, and the sync route share one API version string. Period fields are read from the subscription item on both the Node mapper and the API mapper. A 2-second tolerance decides whether the local row already matches. It does not grant access.
- `userId` written at session create is the `sub` the webhook and the sync route later trust. The sync route still compares it to the current JWT.
- The browser SELECT policy can show the row. It cannot create it. Entitlement updates happen in the webhook and the sync route.
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` stay off `NEXT_PUBLIC_` names. Logs omit the raw body, the session URL query, and the secret key.
- Comment waves and other paid caps call the same tier predicate. When `background-jobs` is in the app, the large comment wave follows `premium` from this row, not a second flag. `BETA_TESTING` forces that tier for every user.

## Finish

Walk the checklist at the end of each of the three modules. Then confirm:

- A monthly user with zero rows and an active intro code sends `discounts` and not `allow_promotion_codes`. A canceled row, a yearly price, and an unknown count send `allow_promotion_codes` only.
- A bad signature and a timestamp 301 seconds off each return 400. Replaying one event id writes one row.
- A `requested_by_customer` refund that `succeeded` deletes the subscription. A `past_due` or `pending` refund does not.
- After cancel-at-period-end, the status stays paid until `current_period_end`. After that timestamp, command checks see the free tier.
- A metadata `userId` that is not the JWT subject does not upsert on the sync route.
- With both beta flags unset, a missing subscription row is the free tier on the API and in the status payload.
