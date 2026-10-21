---
name: lexical-search
title: Keyword search for encrypted items and a catalog tsvector
summary: Rank a user's items with in-memory BM25 after decryption, and rank a shared catalog with a weighted Postgres tsvector built from ICU word breaks. Use this for short queries, and for any body that cannot be indexed because it is encrypted at rest.
stack: [django, postgres]
tags: [search, full-text, bm25]
requires: []
related_skills: []
status: stable
updated: 2026-10-21
---

# Keyword search for encrypted items and a catalog tsvector

Short queries (one to three tokens) are keyword queries. Longer queries go straight to embeddings (`pgvector-similarity-search`). Zero tokens match nothing.

Two indexes exist because the data is different:

- Item title, topics, and body are encrypted at rest. Postgres cannot tokenize ciphertext. Each search loads that user's rows, decrypts them in memory, and ranks with BM25. Nothing plaintext is written back.
- Catalog name and metadata are plaintext. A `tsvector` column is built in Python, with the same word breaker the query uses, and ranked with `ts_rank_cd`.

If this role also runs `<=>`, keep the `pgvector-similarity-search` setup: `vector` in schema `extensions`, `GRANT USAGE ON SCHEMA extensions`, and `search_path` `public, extensions`. The `tsvector` column stays in `public`. A `search_path` that cannot see `extensions` breaks the vector half of hybrid search while full text still works.

## Routing

```python
KEYWORD_MAX_TERMS = 3

def use_keyword(term_count: int) -> bool:
    return 0 < term_count <= KEYWORD_MAX_TERMS
```

`count_terms` is the number of ICU word tokens that are not English stopwords. The same count drives item search and catalog search.

Item search:

1. When `use_keyword` is true, load every non-deleted item for that user (optional mood and date filters in SQL, then again on the decrypted entities).
2. Rank in memory. If any score is positive, return those rows, capped at `limit`, with the BM25 scores, mode `keyword`.
3. Otherwise embed the query (500 characters) and run cosine search, mode `embedding`, scores `null`.

Catalog search:

1. Always embed the query. A provider failure leaves the vector null. Hybrid can still rank on text.
2. When `use_keyword` is true, run hybrid search. A non-empty page is mode `keyword`.
3. Otherwise, or when hybrid returns nothing, run pure vector search at threshold `0.6`, mode `embedding`.

## In-memory BM25

Libraries: `PyICU` (`icu`), `snowballstemmer`, `rank-bm25` (`BM25Okapi`).

Word breaks use the root locale, which splits writing systems that do not use spaces and keeps tokens such as `don't` and `next.js`.

```python
import icu

def segment_tokens(text: str) -> list[str]:
    if not text:
        return []
    breaker = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    breaker.setText(text)
    tokens = []
    start = breaker.first()
    for end in breaker:
        token = text[start:end]
        start = end
        if token and any(ch.isalnum() for ch in token):
            tokens.append(token.casefold())
    return tokens
```

Pipeline for one item, using the ISO language stored on the row at save time:

1. Strip HTML tags from the body (`<[^>]+>` replaced with a space) before tokenizing. Title and topics are tokenized as stored.
2. Drop this English stopword set. Other languages are not stop-filtered. BM25's inverse document frequency down-weights their common words.

```text
a an and are as at be but by for from had has have he her his i in is it its
me my no not of on or our she so that the their them then there they this to
us was we were what when which who will with you your
```
3. Snowball-stem when the language is in the map below. Other codes, including Chinese, Japanese, and Korean, stay as the casefolded token. A regional code uses the part before `-` (`pt-BR` looks up `pt`).

| ISO | Snowball |
| --- | --- |
| ar | arabic |
| da | danish |
| de | german |
| en | english |
| es | spanish |
| fi | finnish |
| fr | french |
| hi | hindi |
| hu | hungarian |
| id | indonesian |
| it | italian |
| nl | dutch |
| no | norwegian |
| pt | portuguese |
| ro | romanian |
| ru | russian |
| sv | swedish |
| ta | tamil |
| tr | turkish |

