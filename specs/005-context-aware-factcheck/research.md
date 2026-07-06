# Research: Context-Aware & Web-Grounded Fact-Check

Phase 0 research resolving the NEEDS CLARIFICATION items from the Technical Context.
Two problem areas: (A) Google Search grounding, (B) persistent conversational context.

---

## A. Google Search grounding

### Decision
Enable Gemini's **native Google Search grounding tool** on the existing
`client.aio.models.generate_content` call, passed via `GenerateContentConfig`:

```python
from google.genai import types as genai_types

config_obj = genai_types.GenerateContentConfig(
    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
    temperature=0.2,
)
response = await client.aio.models.generate_content(
    model=model, contents=contents, config=config_obj,
)
```

Grounding sources are read from `response.candidates[0].grounding_metadata`
(`grounding_chunks[].web.uri` / `.title`).

### Rationale
- **Directly fixes the reported bug.** The bot currently says recent articles/events
  "don't exist" because the model answers from a fixed-cutoff knowledge base. With
  grounding, Gemini issues live web searches and reasons over current results, so
  post-cutoff facts are verifiable.
- **Minimal surface area.** It is a config flag on the call already in the cog. No new
  dependency, no separate HTTP client, no API key beyond the existing Gemini key.
- **Built-in citations.** `grounding_metadata` yields the exact URLs used, which we can
  surface in the embed — improving trust and letting users verify.

### Alternatives considered
- **Google Custom Search JSON API / Programmable Search + manual scrape.** Rejected:
  second API key + quota, we'd have to fetch/summarize pages ourselves, and the model
  wouldn't natively reason over results. More code, more failure modes.
- **Third-party search (Serper/Tavily/Bing).** Rejected: extra vendor, extra secret,
  no advantage over first-party grounding for a Gemini-based bot.
- **URL context tool only.** Useful complement (fetching a specific linked article) but
  does not solve open-ended "does this claim check out against current info." Grounding
  is the primary; URL-context can be a future add-on.

### Prompt instructions (what actually fixes "doesn't exist")
Enabling the tool only makes search *available* — the model can still answer from its prior
and declare a real article fake. The behavioral fix is an explicit instruction block prepended
in `_build_content_parts` when grounding is on:

1. **State the limitation**: "Your training data has a cutoff and may be stale. Do not assume
   an article, study, event, or product does not exist just because you don't recognize it."
2. **Search-before-denial**: "Before judging any claim that depends on a specific source,
   recent event, or date, use web search to check whether it exists and what it actually says.
   Treat an unfamiliar reference as something to look up, not as evidence it is false."
3. **Verify dates/recency explicitly**: "When a claim hinges on *when* something happened or
   was published, confirm the date via search rather than memory."
4. **Prefer grounded results over prior belief**: "If current search results conflict with your
   training-time knowledge, trust the search results and say so."
5. **Distinguish 'not found' from 'false'**: "If search genuinely finds no evidence a source
   exists, report that as Unverifiable with what you searched — not as a confident 'False'."

Without (1)–(5) the failure mode recurs even with the tool attached; with them, the tool is
actually exercised on the exact cases that were breaking (new articles, recent events, dates).

### Hard guardrail (belt-and-suspenders on the prompt)
Instructions bias behavior but don't guarantee it. So, gated by
`grounding.require_source_for_negative` (default true), a **post-processing rule** enforces it
deterministically: a "Mostly False" verdict returned with **zero** grounding sources is
downgraded to "Unverifiable" with a note, and the downgrade is logged (see data-model §4,
FR-4b). This makes the "confident denial with nothing to back it up" outcome structurally
impossible when grounding is on — the model must produce at least one live source before the
bot will assert something is false. Deliberately scoped to "Mostly False" only, so it never
suppresses grounded True/Mixed verdicts.

### Compatibility notes
- Keep **prompt-based JSON** parsing (current approach). Do **not** switch to
  `response_schema` — combining a strict response schema with the search tool is
  restricted; the existing regex-strip + `json.loads` path stays.
- Grounding raises latency and token use per call; the existing 45s timeout is adequate.
  Add a small temperature (0.2) for stable factual output.
- Google's usage policy requires displaying grounded sources / search suggestions when
  shown to users — satisfied by the new "Sources" embed section.

---

## B. Persistent conversational context (server-wide store)

### Decision
Add a new SQLite table `message_context` storing message **text** server-wide, populated by
an `on_message` listener, retained per a granular `storage_retention_days` horizon, and
retrieved at fact-check time (recency + same-channel weighting for tier 1). Gated by
`factcheck.context.enabled`. Storage retention is decoupled from the recency window so the
FTS relevance tier (§C) can reach messages older than the recency window.

