---
name: k6-backend-load
title: Local k6 against the test settings
summary: Seed a manifest of committed rows that share one fake embedding, then run a named k6 scenario at a local ASGI process with provider calls stubbed out. Use this when you want a repeatable concurrency check on a laptop. It is not a pull-request or release gate.
stack: [k6, django, postgres]
tags: [load-test, k6, django, performance]
requires: [django-testing]
related_skills: []
status: stable
updated: 2026-10-26
---

# Local k6 against the test settings

A local harness around the same test settings module the integration suite uses (`django-testing`). It writes real rows, signs real test JWTs, and drives HTTP with k6. The queue worker is not started. Completion providers, speech, and the live agent stream are not in the mix. Nothing in the API workflow or the release workflow runs this suite.

Run it only at the local database. The seed writes and the cleanup deletes.

## Layout

```
tests/load/
  config.json          # scenario stages, weights, seed counts, bind address
  load.js              # one scenario per invocation, from SCENARIO
  helpers/             # auth headers, weighted pick, signed webhook
  seed_data.py         # seed and cleanup; writes data/users.json
  run_load_server.py   # forces the test settings module, then uvicorn
  run_e2e.sh           # seed, wait for health, k6; leaves the process up
  data/                # manifest and summary JSON (gitignored)
```

`data/users.json` and `data/summary-*.json` are gitignored. The manifest holds bearer tokens. Do not commit it.

## Test-settings doubles

`ENV` is `test`. That is what mounts the doubles and what short-circuits provider calls (`django-settings-and-env`).

| Double | Behavior |
| --- | --- |
| Search embed | Returns one constant vector and ignores the text. Length 384, each component `0.01`, matching the embedding column. |
| Seeded vectors | The same constant, so cosine search hits rows instead of an empty result. |
| Rate limit | `RATE_LIMIT_REQUESTS` 100000, `RATE_LIMIT_WINDOW` 60, `RATE_LIMIT_BURST` 5000. Production is 100 / 60 / 20. The limiter is not what this suite measures. |
| Webhook secret | A fixed test value on the settings module. The script reads the same value from config and signs the raw body. Do not copy it into docs. |
| Provider credentials | Blank, same as the rest of the test module. |
| Schema route | Mounted only when `ENV` is `test`, and appended to the auth-exempt and rate-limit-exempt lists. A follow-on scanner reads it. Dev and production do not mount it. |
| Mock stream | Mounted only when `ENV` is `test`. |

The mock stream requires a JWT, because auth middleware still runs, and then holds the connection. It does not touch the database or a model. Duration is `LOAD_MOCK_STREAM_SECONDS` (default 10, minimum 0.1, unparsable values fall back to 10). It emits `start`, one `token` per second of that duration, then `done`, as `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`. GET and POST are allowed. Anything else is 405.

## Seed and cleanup

Preconditions are the same as integration: local database up, migrations applied, Redis up. The seed script sets `DJANGO_SETTINGS_MODULE` to the test module and defaults the field-encryption key when it is unset. That key is the test material from `django-testing`. Do not copy it.

`seed` reads counts from config: how many users, how many items, comments, goals, and messages per user, the JWT lifetime, and the fraction of users that get a paid row. `--users N` overrides the user count. Each run mints a `run_id`. User ids are new UUIDs. A handful of shared catalog rows are created for the run and stored on the manifest so cleanup can delete them.

The JWT is ES256. `sub` is the user id. `iss` and `aud` come from settings. Lifetime is the config value (the reference uses 14400 seconds). Headers on every ordinary call are `Authorization`, `X-User-Created-At`, and `X-Client-Local-Time`, the same trio as `django-testing`.

Paid users also get a provider customer id and subscription id stored on the row and copied onto the manifest. The sync and webhook routes need those ids. Other users skip those two routes and hit a status read instead.

The manifest is one JSON file: `run_id`, the JWT `iss` and `aud`, shared catalog ids, and per user the id, token, created-at, paid flag, provider ids, and the ids of the rows k6 will address.

`cleanup` deletes only those ids, children before parents, then the shared catalog rows, then unlinks the manifest. It does not flush the database. A new `seed` runs cleanup first when a manifest is already present.

## Scenarios

`SCENARIO` selects one entry. The default is `gradual_ramp`. An unknown name throws and lists the keys. The reference config's traffic-model comment is a sizing note for that file. The stages below are the contract. Change them when the host changes. Do not copy the monthly-active-user arithmetic into product docs.

| Name | Executor | Shape in the reference config |
| --- | --- | --- |
| `smoke` | `ramping-vus` | 5 VUs, about 30s, then drain |
| `gradual_ramp` | `ramping-vus` | Climb through 25 and 50 to 100, hold, drain |
| `stable_high` | `constant-vus` | 100 VUs for 5m |
| `zigzag` | `ramping-vus` | Oscillate between about 20 and 120 |
| `spike` | `ramping-vus` | 40, then 150, then back to 40 |

`ramping-vus` uses `gracefulRampDown: 30s`. `constant-vus` uses `gracefulStop: 30s`. Think time is uniform between 0.3s and 1.2s. Virtual user *i* takes manifest user `(i - 1) modulo length`, from a `SharedArray` loaded once. `setup()` fails the run when the manifest is empty or when `GET /health/` returns status >= 400.

When the scenario is `smoke` and `LOAD_MOCK_STREAM_SECONDS` is unset, the wrapper exports `2` so the short run does not spend its clock on the default 10s hold.

## Request mix

Weights are integers in `routeWeights`. A pick subtracts from a random roll in `[0, sum)`. The last entry wins if the roll lands on the boundary.

