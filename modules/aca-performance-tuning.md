---
name: aca-performance-tuning
title: Container Apps CPU, scale rules, and probes
summary: Size Consumption replicas on a legal CPU and memory pair, scale the API on HTTP concurrency and the worker on Redis list length, and probe the API with Host localhost. Use this when an internal API and a queue worker share an image and must stay warm.
stack: [azure, terraform]
tags: [performance, containers, scaling]
requires: [azure-container-apps-and-cicd]
related_skills: []
status: stable
updated: 2026-11-03
---

# Container Apps CPU, scale rules, and probes

One image, three apps. This module is how big each replica is, when a new replica starts, and how the platform knows the API is up. Image tags, the release job, and OIDC are `azure-container-apps-and-cicd`. The tunnel token and why ingress stays internal are `cloudflare-tunnel`. Actor retries and the ORM thread are `dramatiq-redis-workers`.

The environment uses the built-in Consumption profile. Each app sets `workload_profile_name = "Consumption"`. Consumption bills per replica for the time that replica is running. A minimum above zero is an idle floor, not a scale-to-zero app.

Microsoft's container docs list the legal Consumption pairs: 0.25 vCPU with `0.5Gi`, then 0.5 vCPU with `1.0Gi`, up through 4 vCPU with `8Gi`, with memory in GiB equal to twice the vCPU. A Consumption-only environment stops at 2 vCPU and 4 GiB. A pair that is not on that list is rejected. The variable comments in this repo state the same 2 GiB per vCPU rule.

Azure's scale defaults, when the fields are omitted, are minimum 0 and maximum 10. This spec sets both so apply does not fall back to scale-to-zero.

## Sizes

| App | CPU | Memory | Min | Max | What adds a replica |
| --- | --- | --- | --- | --- | --- |
| API | 1 | `2Gi` | 1 | 3 | HTTP, 50 concurrent requests |
| Worker | 1 | `2Gi` | 2 | 4 | Redis list `dramatiq:default`, target length 8 |
| Tunnel | 0.25 | `0.5Gi` | 2 | 2 | Nothing. Min equals max |

1 vCPU and `2Gi` is a listed pair and sits under the Consumption-only cap. The tunnel's 0.25 / `0.5Gi` is the smallest listed pair. The tunnel does not take request traffic; two fixed replicas are enough for the outbound connections. Do not put an HTTP scale rule on an app with no ingress.

## API process

The container command replaces the image `CMD`. Both are one Uvicorn process:

```text
uvicorn app.asgi:application --host 0.0.0.0 --port 8000 --timeout-keep-alive 120 --timeout-graceful-shutdown 30
```

There is no `--workers` flag. A second Uvicorn worker would be another process on the same 1 vCPU and the same `2Gi`. The image comment leaves Gunicorn out at this size: it is a process manager, and one process is the replica.

`--timeout-graceful-shutdown 30` is the process budget. The app template sets `termination_grace_period_seconds` to 35. The platform wait stays longer than the process timeout so in-flight requests finish before the container is killed.

`--timeout-keep-alive 120` controls idle HTTP connections between requests, independently of shutdown grace. The per-hop timer checks and bounded GET retry policy are in `http-keepalive-econnreset` (P45).

Ingress is internal, port 8000, transport `http`. Internal ingress still has an HTTP concurrency count. The scale rule replaces Azure's implicit rule.

```hcl
http_scale_rules = [
  {
    name                = "http"
    concurrent_requests = 50
  }
]
```

Microsoft's scale docs: the default concurrent-request threshold is 10, and the count is calculated every 15 seconds. Setting `concurrent_requests` to 50 means a replica is not added until that replica is busier than the default. Minimum 1 keeps a warm replica (the platform default of 0 would scale to zero). Maximum 3 caps a spike. One rule is enough. Microsoft documents that when several rules are set, the first condition that is met scales out.

## Worker process

The worker command is required. Without it the same image runs Uvicorn.

```text
python -m dramatiq run_workers --processes 1 --threads 8
```

`--processes 1` is one Dramatiq process on the 1 vCPU pair. `--threads 8` is the width. The variable comment says these jobs wait on model I/O, and one thread would serialize the queue. Threads buy concurrent waits. They do not add ORM concurrency: `dramatiq-redis-workers` runs the synchronous ORM on one thread. A second process on this pair would split `2Gi` without raising that ORM limit.

The worker has no ingress and `enable_http_probes` is false. It does not scale on HTTP. CPU and memory stay at the pair above while the queue grows. Replicas are the only scale dimension.

```hcl
custom_scale_rules = [
  {
    name             = "dramatiq-queue"
    custom_rule_type = "redis"
    metadata = {
      address    = var.redis_scaler_address
      listName   = "dramatiq:default"
      listLength = "8"
      enableTLS  = "true"
    }
    authentication = [
      {
        secret_name       = "redis-password"
        trigger_parameter = "password"
      }
    ]
  }
]
```

`address` is `host:port`, not a URL. The password is a Key Vault secret on the scale rule. It is not interpolated into the address. Metadata values are strings. `listName` is Dramatiq's key for the default queue. `enableTLS` is true because the broker connection is TLS.

`listLength` is `"8"`. `dramatiq-redis-workers` treats a depth of about 8 as the target per busy replica, matching `--threads 8` so one replica can hold a short queue in I/O waits. Minimum 2 means two consumers stay up when the list is empty, so one replica dying does not stall the queue. Maximum 4 is the cap.

