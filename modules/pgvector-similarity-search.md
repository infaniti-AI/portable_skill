---
name: pgvector-similarity-search
title: pgvector cosine search with a typed query vector
summary: Store 384-dimension embeddings and rank rows with cosine distance, casting the query to extensions.vector so the non-superuser API role can run the operator. Use this for semantic search over a user's items, a shared catalog, past chat turns, and knowledge chunks.
stack: [django, postgres, pgvector]
tags: [search, embeddings, pgvector]
requires: [supabase-rls-client-reads]
related_skills: [ai-chat-with-memory]
status: stable
updated: 2026-10-21
---

# pgvector cosine search with a typed query vector

Semantic search embeds text with the same model that wrote the stored vectors, then orders rows by cosine distance. The database role is `app_api` from `supabase-rls-client-reads`. That role is not a superuser. A query vector bound as `unknown` makes `<=>` fail for `app_api` and succeed for a superuser. Cast the literal to `extensions.vector` in the SQL expression.

The extension lives in schema `extensions`. The operator stays unqualified and is found because `app_api`'s `search_path` is `public, extensions`. The current migrations create no ANN index. Nearest-neighbor is a sequential scan of the filtered set: `ORDER BY embedding <=> query`.

## Architecture

```mermaid
flowchart LR
  API[API handler]
  Embed[Embedding client]
  DB[(Postgres app_api)]
  API -->|user id from JWT| DB
  API -->|query text, max 500 chars| Embed
  Embed -->|384 floats| API
  API -->|cosine distance, typed cast| DB
```

Writes and reads go through the API as `app_api`. The handler filters on the verified user id (`m2m-jwt-service-auth`). RLS does not isolate that role. Shared catalog rows are visible to every caller; the query still applies the catalog's publish rules in SQL.

Response JSON is built by one serializer per entity. That serializer drops the vector. The browser never receives an embedding column.

## Schema

Every vector column is `vector(384)`, nullable. A null means "not embedded yet" and is excluded from search.

| Table | What is embedded | Extra filters |
| --- | --- | --- |
| `items` | Item body only, at save time. The title is not part of the vector | `user_id`, `is_deleted = false`, optional mood and time range |
| `catalog_items` | Name, tagline, description, and personality, truncated to 500 characters, and only after publish | Visibility rules below |
| `chat_turns` | `User: {question}\n{catalog name}: {answer}`, not truncated. Question and answer columns are encrypted | `user_id`, optional catalog id, exclude the current thread |
| `knowledge_chunks` | Chunk text | Optional `source` |
| `curated_items` | The text used to personalize a catalog of recommendations | Category, then cosine against a summary vector |

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
GRANT USAGE ON SCHEMA extensions TO app_api;
ALTER ROLE app_api SET search_path TO public, extensions;

ALTER TABLE items ADD COLUMN embedding extensions.vector(384);
```

`GRANT USAGE` is required even for the schema-qualified cast. Moving an extension that was created in `public` is `ALTER EXTENSION vector SET SCHEMA extensions`. A bare `CREATE EXTENSION vector` lands in `public` and the cast `::extensions.vector` then fails.

There is no HNSW or IVFFlat index in the migrations. Add one only after measuring a catalog that is too large for a sequential scan, and build it with the same distance operator the query uses (`vector_cosine_ops`).

## Embedding client

One provider per process, selected by `LLM_PROVIDER` (`gemini` or `azure`). Dimension is a constant, `384`. The client passes `dimensions: 384` and rejects a response whose length is anything else. Do not store a mismatched vector.

| Name | When |
| --- | --- |
| `LLM_PROVIDER` | `gemini` (default) or `azure`. Any other value raises at startup. |
| `GEMINI_EMBEDDING_MODEL` | Default `gemini-embedding-001`. A bare name is prefixed `vertex_ai/`. |
| `VERTEX_AI_SERVICE_ACCOUNT_FILE` | Path to the service-account JSON. Relative paths are from the repo root. Sets `GOOGLE_APPLICATION_CREDENTIALS`. |
| `VERTEX_AI_PROJECT` | Vertex project. If empty, read `project_id` from the service-account file. |
| `VERTEX_AI_LOCATION` | Default `us-central1`. A model id containing `gemini-3` uses `global` instead. |
| `AZURE_EMBEDDING_DEPLOYMENT` | Default `text-embedding-3-small`. Sent as `openai/<deployment>` unless it already has an `openai/` or `azure_ai/` prefix. |
| `AZURE_OPENAI_API_KEY` | Copied to `AZURE_API_KEY` for the client library. |
| `AZURE_OPENAI_ENDPOINT` | Stripped, trailing slash removed, then `/openai/v1` appended when missing. Copied to `AZURE_API_BASE`. |

Call the embedding API with a 4 second timeout. Log elapsed time and the model id. Do not log the input text.

```python
EMBEDDING_DIMENSIONS = 384
QUERY_MAX_CHARS = 500

