# Contracts: Context-Aware & Web-Grounded Fact-Check

The bot exposes no HTTP API. Its "contracts" are the Discord-facing behaviors and the
internal function signatures other code depends on.

---

## C1. Reaction trigger (unchanged interface, richer behavior)

- **Trigger**: react with `factcheck.emoji` on any message (as today).
- **Precondition additions**: none. Gate checks, cooldown, rate limit unchanged.
- **New behavior**:
  - Verdict incorporates prior-conversation context (when `factcheck.context.enabled`).
  - Verdict is grounded in live web results (when `factcheck.grounding.enabled`).
  - Verdict embed gains a **"Sources"** field listing up to `grounding.max_sources`
    grounded links, when grounding metadata is present.
- **Backward compatibility**: with both toggles off, output and latency match current.

### Verdict embed additions
| Element | Contract |
|---------|----------|
| Sources field | Present only when ≥1 grounding source returned. Each line: `[title](uri)` (title falls back to the host if absent). Max `grounding.max_sources` lines. |
| Footer | Appends " · web-grounded" when grounding contributed sources; otherwise unchanged. |

---

## C2. `on_message` listener (new)

```
async def on_message(self, message: discord.Message) -> None
```
- **Records** to `message_context` when: `factcheck.context.enabled` is true, message is in
  a guild, author is not a bot, and stripped text is non-empty.