Field weight is token repetition, which raises BM25 term frequency:

| Field | Repeats |
| --- | --- |
| Title | 3 |
| Topics, joined with spaces | 2 |
| Body, after HTML strip | 1 |

The query is too short for language detection. Stem each query token under every language that appears on the loaded items, and keep the raw token as well, so an unstemmed document still matches. Union those forms into the query token list.

```python
def rank(entries, query: str) -> list[tuple]:
    if not entries or not query or not query.strip():
        return []
    corpus = [entry_tokens(entry) for entry in entries]
    if not any(corpus):
        return []
    languages = {entry.language for entry in entries if entry.language}
    query_tokens = tokenize_query(query, languages)
    if not query_tokens:
        return []
    scores = BM25Okapi(corpus).get_scores(query_tokens)
    ranked = [(entry, float(score)) for entry, score in zip(entries, scores) if score > 0]
    ranked.sort(key=lambda pair: (pair[1], timestamp(pair[0])), reverse=True)
    return ranked
```

`BM25Okapi` divides by average document length. An all-empty corpus returns no rows instead of dividing by zero. A ranker exception is logged and returns no rows, so the caller can fall through to embeddings. Sort by score descending, then by the item timestamp descending.

Decrypt only inside the request. The corpus is the user's own rows. Filter `user_id` from the verified JWT before decrypt.

## Catalog tsvector

Column: `search_vector tsvector null`, not edited by forms. The current migrations do not install a GIN index and do not install a trigger. Writes happen in the application on create and on update. For a catalog that outgrows a sequential scan, add `CREATE INDEX catalog_items_search_idx ON catalog_items USING GIN (search_vector)`.

Postgres `simple` does not split text that has no spaces. Segment in Python first, join the tokens with spaces, then hand that string to `to_tsvector('simple', ...)` and `websearch_to_tsquery('simple', ...)`. Both sides must use that segmentation. A trigger in the database cannot call PyICU, which is why the vector is built in the app.

Weights:

| Weight | Text |
| --- | --- |
| A | Name |
| B | Tagline |
| C | Description and personality, space-joined |

Category, function, and avatar paths are excluded, matching the embedding text in `pgvector-similarity-search`.

```python
def search_vector_expr(name, metadata):
    name_text, tagline, body = weighted_parts(name, metadata)
    return (
        SearchVector(Value(segment_text(name_text)), weight="A", config="simple")
        + SearchVector(Value(segment_text(tagline)), weight="B", config="simple")
        + SearchVector(Value(segment_text(body)), weight="C", config="simple")
    )

def segment_text(text: str) -> str:
    return " ".join(segment_tokens(text))
```

`segment_text` is also what the query passes to `SearchQuery(..., search_type="websearch", config="simple")`.

On create and on update, after the row is saved:

```python
CatalogItem.objects.filter(id=item_id).update(
    search_vector=search_vector_expr(name, metadata)
)
```

A failure to update the vector is logged. The row still exists. A follow-up backfill repairs it.

Backfill, once, after a change to segmentation or after rows written before this writer existed:

```text
python manage.py backfill_catalog_search_vector --batch-size 500
python manage.py backfill_catalog_search_vector --dry-run
```

Iterate with `iterator(chunk_size=batch_size)` and `UPDATE` one id at a time. Dry-run counts rows and writes nothing. The command's job is to recompute every row from name and metadata. There is no trigger in the current migrations to keep the column fresh.

## Hybrid rank

Settings:

| Name | Value |
| --- | --- |
| `HYBRID_TEXT_WEIGHT` | `0.75` |
| `HYBRID_VECTOR_WEIGHT` | `0.25` |
| `HYBRID_MIN_SCORE` | `0.3` |
| `VECTOR_MIN_SCORE` | `0.3` (pure vector default when the caller omits a threshold) |

`SearchRank` uses `normalization=32` and `cover_density=True`. In Postgres that is `ts_rank_cd` divided by `rank + 1`. Wrap it in `Coalesce(..., 0.0)` so a row with a null vector still has a numeric text rank.