The mix is ordinary authenticated reads and writes: health, lists, detail by a seeded id, search, unread counts, preferences, goals, threads, a mark-read, billing status, a billing sync POST, a signed webhook, and the mock stream. Weights are the only mix policy. Search is in the mix because the fake embedding makes the database path run. Sync and the webhook run only for paid manifest users.

The sync POST sends the subscription fields the API already accepts. It does not call the payment provider. The webhook is the auth-exempt path. The body is signed `t=<unix>,v1=<hex>` with HMAC-SHA256 over `<timestamp>.<raw json>`, using the test secret. Each iteration uses a new event id (`user`, virtual user, iteration) because `claim_idempotency_key` defaults to `SET NX EX 3600`. A repeated id would be dropped for an hour and the sample would not be load.

The mock stream is tagged `expected_duration:stream`. Every other route is tagged `expected_duration:fast`. A route with no seeded id falls back to a cheap read so the iteration still returns.

Status accounting is two layers. With no `setResponseCallback`, k6 treats a status outside 200–399 as `http_req_failed`. A custom `route_errors` rate counts status >= 500, 429, and 401. A `check()` also flags 403 and any status >= 500. A 403 increments `http_req_failed` and fails that check, and it does not increment `route_errors`.

Routes that call a completion provider or enqueue the worker are left out. The mock stream is the stand-in for a long-lived ASGI connection.

## Process

The load server sets `DJANGO_SETTINGS_MODULE` to the test module before importing the ASGI app, then runs uvicorn with `loop=asyncio`, `http=httptools`, `lifespan=on`. Bind comes from config: `127.0.0.1` and port `8001`, so a dev server on port `8000` can stay up. Workers default to 4 (`LOAD_UVICORN_WORKERS` overrides). That 4 is a laptop split so k6 shares the machine. The container command is one process and no `--workers` (`azure-container-apps-and-cicd`). A local gunicorn target uses 2 workers. The load README's line that 4 matches production is stale.

The wrapper refuses to start when the health URL already returns. It waits up to `startupTimeoutSeconds` (60) and tails the log if the process dies. k6 then runs with `SCENARIO`, `BASE_URL`, and `SUMMARY_PATH`. The summary writer emits a short text block and a JSON file under `data/`.

On exit the reference keeps both the rows and the process so a scanner can hit the same port and the schema route. `FORCE_CLEANUP=1` deletes the manifest's rows. `KEEP_SERVER=0` stops uvicorn (SIGTERM, then SIGKILL). `SKIP_SEED=1` reuses the manifest and fails if the file is missing. `BASE_URL` overrides the target. There is no default that points off the machine.

## Thresholds

From the reference config. The stream tag keeps the multi-second hold out of the fast SLO.

| Expression | Limit |
| --- | --- |
| `http_req_failed` | `rate<0.05` |
| `http_req_duration{expected_duration:fast}` | `p(95)<1500` and `p(99)<3000` |
| `http_req_duration{expected_duration:stream}` | `p(95)<12000` |
| `route_errors` | `rate<0.05` |

These are laptop signals against the local database. A miss is a reason to look at the run, not a production SLO, until the same scenario runs on a host sized like production.

## Failure prevention

- This suite is not a CI job. `azure-container-apps-and-cicd` does not call k6. A green pull request says nothing about these thresholds.
- Point `BASE_URL` at the local load port. The seed and the cleanup use the test database.
- Keep `ENV=test` on that process. Otherwise the mock stream is not mounted, search calls the real embedder, and the schema route is absent.
- Seed embeddings and the test embedder must be the same vector. A different constant makes cosine search look empty and the threshold blames the database.
- Vary the webhook event id per iteration. A fixed id is absorbed by the 3600-second claim.
- Raise the test rate limit or the run measures 429s. Put the higher cap in the test settings module, not in production settings.
- The wrapper will not start a second server on a healthy port. Stop the old pid or change the port in config and in `BASE_URL`.
- `smoke` shortens the mock stream only when `LOAD_MOCK_STREAM_SECONDS` is unset. An explicit `10` on a 30s smoke run is yours to own.
- Worker count 4 does not describe the container. One replica there is one process.

## Tests of the harness

- `pickRoute` over weights `{a: 1, b: 3}` returns only those names, and a zero total throws.
- After `seed` and `cleanup`, the manifest user ids and the shared catalog ids are gone, and unrelated rows remain.
- With `ENV=test`, the embed helper returns the 384-long constant and the mock stream path is routed. With any other `ENV`, that path is absent and embed calls the provider.
- A second webhook with the same event id inside the claim window is not applied twice. A new id is.
- `SCENARIO=smoke` with the duration unset exports `LOAD_MOCK_STREAM_SECONDS=2`. `SKIP_SEED=1` without a manifest exits non-zero.
- No workflow file invokes k6 or `run_e2e.sh`.

## Checklist

- [ ] Local database is migrated with the test settings module, and Redis answers, before `seed`.
- [ ] `DJANGO_SETTINGS_MODULE` for the load server is the test module. `ENV` is `test`.
- [ ] Seed and embed share the constant 384-vector. Provider credentials stay blank.
- [ ] The rate limit in the test module is the raised cap (100000 / 60 / burst 5000), and production stays 100 / 60 / burst 20.
- [ ] Scenario name is one of `smoke`, `gradual_ramp`, `stable_high`, `zigzag`, `spike`. Stages live in config.
- [ ] Mock stream is tagged `stream`. Fast thresholds do not include it. Event ids change every webhook iteration.
- [ ] Bind is loopback on the load port, not the dev port. Worker count is the laptop setting, not the container command.
- [ ] The manifest is gitignored. Cleanup deletes by its ids. The suite is not a workflow gate.