> NOTE: unverified against the KEDA Redis scaler page in this write. Microsoft documents Redis as a custom scale rule and documents `listLength` as scaler metadata. The replica formula KEDA derives from that key was not re-read here. Do not invent a different threshold than `"8"`.

## Probes

Probes exist only when `enable_http_probes` is true. That flag is true on the API and false on the worker. All three API probes are HTTP, port 8000, path `/health/`, timeout 5 seconds, and they send `Host: localhost`.

| Probe | Interval | Failures before action | Successes required |
| --- | --- | --- | --- |
| Startup | 5 seconds | 24 | platform default |
| Liveness | 20 seconds | 3 | platform default |
| Readiness | 10 seconds | 3 | 1 |

Startup's 24 failures at 5 seconds is about 120 seconds of boot before the probe fails the container. Liveness restarts a replica that stops answering. Readiness pulls a replica out of ingress rotation. The Docker `HEALTHCHECK` in the image is a separate `curl` of `http://localhost:8000/health/`. It is not the platform probe. Both send `Host: localhost`.

Django returns 400 when that host is missing from `ALLOWED_HOSTS`. Production always includes `localhost` and `127.0.0.1`. The tunnel's origin name is a different `Host` and belongs in the same list (`cloudflare-tunnel`). Reject `*`. The settings overlay is `django-settings-and-env`. `/health/` is auth-exempt and rate-limit exempt, so a probe does not need a token and does not consume the API quota.

## Setup

1. Pick a Consumption pair from Microsoft's list. Keep memory in GiB at twice the vCPU. Stay within 2 vCPU and 4 GiB on a Consumption-only environment.
2. Set minimum and maximum replicas. API minimum 1. Worker minimum 2. Tunnel minimum equals maximum.
3. Set one HTTP rule on the API at 50 concurrent requests. Leave the worker's HTTP rule list empty.
4. Point the worker rule at `host:port`, list `dramatiq:default`, length `8`, TLS on, password from the vault secret `redis-password`.
5. Run one Uvicorn process with `--timeout-keep-alive 120` in both the image CMD and API override, and one Dramatiq process with 8 threads. Keep the platform grace (35) above the Uvicorn shutdown grace (30).
6. Turn probes on only for the API. Path `/health/`, header `Host: localhost`. Allow that host.

## Failure prevention

- A CPU and memory pair that is not on the Consumption list fails apply. Do not set 1 vCPU with `1Gi` or `4Gi`.
- Omitting `workload_profile_name` clears the live Consumption profile on apply.
- Omitting minimum replicas uses Azure's default of 0 and the API goes cold.
- Leaving the HTTP rule unset uses the implicit threshold of 10, so the API scales out sooner than this spec.
- A worker command that is missing runs Uvicorn. The queue is not consumed.
- `--threads 1` serializes I/O-bound actors. `--processes` greater than 1 on this pair does not add an ORM thread.
- Probes without `Host: localhost`, or `ALLOWED_HOSTS` without `localhost`, return 400. The revision never becomes ready.
- Probes on the worker have no port to hit. Keep `enable_http_probes` false there.
- Putting the Redis password in `address` leaks it into scaler metadata. Use the authentication block and `trigger_parameter` `password`.
- Scaling the tunnel with an HTTP rule does nothing useful. It has no ingress. Keep min and max at 2.

## Tests

`terraform plan` for the API shows 1 vCPU, `2Gi`, replicas 1–3, one HTTP rule with `concurrent_requests` 50, command containing `--timeout-keep-alive` `120` and ending in `--timeout-graceful-shutdown` `30`, and three HTTP probes on `/health/` with header `Host` `localhost`. Check that the image CMD also sets keepalive 120. Startup failure threshold is 24. Readiness success threshold is 1.

The worker plan shows 1 vCPU, `2Gi`, replicas 2–4, command `--processes` `1` `--threads` `8`, no ingress, no probes, and one `redis` rule whose metadata `listLength` is `8`, `listName` is `dramatiq:default`, and `enableTLS` is `true`. The password authentication secret is `redis-password`. The address is not a URL and does not contain the password.

The tunnel plan shows 0.25 vCPU, `0.5Gi`, replicas 2 and 2, and no scale rule.

A settings test boots production with `localhost` in `ALLOWED_HOSTS` and raises when the list contains `*`. A request to `/health/` with `Host: localhost` is 200 and does not require a bearer token.

## Checklist

- [ ] API and worker are 1 vCPU and `2Gi`. The tunnel is 0.25 vCPU and `0.5Gi`. Both pairs are on the Consumption list.
- [ ] API replicas are 1–3 with HTTP concurrency 50. Worker replicas are 2–4 on Redis list length 8. Tunnel replicas are 2 and 2.
- [ ] Uvicorn is one process, keepalive 120 in the image CMD and API override, graceful shutdown 30. Platform termination grace is 35.
- [ ] Dramatiq is `--processes 1 --threads 8`. The worker command is set.
- [ ] The Redis rule uses `host:port`, TLS, and a vault password. The password is not in the address.
- [ ] API probes are `/health/` with `Host: localhost`. The worker has probes off.
- [ ] `ALLOWED_HOSTS` includes `localhost` and rejects `*`.
- [ ] `workload_profile_name` is `Consumption` on each app.
