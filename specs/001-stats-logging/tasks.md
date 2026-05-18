# Tasks: Comprehensive Server Stats Logging

**Input**: Design documents from `specs/001-stats-logging/`
**Prerequisites**: plan.md, research.md, data-model.md, contracts/commands.md, quickstart.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each command.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- Include exact file paths in descriptions

## User Stories (derived from feature description)

| ID | Title | Priority | Command |
|----|-------|----------|---------|
| US1 | Server Activity Dashboard | P1 (MVP) | `/stats` |
| US2 | User Activity Profile | P2 | `/userstats` |
| US3 | Channel Activity Report | P2 | `/channelstats` |
| US4 | Voice Activity Dashboard | P2 | `/voicestats` |
| US5 | Member Growth Tracking | P2 | `/growth` |
| US6 | AI-Powered Insights | P3 | `/insights` |

---

## Phase 1: Setup (Project Configuration)

**Purpose**: Database schema, config keys, and bot-level setup — shared infrastructure for all user stories.

- [x] T001 Add WAL pragma and 6 new stats tables to `init_db()` in `database.py`: `member_snapshots`, `message_events`, `voice_sessions`, `member_events`, `user_activity_daily`, `channel_activity_daily` — with all columns, constraints, and UNIQUE constraints per data-model.md
- [x] T002 Add all `CREATE INDEX IF NOT EXISTS` statements to `init_db()` in `database.py`: `idx_member_snapshots_guild_time`, `idx_message_events_guild_time`, `idx_message_events_user`, `idx_message_events_channel`, `idx_voice_sessions_guild_time`, `idx_voice_sessions_user`, `idx_voice_sessions_open`, `idx_member_events_guild_time`, `idx_member_events_type`, `idx_user_activity_guild_date`, `idx_user_activity_user_date`, `idx_channel_activity_guild_date`, `idx_channel_activity_channel_date`
- [x] T003 [P] Add `stats` config section to `config.yaml` with keys: `enabled`, `snapshot_interval_hours`, `retention_days`, `rollup_hour_utc`, `track_reactions`, `excluded_channels`, `excluded_users`, `exclude_bots` — with defaults per data-model.md
- [x] T004 [P] Add `intents.voice_states = True` in `AdminBot.__init__` and add `await self.tree.sync()` at end of `setup_hook()` in `bot.py`
- [x] T005 [P] Add `"cogs.stats"` to the `COGS` list in `bot.py`

---

## Phase 2: Foundational (Data Collection + Shared Utilities)

**Purpose**: Event-driven data collection, scheduler tasks, database helpers, and display utilities. MUST complete before ANY command can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Database Write Helpers

- [x] T006 Implement `log_message_event(guild_id, channel_id, user_id, word_count)` in `database.py` — insert row into `message_events` with `_now()` timestamp
- [x] T007 [P] Implement `start_voice_session(guild_id, channel_id, user_id)` in `database.py` — insert into `voice_sessions` with `joined_at=_now()`, `left_at=NULL`
- [x] T008 [P] Implement `end_voice_session(guild_id, user_id)` in `database.py` — find open session (`left_at IS NULL`) for this guild+user, set `left_at=_now()` and compute `duration_seconds` from the difference
- [x] T009 [P] Implement `close_orphaned_voice_sessions()` in `database.py` — update all rows where `left_at IS NULL`, set `left_at=_now()` and compute `duration_seconds`
- [x] T010 [P] Implement `log_member_event(guild_id, user_id, event_type)` in `database.py` — insert into `member_events` with `_now()` timestamp
- [x] T011 [P] Implement `save_member_snapshot(guild_id, total, online, bots, boosts, tier)` in `database.py` — insert into `member_snapshots` with `_now()` timestamp

### Database Rollup & Prune Helpers

- [x] T012 Implement `rollup_user_activity(guild_id, date)` in `database.py` — aggregate `message_events` counts and `voice_sessions` durations for the given date into `user_activity_daily` using INSERT ON CONFLICT UPDATE
- [x] T013 [P] Implement `rollup_channel_activity(guild_id, date)` in `database.py` — aggregate `message_events` into `channel_activity_daily` with `message_count` and `COUNT(DISTINCT user_id)` for `unique_users`, using INSERT ON CONFLICT UPDATE
- [x] T014 [P] Implement `prune_old_events(days)` in `database.py` — DELETE from `message_events` and `voice_sessions` where timestamp is older than `days` ago

