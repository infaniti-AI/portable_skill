---
name: django-testing
title: Hermetic Django unit tests and committed integration rows
summary: Run unit tests with no database and no network, and run integration tests against one already-migrated local Postgres plus Redis, cleaning up by primary key. Use this when a Django API is async and a transaction rollback cannot see the rows the test just wrote.
stack: [django, pytest, postgres, redis]
tags: [testing, pytest, django, ci]
requires: []
related_skills: []
status: stable
updated: 2026-10-26
---

# Hermetic Django unit tests and committed integration rows

Two suites. Unit tests mock every external service. Integration tests use the local database and Redis and mock only third-party vendors (the LLM, speech, web search, payments). Both load the test settings module. `ENV` is `test` there, which is what mounts the schema route and the mock stream and what short-circuits provider calls (`django-settings-and-env`).

Pytest is configured with `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = function`. Async tests do not need an extra marker. `python_files` are `tests.py`, `test_*.py`, and `*_tests.py`.

## Layout

```
tests/
  conftest.py            # Django setup, quiet logs, shared ids
  factories.py           # factory_boy models
  fixtures/              # static inputs a factory cannot build
  unit/                  # hermetic
    conftest.py          # marker + socket guard
  integration/
    conftest.py          # committed seeds, real Redis, signed client
```

The root conftest puts the repo root on `sys.path`, defaults `DJANGO_SETTINGS_MODULE` to the test module, defaults the field-encryption key when it is unset (`aes-gcm-field-encryption`), and calls `django.setup()`. structlog is filtered to warning and above.

## Markers

| Marker | Where it comes from | CI |
| --- | --- | --- |
| `unit` | Added at collection for every item under `tests/unit/` | Unit job runs `-m "unit and not slow"` |
| `integration` | Added at collection for every item under `tests/integration/` | Integration job runs that directory |
| `django_db` | Opt-in database access | Forbidden under `tests/unit/` |
| `asyncio` | Registered for strict-markers | Not required while `asyncio_mode` is `auto` |
| `slow` | Registered | Excluded from the unit job. No test carries it. |

`--strict-markers` rejects an unknown marker. Collection of a unit item that has `django_db` raises `pytest.UsageError` and names the node ids. Move that test to `tests/integration/`.

An autouse unit fixture replaces `socket.socket.connect`. Hosts other than `127.0.0.1`, `::1`, and `localhost` raise immediately. Mock the client, or move the test.

## Database flags

`addopts` always includes `--reuse-db` and `--no-migrations`, plus `--tb=short`, `-v`, and `--maxfail=5`. Pytest will not create a schema and will not apply migrations. It reuses the database named in the test settings `TEST.NAME`, which is the same name as the default database. Point that database at the local stack: loopback, the direct session port, TLS off.

Create the schema before pytest:

```bash
# start the local stack, then:
DJANGO_SETTINGS_MODULE=<test module> python manage.py migrate --noinput
DJANGO_SETTINGS_MODULE=<test module> python manage.py setup_langgraph_persistence
```

The unit job does not start Postgres. It stays green because no unit test requests `django_db`. A unit test that did would hit `--reuse-db` with nothing listening. The local unit target inherits the same `addopts` and does not pass `-m`. CI does.

## Integration isolation

Async repositories open their own database connection on a worker thread. An outer `atomic` block does not wrap them, so rollback never sees those rows. `django_db(transaction=True)` flushes the database. These tests share the local database with whatever else uses that stack. Do not flush it.

The `committed` fixture unblocks the Django database blocker for the whole test, yields a seed, and deletes in `finally`.

```python
class CommittedSeed:
    def __init__(self):
        self.user_id = uuid4()
        self._tracked = []

    def create(self, factory, **kwargs):
        obj = factory.create(**kwargs)
        self._tracked.append(obj)
        return obj

    def sweep(self, model, **filters):
        self._sweeps.append((model, filters))

    def cleanup(self):
        for obj in reversed(self._tracked):
            type(obj).objects.filter(pk=obj.pk).delete()
        for model, filters in reversed(self._sweeps):
            model.objects.filter(**filters).delete()
```

