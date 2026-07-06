# Data Model: Context-Aware & Web-Grounded Fact-Check

Only one persistent entity is added. Grounding introduces no schema — it is a transient
per-call structure.

---

## 1. New table: `message_context`

Server-wide store of message text (retained per `storage_retention_days`) used to build
conversational context and searched by the FTS5 relevance tier.

```sql
CREATE TABLE IF NOT EXISTS message_context (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    author_name TEXT    NOT NULL,      -- display name at time of capture (denormalized)
    content     TEXT    NOT NULL,      -- message text only; no attachments/media
    recorded_at TEXT    NOT NULL,      -- ISO-8601 UTC, matches existing convention
    UNIQUE(message_id)
);

CREATE INDEX IF NOT EXISTS idx_message_context_channel
    ON message_context(guild_id, channel_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_message_context_guild_time
    ON message_context(guild_id, recorded_at);
```

### Fields
| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | Surrogate key. |
| `guild_id` | INTEGER | Server scope. |
| `channel_id` | INTEGER | Enables same-channel prioritization. |
| `message_id` | INTEGER | `UNIQUE` — idempotent inserts (edits/re-delivery won't duplicate). |
| `user_id` | INTEGER | Author id. |
| `author_name` | TEXT | Denormalized display name for prompt labeling without extra lookups. |
| `content` | TEXT | Raw message text. Truncated to `max_stored_chars` (default 2000). |
| `recorded_at` | TEXT | ISO-8601 UTC string (consistent with `message_events.recorded_at`). |

### Validation / write rules
- Skip when `factcheck.context.enabled` is false.
- Skip bot-authored messages and messages whose text is empty/whitespace after strip.
- Truncate `content` to `factcheck.context.max_stored_chars`.
- `INSERT OR IGNORE` on `message_id` to stay idempotent.

### Retention / lifecycle — three independent dials

Storage retention (how long we *keep* text), the recency window (what counts as *recent*),
and the relevance lookback (how far *back FTS may reach*) are decoupled. This is what makes
old-but-relevant messages reachable: the recency window can be short while storage retention
is long, so the relevance tier still finds a message from 60 days ago.

| Dial | Config | Role | Deletes rows? |
|------|--------|------|---------------|
| **Storage retention** | `storage_retention_days` (default `0` = forever; set >0 to prune) | Sole prune horizon for the base table + FTS index | **Yes** — the only delete threshold (never, at default) |
| **Per-channel cap** | `max_messages_per_channel` (default `0` = no cap) | Optional trim of the busiest channels | Yes (if > 0) |
| **Recency window** | `recency_window_hours` (default 168 = 7d) | Time filter for the *recency tier* query only | **No** |
| **Relevance lookback** | `history_relevance.lookback_days` (default `0` = all retained) | Optional cap on how far back *FTS* reaches | No |

- Prune deletes rows older than `storage_retention_days` (and trims per-channel if capped);
  the DELETE trigger keeps the FTS index in sync. Runs amortized, not per message.
- The recency and relevance tiers are **queries** over whatever is currently retained — they
  never delete. A relevant message is findable as long as it is still within storage
  retention (and within `lookback_days`, if set).
- Purge path: disable the feature + one prune pass, or drop the table + FTS index.

---

## 1b. FTS5 full-text index (relevance tier)

Enables compute-effective retrieval over *all* history. External-content FTS5 table so the
index references the base table rather than duplicating text.

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS message_context_fts USING fts5(
    content,
    content='message_context',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Keep the index in sync with the base table
CREATE TRIGGER IF NOT EXISTS message_context_ai AFTER INSERT ON message_context BEGIN
    INSERT INTO message_context_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS message_context_ad AFTER DELETE ON message_context BEGIN
    INSERT INTO message_context_fts(message_context_fts, rowid, content)
        VALUES('delete', old.id, old.content);
END;
```

- **Availability**: FTS5 + `bm25()` verified present in the bundled `sqlite3`. A startup
  capability probe sets `history_relevance` on/off; if unavailable, the relevance tier is
  skipped and only the recency tier is used (graceful degradation).
- **Ranking**: `bm25(message_context_fts)` (ascending = most relevant).
- **Cost**: one indexed `MATCH` query per request, independent of archive size.

---

## 2. Retrieval shape (transient — not persisted)

`ContextWindow` assembled at fact-check time:

```
ContextWindow
  messages: list[ContextMessage]   # ordered oldest -> newest for prompt
ContextMessage
  author_name: str
  channel_id: int
  is_same_channel: bool
  content: str
  recorded_at: str
  source: str        # "recency" | "relevance"
```

Assembly algorithm (two tiers):

**Tier 1 — recency** (conversational flow); `since = now − recency_window_hours`:
1. Query up to `same_channel_limit` (default 15) newest rows where
   `channel_id = trigger_channel` and `recorded_at >= since`.
2. Query up to `max_context_messages` newest rows server-wide (any channel) `>= since`.
3. Merge, dedupe by `message_id`, drop the triggering message, cap total at
   `max_context_messages` (default 25).

**Tier 2 — relevance over retained history** (only when `history_relevance.enabled`):
4. Derive FTS query terms from the checked message (stopword-stripped significant terms +
   quoted phrases; see `_history_query_terms`).
5. `get_relevant_history(guild_id, match_query, limit=archive_max_messages, since_iso, min_score)`
   → top-K by `bm25`, excluding rows already chosen in tier 1. `since_iso` = now −
   `lookback_days` (or `None` when `lookback_days == 0` → all retained history).
6. Cap relevance additions at `archive_max_messages` (default 10). Because storage retention
   defaults to unlimited (far exceeding the 7d recency window), tier 2 routinely surfaces
   relevant messages the recency tier can't reach.

**Merge for the prompt**: label each message with its tier; sort the recency block ascending
by `recorded_at`; present the relevance block separately (clearly marked "possibly-related
earlier messages") so the model distinguishes conversational flow from historical recall.

---

## 3. New `database.py` functions

| Function | Purpose |
|----------|---------|
| `log_context_message(guild_id, channel_id, message_id, user_id, author_name, content, recorded_at)` | Idempotent insert (`INSERT OR IGNORE`). |
| `get_recent_context(guild_id, channel_id, same_channel_limit, total_limit, since_iso)` | Merged same-channel + server-wide rows newer than `since_iso` (= now − `recency_window_hours`) for `ContextWindow` tier 1. |
| `get_relevant_history(guild_id, match_query, limit, exclude_ids, since_iso=None, min_score=0.0)` | FTS5 `MATCH` over retained history, ordered by `bm25`, optionally bounded by `since_iso` (from `lookback_days`) and a bm25 floor, excluding already-selected ids (tier 2). Returns `[]` if FTS5 unavailable. |
| `fts5_available()` | One-time capability probe; gates the relevance tier. |
| `prune_message_context(retention_cutoff_iso, max_per_channel)` | Delete rows older than `retention_cutoff_iso` (from `storage_retention_days`; skip when 0/forever) + optional per-channel trim (`max_per_channel`, skip when 0). |
| `count_message_context(guild_id)` | Store size for the `/factcheck` status embed. |
| `latest_context_timestamp(guild_id, channel_id)` | Newest `recorded_at` stored for a channel — lets backfill skip already-captured history and page only the gap (optional optimization). |

Backfill reuses `log_context_message` (idempotent `INSERT OR IGNORE`), so no dedicated
insert function is required — the refresh command walks `channel.history()` and calls it
per eligible message.

All follow existing `database.py` connection/patterns and log at debug/warning.

---

## 4. Grounding response (transient — not persisted)

Extracted from the Gemini response, not stored:

```
GroundingSource
  title: str | None
  uri: str
```

Read from `response.candidates[0].grounding_metadata.grounding_chunks[].web`
(defensive: any missing attribute -> empty source list). Rendered as a "Sources" embed
field, capped at `factcheck.grounding.max_sources` (default 5).

### Negative-verdict guardrail
Applied after parsing, before building the embed, when `grounding.enabled` AND
`grounding.require_source_for_negative`:

- **Rule**: if `verdict == "Mostly False"` and `len(sources) == 0`, downgrade the verdict to
  `"Unverifiable"` and append a note to the analysis (e.g. "Downgraded from Mostly False:
  no live source corroborated this denial.").
- **Scope**: only the "Mostly False" overall verdict (the class that carries "this is fake /
  doesn't exist"). Other verdicts pass through untouched.
- **Only meaningful with grounding on** — with grounding off there are no sources to require,
  so the guardrail is inert (documented). Verdict confidence is left as-is or lowered to
  "Low".
- **Observability**: log every downgrade (guild, message, original→new verdict) per
  Principle V.

Rationale: the model's stale-cutoff failure mode is over-confident *denial*. Requiring at
least one grounded source before the bot is allowed to assert "Mostly False" converts an
un-corroborated denial into an honest "Unverifiable" instead of a wrong "False".

---

## 5. Config additions (`config.yaml`, under existing `factcheck:`)

```yaml
factcheck:
  # ... existing keys ...
  context:
    enabled: true                    # Master toggle for storing + using message context
    # --- storage retention (how long message text is KEPT & searchable) ---
    storage_retention_days: 0        # Sole prune horizon. 0 = keep forever (default). This is what the relevance tier can search.
    max_messages_per_channel: 0      # Optional per-channel trim of busiest channels. 0 = no cap (rely on time retention).
    max_stored_chars: 2000           # Truncate stored message text to this length
    # --- recency tier (conversational flow) — a QUERY window, never deletes ---
    recency_window_hours: 168        # What counts as "recent" for tier 1 (7 days). Retrieval limit only, not a delete threshold.
    max_context_messages: 25         # Max recent messages injected into a fact-check prompt
    same_channel_limit: 15           # Of the above, how many may come from the trigger channel
    # --- relevance tier (FTS5 over retained history) ---
    history_relevance:
      enabled: true                  # Retrieve relevant messages from all retained history via FTS5 (auto-off if FTS5 missing)
      lookback_days: 0               # How far back FTS may reach. 0 = all retained history; >0 = only this many days.
      archive_max_messages: 10       # Max relevance-tier messages injected per fact-check
      min_score: 0.0                 # Optional bm25 relevance floor (0 = no floor); drops weak matches
    # --- backfill ---
    backfill_messages_per_channel: 1000  # Max history messages /factcheck refresh pulls per channel
    backfill_channel_delay: 0.5      # Seconds to sleep between channels during backfill (rate-limit courtesy)
  grounding:
    enabled: true                    # Enable Gemini Google Search grounding on fact-checks
    max_sources: 5                   # Max source links shown in the verdict embed
    require_source_for_negative: true  # Guardrail: a "Mostly False" verdict with 0 grounding sources is downgraded to "Unverifiable"
```
