# Implementation Plan: Context-Aware & Web-Grounded Fact-Check

**Branch**: `main` | **Date**: 2026-07-04 | **Spec**: specs/005-context-aware-factcheck/spec.md
**Input**: Feature specification from `specs/005-context-aware-factcheck/spec.md`

## Summary

Upgrade the reaction-triggered fact-check bot with (1) **persistent, server-wide
conversational context** in two tiers — a *recency* tier (recent messages for conversational
flow) and a *relevance* tier that retrieves the top-K most relevant messages from **all
retained history** via a SQLite **FTS5/bm25** index, so a request can reference anything ever
posted at a bounded, compute-effective cost; and (2) **live Google Search grounding** via the `google.genai` SDK's
native search tool, so claims are verified against current information instead of the
model's stale training data (fixing the "that article doesn't exist" failure). All changes
are confined to `cogs/fact_check.py`, `database.py`, and `config.yaml`. Both features are
independently toggleable and degrade gracefully to today's behavior.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py (commands ext, `Intents.message_content`), `google-genai` (new SDK — `client.aio.models.generate_content`, `genai.types`), sqlite3 (stdlib), aiohttp
**Storage**: SQLite via `database.py`. New table `message_context` + an FTS5 virtual table `message_context_fts` (external-content, kept in sync by triggers) for compute-effective full-history retrieval. No changes to existing tables. FTS5 + `bm25()` verified present in the bundled `sqlite3`.
**Testing**: Manual Discord validation across the {context on/off}×{grounding on/off} matrix; flake8 lint in CI.
**Target Platform**: Windows (Scheduled Task deployment via GitHub Actions).
**Project Type**: Discord bot (cog-modular).
**Performance Goals**: Fact-check latency comparable to today (grounding adds model-side search time; 45s timeout already covers it). `on_message` write path = one lightweight INSERT; pruning amortized.
**Constraints**: SQLite only, no window functions; bounded context queries. Keep prompt-based JSON parsing (no `response_schema`) so search grounding stays compatible. Full-history retrieval MUST stay compute-effective: one indexed FTS5 query + fixed prompt cap per request (no full scans, no per-message embeddings, no native extensions).
**Scale/Scope**: Single-server bot. Two-tier context: recency tier capped at 25 messages; relevance tier (FTS5/bm25 over retained history) capped at `archive_max_messages` (10). Three decoupled retention dials: `storage_retention_days` (default `0` = unlimited; sole delete horizon), `recency_window_hours` (168, query-only), `history_relevance.lookback_days` (0=all, query-only). Per-request cost independent of retained size. Admin `/factcheck refresh` backfills history (bounded, rate-limited).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | All logic stays in the existing `FactCheck` cog; DB access via new `database.py` helpers. No new cog, no cross-cog imports. |
| II. Admin Role Gating (NON-NEGOTIABLE) | PASS | The only command, `/factcheck`, keeps `@has_admin_role()`. The reaction trigger and new `on_message` listener are event handlers (not commands) — consistent with the existing reaction listener's design. |
| III. Audit-First Design | N/A | No audit-domain changes. |
| IV. AI-Augmented Recommendations (Gemini) | PASS | Gemini remains advisory/optional and gracefully degraded (no key / grounding failure / empty context all non-fatal). Grounding is a config flag on the existing call; Gemini makes no destructive or security decisions. |
| V. Observability & Structured Logging | PASS | New listener, retrieval, grounding, and DB helpers log at INFO/WARNING/ERROR with guild context; no bare excepts. Listener failures are logged and swallowed to protect message flow. |

**Post-Design Re-Check**: PASS. No violations. New persistent message-text storage is a
new data responsibility, not a principle conflict — it is toggleable, retention-bounded,
documented in `config.yaml`, and has a purge path (see Complexity Tracking below).

## Project Structure

### Documentation (this feature)