### Database Read Helpers

- [x] T015 Implement `get_server_stats_summary(guild_id, days)` in `database.py` — return dict with total messages, total voice hours, active user count, active channel count, reaction count from rollup tables for the date range
- [x] T016 [P] Implement `get_top_users(guild_id, days, limit=5)` in `database.py` — return list of (user_id, message_count) tuples ordered by message_count DESC from `user_activity_daily`
- [x] T017 [P] Implement `get_top_channels(guild_id, days, limit=5)` in `database.py` — return list of (channel_id, message_count) ordered by message_count DESC from `channel_activity_daily`
- [x] T018 [P] Implement `get_peak_hours(guild_id, days)` in `database.py` — return message counts grouped by hour-of-day (0-23) from `message_events` using `strftime('%H', recorded_at)`
- [x] T019 [P] Implement `get_daily_activity(guild_id, days)` in `database.py` — return daily totals (date, message_count, voice_minutes) from rollup tables for activity trend charts
- [x] T020 [P] Implement `get_user_stats(guild_id, user_id, days)` in `database.py` — return dict with message_count, voice_minutes, reactions_given, reactions_received, top_channel_id, daily_breakdown from rollup tables
- [x] T021 [P] Implement `get_channel_stats(guild_id, channel_id, days)` in `database.py` — return dict with message_count, unique_users, top_users list, daily_breakdown from rollup tables
- [x] T022 [P] Implement `get_voice_leaderboard(guild_id, days, limit=5)` in `database.py` — return list of (user_id, total_minutes) from `user_activity_daily` ordered by voice_minutes DESC
- [x] T023 [P] Implement `get_voice_channel_stats(guild_id, days, limit=5)` in `database.py` — return list of (channel_id, total_seconds) from `voice_sessions` grouped by channel, ordered DESC
- [x] T024 [P] Implement `get_member_growth(guild_id, days)` in `database.py` — return list of (recorded_at, total_members) from `member_snapshots` for growth chart data
- [x] T025 [P] Implement `get_member_events_summary(guild_id, days)` in `database.py` — return joins count, leaves count, daily breakdown of (date, joins, leaves) from `member_events`

### Cog Skeleton + Utility Functions

- [x] T026 Create `cogs/stats.py` with `Stats` cog class skeleton: `__init__(self, bot)`, `async def cog_load(self)` calling `close_orphaned_voice_sessions()`, and `async def setup(bot)` function. Add `logger = logging.getLogger(__name__)` at module top.
- [x] T027 Implement `_build_bar_chart(items, max_width=20)` utility function in `cogs/stats.py` — accept list of `(label, value)` tuples, render proportional Unicode bars using `█` (filled) and `░` (empty) scaled to max value, return formatted string with aligned labels and values. Wrap output in code block markers.
- [x] T028 [P] Implement `_build_quickchart_url(chart_config)` utility function in `cogs/stats.py` — accept a Chart.js config dict, merge with dark-theme defaults (background `rgb(47,49,54)`, white text, subtle grid lines), URL-encode the JSON, return full `https://quickchart.io/chart?c=...&w=500&h=300&bkg=rgb(47,49,54)` URL string
- [x] T029 [P] Implement `_trend_indicator(current, previous)` utility function in `cogs/stats.py` — compute percentage change, return `📈 ↑ X%` for positive, `📉 ↓ X%` for negative, `➡️ ─ 0%` for zero/no-change. Handle division-by-zero (previous=0).
- [x] T030 [P] Implement `_format_duration(minutes)` utility function in `cogs/stats.py` — convert integer minutes to `Xh Ym` string (e.g., 97 → `1h 37m`, 0 → `0m`, 1440 → `24h 0m`)
- [x] T031 [P] Implement `_embed_color_for_trend(current, previous)` utility function in `cogs/stats.py` — return `0x2ECC71` (green) if current > previous, `0xE74C3C` (red) if current < previous, `0x3498DB` (blue) if equal

### Event Listeners