async def embed(text: str) -> list[float]:
    response = await asyncio.to_thread(embedding, **params, input=text)
    item = response.data[0]
    vector = item["embedding"] if isinstance(item, dict) else list(item.embedding)
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Expected {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}")
    return vector
```

Truncation differs by caller:

- Saving an item embeds the body and not the title. A failure aborts the save. The row is not written with a null vector after a failed embed.
- A search query and a catalog-item embed use `text[:500]`. A search embed that throws is logged and treated as "no vector", so the handler returns no semantic hits.
- A stored chat turn embeds `User: {question}\n{catalog name}: {answer}` with no 500-character cap. Skip the write when either side is missing. Retrieval embeds a different string, capped at the last 4000 characters: the first turn is the latest user message; a later turn is the previous user message, the previous reply, and the latest user message, each on its own line with a `user:` or `assistant:` prefix. When that text is empty, reuse the newest stored vector for the thread and do not call the provider.
- A curated-item query either embeds a summary string truncated to 500 characters, or reuses the newest item vector and does not call the provider.
- A catalog item is embedded only when it is published. The text is name, tagline, description, and personality joined by spaces. Category, function, and avatar paths are left out. A blank string stores null. An embed error on catalog write is best-effort: the row can exist with a null vector and stay out of vector search.
- Unpublished custom catalog rows stay at null until publish.

A test process (`ENV=test`) skips the provider and returns `[0.01] * 384`. Seed data uses that same constant so cosine search still hits the database. Production must not set `ENV=test`.

Re-embed missing catalog vectors with the joined text, truncated to 500 characters, in batches, with a dry-run. Skip rows whose joined text is empty. A narrower command that embeds only the description field will disagree with search. Use the joined text.

## Distance expression

`pgvector.django.CosineDistance` binds the query as text (`unknown`). Superusers coerce that. `app_api` cannot, and Postgres reports `extensions.vector <=> unknown`. Build the literal from floats and cast it.

```python
from django.db.models import FloatField, Func, Value

def vector_literal(embedding: list[float] | str) -> str:
    if isinstance(embedding, str):
        return embedding
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"

class CastToVector(Func):
    arity = 1
    template = "(%(expressions)s)::extensions.vector"

class CosineDistance(Func):
    output_field = FloatField()
    arity = 2
    function = ""
    arg_joiner = " <=> "
    template = "(%(expressions)s)"

    def __init__(self, expression, embedding, **extra):
        super().__init__(
            expression,
            CastToVector(Value(vector_literal(embedding))),
            **extra,
        )
```

`float(x)` is what keeps user text out of the SQL literal. Pass a sequence of numbers, or a bracket literal you already validated. Similarity is `1 - distance`. Higher is closer. A null distance becomes similarity `0` and fails the threshold.

## Queries

Scope every user-owned query with the JWT subject. Order by distance ascending. Keep rows at or above the threshold. Return at most `limit`.

Because similarity is `1 - distance`, taking the nearest `limit` rows and then dropping those under the threshold returns the same set as filtering in SQL and then limiting. Item search and chat search do the filter in Python after the slice. Catalog search filters `vector_score >= threshold` in SQL, then paginates. Both are safe only while the score stays monotonic with distance.

Item search defaults: service threshold `0.7`, limit `50`. The repository default, used by retrieval that is not the search box, is threshold `0.6` and limit `5`. Catalog search from the search box uses `0.6`. The catalog method's own default, when the caller passes no threshold, is `VECTOR_MIN_SCORE = 0.3`. Chat retrieval uses `0.6` and limit `5`.

```python
def has_embedding(embedding) -> bool:
    return embedding is not None and len(embedding) > 0

async def find_similar_items(user_id, embedding, *, limit, threshold, mood, start, end, days, reference_now):
    if not has_embedding(embedding):
        return []
    qs = Item.objects.filter(user_id=user_id, is_deleted=False, embedding__isnull=False)
    if days is not None:
        if reference_now is None:
            raise ValueError("reference_now is required for a recency cutoff")
        qs = qs.filter(occurred_at__lt=reference_now - timedelta(days=days))
    qs = apply_mood_and_range(qs, mood, start, end)
    hits = []
    async for row in qs.annotate(distance=CosineDistance("embedding", embedding)).order_by("distance")[:limit]:
        similarity = 1 - row.distance if row.distance is not None else 0
        if similarity >= threshold:
            hits.append(row)
    return hits
```

`days` means "older than this many days", measured from the client clock `reference_now`. Server `now()` is the wrong clock for that window.

Chat search adds `audience_id` (the catalog item this thread is with) and `exclude(thread_id=current)`. A short follow-up can reuse the newest stored vector for that thread instead of embedding a thin message:

```python
row = await ChatTurn.objects.filter(
    thread_id=thread_id, user_id=user_id, embedding__isnull=False
).order_by("-created_at").only("embedding").afirst()
if row is None or row.embedding is None:
    return None