```text
specs/005-context-aware-factcheck/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0: grounding + context decisions
├── data-model.md        # Phase 1: message_context schema + query functions + config
├── quickstart.md        # Phase 1: 6-phase implementation guide
└── contracts/
    └── fact_check_contract.md  # Discord-facing + internal function contracts
```

### Source Code (repository root)

```text
cogs/
└── fact_check.py        # + on_message listener, _build_context_window,
                         #   _format_context_block, extended _call_gemini (grounding),
                         #   negative-verdict guardrail (downgrade unsourced "Mostly False"),
                         #   extended _build_embed (Sources), extended /factcheck status,
                         #   /factcheck refresh (admin backfill from channel history)

database.py              # + message_context table & indexes + message_context_fts (FTS5)
                         #   + sync triggers; log_context_message / get_recent_context /
                         #   get_relevant_history (bm25) / prune_message_context /
                         #   count_message_context / fts5_available

config.yaml              # + factcheck.context.* and factcheck.grounding.* blocks
```

**Structure Decision**: Single-cog change. The feature is a natural extension of the
existing `FactCheck` cog, so it lives there (Principle I). Persistence goes through
`database.py` to keep SQL in one place, consistent with the stats/audit cogs.

## Complexity Tracking

> Filled because the feature introduces persistent storage of user message text — a new
> data-handling responsibility worth explicitly justifying.

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `message_context` table storing message text | Product decision: a *persistent, server-wide running context* across channels and sessions | On-demand `channel.history()` fetch can't provide efficient cross-channel/persistent context; reusing metadata-only `message_events` is impossible (no text column) and would couple concerns |
| `on_message` listener in FactCheck cog | Populate the store as messages arrive | Backfilling on demand would miss most history and add per-check API latency |
| New privacy surface (stored text) | Enables reference resolution + relevance retrieval | Partially mitigated: default `storage_retention_days: 0` keeps message text **indefinitely** (deliberate — maximizes recall of old-but-relevant messages), so retention is not a mitigation at the default. Remaining mitigations: `enabled` toggle (default on, documented), admin can set a finite retention for a tighter posture, optional per-channel cap, purge path, text-only (no media), and only top-K relevant rows ever reach the prompt (data minimization) |
| FTS5 virtual table + sync triggers | Compute-effective relevance retrieval over all history | Recency/keyword-in-Python can't scale to full history; embeddings/vectors add API cost + a native extension to deploy. FTS5 is stdlib, indexed, zero per-message cost |

## Phase 0 — Outline & Research

Complete. See [research.md](./research.md). Resolved:
- **Grounding**: use the native `Tool(google_search=GoogleSearch())` on the existing call;
  read `grounding_metadata` for citations; keep prompt-based JSON (no response schema).
- **Context (two tiers)**: `message_context` table + FTS5 index. Tier 1 recency
  (same-channel-weighted); tier 2 relevance over **all history** via FTS5/bm25 (top-K,
  bounded cost) — no embeddings for v1. Three decoupled retention dials so old-but-relevant
  messages stay reachable: `storage_retention_days` (delete horizon) separate from
  `recency_window_hours` and `lookback_days` (query-only). Admin `/factcheck refresh`
  backfills from channel history (idempotent, rate-limited).

## Phase 1 — Design & Contracts

Complete. Artifacts:
- [data-model.md](./data-model.md) — `message_context` schema, retrieval algorithm, 4 new
  `database.py` functions, config keys.
- [contracts/fact_check_contract.md](./contracts/fact_check_contract.md) — reaction/embed
  behavior, `on_message`, retrieval helpers, extended `_call_gemini`, `/factcheck` status.
- [quickstart.md](./quickstart.md) — 6-phase implementation guide with per-phase verification.

Agent context: `CLAUDE.md` plan pointer updated to this file.

## Phase 2 — Next step

Run `/speckit-tasks` to generate `tasks.md` (dependency-ordered task list) from these
artifacts. Suggested task ordering mirrors the quickstart phases: config+schema →
capture listener → retrieval/injection → grounding → embed/status surfacing → hardening.