- **Never blocks or interferes** with other cogs' `on_message` handling; does not call
  `process_commands` (the bot's command processing is owned elsewhere).
- **Idempotent**: repeated delivery of the same `message_id` does not duplicate rows.
- **Failure**: any exception is logged (warning) and swallowed so message flow is unaffected.

---

## C3. Context retrieval helper (new)

```
async def _build_context_window(self, message: discord.Message) -> ContextWindow
```
- Assembles **two tiers** (data-model §2): tier 1 recency (windowed) + tier 2 relevance
  (FTS5/bm25 over all history, only when `history_relevance.enabled` and FTS5 is available).
- Excludes the triggering message; dedupes across tiers by `message_id`.
- Each `ContextMessage` carries `source = "recency" | "relevance"`.
- Returns an **empty** window (not an error) when the store is empty or the feature is off.
- Relevance tier failure/unavailability degrades to recency-only, never errors the check.

```
@staticmethod
def _history_query_terms(text: str) -> str | None
```
- Derives a deterministic FTS5 `MATCH` query from the checked message: lowercase, strip
  stopwords, keep quoted phrases, prefer high-signal tokens (capitalized words, numbers,
  URLs). Returns `None` when nothing meaningful remains (→ skip relevance tier). No LLM call.

```
@staticmethod
def _format_context_block(window: ContextWindow) -> str
```
- Renders the window as a delimited prompt section, e.g.:
  ```
  Prior conversation on this server (oldest first). Use it to resolve references
  like "that article" or "he said"; do NOT fact-check these lines themselves:
  [#general] Alice: <text>
  [#news] Bob: <text>
  ---
  ```
- Returns `""` for an empty window (prompt unchanged from today).

---

## C4. Gemini call (extended)

```
async def _call_gemini(self, contents: list, *, grounding: bool) -> tuple[dict | None, list[GroundingSource]]
```
- When `grounding` is true, attaches `Tool(google_search=GoogleSearch())` via
  `GenerateContentConfig` **and** the prompt built by `_build_content_parts` MUST include the
  grounding instruction block (verify existence/dates via search; prefer live results over
  training; unfindable → Unverifiable, not False — see research §A / FR-4a). Tool without the
  instructions does not satisfy the contract.
- Returns `(parsed_verdict_dict | None, sources)`. `sources` is `[]` when none/failure.
- JSON parsing path is unchanged (regex-strip fences + `json.loads`). Grounding failures
  degrade to a normal (ungrounded) result rather than erroring the whole check.

---

## C4a. Negative-verdict guardrail (new)

Applied to the `(result, sources)` from `_call_gemini` before `_build_embed`, when
`grounding.enabled` and `grounding.require_source_for_negative`:

- If `result["verdict"] == "Mostly False"` and `sources == []` → set verdict to
  `"Unverifiable"`, append a note to `analysis`, and log the downgrade
  (`guild`, `message_id`, `Mostly False → Unverifiable`).
- All other verdicts and the grounding-off case pass through unchanged.
- Idempotent and side-effect-free beyond the log line; never raises.

---

## C5. `/factcheck` status command (extended)

New fields in the existing status embed:
| Field | Value |
|-------|-------|
| Context awareness | `On/Off` + `storage <N>d` (or `forever`) / `recency <H>h` |
| History relevance | `On/Off` (FTS5, `lookback <N>d`/`all`) — shows `unavailable` if FTS5 missing |
| Context store size | `count_message_context(guild)` rows |
| Web grounding | `On/Off` (+ `require-source guardrail On/Off`) |

---

## C6. `/factcheck refresh` — admin backfill command (new)

```
@has_admin_role()  @commands.guild_only()
async def factcheck_refresh(self, ctx) -> None
```
- **Gating**: admin-only (Principle II). No-op with an explanatory reply when
  `factcheck.context.enabled` is false.
- **Behavior**: iterate readable text channels in the guild; for each, pull up to
  `context.backfill_messages_per_channel` messages via `channel.history(...)`; apply the same
  filters as the listener (skip bots, skip empty text, truncate to `max_stored_chars`); write
  via `database.log_context_message` (idempotent).
- **Horizon**: `after=<now − storage_retention_days>` so it never imports rows the prune would
  immediately delete (when `storage_retention_days == 0`/forever, no `after` bound); depth is
  otherwise limited by `backfill_messages_per_channel`.
- **Rate limiting**: `asyncio.sleep(context.backfill_channel_delay)` between channels.
- **Idempotent & concurrency-safe**: re-runnable and safe alongside live capture
  (`INSERT OR IGNORE` on `message_id`) — no duplicates.
- **Progress/result**: reply with an embed summarizing channels scanned and rows inserted.
  Per-channel `Forbidden`/errors are logged and skipped, not fatal.

---

## C7. `database.py` public functions (new — see data-model §3)

- `log_context_message(...)` → None
- `get_recent_context(guild_id, channel_id, same_channel_limit, total_limit)` → `list[Row]`
- `prune_message_context(retention_cutoff_iso, max_per_channel)` → int (rows deleted)
- `count_message_context(guild_id)` → int
- `get_relevant_history(guild_id, match_query, limit, exclude_ids)` → `list[Row]` (FTS5 bm25; `[]` if FTS5 unavailable)
- `fts5_available()` → bool (capability probe, gates the relevance tier)
- `latest_context_timestamp(guild_id, channel_id)` → str | None (optional backfill optimization)

Each preserves existing `database.py` conventions (connection handling, ISO timestamps,
structured logging, no bare excepts).

---

## Acceptance checks
1. Message referencing an earlier message resolves the reference via stored context.
2. Claim about a post-cutoff article is verified (not "doesn't exist") with grounding on.
2a. A "Mostly False" result with zero grounding sources is downgraded to "Unverifiable" (and
    logged) when the guardrail is on; unaffected when off.
3. Sources field renders with valid links when grounding returns metadata.
4. Both toggles off → identical behavior/latency to current bot.
5. Context store never retains rows older than `storage_retention_days` (unless `0`/forever)
   or beyond the per-channel cap (if set) after prune; the recency window never deletes.
6. Listener exception does not disrupt message flow or other cogs.
7. `/factcheck refresh` seeds the store from history, is admin-gated, and produces no
   duplicates when re-run.
8. A claim matching a message from long ago (outside the recency window) surfaces that
   message via the FTS5 relevance tier; per-request cost stays bounded as history grows.
9. With FTS5 unavailable or `history_relevance.enabled: false`, the check still runs on the
   recency tier with no error.