return list(row.embedding)
```

Catalog visibility, applied before the score filter:

- Default: platform rows (`is_custom = false`) plus custom rows that are published, not paused, and not deleted.
- Community-only: custom, published, not paused, not deleted.
- An unpublished row the caller created stays out of search. A ranking job that must see it opts in with a separate flag.

Catalog vector search returns `[], 0` when the query vector is missing. Hybrid search can still rank on text; that path is `lexical-search`.

Routing shared with lexical search: count tokens in the query. One to three tokens try the keyword ranker first. Zero hits, or four or more tokens, use this vector path. Item keyword search loads that user's decrypted rows into memory. Catalog keyword search is the hybrid of `tsvector` and this distance.

## Wire format

`asdict` on the domain object includes `embedding` and the internal timestamp. Every item, catalog, and curated-item response goes through one function that removes them.

```python
def serialize_item(entry, score=None):
    item = asdict(entry)
    item["id"] = str(entry.id)
    item["occurred_at"] = (
        entry.occurred_at_.strftime("%Y-%m-%d %H:%M:%S") if entry.occurred_at_ else None
    )
    item.pop("occurred_at_", None)
    item.pop("embedding", None)
    if score is not None:
        item["search_score"] = score
    return item
```

Catalog serialization also drops `embedding` and the owner id, and adds a boolean for "created by the caller". Curated items list the public fields and omit the vector. Chat-turn entities used inside the server decrypt text fields and do not copy `embedding` onto the object that leaves the repository, except the explicit "latest vector for this thread" helper, which stays on the server.

A list endpoint and a detail endpoint both call the same function. A second mapper that forgets `pop("embedding")` puts the full 384 floats, and the raw timestamp field, on the wire.

## Failure prevention

- **P10.** Cast with `::extensions.vector`. Install `vector` in `extensions`, `GRANT USAGE ON SCHEMA extensions` to `app_api`, and set `search_path` to `public, extensions`. Reproduce the query as `app_api`, not as a superuser. A superuser success hides the `unknown` bind.
- **P12.** A vector from the ORM or from NumPy has no boolean value. `if embedding` raises. Test `embedding is not None` and then `len(embedding) > 0`. Use that helper before similarity search and before any job that reads a stored vector. A Python `list[float] | None` from the embed client is safe to length-check the same way.
- **P23.** One serializer per entity. Strip `embedding` and the internal timestamp there. Clients never see vector columns. Review any new `asdict` or `values()` response before it ships.
- Filter user-owned tables on the JWT subject. `app_api` can read every row.
- Reject a vector whose length is not 384. Mixed dimensions make `<=>` fail or return nonsense.
- Keep the query literal numeric. Build it with `float(x)` or pass a bound parameter that is still cast with `::extensions.vector`.
- Item save: if embed throws, fail the save. A stored body with a null vector cannot be found later.
- Search embed: if the provider throws, return an empty semantic result. Do not fail the whole request when a keyword result was already empty and the provider is down.
- Pass `reference_now` whenever a day window is applied.

## Tests

Run the SQL as `app_api`.

- A stored vector of 384 times `0.5` and a query of 384 times `0.5` returns similarity `1` and is included at threshold `0.6`.
- A stored vector of 384 times `-0.9` against a query of 384 times `0.9` is excluded at threshold `0.6`.
- `LIMIT` keeps the nearest rows. A second user's rows are absent even when their vector matches.
- The SQL contains `::extensions.vector`. The same statement run as a role without `USAGE` on `extensions` fails.
- `has_embedding(None)` is false. `has_embedding([])` is false. A NumPy array of 384 floats does not get used as a boolean.
- `embed` raises when the provider returns a length other than 384.
- `ENV=test` returns the constant vector and does not call the provider.
- Serializer output has no `embedding` key and no internal timestamp field. A catalog payload has no owner id.
- A query of four tokens does not call the keyword ranker. A query of two tokens uses keyword results when that ranker returns a row, and falls through to cosine when it returns none.

## Checklist

- [ ] `vector` is in schema `extensions`. `app_api` has `USAGE` and `search_path` `public, extensions` (P10).
- [ ] Columns are `vector(384)`, nullable. The client rejects any other length.
- [ ] Distance uses `CastToVector` / `::extensions.vector`, including queries that used to call `pgvector.django.CosineDistance`.
- [ ] User-owned searches filter on the JWT subject. Catalog searches apply the publish rules.
- [ ] Thresholds match the caller: item search `0.7`, catalog search box `0.6`, chat `0.6`, catalog default `0.3`.
- [ ] Item save embeds the body only and aborts on failure. Search and catalog embeds truncate to 500 characters. Chat-turn vectors use the labeled question and answer with no cap.
- [ ] Vector checks use `is not None` and `len` (P12).
- [ ] Every response serializer drops the vector and the internal timestamp (P23).
- [ ] Tests run as `app_api`. `ENV=test` is off in production.
