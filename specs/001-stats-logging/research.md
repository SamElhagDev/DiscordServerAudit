# Research: Comprehensive Server Stats Logging

**Date**: 2026-05-18 | **Branch**: `001-stats-logging`

## R1: Discord.py Event Availability for Stats Collection

**Decision**: Use `on_message`, `on_voice_state_update`, `on_member_join`, `on_member_remove`, `on_member_ban`, `on_member_unban`, and `on_raw_reaction_add`/`on_raw_reaction_remove` events.

**Rationale**: These events are all available with the bot's existing intents (`Intents.members`, `Intents.message_content`). Voice state tracking requires `Intents.voice_states` which must be added. These events fire passively — no API polling needed, which avoids rate-limit concerns entirely.

**Alternatives considered**:
- Polling the Discord API periodically for stats: Rejected — rate-limit prone, unnecessary when events are available.
- Using `on_raw_message_delete` to track deletions: Deferred — not in initial scope; can be added later without schema changes.

## R2: Voice State Intent Requirement

**Decision**: Add `intents.voice_states = True` in `bot.py:AdminBot.__init__`.

**Rationale**: `on_voice_state_update` requires the voice states intent. This is a non-privileged intent (does not require Discord developer portal approval), so it can be enabled freely. Without it, voice session tracking is impossible.

**Alternatives considered**:
- Skip voice tracking entirely: Rejected — voice usage stats are a core requirement.

## R3: Message Content Storage Policy

**Decision**: Store only metadata (guild_id, channel_id, user_id, timestamp, word_count). Do NOT store message content.

**Rationale**: Privacy-first approach. Message content is not needed for statistical analysis (message counts, activity heatmaps, per-user/per-channel volume). Storing content would create data retention liability and inflate the SQLite database. Word count provides a useful proxy for engagement depth without privacy concerns.

**Alternatives considered**:
- Store full message content for keyword analysis: Rejected — privacy concerns, storage bloat, no clear requirement.
- Store message hashes for deduplication: Rejected — adds complexity with no benefit for stats.

## R4: Data Aggregation Strategy

**Decision**: Dual-layer approach — raw event tables for recent granular data + daily rollup tables for long-term trends.

**Rationale**: Raw events (individual messages, voice sessions) provide granular recent-history queries. Daily rollup tables (`user_activity_daily`, `channel_activity_daily`) enable efficient long-term trend analysis without scanning millions of rows. A scheduled daily aggregation task computes rollups from raw data, and raw data older than a configurable retention period (default 30 days) can be pruned.

**Alternatives considered**:
- Raw events only: Rejected — query performance degrades as data grows; SQLite lacks the indexing power of larger databases for full-table scans.
- Rollups only: Rejected — loses granularity for "what happened in the last hour" queries.
- Bucketed time-series (5-minute buckets): Over-engineered for the scale (~500 members, ~1000 msgs/day).

## R5: Member Snapshot Frequency

**Decision**: Hourly snapshots via the existing `IntervalScheduler`, capturing total members, online count (approximated), bot count, and boost status.

**Rationale**: Hourly provides sufficient resolution for growth trends without excessive database writes (~24 rows/day per guild). The existing scheduler infrastructure handles interval tracking and persistence across restarts. Online count is best-effort since discord.py's `Guild.members` may not reflect presence accurately without the `presences` privileged intent.

**Alternatives considered**:
- 5-minute snapshots: Rejected — excessive writes (288/day) for minimal additional insight.
- Daily snapshots only: Rejected — misses intra-day patterns (e.g., peak hours).
- Event-driven only (on join/leave): Rejected — misses the "current state" dimension (online count, boost tier).

## R6: Voice Session Tracking Design

**Decision**: Track voice sessions as start/end pairs. On `voice_state_update` join, insert a row with `joined_at` set and `left_at` NULL. On disconnect, update the row with `left_at` and computed `duration_seconds`. On bot restart, close any orphaned sessions with the restart timestamp.

**Rationale**: Start/end pairs enable duration queries and concurrent-user-count queries. The orphan-cleanup on restart handles the edge case where the bot goes offline while users are in voice.

**Alternatives considered**:
- Heartbeat-based presence tracking: Rejected — requires polling, complex, rate-limit prone.
- Event log only (join events, leave events as separate rows): Rejected — computing duration requires expensive join queries.

## R7: Gemini Integration for Trend Analysis

**Decision**: Add an `analyze_trends()` function to `utils/gemini.py` that accepts a structured stats summary (JSON dict of member growth, top channels, peak hours, voice usage) and returns a natural-language insights report. Triggered via a `!insights` command, not automatically.

**Rationale**: Follows Constitution Principle IV — Gemini is advisory, optional, and triggered after data collection. The structured input keeps token usage predictable. A dedicated command (vs. automatic) gives admins control over when they consume Gemini API credits.

