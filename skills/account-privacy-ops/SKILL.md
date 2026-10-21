---
name: account-privacy-ops
description: Export a signed-in user's decrypted rows, send transactional mail, sync a CRM contact, and wipe application data after the auth user is deleted. Use when a person can download their data and close the account, and when lifecycle mail must not read the database from the request role.
---

# Account privacy operations

This skill orders export, mail, the CRM contact, and account close. It does not replace the modules. The request path runs as `app_api` and filters on the JWT subject. The daily scan and the row wipe run as the admin role, on the timer.

The signed-in caller comes from `supabase-magic-link-auth` and `m2m-jwt-service-auth`. If that session is missing, stop and run `bootstrap-next-supabase` first. The Flex timer, the zip, and the admin connection are `azure-functions-timer` in `background-jobs`. Package the function only after the CRM scan and the wipe below exist. Chat traces, when this product has them, are `ai-chat-with-memory`. This skill adds the mask and the log pipeline. It does not rebuild the graph.

## Load the modules

Follow the `search-modules` skill. From the catalog root:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" deletion export loops email langfuse privacy
```

Open these files in this order. For each file, open every name in `requires` and repeat until no new name appears.

1. `aes-gcm-field-encryption`
2. `azure-communication-email`
3. `loops-crm`
4. `account-deletion-and-export`
5. `langfuse-and-log-ingest`

`account-deletion-and-export` requires `m2m-jwt-service-auth` and `supabase-rls-client-reads`. Read both before the export and delete routes. `langfuse-and-log-ingest` requires `langgraph-streaming-chat`. When chat is already implemented, wire the mask into that worker. When it is not, still install the log pipeline and the mask installer so a later import of the stream module cannot export unmasked spans. Do not implement the graph in this skill.

Load `stripe-webhooks-refunds` for the subscription cancel on account close. The reference never calls it. Load `r2-presigned-uploads` for owned object keys. The reference never deletes them. Load `azure-functions-timer` for the host that runs the scan and the wipe. The job bodies stay here.

Follow each module's architecture, env names, schema, setup, failure prevention, and checklist. Stop at a line marked `> NOTE: unverified` and confirm it in current vendor docs. The Loops update route's 404 is one of those points.

## Build order

### 1. Decrypt on read

Export returns plaintext JSON. Implement `aes-gcm-field-encryption` first. One `FIELD_ENCRYPTION_KEY`, 64 hex characters, shared with every process that reads private columns. Production boot refuses to start when `DJANGO_DEBUG` is false and the key is missing.

Repositories decrypt field strings and JSON on read. The export handler does not decrypt a second time and does not invent a second key. Vectors stay plaintext and stay out of the JSON. Store values, if chat memory exists, are `{"enc": ...}` (P08). The export omits graph-memory rows. The wipe deletes them. A complete export still includes every application table the wipe deletes, except rows you intentionally retain.

### 2. Transactional mail

Implement `azure-communication-email` before signup provisioning. All four settings are required: `AZURE_COMMUNICATION_CONNECTION_STRING`, `AZURE_EMAIL_SENDER_ADDRESS`, `SUPPORT_INBOX_EMAIL`, and `FRONTEND_URL`. If any is empty, skip the send and return false. Do not substitute a baked-in origin or inbox.

Call `begin_send` on a worker thread. Do not call `poller.result()`. A downed mailbox returns false. Welcome is `POST` and always responds 200 with `{ "sent": true | false }`. Ticket create stores the row first and stays 201 when mail fails. Bind the welcome recipient to the authenticated user's address. The reference sends the body address without that check.

Templates substitute each `{{token}}` once, with HTML-escaped values, and ship a real plaintext body. Build every link from `FRONTEND_URL`. Leave the connection string and the HTML body out of logs.

### 3. CRM contact and the daily scan

Implement `loops-crm` on the server and on the timer. `LOOPS_API_KEY` is never a `NEXT_PUBLIC_` name. An empty key on the web tier skips the call and does not block sign-in. An empty key on the timer fails the scan. A silent skip must not look like a successful job.

Run `provisionNewUser` only when this request inserted the preference row. Create the contact, and on 409 update it. Send `signup_completed` only after that write succeeds, with an `Idempotency-Key`. A 409 on that header is success. `Promise.allSettled` runs the contact write and the welcome mail together. One failure does not cancel the other. Retry a failed signup. The preference row already existing is not a record that the contact write succeeded.

Welcome inserts `app_user_activity` with `get_or_create` before the send, so a retry does not overwrite a later item-save timestamp. Item create and update move `last_activity_at` and leave `crm_subscribed` alone. Delete does not move the clock. `authenticated` has no grant on that table.

The daily scan joins `auth.users` with `SUPABASE_DB_ADMIN_*`, `sslmode` `verify-full`, a required host, and a CA file (P31). Port is the session pooler, `5432`, not the transaction pooler `6543`. Do not grant `app_api` access to `auth.users`. The 7-day window is one day wide and emits `one_week_inactive`. The 14-day unsubscribe is open-ended and skips users whose subscription is `active` or `trialing` with `current_period_end` still ahead. Set `crm_subscribed` false only after the unsubscribe PUT succeeds. Stay under 10 requests per second. Retry HTTP 429. The reference sleeps 0.2 seconds and does not retry.

### 4. Export and account close

Export, the deletion request, and account delete are not auth-exempt. Take the user id from `request.user_id`. Reject a missing id before `UUID()` (P13). Catch `TypeError` as well as `ValueError`. If a body still carries `user_id`, it must equal the subject or the response is 403 (P14). Then store the subject. `app_api` can read every row. The `WHERE` clause is the authorization.

`GET` export returns one decrypted JSON object. Include the tables the wipe deletes. Omit vectors, provider customer ids, comments, and graph-memory rows unless you add them on purpose. Soft-deleted items stay out of the export and are hard-deleted later.

Close, in order:

1. Insert a `pending` deletion request. 409 means one is already queued. Continue. Any other status stops. The auth user stays.
2. Cancel each provider subscription. On failure, set the request to `cancelled` and do not delete the auth user. The reference skips this call. The local row still holds the provider id until the wipe, and the provider keeps billing.
3. Look up the auth user with the service role. The email comes from that payload, not from the client.
4. Unsubscribe the CRM contact when the email is non-empty. A missing `LOOPS_API_KEY` continues. HTTP 404 continues. Any other status at or above 400 aborts the auth delete. The current update API treats a missing contact as a create and does not list 404. Keep the 404 branch so "nothing to update" does not abort deletion.
5. Delete the auth user. On failure, set the request to `cancelled`. The reference leaves it `pending`, and the next job then wipes a person who is still signed in.
6. On 200, `signOut()` and clear the signed-in cookie. Application rows are still there.

The daily wipe uses the same admin connection as the CRM scan. Select `pending`, and also `processing` rows older than the job's maximum runtime. The reference selects only `pending`, so a crash after the processing commit is stuck. Delete graph rows while thread ids still exist: `store_vectors`, `store`, then checkpoint tables, then `threads`. Children before parents. The foreign keys are deferred and do not cascade. Anonymize a custom profile another person still uses. Do not delete catalog profiles. `consent_records` stays. That retention is a product decision. The completed request row still holds the free-text reason. Purge it if you do not need it.

Delete owned object keys. The reference does not. Record a failure on the request row if the object delete fails.

Each job has its own `try` inside the timer. A CRM failure is logged, then the wipe still runs. The function raises after both attempts when either failed.

### 5. Logs and traces

Process logs follow `langfuse-and-log-ingest`. Use `sys.__stdout__` and `sys.__stderr__`, then `basicConfig(..., force=True)`. `show_locals` is false. Do not pass export JSON, message bodies, prompts, or the service-role key into log events. Auth-admin failures log the status code, not the response body.

`BETTERSTACK_INGESTING_HOST` is a hostname with no scheme, set per environment before the handler is built (P34). A token with an empty host fails startup. No hostname literal in the image.

When the chat worker is in this product, the mask installer runs once after `django.setup()` and before `get_client()`. A raising hook drops the batch (P33). `PII_MASKING_ENABLED` false means `<REDACTED>`, not the original text. The actor does not call `flush()`.

## Cross-checks

- Export decrypts with `FIELD_ENCRYPTION_KEY`. The wipe does not decrypt. It deletes ciphertext. The admin password is on the timer only. It is absent from the web process and from the Dramatiq worker (P31).
- Signup provisioning is create-or-update, then `signup_completed`, in parallel with welcome mail. Account close unsubscribes that same contact before the auth user is deleted. A failed unsubscribe or a failed auth delete cancels the pending row.
- Welcome writes `app_user_activity` with `get_or_create` before `begin_send`. The daily 7-day window reads that clock. It does not read a sign-in timestamp or the item table.
- Graph prefixes deleted by the wipe are the namespaces from `langgraph-postgres-persistence`. Delete them before `threads`. Store rows are `{"enc": ...}` objects. The export does not return them (P08).
- `app_api` filters on the JWT subject on every request-path query. The admin role is what deletes a user who can no longer sign in (P13, P14).
- Mail returns false when Azure is down. Sign-in and ticket create still succeed. CRM signup failure does not block the dashboard. The timer does not swallow a failed scan.

## Finish

Walk the checklist at the end of each of the five modules, plus `azure-functions-timer` if this round packaged the function. Then confirm:

- Export JSON for one subject contains that subject's item and omits a second user's rows, vectors, and provider customer ids.
- A missing subject is 401 before `UUID()`. A body `user_id` that is not the subject is 403.
- Welcome with any of the four settings empty does not call the SDK. `begin_send` is not polled. Ticket create stays 201 when send returns false.
- Contact create 409 falls through to update. The signup event is not sent when the contact write failed. A missing web key does not throw. A missing timer key fails the scan.
- `crm_subscribed` flips false only after the unsubscribe PUT. The 14-day query skips a paying user.
- A failed Loops unsubscribe or a failed auth delete sets the deletion request to `cancelled`. 409 on create still continues into account delete.
- The wipe uses the admin connection and `verify-full`. Graph rows go before `threads`. A stale `processing` row is selected again. Consent rows remain.
- `SUPABASE_DB_ADMIN_PASSWORD` and `SUPABASE_SERVICE_ROLE_KEY` are absent from the browser bundle. Two environments do not share a log ingest host.