Candidate pool, before visibility and the hybrid cutoff:

- If the segmented query is non-blank, take the top `candidate_limit` ids by text rank where `search_vector` matches the query.
- If a query embedding is present, take the top `candidate_limit` ids by cosine distance among rows with a non-null embedding. The distance expression is the typed cast from `pgvector-similarity-search`.
- `candidate_limit = max(page * page_size * 4, 50)`.
- No candidates returns `[], 0`.

Re-score that id set with the visibility filter:

- Default: platform rows (`is_custom = false`) or custom rows that are published, not paused, and not deleted.
- Community-only: custom, published, not paused, not deleted.

```python
hybrid_score = vector_weight * vector_score + text_weight * text_rank
```

`vector_score` is `1 - cosine_distance` when the row has an embedding, else `0`. Keep `hybrid_score > 0.3`. Order by hybrid score, then likes, then related-comment count, then `created_at`, all descending. Paginate with `offset = max(page - 1, 0) * page_size`.

Pure vector search, used for long queries and for a hybrid miss, filters `vector_score >= threshold` in SQL (search box passes `0.6`), requires a query vector, and returns `[], 0` without one. Check the vector with `is not None` and a length test when the value came from the ORM (`pgvector-similarity-search`, P12). A `list[float] | None` from the embed client is empty when it is `None`.

## Failure prevention

- Build the catalog `tsvector` in the application from ICU tokens and config `simple`. The query uses the same segmentation and the same config. A language-specific Postgres configuration tokenizes the raw query differently from the stored vector, and CJK queries match nothing.
- Rewrite `search_vector` on every create and update. Run the backfill after changing the segmenter. A stale vector keeps ranking the old name.
- Decrypt item text only for the requesting user, in memory, for the duration of `rank`. Persist ciphertext.
- Strip HTML before BM25 on the body. Tags would otherwise become tokens.
- Stem the query under every language present in the loaded page, and keep the unstemmed token.
- Return only BM25 scores greater than zero. A zero-score row is not a keyword hit, and the caller then tries embeddings.
- Share `search_path` `public, extensions` with the vector role when hybrid search runs both `tsvector` and `<=>`.
- Gate ORM vectors with `is not None` and `len`, never with a bare boolean test.

## Tests

- `count_terms("the cat")` is `1`. `count_terms("")` is `0`. A three-token query uses the keyword path. A four-token query does not.
- An English item whose body contains `running` matches the query `runs` after stemming, and a Chinese item matches the raw characters with no stemmer.
- Title matches outrank body matches when the token counts are otherwise equal.
- HTML in the body does not add tokens from the tags.
- An empty corpus, a blank query, and a corpus of empty documents each return no rows.
- Catalog: a query segmented the same way as the stored name matches weight A. A query that skips segmentation misses a space-less name that the stored vector split.
- Hybrid score uses `0.25 * vector + 0.75 * text` and drops rows at or below `0.3`.
- A short query with a hybrid miss falls through to vector search. A long query never calls hybrid.
- Backfill dry-run writes nothing and reports the row count. A second backfill leaves an already-correct vector unchanged.
- Item keyword search never returns another user's rows.

## Checklist

- [ ] `count_terms` uses the ICU breaker and the English stopword list. Routing is `1..3` tokens for keyword, otherwise embeddings.
- [ ] Item BM25 runs on decrypted text in memory. Weights are title ×3, topics ×2, body ×1. HTML is stripped.
- [ ] Snowball covers the ISO map above. Other languages pass through. The query is stemmed for every language in the corpus.
- [ ] Catalog `search_vector` uses config `simple` on pre-segmented text, weights A/B/C, and is written on create, update, and backfill.
- [ ] Hybrid weights are `0.75` text and `0.25` vector, cutoff `0.3`, candidate pool `max(page * page_size * 4, 50)`.
- [ ] `search_path` includes `extensions` when the same role runs cosine distance.
- [ ] Tests cover stemming, CJK segmentation, the hybrid miss, and user scoping.