- [x] T032 Implement `on_message` listener in `cogs/stats.py` — on each message: check `config.get("stats.enabled")`, skip if author is bot and `exclude_bots` is true, skip if `channel.id` in `excluded_channels`, skip if `author.id` in `excluded_users`, then call `database.log_message_event(guild_id, channel_id, user_id, word_count)` where `word_count = len(message.content.split())`
- [x] T033 Implement `on_voice_state_update` listener in `cogs/stats.py` — handle 3 cases: (1) user joins voice channel (`before.channel is None, after.channel is not None`) → `start_voice_session`, (2) user leaves (`before.channel is not None, after.channel is None`) → `end_voice_session`, (3) user switches channels → `end_voice_session` + `start_voice_session`. Skip bots if configured. Log each transition.
- [x] T034 [P] Implement `on_member_join` and `on_member_remove` listeners in `cogs/stats.py` — call `database.log_member_event()` with event_type `join` or `leave` respectively. Log guild name and user.
- [x] T035 [P] Implement `on_member_ban` and `on_member_unban` listeners in `cogs/stats.py` — call `database.log_member_event()` with event_type `ban` or `unban`. Note: `on_member_ban` receives `(guild, user)` not `(member)`.
- [x] T036 [P] Implement `on_raw_reaction_add` and `on_raw_reaction_remove` listeners in `cogs/stats.py` — check `config.get("stats.track_reactions")`, skip bots, then upsert `user_activity_daily` row: increment `reactions_given` for the reactor's user_id, increment `reactions_received` for the message author's user_id (requires fetching the message or using payload.message_author_id if available). Use today's date string `YYYY-MM-DD`.

### Scheduler Tasks

- [x] T037 Register `stats_snapshot_{guild_id}` scheduler task in `cog_load` of `cogs/stats.py` — use `self.bot.scheduler.register()` with interval from `config.get("stats.snapshot_interval_hours", 1)`. The coroutine should call `guild.chunk()` if needed, then `database.save_member_snapshot(guild_id, total_members, approximate_online, bot_count, premium_subscription_count, premium_tier)`.
- [x] T038 Register `stats_rollup_{guild_id}` scheduler task in `cog_load` of `cogs/stats.py` — use 24-hour interval. The coroutine should compute yesterday's date, call `database.rollup_user_activity(guild_id, yesterday)`, `database.rollup_channel_activity(guild_id, yesterday)`, then `database.prune_old_events(config.get("stats.retention_days", 30))`. Log the rollup results.

**Checkpoint**: Data collection is now active. Bot logs messages, voice sessions, member events, reactions, and periodic snapshots. All database read/write helpers and display utilities are ready. Commands can now be built.

---

## Phase 3: User Story 1 — Server Activity Dashboard (Priority: P1) 🎯 MVP

**Goal**: `/stats` command showing a 4-embed server dashboard with overview metrics, user leaderboard, channel leaderboard, and daily activity chart.

**Independent Test**: Run `/stats` or `!stats` — should display 4 embeds: overview with 6 inline fields, top-5 users bar chart, top-5 channels bar chart, and a QuickChart line chart of daily activity. Run with `days=1` and `days=30` to verify range handling. Run with no data to verify empty-state message.

- [x] T039 [US1] Implement `/stats` hybrid command in `cogs/stats.py` — accept optional `days: int = 7` parameter (clamped 1-90). Fetch data via `get_server_stats_summary()`, `get_top_users()`, `get_top_channels()`, `get_daily_activity()`, `get_peak_hours()`. Build 4 embeds per contracts/commands.md:
  - **Embed 1 (Overview)**: color `0x3498DB`, thumbnail=guild icon, title `📊 Server Stats — Last {days} Days`, description with bold summary sentence, 6 inline fields (Messages, Voice Hours, Active Users, Active Channels, Reactions, Trend via `_trend_indicator`), footer with data range
  - **Embed 2 (Top Users)**: title `🏆 Most Active Users`, description = `_build_bar_chart()` output with user mentions and message counts
  - **Embed 3 (Top Channels)**: title `📌 Most Active Channels`, description = `_build_bar_chart()` output with channel names and message counts
  - **Embed 4 (Chart)**: title `📈 Daily Activity`, image = `_build_quickchart_url()` with line chart config — X-axis dates, blue filled-area line for messages, purple line for voice hours, dual Y-axes
  - Send all 4 via `await ctx.send(embeds=[...])`. Handle no-data case with single info embed.

**Checkpoint**: `/stats` is fully functional — validates all utility functions, database reads, and embed rendering. MVP complete.

---

## Phase 4: User Story 2 — User Activity Profile (Priority: P2)