**Alternatives considered**:
- Automatic daily Gemini analysis posted to audit channel: Rejected — unpredictable cost, noise.
- Gemini analyzing raw database rows: Rejected — token waste; pre-aggregated summary is more efficient.
- Skip Gemini entirely: Rejected — user explicitly requested Gemini integration consideration.

## R8: SQLite Performance at Scale

**Decision**: Use WAL (Write-Ahead Logging) mode, add indexes on frequently-queried columns (guild_id, user_id, channel_id, timestamp columns), and keep raw event retention bounded (configurable, default 30 days).

**Rationale**: WAL mode allows concurrent reads during writes, critical since event handlers write while commands read. Indexes on foreign-key and timestamp columns prevent full-table scans. Bounded retention prevents unbounded growth. At the expected scale (~1000 msgs/day), SQLite handles this comfortably — PostgreSQL would be over-engineering.

**Alternatives considered**:
- Switch to PostgreSQL: Rejected — adds deployment complexity for a single-server bot; SQLite handles the scale.
- No retention policy: Rejected — unbounded growth would eventually degrade performance.

## R9: Reaction Tracking Scope

**Decision**: Track reaction counts per-user as part of daily aggregation (reactions_given, reactions_received) but do NOT track individual reaction events in a raw table.

**Rationale**: Reaction volume can be very high in active servers. Tracking every add/remove event would dominate database writes. Incrementing daily counters is sufficient for "who is most engaged" analysis. The `on_raw_reaction_add`/`on_raw_reaction_remove` events work without message cache, making them reliable.

**Alternatives considered**:
- Full reaction event log: Rejected — too many writes for minimal analytical value.
- Skip reactions entirely: Rejected — reactions are a meaningful engagement signal.

## R10: Chart and Graph Rendering

**Decision**: Use QuickChart.io (https://quickchart.io) for line/bar chart images embedded in Discord, combined with Unicode block characters for inline bar charts in embed fields.

**Rationale**: QuickChart is a free, open-source API that renders Chart.js configs as PNG images via URL construction — zero dependencies, no file I/O, no uploads. The URL is set as the embed's `image` property and Discord renders it inline. Unicode bar charts (`█▓▒░▁▂▃▄▅▆▇`) provide compact inline visualizations within embed fields for leaderboards and distributions without requiring an external service.

**Alternatives considered**:
- Matplotlib (local image generation): Rejected — adds a heavy dependency (~50MB), requires file write + Discord upload, slower than URL-based approach.
- Text-only output: Rejected — user explicitly requested graphs and rich visual output.
- Discord canvas/attachment-based rendering: Over-engineered; QuickChart covers all chart types needed.

**Chart types planned**:
- Line charts: daily message/voice activity trends, member growth over time
- Bar charts: top users/channels, hourly activity distribution
- Unicode inline bars: leaderboard rankings, percentage indicators

## R11: Rich Output Design Philosophy

**Decision**: Each stats command produces multiple embeds (2-5 per command) using the full 10-embed-per-message limit where valuable. Embeds use server icon/user avatar as thumbnails, emoji indicators for visual scanning, inline fields for dashboard-style layout, and QuickChart images for trend visualization.

**Rationale**: Discord embeds support up to 6000 characters each with 25 fields — using multiple embeds creates a dashboard-like experience that stands out in the channel and provides information at a glance. Rich formatting (emoji prefixes, progress bars, sparklines) reduces cognitive load compared to raw numbers.

**Design principles**:
- First embed is always a summary/overview with the most important numbers
- Leaderboards use Unicode bar charts for visual weight
- Trend data gets a QuickChart line/bar image
- Inline fields used for side-by-side metrics (e.g., Messages | Voice Hours | Active Users)
- Color-coded embeds: green for positive trends, orange for neutral, red for declining
- Footer shows data range and collection period

**Alternatives considered**:
- Single embed per command: Rejected — too cramped, wastes Discord's embed capacity, poor visual hierarchy.
- Plain text responses: Rejected — embeds are the standard for bot output; text lacks structure and visual appeal.

## R12: Command Design Philosophy

**Decision**: Provide 6 hybrid commands organized around natural query patterns: `/stats` (server overview), `/userstats` (per-user), `/channelstats` (per-channel), `/voicestats` (voice overview), `/growth` (member trends), `/insights` (Gemini analysis). All return rich multi-embed responses with charts.

**Rationale**: Maps to the natural questions users ask: "How is the server doing?", "Who is most active?", "Which channels get used?", "How is voice adoption?", "Are we growing?", "What should I focus on?" Each command is self-contained and produces a rich dashboard response.

**Alternatives considered**:
- Single `!stats` command with subcommands: Rejected — discord.py command groups add UX complexity for a flat command set.
- Natural language via `!ask`: Already supported via the existing planner — but dedicated commands are faster and don't consume Gemini credits.
