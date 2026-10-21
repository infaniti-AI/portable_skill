---
name: ai-chat-with-memory
description: Stream a tool-calling chat graph with encrypted checkpoints, hierarchical memory, cosine retrieval, and masked traces. Use when a reply must survive a refresh, keep writing memory after the browser has the final text, and user text must not leave the process unmasked.
---

# AI chat with memory

This skill orders one chat turn. It does not replace the modules. The browser posts a message and reads server-sent events. The graph, the checkpoints, and the memory writes run in a worker.

The signed-in caller comes from `supabase-magic-link-auth` and `m2m-jwt-service-auth`. If that session is missing, stop and run `bootstrap-next-supabase` first. The Redis broker, actor retries, and the ORM connection release are `dramatiq-redis-workers` in `background-jobs`. Stand that worker up before the first actor.

## Load the modules

Follow the `search-modules` skill. From the catalog root:

```bash
python3 "$ROOT/skills/search-modules/scripts/search.py" langgraph encryption pgvector langfuse chat
```

Open these files in this order. For each file, open every name in `requires` and repeat until no new name appears.

1. `aes-gcm-field-encryption`
2. `langgraph-postgres-persistence`
3. `langgraph-streaming-chat`
4. `pgvector-similarity-search`
5. `langfuse-and-log-ingest`

`langgraph-streaming-chat` also requires `dramatiq-redis-workers` and `m2m-jwt-service-auth`. `pgvector-similarity-search` requires `supabase-rls-client-reads`. Read those before writing the send route or a similarity query. Load `nextjs-cache-components` for the stream route: it calls `connection()` before its `try`, so a prerender bailout is not logged as a stream failure. Load `subscription-entitlements` when send must refuse over the plan cap. The graph receives retrieval limits as integers. It does not hard-code plan numbers.

Follow each module's architecture, env names, schema, setup, failure prevention, and checklist. Stop at a line marked `> NOTE: unverified` and confirm it in current vendor docs.

## Build order

### 1. Key and stored shapes

Implement `aes-gcm-field-encryption` before any private column or store write. `FIELD_ENCRYPTION_KEY` is 64 hex characters. Production boot (`DJANGO_DEBUG` false) refuses to start when it is missing. A missing key in development stores plaintext. Turn that off before a shared environment.

Field strings are `iv:ciphertext:tag`. JSON is `json.dumps` with `sort_keys`, then that format. Checkpoint blobs use the `AESGCM1` prefix. Store values are the dict `{"enc": "<ciphertext>"}` (P08). A bare encrypted string is not JSON. `asearch` throws inside the jsonb reader, and one bad row fails the whole batch. Decrypt only after the read returns.

Vectors stay plaintext. Ciphertext cannot be an operand of `<=>`. Chat-turn question and answer columns are encrypted. The embedding column is not.

### 2. Checkpointer and store

Implement `langgraph-postgres-persistence` on its own psycopg pool. Prefer `LANGGRAPH_DATABASE_URL`. Otherwise use the `app_api` fields and port `6543`. This is not Django's pool and not the admin session port.

Connection kwargs set `prepare_threshold=None`, `supports_pipeline=False` on the saver and the store, and `statement_timeout=15000` (P09, P07). `prepare_threshold=0` still prepares. Pool `min_size` is `0`, `max_idle` is `60`, checkout timeout is `10`. A request must not call `pool.close()`. The worker warms the pool at boot and leaves it open.

`saver.setup()` and `store.setup()` run once per environment from the management command, after migrations. They are not a Django migration. The command closes the pool in `finally`. Repair string-typed `store.value` rows for this app's prefixes before serving search. Dry-run is the same `WHERE` as a count.

There is no Redis lock in front of the checkpointer (P07). One `thread_id` is serialized by the checkpoint lock. A waiter cancelled after 15 seconds is `QueryCanceled`. Tell that user the turn failed. A 10-second checkout timeout is a full pool. Log them as different errors.

Nested summary agents pass `checkpointer=False`, `InMemoryStore()`, and a new `memory-{uuid}` thread id. `checkpointer=None` inherits the parent saver and waits on the same lock.

### 3. Graph and stream

Compile the graph once per process with the checkpointer, the store, and both stream transformers. `astream_events` uses `version="v3"` and `async with await`. Stay inside that context until memory nodes finish. Close it early only for stop and for a failed reply.

Node caps are the table in `langgraph-streaming-chat`. The older design note (answer 15s/5s, memory 10s/8s) is stale. Answer fallbacks are a loop in the `llm` node. LiteLLM `fallbacks` do not run when `streaming=True`. Do not fan out after `finalize`. A parallel superstep holds the checkpoint lock until every sibling finishes.

`asearch` takes the namespace as a positional tuple and `limit` as a keyword. `namespace_prefix=` raises `TypeError` on this pin. Do not catch that and return `[]` (P32). Let the memory node log it and continue. Decrypt `item.value` only after `asearch` returns.

The web-search helper is not in the bound tool list. `TAVILY_API_KEY` and `EXA_API_KEY` do nothing until a product decision binds it.

Send checks entitlement before insert. Denial is 403 with `upgrade_required: true` and no enqueue. Require `X-Client-Local-Time`. The run row stores it. Overlap the broker `send` with the user-message insert. Prior turns come from the checkpointer, not from a reload of the messages table.