**Goal**: `/userstats` command showing a 3-embed user profile with activity metrics, daily sparkline chart, and channel breakdown.

**Independent Test**: Run `/userstats @yourself` — should show 3 embeds with avatar thumbnail, activity metrics, daily bar chart, and per-channel breakdown. Run for a user with no data to verify empty state.

- [x] T040 [US2] Implement `/userstats` hybrid command in `cogs/stats.py` — accept `member: discord.Member` (required) and optional `days: int = 30` (clamped 1-90). Fetch data via `get_user_stats()` and `get_daily_activity()` filtered by user. Build 3 embeds per contracts/commands.md:
  - **Embed 1 (Profile)**: color = `_embed_color_for_trend()`, thumbnail = member's avatar URL, title `📊 Stats for {display_name}`, 8 inline fields (Messages Sent, Voice Time via `_format_duration`, Reactions Given, Top Channel, Daily Average, Trend, Reactions Received, Avg Voice Session)
  - **Embed 2 (Sparkline)**: title `📈 Daily Activity`, description with text sparkline using `▁▂▃▄▅▆▇█` for last 14 days plus avg/peak/quiet summary, image = QuickChart bar chart of daily messages
  - **Embed 3 (Channels)**: title `📌 Channel Activity`, description = `_build_bar_chart()` with per-channel message counts and percentages, top 5 + "other" bucket
  - Handle no-data with single embed.

**Checkpoint**: `/userstats` works independently — any user can query their own or another member's stats.

---

## Phase 5: User Story 3 — Channel Activity Report (Priority: P2)

**Goal**: `/channelstats` command showing a 3-embed channel report with overview, top contributors, and daily trend chart.

**Independent Test**: Run `/channelstats #general` — should show 3 embeds with channel metrics, contributor leaderboard, and daily volume bar chart. Run for an excluded or empty channel to verify edge cases.

- [x] T041 [US3] Implement `/channelstats` hybrid command in `cogs/stats.py` — accept `channel: discord.TextChannel` (required) and optional `days: int = 30` (clamped 1-90). Fetch via `get_channel_stats()`, `get_peak_hours()` filtered by channel. Build 3 embeds per contracts/commands.md:
  - **Embed 1 (Overview)**: color = `_embed_color_for_trend()`, thumbnail = guild icon, title `📌 Stats for #{channel_name}`, 6 inline fields (Total Messages, Unique Users, Daily Average, Peak Hour, Trend, Busiest Day)
  - **Embed 2 (Contributors)**: title `🏆 Top Contributors`, description = `_build_bar_chart()` of top 5 users with percentages, footer with remaining contributor count
  - **Embed 3 (Trend)**: title `📈 Daily Message Volume`, image = QuickChart bar chart with daily counts
  - Handle no-data with single embed.

**Checkpoint**: `/channelstats` works independently.

---

## Phase 6: User Story 4 — Voice Activity Dashboard (Priority: P2)

**Goal**: `/voicestats` command showing a 4-embed voice dashboard with overview, user leaderboard, channel usage, and daily trend chart.

**Independent Test**: Run `/voicestats` — should show 4 purple-themed embeds. Check "Currently In" count matches actual voice occupancy. Run with no voice data to verify empty state.

- [x] T042 [US4] Implement `/voicestats` hybrid command in `cogs/stats.py` — accept optional `days: int = 7` (clamped 1-90). Fetch via `get_voice_leaderboard()`, `get_voice_channel_stats()`, `get_daily_activity()`. Also count currently-connected voice members from `guild.voice_channels`. Build 4 embeds per contracts/commands.md:
  - **Embed 1 (Overview)**: color `0x9B59B6`, thumbnail = guild icon, title `🎤 Voice Stats — Last {days} Days`, 6 inline fields (Total Time, Unique Users, Sessions, Avg Session, Peak Concurrent, Currently In)
  - **Embed 2 (Users)**: title `🏆 Voice Leaderboard`, description = `_build_bar_chart()` with user mentions and formatted durations
  - **Embed 3 (Channels)**: title `📌 Channel Usage`, description = `_build_bar_chart()` with voice channel names prefixed by 🔊 and formatted durations
  - **Embed 4 (Trend)**: title `📈 Daily Voice Hours`, image = QuickChart line chart, purple filled area, dark theme
  - Handle no-data with single embed.

**Checkpoint**: `/voicestats` works independently with purple color theme.

---