Retrieval strategy for v1: **recency-first with same-channel priority**, no embeddings.
Pull the most recent N messages from the triggering channel, then backfill up to a total
cap with the most recent server-wide messages, newest-last in the prompt.

### Rationale
- **Matches the chosen product direction** (persistent, server-wide store with granular
  retention).
- **No window functions**, bounded queries — fits the SQLite-only constraint. A simple
  `ORDER BY recorded_at DESC LIMIT ?` with indexes is enough.
- **Recency + same-channel weighting** captures the dominant use case ("that article
  above", "he just said") without the complexity/cost of vector search. Embedding-based
  retrieval is a clearly-scoped future upgrade.
- **Retention-bounded** keeps the table small and time-limited, containing the privacy
  footprint of storing message text.

### Alternatives considered
- **On-demand `channel.history()` fetch, no persistence.** Simpler and more private, but
  the product decision was an explicit *persistent, server-wide* running context, which
  channel.history cannot provide across channels/sessions efficiently. Rejected per
  product direction. (Retrieval still falls back gracefully to whatever is stored.)
- **Reuse `message_events`.** Not viable — it stores only metadata (no text). Adding a
  `content` column there would bloat the stats-retention table and couple two concerns;
  a dedicated table with its own retention is cleaner (Principle: separation of concerns).
- **Vector/semantic retrieval (sqlite-vss / embeddings).** Rejected for v1: adds an
  embedding call per stored message + a vector extension. Recency covers most value.
  Documented as a future enhancement.
- **Summarize-on-write rolling summary.** Rejected for v1: extra Gemini calls on every
  message; recency window is cheaper and adequate.

### Privacy decision
- Storing message text is opt-in via `factcheck.context.enabled` (default **true**, but
  documented). Retention defaults to **168h (7 days)** with a per-channel cap (default 500 —
  raised alongside the window so an active channel isn't capped well under 7 days).
- A purge path exists: setting `enabled: false` stops writes; a prune helper (and the
  existing retention prune loop) clears rows past the window. Disabling + one prune pass
  empties the store.
- Only text + author id + channel id + timestamp are stored. No attachments/media.

### Retrieval sizing (defaults, tunable)
- Recency tier `max_context_messages`: 25 total injected into the prompt; up to 15 from the
  triggering channel, remainder server-wide — prevents one busy channel from crowding out
  cross-channel awareness.
- Relevance tier `archive_max_messages`: 10 top-K FTS5/bm25 hits over all history.
- `storage_retention_days` (sole delete horizon; what the relevance tier can search): 0
  (**unlimited by default** — keep everything; set >0 to prune). `max_messages_per_channel`
  (optional trim): 0 (no cap).
- `recency_window_hours` (tier-1 query window, never deletes): 168 (7 days).
- `history_relevance.lookback_days` (tier-2 reach; `0` = all retained): 0.

**Why three dials, not one.** Collapsing them caused the "old-but-relevant is unreachable"
bug: a 7-day *prune* deletes exactly what the relevance tier wants to search. Keeping storage
retention (unlimited by default) well above the recency window (7d) is what lets tier 2
surface a message from two months ago while tier 1 still only injects the last week for flow.

---

### Backfill / repopulate

**Decision**: add an admin-gated `/factcheck refresh` command that walks readable text
channels via `channel.history(limit=backfill_messages_per_channel, after=<retention cutoff>)`
and feeds each eligible message through the same `log_context_message` path.

**Rationale**:
- The store is empty on first enable and only fills going forward — without backfill the
  bot has no historical awareness until the server has been chatting for a while. Refresh
  seeds it immediately from existing history.
- Same use after clearing the store, changing retention, or recovering from downtime where
  the listener wasn't running.
- Idempotent (`INSERT OR IGNORE` on `message_id`) so it's safe to re-run and safe to run
  while the live listener is active — no duplicates.

**Design points**:
- Admin-only (`@has_admin_role()`) — it's a bulk read over server history (Principle II).
- Bounded: at most `backfill_messages_per_channel` per channel, and `after=` the retention
  cutoff so it never imports rows the prune would immediately delete.
- Rate-limit courtesy: `asyncio.sleep(backfill_channel_delay)` between channels; applies the
  same filters (skip bots, empty text, truncate) as the live listener.
- Reports channels scanned + messages inserted; failures per channel logged and skipped, not
  fatal to the whole run.

**Rejected**: automatic backfill on startup — expensive, surprising, and re-runs every
deploy/restart. Manual admin trigger is predictable and cheap.

---

## C. Full-history retrieval (compute-effective "reference all history")

### Problem
The recency window (tier 1) answers "what was just said," but a request may need to
reference something from *any* point in the server's history ("didn't we debunk this months
ago?", "there was an article about X"). Feeding all history into the prompt is impossible
(token limits) and scanning it per request is wasteful.

### Decision
Add a second retrieval tier: **SQLite FTS5 full-text index over all retained history**,
queried per request with **bm25** ranking to inject only the top-K relevant historical
messages. Verified available in the bundled `sqlite3` (FTS5 + `bm25()` both present) — no
new dependency, no external service, no API cost.

Two-tier context assembled per fact-check:
1. **Recency tier** — time-windowed recent messages (existing behavior).
2. **Relevance tier** — top-K messages across *all* history whose text best matches search
   terms derived from the message being checked.

Storage model shift: the base table **retains history for `storage_retention_days`** (default
`0` = forever; set >0 to prune) — a single granular delete horizon, decoupled from the recency window. The
recency window and per-channel cap become *query* limits, not delete thresholds. The FTS5
index makes lookup over the retained history cheap
regardless of table size.

### Query-term extraction (kept deterministic — no extra LLM call)
Derive the FTS `MATCH` query from the checked message locally: lowercase, strip stopwords,
keep quoted phrases, prefer capitalized tokens / numbers / URLs / proper nouns as high-signal
terms, OR the remaining terms. This avoids an extra Gemini round-trip on the hot path. (An
LLM-based term extraction is a possible future upgrade if recall proves insufficient.)

### Why this is compute-effective
- **O(1)-ish per request**: one indexed FTS5 query + `bm25` sort + `LIMIT K` — sublinear in
  archive size, not a full scan.
- **Bounded prompt**: injected history capped at `archive_max_messages` (default 10); prompt
  size is independent of how much history exists.
- **No embedding cost**: zero per-message API calls; write path stays a single INSERT (FTS
  index maintained by triggers / external-content table).
- **No deployment friction**: pure stdlib SQLite; nothing extra to install on the Windows
  scheduled-task host.

### Alternatives considered
- **Vector / semantic retrieval (embeddings + sqlite-vss / faiss).** Better paraphrase
  recall, but: an embedding API call (cost + latency) per stored message, a vector store /
  native extension to deploy on Windows, and reindex complexity. Rejected as the *primary*
  for a compute-first goal; documented as a future optional rerank layer on top of FTS
  candidates (embed only the K FTS hits, not the whole archive — keeps it cheap).
- **Feed all history / large recency window.** Rejected: blows the token budget and cost,
  and degrades verdict quality with irrelevant noise.
- **Periodic LLM summarization of old history into digests.** Useful complement but adds
  recurring summarization compute and is lossy; FTS over raw text is cheaper and exact.
- **Per-request Discord history scan.** Rejected: can't search across all channels/time
  efficiently and hammers the Discord API.

### Risks / notes
- FTS5 presence is environment-dependent in theory; verified present here. Add a startup
  capability check — if FTS5 is unavailable, the relevance tier disables gracefully and the
  bot falls back to the recency tier only (feature-flag `context.history_relevance.enabled`).
- Longer `storage_retention_days` = better recall but a larger privacy footprint of retained
  message text — a granular admin trade-off (see Privacy decision below).

### Privacy decision (granular retention)
- Retention is a single granular dial, `storage_retention_days` (default **`0` = unlimited**;
  set >0 to prune), rather than a keep-all boolean — strictly more expressive and it decouples
  "how long we keep" from "what's recent." The default keeps everything for maximum recall;
  admins who want a tighter privacy posture set a finite value (e.g. 30, 90, 365). Only text +
  author id + channel id + timestamp are stored; no media.
- Data minimization still holds at the prompt: however much is retained, only the top-K
  relevant rows (`archive_max_messages`) plus the recency window are ever sent to Gemini.
- Documented purge path unchanged: disable + prune (or drop the table + FTS index) clears
  everything. `/factcheck refresh` can re-seed from whatever Discord still retains.

## Cross-cutting decisions

| Topic | Decision |
|-------|----------|
| Where does the listener live | Same `FactCheck` cog (`on_message` listener) — keeps the feature self-contained per Principle I. DB writes go through new `database.py` helpers. |
| Write path performance | `on_message` does a single lightweight INSERT; pruning is amortized (periodic, not every message). |
| Config namespacing | Nest under existing `factcheck:` as `factcheck.context.*` and `factcheck.grounding.*`. |
| Failure handling | Every new path wrapped with logging; no bare excepts (Principle V). Empty/missing context and grounding failures are non-fatal. |
| Backward compatibility | Both features independently toggleable; disabling both reproduces today's behavior. |
| Testing | Manual Discord validation + flake8, consistent with prior fact-check features. |

All NEEDS CLARIFICATION items are resolved. Proceed to Phase 1.
