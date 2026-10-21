---
name: background-jobs
description: Run Dramatiq workers for delayed companion comments and a separate Azure Functions timer for the daily CRM scan and account wipe. Use when a request must return before that work finishes, and when scheduled admin SQL must stay off the web process.
---

# Background jobs

This skill orders two different processes. Dramatiq drains a Redis list for work the API enqueues. The Azure Functions timer runs once a day as its own app, with the admin database role. They do not share a process, a connection port, or a credential.

Request handlers enqueue and return. They do not generate comments, scan CRM, or delete accounts inline.

## Load the modules

Follow the `search-modules` skill. From the catalog root:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" dramatiq worker comment timer azure
```

Open these files in this order. For each file, open every name in `requires` and repeat until no new name appears.

1. `dramatiq-redis-workers`
2. `llm-comment-generation`
3. `azure-functions-timer`

`llm-comment-generation` names two modules it does not list in `requires`. Load both before writing the comment path:

- `pgvector-similarity-search` for the cosine shortlist
- `aes-gcm-field-encryption` for the comment text

Load `supabase-realtime-postgres-changes` when the UI should see a comment row without a refresh. Load `subscription-entitlements` when the large comment wave must follow the paid tier. The wave reads `get_user_subscription_tier`. `BETA_TESTING` makes that function return `premium` for every user.

`azure-functions-timer` requires `loops-crm` and `account-deletion-and-export`. Read both before packaging the function. The timer module is the host, the zip, and the admin connection. The job bodies stay in those two modules.

## Process A — Dramatiq

Stand this up before any comment actor.

### Worker

One module, imported by the API and by `python -m dramatiq run_workers`. Call `django.setup()` before importing models. Point log records below `ERROR` at `sys.__stdout__` and the rest at `sys.__stderr__`.

`REDIS_URL` is set on both processes. The worker raises at import when it is missing. The API import does not raise, and a later `send` then uses Dramatiq's default broker, so the message never lands on `dramatiq:default`. Schema commands import the same module and must not raise on an empty URL.

Replace the broker middleware list with `AgeLimit`, `TimeLimit`, `ShutdownNotifications`, `Callbacks`, `Pipelines`, and `Retries(max_retries=2)`, then `AsyncIO`. Actors are `async def`. ORM work goes through `sync_to_async(..., thread_sensitive=True)`. The connection decorator's `finally` calls `close_all` on that same sensitive thread (P03). `CONN_MAX_AGE` is `0` and the Django pool `min_size` is `0` (P02). Point this process at the transaction pooler (`SUPABASE_DB_PORT` `6543`) and set `DISABLE_SERVER_SIDE_CURSORS`. Pass actor arguments as JSON-serializable ids. `delay` is milliseconds.

Restart the worker after any actor, weight, or prompt change, and confirm the new process is the one consuming the queue (P04). Keep a removed actor as an empty function until its messages are gone.

### Comments

Enqueue generation only after a new item is saved and analyzed. Pass the item id and the user id. Edits do not enqueue. A failed `send` is logged and the save still succeeds.

The worker ranks companions, calls one primary model and then one fallback, and enqueues an insert actor per comment. Register `@dramatiq.actor` above the database wrapper. Re-raise after logging so the two retries run. A missing item or an empty shortlist returns without raising.

Vector checks use `is not None` and `len`. Materialize a Python list at the ORM boundary (P12). `if embedding` raises on a numpy value.

Index 0 is delay 0. One or two other indexes wait 6–12 hours. The rest use the half-normal the module specifies. Multiply seconds by 1000. Encrypt the comment before insert. Increment the counter with one `UPDATE` of `F("related_comments_count") + 1` in the same transaction. There is no unique pair on item and companion, so a retry can insert again. Skip generation when that item already has comments.

`premium` takes the large wave (cap 20). Any other tier takes the small wave. A showcase item uses the premium wave and leaves the account tier unchanged. Recent and similar cutoffs use the item timestamp. Similar search leaves `days` unset.

## Process B — daily timer

Package this only after `loops-crm` and `account-deletion-and-export` are implemented. The CRM scan joins `auth.users` with the admin role. The wipe selects `pending` deletion requests and removes app rows. Both need `SUPABASE_DB_ADMIN_*`. That password is absent from the web process and from the Dramatiq worker.

The function is Linux Flex Consumption, Python 3.11, NCRONTAB `0 0 13 * * *` (13:00:00 UTC). Do not set `WEBSITE_TIME_ZONE` or `TZ` on Flex (P29). `run_on_startup` is false. A `past_due` invocation still runs.

Each job has its own `try`. A CRM failure is logged, then the wipe still runs. The function raises after both attempts when either failed. Swallowing that raise makes a failed scan look successful.

The admin connection uses session port `5432` and `verify-full`, not the transaction pooler port `6543` from Process A (P31). Require an explicit host and CA file. Build Linux manylinux wheels before the zip. OneDeploy `released-package.zip` with `remoteBuild` false. The deploy script fails when the function list stays empty.

## Cross-checks

- Comment inserts are Dramatiq messages. The daily CRM unsubscribe and the account wipe are the timer. Moving either job onto the other process puts the admin password on the request tier, or runs user-facing LLM work inside the daily timeout.
- Worker Postgres is the transaction pooler with `CONN_MAX_AGE` 0. Timer Postgres is the session port with certificate verification. The two URLs are different settings.
- `BETA_TESTING` changes both the subscription tier and the comment wave. Leave it unset when Checkout should decide the wave. See `add-stripe-billing` for the paired public flag.
- Live comment rows still need the Realtime publication and the browser client from `supabase-realtime-postgres-changes`. The insert actor does not push the websocket itself.

## Finish

Walk the checklist at the end of `dramatiq-redis-workers`, `llm-comment-generation`, and `azure-functions-timer`, plus the checklists in `loops-crm` and `account-deletion-and-export` if this round added those jobs. Then confirm:

- With `REDIS_URL` set, a probe message is consumed by `python -m dramatiq run_workers`. With it unset, the worker refuses to boot and a schema command still imports.
- An actor that raises `OperationalError` still closes the ORM connection on the sensitive thread, and the exception leaves the actor so the broker can retry.
- A new analyzed item enqueues generation. An edit does not. Delay 0 becomes `delay=0`. A 6-hour surprise becomes `delay=21600000`.
- After an actor change, the running consumer is a new process (P04).
- The function app has no `WEBSITE_TIME_ZONE` and no `TZ`. A CRM exception still attempts the wipe, and the invocation fails if either job failed.
- `SUPABASE_DB_ADMIN_PASSWORD` is present on the function app and absent from the web and worker settings.
