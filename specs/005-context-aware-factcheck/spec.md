# Spec: Context-Aware & Web-Grounded Fact-Check

**Date**: 2026-07-04 | **Feature**: 005-context-aware-factcheck
**Status**: Draft

## Summary

Two upgrades to the existing reaction-triggered fact-check bot (`cogs/fact_check.py`):

1. **Conversational context awareness** — the bot builds a persistent, server-wide
   running record of message text (retained per a configurable horizon) and feeds relevant
   prior messages into each
   fact-check so verdicts account for what was said before (references, follow-ups,
   shared links, ongoing threads of discussion).
2. **Live web grounding via Google Search** — fact-checks use Gemini's native Google
   Search grounding tool so the model can verify claims against current information
   instead of relying on stale training data. This fixes the recurring failure where
   the bot declares a real, recent article or event "does not exist" because it post-dates
   the model's knowledge cutoff.

## Goals

- Persist recent message content server-wide in a new SQLite table, populated by an
  `on_message` listener, pruned on a retention window.
- At fact-check time, retrieve relevant recent messages (server-wide) and include them
  as conversational context in the Gemini prompt.
- Retrieve the most *relevant* messages from **all retained history** (not just the recency
  window) via an FTS5/bm25 index, so a request can reference anything ever posted — at a
  bounded, compute-effective per-request cost.
- Enable Google Search grounding on the fact-check Gemini call so claims are checked
  against live web results.
- Surface grounding sources (citations / links) in the verdict embed so users can see
  what the verdict was based on.
- Remain fully backward compatible: if the context store is empty or grounding is
  disabled/unavailable, the bot behaves exactly as it does today.

## Non-Goals

- Embedding/vector semantic search over history. Full-history retrieval uses SQLite FTS5
  keyword/bm25 ranking (compute-effective, no embedding cost); vector rerank is a future
  option, not v1.
- Storing attachments, images, or media bytes in the context store (text only).
- Analytics, export, or search UIs over stored message content. Retained text exists solely
  to feed fact-check context/retrieval, governed by `storage_retention_days` — not a
  user-facing message archive.
- Changing the reaction trigger, verdict taxonomy, or abuse-protection model.
- Per-user opt-out UI beyond a server-level config toggle (may be a future feature).

## User-Visible Behavior

- Reacting with the fact-check emoji works exactly as before, but verdicts now:
  - Understand pronouns/references to earlier messages ("that article", "he said",
    "the study above").
  - Cite live web sources when Google grounding returns them (new "Sources" section in
    the embed).
- `/factcheck` status command reports whether context awareness and web grounding are
  enabled, plus the current size of the context store.

## Functional Requirements

- **FR-1**: A new listener MUST record each eligible guild message's text, author,
  channel, and timestamp to a `message_context` table. Bot messages and empty/text-less
  messages MAY be skipped.
- **FR-2**: Storage retention, the recency window, and the relevance lookback MUST be
  independently configurable. `storage_retention_days` (0 = keep forever) is the sole delete
  horizon and defines what the relevance tier can search; `recency_window_hours` and
  `history_relevance.lookback_days` are query-only limits that MUST NOT delete data. An
  optional `max_messages_per_channel` cap (0 = none) MAY trim the busiest channels.
- **FR-3**: At fact-check time, the bot MUST retrieve up to
  `factcheck.context.max_context_messages` recent messages, prioritizing the triggering
  channel then filling server-wide, and MUST include them (with author + channel labels)
  in the Gemini prompt as clearly delimited "prior conversation" context. Context is
  server-wide and NOT restricted to the reacted-to author — a referenced item (e.g. an
  article) may have been posted by someone else.
- **FR-3a**: The verdict MUST be about the reacted-to message's claims only. Injected context
  (from any author) is reference material to resolve references and disambiguate the claim; the
  prompt MUST instruct the model NOT to fact-check the context lines themselves. Retrieval
  scope (server-wide) and verdict scope (the reacted-to message) are distinct concerns.