`create` is synchronous. `acreate` runs the factory through `sync_to_async` so an `async def` test does not touch the ORM on the event-loop thread. `sweep` is for rows the code under test inserts, filtered tightly (the seed's user id, not the whole table). Cleanup is best-effort and swallows a delete error. A crash before `finally` is the only path that skips it. Children are deleted before parents because the lists are reversed.

Factories are `factory_boy` `DjangoModelFactory` classes with `LazyFunction(uuid4)` ids. Pass the seed's `user_id` into the factory. Do not rely on a factory default that generates a different owner than the JWT subject.

Redis in this suite is the process client the views import, connected to `REDIS_URL`. The fixture closes it, calls initialize, and closes it again on the way out. It skips when `REDIS_URL` is empty or the server does not answer. The views and the test share that client. Unit tests that need Redis use `fakeredis`.

Authenticated calls use an in-process async client. A helper signs an ES256 JWT with the test private key (`m2m-jwt-service-auth`): `sub` is the user id, `iss` and `aud` come from settings, lifetime defaults to 3600 seconds. The fixture sends `Authorization`, `X-User-Created-At`, and `X-Client-Local-Time` on every verb. Extra `headers=` merge over those defaults. The async client reads `headers=`. WSGI-style `HTTP_*` kwargs are ignored.

One graph file runs only when `RUN_AGENT_INTEGRATION=1`. The default integration target does not set it.

## Keys the workflow supplies

The workflow does not generate a key. It assigns fixed test values, and the test settings module embeds a deterministic ES256 keypair used only to sign and verify those tests.

| Setting | Where |
| --- | --- |
| Field-encryption key, 64 hex characters | Workflow env on the migration, unit, and integration steps. Root conftest `setdefault` so a local unit run boots. |
| M2M public key, PEM | The test settings module. The unit and integration steps also set the env var, because those jobs verify tokens. |
| M2M private key, PEM | The test settings module only. Integration code signs with it. Do not put it in the unit job. |

Do not copy either PEM or the hex into docs, logs, or a second module. They are test material. Production boot still requires the real secret (`aes-gcm-field-encryption`, `django-settings-and-env`).

The test settings module also clears the log-ingest token before importing the base module, raises rate limits for the load harness, and blanks provider credentials so an import does not call a vendor. The OpenAPI schema app is installed only in this module, and its path is appended to the auth-exempt and rate-limit-exempt lists.

## CI

Python 3.13. Pull requests into the default branch.

The unit job installs dependencies, exports the encryption key and the public key, and runs:

```bash
pytest tests/unit/ -m "unit and not slow" \
  --cov=<app package> --cov=<project package> \
  --cov-config=.coveragerc.unit \
  --cov-report=xml --cov-report=html --cov-report=term-missing \
  --cov-fail-under=70 \
  --junitxml=junit-unit.xml
```

The unit coverage config omits migrations, tests, HTTP handlers, middleware, URL routing, the ASGI and WSGI entry, the production settings module, admin, the OpenAPI module, and management commands. Those are what the integration job exercises. The 70 percent floor applies to what remains.

The integration job needs the unit job. It is skipped when the pull request comes from a fork. It installs a Postgres client and Redis, starts Redis, starts the local Supabase CLI, migrates with the test settings module, runs `setup_langgraph_persistence`, then `pytest tests/integration/` with coverage and no fail-under. `REDIS_URL` is `redis://127.0.0.1:6379/0`. Supabase is stopped with `--no-backup` even when the tests fail. Coverage upload does not fail the job.

Formatter, migration-safety, and the dependency audit are other jobs in the same workflow. They are not this suite. Release order is `azure-container-apps-and-cicd`. The load harness is not a CI job (`k6-backend-load`).

## Failure prevention

- Unit tests do not use `django_db` and do not open a non-local socket. The collection hook and the socket fixture are the gates.
- `--reuse-db --no-migrations` means a missing `migrate` shows up as a relation error, not as pytest creating a database.
- Do not mark an async integration test `transaction=True`. That flushes the shared local database.
- Seed with a fresh user id and delete those primary keys. A `sweep` filter must include that id.
- The encryption key and the JWT keypair in CI are fixed test values. The job does not generate them. Do not reuse them outside the test module.
- Fork pull requests skip integration. Unit tests still run.
- `maxfail=5` stops the run. A green unit job can still hide later failures.

## Tests of the harness

- Collecting a unit module that is marked `django_db` fails with `UsageError` and lists the node id.
- A unit test that connects to a non-local host raises the socket guard's `RuntimeError`.
- `committed.cleanup` deletes the created row and a swept row, children first.
- A signed test token verifies under the test public key. A missing `REDIS_URL` skips the Redis fixture instead of failing the suite.

## Checklist

- [ ] `DJANGO_SETTINGS_MODULE` is the test module for pytest, migrate, and the persistence setup command.
- [ ] Unit CI is `-m "unit and not slow"` and `--cov-fail-under=70`. Integration has no coverage floor and does not run for a fork.
- [ ] Schema exists before pytest because of `--reuse-db --no-migrations`.
- [ ] Async tests use `committed` and delete by primary key. They do not flush.
- [ ] CI sets the field-encryption key and the M2M public key. The private key stays in the test settings module and is not a unit-job env var.
- [ ] Vendor calls are mocked in both suites. Redis is fake in unit tests and `REDIS_URL` in integration tests.
