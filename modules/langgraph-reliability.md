---
name: langgraph-reliability
title: Reliable LangGraph turns on a transaction pooler
summary: Flatten checkpoint messages to plain text, retry one stale prepared statement, and end a failed answer without saving it as the reply. Use this when a streaming graph must survive a pooler, a partial tool tag, and a model that writes its reasoning into the checkpoint.
stack: [python, langgraph, langchain, postgres, redis]
tags: [langgraph, reliability, checkpoints, streaming]
requires: [langgraph-streaming-chat, langgraph-postgres-persistence]
related_skills: []
status: stable
updated: 2026-10-26
---

# Reliable LangGraph turns on a transaction pooler

The graph shape, the Redis list, and the SSE proxy are `langgraph-streaming-chat`. The pool, `prepare_threshold=None`, `supports_pipeline=False`, and checkpoint encryption are `langgraph-postgres-persistence`. Provider flags and `ToolStrategy` are `litellm-langchain-structured-output`. This module is what happens when a turn is partial, retried, or poisoned by a previous checkpoint.

There is no LangGraph `RetryPolicy`. Reliability is the message reducer, one store retry, timeouts, and the in-node model chain.

## Checkpoint text

The state reducer flattens both sides before `add_messages`. An old checkpoint that still holds reasoning blocks is rewritten the next time any message is merged (P43).

```python
def add_text_messages(left, right):
    return add_messages(flatten(left), flatten(right))
```

For a `BaseMessage`, copy `content` down to a string and drop `reasoning_content` from `additional_kwargs`. A list of blocks keeps `type` `text` (and bare strings) and skips every other block, including `reasoning`, `thinking`, and `non_standard`. Tool calls stay. `RemoveMessage` passes through. A dict message gets the same treatment. A `(role, content)` pair becomes `(role, text)` when the content is a string or a text-block list.

The copy sent to the model is flattened again, XML tool tags are stripped, and an assistant message with no text and no tool call is omitted. The checkpoint row is what the reducer stored, not that trimmed copy. System messages are moved to the front of the model copy. `add_messages` leaves a refreshed system message at the end, and a system message after the user turn makes the model close a tool tag.

The streaming filter holds a buffer that starts with `<` until the tag is known or the buffer reaches 200 characters. A cut-off drop tag with no `>` (`</inv`, `</parameter`) is discarded on flush, including at the end of the turn. Visible text is what gets appended. A tool-call-only turn flushes to empty and is not saved as an assistant row.

## Idempotent steps

Turns are groups that start at each human message. Summarize at 10, 11, and 12 turns, then wait through 19. At 20, remove the oldest 10 even when the summary call fails, and summarize the newest 10. A failed summary at 20 leaves those 10, so the next tries are 11 and 12 again.

`runs_to_remove` is a union. The summary node adds ids and does not write `[]` when the model returns nothing. The prune node clears the list only on its success return. On an exception it emits the error and returns nothing, so the ids stay. A helper subtracts that pending count from the human-message count so a failed prune would not be folded again. Unit tests cover the helper. The edge after finalize does not call it. That edge uses the raw turn count, so a failed prune still matches 10, 11, 12, or 20 and the summary node runs again. Never put the current run id, or the latest human turn's run id, into the removal list.

Phase rows are written one key at a time. A continuation flag overwrites the latest key. Any other phase inserts a new key. If the node fails after the first write, those keys remain. The failure return does not include `runs_to_remove`, so a later turn can summarize the same window again and insert another phase. Do not treat the store as a single transaction. A missing `structured_response` writes no phase and emits `empty summary`.

Durable facts: a parsed empty list writes nothing and emits success. A missing `structured_response` writes nothing. Rejected keys are skipped one by one. The completion event on the missing-object path still has an empty error string (`litellm-langchain-structured-output`).

Nested extract agents set `checkpointer=False`, use a private in-memory store, and a `memory-{uuid}` thread id. `None` would inherit the parent saver and deadlock on this thread. That setup is `langgraph-postgres-persistence`.

After the reply is finalized, memory nodes are a sequence, not a fan-out. A parallel superstep holds the checkpointer lock until every sibling finishes. The phase node, the fact node, and the embedding node each catch their own errors. Their graph timeout handler logs and goes to the next node. The prune node catches its own errors and has no timeout policy. The user already has the reply. Timeouts: answer run 20s / idle 12s, tools run 8s / idle 5s, phase, facts, and embedding run 15s / idle 8s. Context gather is 5 seconds and then the answer node still runs, with empty memory blocks.

## One prepared-statement retry

`call_store` retries once, and only when the error is a stale prepared statement. Other exceptions raise immediately. Match the class (`InvalidCachedStatement`, `DuplicatePreparedStatement`, `InvalidSqlStatementName`) or a message that contains `prepared statement` and either `does not exist` or `already exists`.