## Phase 7: User Story 5 — Member Growth Tracking (Priority: P2)

**Goal**: `/growth` command showing a 4-embed growth dashboard with summary, member count chart, daily breakdown table, and join/leave distribution chart.

**Independent Test**: Run `/growth` and `/growth days:7` — should show 4 embeds with growth metrics and charts. Verify retention rate calculation. Run early (before snapshots accumulate) to verify graceful empty state.

- [x] T043 [US5] Implement `/growth` hybrid command in `cogs/stats.py` — accept optional `days: int = 30` (clamped 1-365). Fetch via `get_member_growth()`, `get_member_events_summary()`. Build 4 embeds per contracts/commands.md:
  - **Embed 1 (Summary)**: color = `_embed_color_for_trend()`, thumbnail = guild icon, title `👥 Member Growth — Last {days} Days`, bold description with net change and percentage, 6 inline fields (Current, N-days Ago, Net Change, Joins, Leaves, Retention Rate as `(joins-leaves)/joins*100`)
  - **Embed 2 (Chart)**: title `📈 Member Count Over Time`, image = QuickChart line chart with green filled area, Y-axis min set below data minimum
  - **Embed 3 (Table)**: title `📅 Daily Activity (Last 7 Days)`, description = code block with formatted table showing Date, Joins, Leaves, Net, Members columns with box-drawing separator lines and totals row
  - **Embed 4 (Distribution)**: title `📊 Joins vs Leaves`, image = QuickChart stacked bar chart with green (joins) and red (leaves)
  - Handle no-data with single embed noting snapshot collection start.

**Checkpoint**: `/growth` works independently — shows both snapshot-based trends and event-based join/leave data.

---

## Phase 8: User Story 6 — AI-Powered Insights (Priority: P3)

**Goal**: `/insights` command using Gemini to analyze server trends and provide recommendations in a gold-themed embed.

**Independent Test**: Run `/insights` with Gemini configured — should show 2-3 gold-themed embeds with AI analysis and data summary. Run without Gemini configured to verify graceful degradation message. Run with no data to verify the "not enough data" message.

- [x] T044 [US6] Implement `analyze_trends(stats_summary: dict) -> str` function in `utils/gemini.py` — follow the existing `summarize_findings()` pattern: check for API key, build a prompt asking Gemini to analyze the stats summary dict (member growth, top channels, peak hours, voice usage, active users) and provide 3-5 actionable recommendations, call Gemini with token cap, return the response text. Handle missing key (return None) and API errors (log + return None) gracefully.
- [x] T045 [US6] Implement `/insights` hybrid command in `cogs/stats.py` — accept optional `days: int = 7` (clamped 1-90). Gather stats via `get_server_stats_summary()`, `get_top_users()`, `get_top_channels()`, `get_member_events_summary()`, `get_peak_hours()`. Build summary dict and pass to `analyze_trends()`. Build 2-3 embeds per contracts/commands.md:
  - **Embed 1 (Analysis)**: color `0xF39C12`, thumbnail = guild icon, title `🤖 AI Insights — Last {days} Days`, description = Gemini response text (up to 4096 chars), footer with "Powered by Gemini" and data point counts
  - **Embed 2 (Metrics)**: title `📊 Data Summary`, 6 inline fields showing the raw numbers fed to Gemini (Messages, Voice Hours, Active Users, Growth, Peak Day, Quietest Day)
  - **Embed 3 (Recommendations, optional)**: title `🎯 Recommendations`, only if Gemini output exceeds single embed
  - Handle 3 error cases: Gemini not configured (red embed with setup instructions), API failure (red embed with fallback message), no data (info embed suggesting wait time).

**Checkpoint**: `/insights` works with and without Gemini configured — graceful degradation confirmed.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final validation, edge cases, and cleanup across all commands.