Redis: the list is the stream. Pub/sub is only a wake-up. Subscribe before `LRANGE`. Ignore every frame whose `type` is not `message`. The first frame is a subscribe acknowledgement. Treating it as the end of the channel closes SSE before any token (P06). Yield `: connected\n\n` first. Do not delete or replace the list. A client tracks an index. The error sentence is pushed onto the list and is not inserted into `messages`.

`agent_terminated` sets `completed`, publishes `END_STREAM`, and keeps the actor in the memory nodes. The push of later chunks happens only while status is `running`. The browser already has the complete assistant frame.

On every actor exit, if status is still `running`, write `failed` before returning, then re-raise a limit or cancellation (P05). The reference `finally` only expires the list. Resume a thread only when the row is `running` and `created_at` is under 90 seconds. The read helper that filters `status = running` does not apply that age check. Add it before opening SSE. Stream and stop 404 a run owned by someone else.

### 4. Embeddings

Chat-turn vectors are 384 floats from the same model that writes them (`pgvector-similarity-search`). Cast the query with `::extensions.vector`. Install `vector` in schema `extensions`, grant `app_api` `USAGE`, and set `search_path` to `public, extensions` (P10). Reproduce the query as `app_api`. A superuser success hides an `unknown` bind.

Test a vector with `is not None` and `len`. `if embedding` raises on a NumPy value (P12). The chat-turn string is `User: {question}\n{catalog name}: {answer}` with no character cap. Item and catalog query text still truncate at 500 characters. Reject a vector whose length is not 384. Every response serializer drops `embedding` and the internal timestamp (P23). The insert after the reply is fail-soft. The user already has the text.

### 5. Traces and logs

Import the mask installer once, immediately after `django.setup()`, and before any module that calls `get_client()` at import. The stream module does. Init failure sets tracing off. There is no path that exports without the hook (P33).

`mask_otel_spans` rewrites Langfuse content keys. A raising hook drops the batch. That is the safe direction. `PII_MASKING_ENABLED` true runs the pattern recognizer. False replaces those attributes with `<REDACTED>`. It never sends the original. Do not install a GenAI instrumentor that copies prompts into `gen_ai.*` keys. Those keys are not in the mask list.

Hash user and session ids. Leave the raw user id off metadata. Generation input is prior user turns, the last assistant text, and the current user text. Leave the system prompt off that object. Do not call `flush()` at the end of the generator. Flush waits on the exporter and blocks the actor after the graph has finished. A tracing error in the consumer must not fail the turn.

`LANGFUSE_HOST` is the host for that environment's project. Read it from the environment and default only when it is empty. Do not overwrite a host the operator already set. Clamp `LANGFUSE_SAMPLE_RATE`. Non-dev default is `0.2`. The SDK default of `1.0` is what you get if settings never export the variable.

Logs use `sys.__stdout__` and `sys.__stderr__`, then `basicConfig(..., force=True)`. `show_locals` is false. Do not pass message bodies into log events. `BETTERSTACK_INGESTING_HOST` is a hostname with no scheme, set per environment before the handler is built (P34). A token with an empty host fails startup. No hostname literal in the image.

## Cross-checks

- Store writes, checkpoint blobs, and column ciphertext are three formats. Store search uses `{"enc": ...}`. The checkpointer serializer does not wrap store rows. Repair string-typed store rows before the first `asearch` (P08).
- The chat pool is the transaction pooler on port `6543` with `prepare_threshold=None` and a 15-second statement timeout. Django's ORM pool is a different object. The admin session port `5432` is not this URL.
- `END_STREAM` means the reply is done. Memory nodes still run. A subscriber that treats the subscribe acknowledgement as `END_STREAM` paints nothing (P06). A `running` row older than 90 seconds is a dead actor (P05).
- `asearch((thread_id, "thread_summary"), limit=10)` is the call shape. A keyword namespace is a bug, not an empty memory (P32).
- Similarity SQL contains `::extensions.vector` and a filter on the JWT subject. Serializers omit the vector (P10, P23).
- Observations are recorded in the stream consumer, then masked on the export thread. The actor does not flush. Two environments do not share a log ingest host (P33, P34).

## Finish

Walk the checklist at the end of each of the five modules, plus `dramatiq-redis-workers` if this round added the actor. Then confirm:

- Production boot fails when `FIELD_ENCRYPTION_KEY` is missing. A store round-trip returns the object, and the stored `value` is a JSON object with key `enc`.
- Pool kwargs have `prepare_threshold is None` and `statement_timeout=15000`. Nested agents pass `checkpointer=False` and a new thread id.
- A subscribe acknowledgement does not close SSE. `END_STREAM` does, after a list drain. An actor that exits while status is `running` writes `failed`.
- A `running` row older than 90 seconds is not resumed. Another user's run id is 404.
- `asearch` with `namespace_prefix=` raises, and that `TypeError` is logged rather than returned as `[]`.
- A chat-turn search as `app_api` uses `::extensions.vector`. The HTTP response has no `embedding` key.
- A sampled trace shows `<EMAIL_ADDRESS>` or `<REDACTED>` and does not show the original address. The actor returns without calling `flush()`.
- Two environments build log handlers pointed at different hosts. A token with an empty host refuses to boot.