A caller that passes `fallback` gets that value after the retry fails, so an empty memory read does not block the reply. Checkpointer `aget_tuple`, `aput`, and `aput_writes` go through the same helper with no fallback: a failed checkpoint write must surface. The pooler settings that stop new prepares are `langgraph-postgres-persistence`. This retry is for names left behind after a truncate or a bounced pooler session.

## Fixed-delay helper

```python
async def retry(fn, max_attempts=3, delay_seconds=1):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as error:
            last = error
            if attempt == max_attempts:
                break
            await asyncio.sleep(delay_seconds)
    raise last
```

The sleep is `delay_seconds` every time. The docstring that says exponential backoff is stale. `max_attempts <= 0` raises `ValueError` before the loop. Redis client startup and the worker's control-channel subscribe use the defaults: 3 attempts, 1 second. A command that fails with `ConnectionError`, `TimeoutError`, `OSError`, or `RuntimeError` disconnects idle sockets and runs once more. That second call is not the helper above. Leave it as one extra attempt.

## When the answer fails

Build each streaming model with LiteLLM fallbacks off and invoke primary, then the configured fallback. `num_retries` (default 2) still applies inside one model (`litellm-langchain-structured-output`). If every model raises, or the node hits its timeout, the error handler removes checkpoint messages after the last human message (skip a system message and any message with no id) and goes to `END`. It emits `llm_error` and does not write the universal error string into graph state.

The stream consumer stores that error and yields `{type: status, status: error}` only after `astream_events` returns. The `RemoveMessage` checkpoint is in before the worker rolls back database rows. Rollback deletes rows for that run whose type is not `user`. The user message stays. The universal text is appended to the Redis list and is not inserted into the message table, so a refresh does not replay it into the next prompt. Leave the list append-only. Deleting it would move the fallback chunk back to index 0 and a client that already advanced its cursor would miss it.

A tools-node timeout synthesizes one `ToolMessage` per pending tool call (`Tool timed out: ...`) and returns to the answer node. It does not end the turn. `ToolNode(handle_tool_errors=True)` already turns a tool exception into a tool message the model can read. The node timeout is the case that handler does not see, because the node itself was cancelled.

An exception from the stream generator yields the same error status. The worker then uses the same rollback. Do not call the trace client's `flush()` at the end of the generator. Flush waits on the export batch and blocks the actor after the graph has finished.

`recursion_limit` on the run config is 20. The per-turn tool budget is 3 `ToolMessage`s after the latest human message. At the budget, stop binding tools so the model has to answer. Record the model from the LiteLLM success callback. The name on the streamed metadata stays the primary.

A stop signal breaks the worker loop and finalizes `stopped`. It does not go through the LLM error handler.

## Failure prevention

- **P43.** The reducer flattens both sides. A checkpoint that already contains `reasoning_content` or a thinking block becomes plain text on the next merge. Tool calls are kept.
- **P43.** Streaming models are built with fallbacks off. Downgrade in the node. The error handler ends the turn. It does not append another model call inside the handler.
- **P43.** Strip XML tool tags from streamed text, including a cut-off tag with no `>`. Do not save an empty assistant row for a tool-only turn.
- Retry a stale prepared statement once. Do not retry other database errors. Checkpoint methods have no fallback.
- The fixed-delay helper does not exponential-backoff. Redis command recovery is one extra attempt after dropping idle sockets.
- Memory nodes swallow errors and continue. They do not replace the reply.
- Yield the LLM error after the event stream closes, then roll back non-user rows for that run. Keep the Redis list append-only.
- Do not fan out after the reply. Do not flush traces at the end of the generator.

## Tests

- Merging a stored assistant message that has thinking blocks and `reasoning_content` yields plain text, the same id, and the original tool call. The incoming dict is plain text too. The original message object is unchanged.
- A model copy drops a reasoning block and a cut-off `</inv` message, and sends the system message first.
- Routing: tool calls go to tools, a plain reply goes to finalize, counts 10–12 and 20 summarize, 13–19 do not.
- The LLM error handler's update removes messages after the last human and the command goes to `END`.
- A tools timeout update contains one tool message per call id and goes back to the answer node.
- A stale `prepared statement "_pg3_N" does not exist` on the first store search is retried. The fact is stored.
- A missing `structured_response` writes no phase. At the hard cap the oldest turns are still removed.
- The stream filter holds a split closing tag across chunks and drops it.

## Checklist

- [ ] State uses the text reducer, so both the new message and the checkpoint are flattened (P43).
- [ ] Streaming `ChatLiteLLM` is constructed with fallbacks off (P43).
- [ ] A failed answer removes the partial turn from the checkpoint, then the worker deletes non-user rows for that run. The fallback text is Redis-only.
- [ ] Tool timeouts return to the model. Memory timeouts continue the chain.
- [ ] `call_store` retries once on a stale prepared statement and does not swallow other errors.
- [ ] Redis startup uses the fixed delay. The helper docstring is not the behavior.
- [ ] The generator does not flush the trace client.
- [ ] Summaries at 10, 11, 12, and 20 do not prune the current run or the latest human turn.