- [x] T046 Add `description` parameter to all 6 hybrid commands in `cogs/stats.py` for Discord's slash command UI — e.g., `@commands.hybrid_command(description="View server activity dashboard")`. Verify all 6 commands show descriptions in Discord's `/` menu.
- [x] T047 [P] Add input validation to all commands in `cogs/stats.py` — clamp `days` parameter to valid range (1-90 for most, 1-365 for growth), return user-friendly error embed if `days` is out of range rather than crashing. Verify `guild_only()` check prevents DM usage.
- [x] T048 [P] Verify all event listeners in `cogs/stats.py` handle edge cases: `on_message` ignores DMs (`message.guild is None`), `on_voice_state_update` handles channel switches (not just join/leave), reaction listeners handle `payload.message_author_id` being None (deleted messages).
- [x] T049 Run end-to-end test per quickstart.md Step 7: start bot, send messages, join/leave voice, run all 6 commands via both prefix and slash, verify QuickChart images render with dark theme, verify Unicode bar charts align in code blocks, verify embeds use correct colors and emoji.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (T001-T002 must complete for DB helpers) — BLOCKS all user stories
- **User Stories (Phase 3-8)**: All depend on Phase 2 completion
  - US1 (`/stats`) is the MVP — implement first to validate all utilities
  - US2-US5 can proceed in parallel after US1 validates the pattern
  - US6 (`/insights`) depends on `utils/gemini.py` change (T044) but is otherwise independent
- **Polish (Phase 9)**: Depends on all user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — No dependencies on other stories. **Implement first** to validate utilities.
- **US2 (P2)**: Can start after Phase 2 — independent of US1
- **US3 (P2)**: Can start after Phase 2 — independent of US1/US2
- **US4 (P2)**: Can start after Phase 2 — independent of other stories
- **US5 (P2)**: Can start after Phase 2 — independent of other stories
- **US6 (P3)**: Can start after Phase 2 — T044 (Gemini helper) must complete before T045

### Within Each User Story

- Each story is a single task (one command implementation)
- No intra-story parallelism needed — each is one focused hybrid command
- Story complete = command works via both prefix and slash

### Parallel Opportunities

- **Phase 1**: T003, T004, T005 can all run in parallel (different files)
- **Phase 2**: T007-T011 (write helpers) can all run in parallel. T016-T025 (read helpers) can all run in parallel. T028-T031 (utility functions) can all run in parallel. T034-T036 (event listeners) can all run in parallel.
- **Phase 3-8**: US2-US5 can all run in parallel after US1 validates the pattern
- **Phase 9**: T046 and T047 can run in parallel

---

## Parallel Example: Phase 2 Database Helpers

```text
# Launch all write helpers in parallel (different functions, no dependencies):
T007: Implement start_voice_session() in database.py
T008: Implement end_voice_session() in database.py
T009: Implement close_orphaned_voice_sessions() in database.py
T010: Implement log_member_event() in database.py
T011: Implement save_member_snapshot() in database.py

# Launch all read helpers in parallel:
T016-T025: All get_* functions in database.py (independent queries)

# Launch all utility functions in parallel (different functions in same file):
T028: _build_quickchart_url() in cogs/stats.py
T029: _trend_indicator() in cogs/stats.py
T030: _format_duration() in cogs/stats.py
T031: _embed_color_for_trend() in cogs/stats.py
```

## Parallel Example: User Stories After MVP

```text
# After US1 (/stats) validates the pattern, launch US2-US5 in parallel:
T040: [US2] /userstats command in cogs/stats.py
T041: [US3] /channelstats command in cogs/stats.py
T042: [US4] /voicestats command in cogs/stats.py
T043: [US5] /growth command in cogs/stats.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T005)
2. Complete Phase 2: Foundational (T006-T038)
3. Complete Phase 3: User Story 1 — `/stats` (T039)
4. **STOP and VALIDATE**: Test `/stats` independently via both prefix and slash
5. Deploy if ready — server members can immediately see activity dashboards

### Incremental Delivery

1. Complete Setup + Foundational → Data collection active, utilities ready
2. Add `/stats` → Test independently → Deploy (MVP!)
3. Add `/userstats` + `/channelstats` → Test → Deploy
4. Add `/voicestats` + `/growth` → Test → Deploy
5. Add `/insights` → Test with/without Gemini → Deploy
6. Polish → Final validation → Done

---

## Notes

- [P] tasks = different files or independent functions, no dependencies
- [Story] label maps task to specific user story for traceability
- All commands use `@commands.hybrid_command()` — no `@has_admin_role()` (open to all users)
- All commands send multiple embeds via `await ctx.send(embeds=[...])`
- QuickChart URLs use dark theme matching Discord: `backgroundColor=rgb(47,49,54)`
- Unicode bar charts use `█` (filled) and `░` (empty), wrapped in code blocks
- Commit after each phase or logical group
- Stop at any checkpoint to validate independently