- **FR-4**: The fact-check Gemini call MUST enable the Google Search grounding tool when
  `factcheck.grounding.enabled` is true.
- **FR-4a**: When grounding is enabled, the prompt MUST instruct the model to (a) treat its
  training data as potentially stale, (b) use web search to verify the existence and dates of
  articles/studies/events before judging them, (c) prefer current search results over
  training-time knowledge on conflict, and (d) report a genuinely unfindable source as
  Unverifiable rather than confidently False. Attaching the tool alone does NOT satisfy this
  requirement — the anti-"doesn't exist" instructions are the behavioral fix.
- **FR-4b**: When `grounding.require_source_for_negative` is true and grounding is enabled, a
  "Mostly False" verdict returned with zero grounding sources MUST be downgraded to
  "Unverifiable" (with an explanatory note) rather than surfaced as a confident denial. The
  downgrade MUST be logged. This is a hard guardrail on top of FR-4a's prompt instructions.
- **FR-5**: When grounding metadata is returned, the verdict embed MUST display up to
  `factcheck.grounding.max_sources` source links.
- **FR-6**: All new behavior MUST degrade gracefully — missing table, empty context,
  grounding failure, or absent API key MUST NOT break a fact-check.
- **FR-7**: All new config keys MUST be documented in `config.yaml` with safe defaults,
  and context awareness/grounding MUST each be independently toggleable.
- **FR-8**: Message content storage MUST be gated by `factcheck.context.enabled`; when
  false, no message text is persisted.
- **FR-9**: An admin-gated refresh command MUST backfill the context store from existing
  channel history (bounded per channel, rate-limited), reusing the same capture filters and
  idempotent write path. Re-running it MUST NOT create duplicates and MUST be safe while live
  capture is active.
- **FR-10**: The bot MUST be able to reference relevant messages from *all* retained history
  (not just the recency window) in a compute-effective way: a SQLite FTS5 index over stored
  messages, queried with bm25 ranking to inject only the top-K relevant historical messages
  per request. Per-request cost MUST be bounded (one indexed query + a fixed prompt cap),
  independent of total history size.
- **FR-11**: The relevance tier MUST be able to reach messages older than the recency window
  — i.e. it searches everything within `storage_retention_days` (bounded by `lookback_days`
  if set), not just recent messages. When FTS5 is unavailable or `history_relevance.enabled`
  is false, the relevance tier MUST disable gracefully and the bot MUST fall back to the
  recency tier without error.

## Privacy & Constraints

- Storing message text is a new data-handling responsibility. Retention MUST default to a
  bounded window (default 7 days / 168h), and a documented purge path MUST exist
  (disable + prune).
- Requires `Intents.message_content` (already enabled per constitution).
- SQLite only; no window functions relied upon. Context retrieval MUST be a bounded query.
- Grounding + prompt-based JSON output MUST remain compatible (the cog already parses JSON
  from response text rather than using a response schema).

## Success Criteria

- A claim about an article/event published after the model's training cutoff is verified
  against live search (not dismissed as nonexistent) when grounding is on.
- A claim whose truth hinges on a publication/event date is checked against searched dates,
  not the model's memory.
- A source that genuinely cannot be found after searching is reported as Unverifiable (with
  the terms searched), not confidently False.
- A fact-check on a message that references an earlier message resolves the reference using
  stored context.
- With context and grounding disabled, behavior and latency match the current bot.

## Open Questions

- Three retention dials are decoupled (see research.md §B/§C): `storage_retention_days`
  (default **`0` = unlimited**) is the only delete horizon; `recency_window_hours`
  (default 168) and `history_relevance.lookback_days` (default 0 = all) are query-only.
- `storage_retention_days` defaults to **unlimited** (keep all history) for maximum recall of
  old-but-relevant messages. Admins who want a tighter privacy posture set a finite number of
  days. Note the privacy implication: at the default, message text is retained indefinitely.
- Relevance retrieval approach — resolved in research.md: two-tier context (recency window +
  FTS5/bm25 over retained history); vectors deferred.
