# Quickstart: Context-Aware & Web-Grounded Fact-Check

Implementation guide. All code changes are in `cogs/fact_check.py`, `database.py`, and
`config.yaml`. No new cog, no new files. Follow phases in order.

## Prerequisites
- Existing bot runs; `gemini_key` set; `Intents.message_content` enabled.
- `google-genai` SDK already in use (`from google.genai import types as genai_types`).

---

## Phase 1 — Config & schema (foundation)

1. **`config.yaml`**: add the `factcheck.context.*` and `factcheck.grounding.*` blocks
   from data-model §5, with documented defaults.
2. **`database.py`**:
   - Add the `message_context` table + indexes to the schema-init block (data-model §1).
   - Add the `message_context_fts` FTS5 virtual table + sync triggers (data-model §1b),
     guarded by an `fts5_available()` probe so init degrades gracefully if FTS5 is missing.
   - Add `log_context_message`, `get_recent_context`, `prune_message_context`,
     `count_message_context` (data-model §3).
3. Verify: start bot, confirm base table + FTS table created; `flake8` clean.

---

## Phase 2 — Capture: `on_message` listener

1. Add `@commands.Cog.listener() async def on_message(self, message)` to `FactCheck`
   (contract C2): gate on `context.enabled`, guild-only, non-bot, non-empty text; truncate
   to `max_stored_chars`; call `database.log_context_message(...)`.
2. Amortized prune: keep an insert counter; every K inserts call `prune_message_context`
   with the retention cutoff + per-channel cap. Wrap all of it so exceptions are logged and
   swallowed (never disrupt message flow).
3. Verify: send messages, confirm rows appear; confirm bot/empty messages are skipped;
   confirm rows prune only past `storage_retention_days` (not the recency window).

---

## Phase 3 — Retrieve & inject context (two tiers)

1. Add `ContextWindow` / `ContextMessage` (with `source`) dataclasses.
2. **Tier 1 (recency)**: `get_recent_context` + merge/dedupe/sort (data-model §2, tier 1).
3. **Tier 2 (relevance, all history)**: add `_history_query_terms(text)` (C3) and
   `database.get_relevant_history(guild_id, match_query, limit, exclude_ids)` using FTS5
   `MATCH ... ORDER BY bm25(...)`. Gate on `history_relevance.enabled` AND `fts5_available()`.
4. `_build_context_window(message)` (C3): assemble both tiers, dedupe by `message_id`,
   exclude the trigger message. Relevance failure → recency-only (never error).
5. `_format_context_block(window)`: render a "prior conversation" (recency) block and a
   separate "possibly-related earlier messages" (relevance) block; empty → `""`. Instruct the
   model NOT to fact-check the context lines themselves.
6. Insert the block in `_build_content_parts` **before** the message-under-check text.
7. Verify: (a) reference an earlier message → resolved via recency; (b) make a claim matching
   something posted long ago (outside the window) → the old message surfaces via FTS5.

---

## Phase 4 — Google Search grounding

1. Extend `_call_gemini` to accept `grounding: bool` and build a
   `GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], temperature=0.2)`
   when enabled; pass `config=` to `generate_content` (research §A).
2. **Add the grounding instruction block** to `_build_content_parts` when grounding is on
   (research §A / FR-4a): training data may be stale → search to verify existence + dates
   before judging; prefer live results on conflict; genuinely unfindable → Unverifiable, not
   False. **This step, not the tool attachment, is what stops the "doesn't exist" failure.**
3. Parse `response.candidates[0].grounding_metadata` defensively into `list[GroundingSource]`.
   Return `(result, sources)`.
4. Keep the existing JSON text-parsing path unchanged (do NOT add `response_schema`).
5. In the listener, pass `grounding=config.get("factcheck.grounding.enabled", True)`.
6. Verify (the exact failing case): fact-check a claim citing an article/event newer than the
   model's cutoff, and one with a specific date — both should be checked against search and
   verified, not dismissed as nonexistent; a genuinely fake source → Unverifiable with the
   searched terms, not a confident False.

---

## Phase 5 — Surface sources + guardrail + status

1. **Negative-verdict guardrail** (C4a / FR-4b): after `_call_gemini`, if
   `grounding.require_source_for_negative` and verdict is "Mostly False" with 0 sources,
   downgrade to "Unverifiable", append a note to the analysis, and log the downgrade.
2. Extend `_build_embed` to accept sources and add a "Sources" field (C1) capped at
   `grounding.max_sources`; adjust footer to note web-grounding when sources present.
3. Extend `/factcheck` status embed with context on/off + store size + grounding on/off +
   guardrail on/off (C5).
4. Verify the guardrail: force a "Mostly False" with no sources (e.g. grounding returns
   nothing) → embed shows "Unverifiable" with the note, and a downgrade log line is emitted.
5. Add the `/factcheck refresh` admin backfill command (C6): iterate readable text channels,
   `channel.history(...)` up to `backfill_messages_per_channel` (with `after=<now −
   storage_retention_days>` only when retention is finite; no age bound at the default
   unlimited), reuse the listener's filters + `log_context_message`, sleep
   `backfill_channel_delay` between channels, reply with channels-scanned / rows-inserted.
6. Verify: on an empty store, run `/factcheck refresh`; confirm rows appear, a finite
   `storage_retention_days` clips older imports (no clip at the default unlimited), and a
   second run inserts 0 duplicates.
7. Verify: sources render as clickable links; `/factcheck` shows accurate state.

---

## Phase 6 — Hardening & compatibility

1. Confirm graceful degradation: no key, grounding failure, empty store, missing base table,
   **FTS5 unavailable**, or `history_relevance` off → fact-check still returns a normal verdict.
2. Confirm both toggles off reproduce current behavior/latency.
3. Confirm per-request cost stays bounded as history grows (relevance tier = one indexed
   query + `archive_max_messages` cap), independent of `storage_retention_days`.
4. Confirm the decoupling: with `recency_window_hours=168` and the default
   `storage_retention_days=0` (unlimited), a claim matching a ~60-day-old (or older) message
   still surfaces it via the relevance tier.
5. `flake8` clean; manual matrix: {context on/off} × {grounding on/off} × {unlimited vs finite `storage_retention_days`}.
6. Update `README`/config docs noting that `storage_retention_days` defaults to **unlimited**
   (message text kept indefinitely) and how to set a finite retention for a tighter privacy posture.

---

## Rollback
- Set `factcheck.context.enabled: false` and `factcheck.grounding.enabled: false` to fully
  revert behavior without a redeploy.
- Run one prune pass (or drop `message_context`) to purge stored text.
