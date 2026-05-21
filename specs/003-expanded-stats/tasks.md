# Tasks: Expanded Stats & Metrics

**Input**: Design documents from `specs/003-expanded-stats/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/commands.md, quickstart.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Foundational (Database & Utilities)

**Purpose**: Schema migration, rollup updates, and shared utility functions that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T001 Add `total_words INTEGER DEFAULT 0` column migration to `init_db()` for both `user_activity_daily` and `channel_activity_daily` in `database.py`
- [X] T002 [P] Update `rollup_user_activity()` to SUM `word_count` into `total_words` in `database.py`
- [X] T003 [P] Update `rollup_channel_activity()` to SUM `word_count` into `total_words` in `database.py`
- [X] T004 [P] Add `_gini_coefficient(values: list[int]) -> float` utility function in `cogs/stats.py`
- [X] T005 [P] Add `_compute_streak(daily_rows: list[dict]) -> tuple[int, int]` utility function in `cogs/stats.py`
- [X] T006 [P] Add `_consistency_score(daily_counts: list[int]) -> float` utility function (0-100 scale) in `cogs/stats.py`
- [X] T007 [P] Add `_composite_health_score(metrics: dict) -> int` utility function (0-100, five 0-20 components) in `cogs/stats.py`
- [X] T008 [P] Add `_day_name(day_num: int) -> str` utility function (SQLite strftime('%w') mapping) in `cogs/stats.py`
- [X] T009 [P] Add `_build_heatmap_bar(hours_data: list, width: int = 24) -> str` utility function using `░▁▂▃▄▅▆▇█` chars in `cogs/stats.py`

**Checkpoint**: Migration runs cleanly, rollups populate `total_words`, all 6 utility functions defined

---

## Phase 2: US1 — Enhanced Server Dashboard `/stats` (Priority: P1)

**Goal**: Add avg msg length, msgs/day, weekday/weekend fields to Embed 1; add new Embed 5 (Server Health) with DAU/WAU/MAU, velocity, diversity, growing/declining channels

**Independent Test**: Run `/stats 30` — verify 5 embeds appear; Embed 1 shows new fields; Embed 5 shows health metrics with fallback to 0 when no data

- [X] T010 [P] [US1] Add `get_dau_wau_mau(guild_id, days)` returning `{dau, wau, mau, dau_wau, dau_mau}` in `database.py`
- [X] T011 [P] [US1] Add `get_server_word_stats(guild_id, days)` returning `{total_words, avg_words_per_msg}` from `user_activity_daily.total_words` in `database.py`
- [X] T012 [P] [US1] Add `get_weekday_weekend_split(guild_id, days)` returning `{weekday_msgs, weekend_msgs, ratio}` from `message_events` in `database.py`
- [X] T013 [P] [US1] Add `get_channel_growth_trends(guild_id, days)` returning `[{channel_id, current, previous, change_pct}]` from `channel_activity_daily` in `database.py`
- [X] T014 [P] [US1] Add `get_activity_diversity(guild_id, days)` returning `{gini, top3_share}` from `channel_activity_daily` (uses `_gini_coefficient`) in `database.py`
- [X] T015 [P] [US1] Add `get_message_velocity(guild_id, days)` returning `{current_rate, prior_rate, change_pct}` from `user_activity_daily` in `database.py`
- [X] T016 [US1] Enhance `/stats` Embed 1 (Overview) — add Avg Msg Length, Msgs/Day Avg, Wkday/Wkend fields per contracts in `cogs/stats.py`
- [X] T017 [US1] Add `/stats` Embed 5 (Server Health) — DAU/WAU/MAU, DAU/MAU ratio, message velocity, channel diversity, top growing/declining channels per contracts in `cogs/stats.py`

**Checkpoint**: `/stats` shows 5 embeds with all new server-level metrics

---

## Phase 3: US2 — Enhanced User Profile `/userstats` (Priority: P1)

**Goal**: Add avg msg length, rank, streaks to Embed 1; add new Embed 4 (Activity Profile) with active hour, streaks, consistency, dormancy, heatmap

**Independent Test**: Run `/userstats @User 30` — verify 4 embeds appear; Embed 1 shows rank/streaks; Embed 4 shows hourly heatmap bar and consistency score

- [X] T018 [P] [US2] Add `get_user_word_stats(guild_id, user_id, days)` returning `{total_words, avg_words}` from `user_activity_daily.total_words` in `database.py`
- [X] T019 [P] [US2] Add `get_user_active_hours(guild_id, user_id, days)` returning 24-entry `[{hour, count}]` from `message_events` in `database.py`
- [X] T020 [P] [US2] Add `get_user_streaks(guild_id, user_id, days)` returning `{current, longest, active_days, total_days}` — fetch `user_activity_daily` rows then compute via `_compute_streak` in `database.py`
- [X] T021 [P] [US2] Add `get_user_consistency(guild_id, user_id, days)` returning `{mean, std_dev, score}` — fetch daily counts then compute via `_consistency_score` in `database.py`
- [X] T022 [P] [US2] Add `get_user_weekday_split(guild_id, user_id, days)` returning `{weekday, weekend}` from `message_events` in `database.py`
- [X] T023 [P] [US2] Add `get_user_rank(guild_id, user_id, days)` returning `{msg_rank, voice_rank, total_users}` from `user_activity_daily` in `database.py`
- [X] T024 [P] [US2] Add `get_user_dormancy(guild_id, user_id)` returning `{days_since_last, last_date}` from `user_activity_daily` in `database.py`
- [X] T025 [P] [US2] Add `get_user_engagement_ratios(guild_id, user_id, days)` returning `{reaction_per_msg, received_per_msg}` from `user_activity_daily` in `database.py`
- [X] T026 [US2] Enhance `/userstats` Embed 1 (Profile) — add Avg Msg Length, Server Rank, Current/Longest Streak fields per contracts in `cogs/stats.py`
- [X] T027 [US2] Add `/userstats` Embed 4 (Activity Profile) — most active hour, weekday/weekend, consistency score, dormancy, engagement ratio, hourly heatmap bar per contracts in `cogs/stats.py`

**Checkpoint**: `/userstats` shows 4 embeds with full activity profile

---

## Phase 4: US3 — Enhanced Channel Stats `/channelstats` (Priority: P1)

**Goal**: Add avg words, msgs/user, top-3 share to Embed 1; add new Embed 4 (Channel Profile) with weekday/weekend, growth, user concentration, heatmap

**Independent Test**: Run `/channelstats #channel 30` — verify 4 embeds appear; Embed 1 shows density metrics; Embed 4 shows heatmap bar and Gini coefficient

- [X] T028 [P] [US3] Add `get_channel_word_stats(guild_id, channel_id, days)` returning `{total_words, avg_words}` from `channel_activity_daily.total_words` in `database.py`
- [X] T029 [P] [US3] Add `get_channel_hourly_heatmap(guild_id, channel_id, days)` returning 24-entry `[{hour, count}]` from `message_events` in `database.py`
- [X] T030 [P] [US3] Add `get_channel_user_concentration(guild_id, channel_id, days)` returning `{top3_share, gini}` from `message_events` (uses `_gini_coefficient`) in `database.py`
- [X] T031 [P] [US3] Add `get_channel_weekday_split(guild_id, channel_id, days)` returning `{weekday, weekend}` from `message_events` in `database.py`
- [X] T032 [P] [US3] Add `get_channel_density(guild_id, channel_id, days)` returning `{msgs_per_user}` from `channel_activity_daily` in `database.py`
- [X] T033 [P] [US3] Add `get_channel_growth(guild_id, channel_id, days)` returning `{current, previous, change_pct}` from `channel_activity_daily` in `database.py`
- [X] T034 [US3] Enhance `/channelstats` Embed 1 (Overview) — add Avg Words/Msg, Msgs/User, Top-3 Share fields per contracts in `cogs/stats.py`
- [X] T035 [US3] Add `/channelstats` Embed 4 (Channel Profile) — weekday/weekend, growth vs prior, user concentration Gini, hourly heatmap bar per contracts in `cogs/stats.py`

**Checkpoint**: `/channelstats` shows 4 embeds with full channel profile

---

## Phase 5: US4 — Enhanced Voice Stats `/voicestats` (Priority: P2)

**Goal**: Add median/longest session and busiest day to Embed 1; add new Embed 5 (Session Analysis) with session length distribution histogram and day-of-week breakdown

**Independent Test**: Run `/voicestats 30` — verify 5 embeds appear; Embed 1 shows session stats; Embed 5 shows distribution buckets and day-of-week bars

- [X] T036 [P] [US4] Add `get_voice_session_distribution(guild_id, days)` returning `{median, p25, p75, max, count}` + bucket counts from `voice_sessions` in `database.py`
- [X] T037 [P] [US4] Add `get_voice_day_of_week(guild_id, days)` returning 7-entry `[{day, sessions, minutes}]` from `voice_sessions` in `database.py`
- [X] T038 [P] [US4] Add `get_voice_peak_hours(guild_id, days)` returning 24-entry `[{hour, sessions}]` from `voice_sessions` in `database.py`
- [X] T039 [US4] Enhance `/voicestats` Embed 1 (Overview) — add Median Session, Longest Session, Busiest Day fields per contracts in `cogs/stats.py`
- [X] T040 [US4] Add `/voicestats` Embed 5 (Session Analysis) — session length distribution histogram (<5m, 5-15m, 15-30m, 30-60m, 1-2h, 2h+) and day-of-week breakdown per contracts in `cogs/stats.py`

**Checkpoint**: `/voicestats` shows 5 embeds with session analysis

---

## Phase 6: US5 — Enhanced Growth Dashboard `/growth` (Priority: P2)

**Goal**: Add churn rate, ban rate, avg tenure to Embed 1; add new Embed 5 (Member Lifecycle) with join day distribution, new account detection, online ratio

**Independent Test**: Run `/growth 30` — verify 5 embeds appear; Embed 1 shows churn metrics; Embed 5 shows join day-of-week bars

- [X] T041 [P] [US5] Add `get_churn_metrics(guild_id, days)` returning `{churn_rate, ban_rate, turnover}` from `member_events` + `member_snapshots` in `database.py`
- [X] T042 [P] [US5] Add `get_join_day_distribution(guild_id, days)` returning 7-entry `[{day, count}]` from `member_events` in `database.py`
- [X] T043 [US5] Enhance `/growth` Embed 1 (Summary) — add Churn Rate, Ban Rate, Avg Tenure fields per contracts in `cogs/stats.py`
- [X] T044 [US5] Add `/growth` Embed 5 (Member Lifecycle) — busiest join day, new accounts (<7d old), online ratio avg, joins by day-of-week per contracts in `cogs/stats.py`

**Checkpoint**: `/growth` shows 5 embeds with member lifecycle data

---

## Phase 7: US6 — Enhanced Peak Hours `/peakhours` (Priority: P2)

**Goal**: Add weekday/weekend peak, volume change to Embed 1; add new Embed 2 (Channel Breakdown) with per-channel peak hours and weekday vs weekend heatmaps

**Independent Test**: Run `/peakhours 30` — verify 2 embeds appear; Embed 1 shows weekday/weekend peaks; Embed 2 shows top 5 channel bars with peak hours and weekday/weekend heatmap comparison

- [X] T045 [P] [US6] Add `get_per_channel_peak_hours(guild_id, days, limit=5)` returning `[{channel_id, total_msgs, peak_hour}]` from `message_events` in `database.py`
- [X] T046 [P] [US6] Add `get_hourly_weekday_weekend(guild_id, days)` returning `{weekday: [{hour, count}], weekend: [{hour, count}]}` from `message_events` in `database.py`
- [X] T047 [US6] Enhance `/peakhours` Embed 1 — add Weekday Peak, Weekend Peak, Volume Change vs prior, Quietest Hour fields per contracts in `cogs/stats.py`
- [X] T048 [US6] Add `/peakhours` Embed 2 (Channel Breakdown) — top 5 channels with bar + peak hour, weekday vs weekend heatmap lines per contracts in `cogs/stats.py`

**Checkpoint**: `/peakhours` shows 2 embeds with channel breakdown and weekday/weekend comparison

---

## Phase 8: US7 — Server Pulse `/serverpulse` (Priority: P2)

**Goal**: New admin-only command showing a single embed with composite health score (0-100), today vs 7-day averages, current state, and engagement metrics

**Independent Test**: Run `/serverpulse` as admin — verify 1 embed with health score label (Critical/Needs Attention/Average/Healthy/Thriving), today vs avg comparisons, and live voice/online counts

**Dependencies**: Reuses `get_dau_wau_mau` (US1), `get_server_word_stats` (US1), `get_message_velocity` (US1), `get_churn_metrics` (US5)

- [X] T049 [US7] Add `/serverpulse` hybrid command with `@has_admin_role()` decorator — compute health score via `_composite_health_score`, build single embed with Today vs 7d Avg section, Right Now section (top channel, voice count, online count), and Engagement section per contracts in `cogs/stats.py`

**Checkpoint**: `/serverpulse` returns health score embed for admins, denied for non-admins

---

## Phase 9: US8 — Leaderboard `/leaderboard` (Priority: P3)

**Goal**: New command showing top 10 users with bar chart for selected category (messages, voice, streaks, engagement, social)

**Independent Test**: Run `/leaderboard 7 messages` — verify 10-user bar chart embed; try each category; verify streaks category computes correctly

- [X] T050 [P] [US8] Add `get_leaderboard(guild_id, days, category, limit=10)` returning `[{user_id, value}]` — support categories: messages (SUM message_count), voice (SUM voice_minutes), streaks (current streak via _compute_streak), engagement (reactions_received/message_count), social (SUM reactions_given) in `database.py`
- [X] T051 [US8] Add `/leaderboard` hybrid command with `days` (int, default 7) and `category` (choice: messages/voice/streaks/engagement/social) parameters — build embed with medal emojis for top 3, `_build_bar_chart` for all 10 per contracts in `cogs/stats.py`

**Checkpoint**: `/leaderboard` shows correct 10-user ranking for all 5 categories

---

## Phase 10: US9 — Activity Comparison `/activity` (Priority: P3)

**Goal**: New command comparing two users side-by-side with head-to-head embed and daily overlay chart

**Independent Test**: Run `/activity @User1 @User2 30` — verify 2 embeds: head-to-head metrics table with directional bars, and QuickChart overlay line chart with both users' daily message counts

**Dependencies**: Reuses query functions from US2 (user word stats, streaks, consistency, engagement)

- [X] T052 [US9] Add `/activity` hybrid command with `user1` (discord.Member), `user2` (discord.Member), `days` (int, default 30) parameters — build head-to-head embed comparing messages, voice, reactions given/received, avg words, current streak, consistency, top channel per contracts in `cogs/stats.py`
- [X] T053 [US9] Add daily overlay QuickChart line chart to `/activity` — fetch both users' daily message counts from `user_activity_daily`, build two-dataset line chart URL via `_build_quickchart_url` in `cogs/stats.py`

**Checkpoint**: `/activity` shows versus-style comparison with overlay chart

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Documentation updates and final validation

- [X] T054 [P] Update `_COG_DESCRIPTIONS["Stats"]` in `utils/help.py` to mention `/serverpulse`, `/leaderboard`, `/activity` commands
- [X] T055 [P] Add `/serverpulse`, `/leaderboard`, `/activity` to command table in `README.md` and update Stats feature description
- [X] T056 Verify all enhanced commands handle empty data gracefully — test each command with `days=1` on a quiet server to confirm 0/N/A fallbacks appear correctly

**Checkpoint**: Help command lists new commands; README is current; no crashes on empty data

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — start immediately. BLOCKS all user stories.
- **US1-US6 (Phases 2-7)**: All depend on Foundational completion. Can proceed in parallel or sequentially.
- **US7 /serverpulse (Phase 8)**: Depends on US1 (for DAU/MAU, word stats, velocity queries) and US5 (for churn metrics).
- **US8 /leaderboard (Phase 9)**: Depends on Foundational only. Independent of US1-US7.
- **US9 /activity (Phase 10)**: Depends on US2 (reuses user query functions for comparison).
- **Polish (Phase 11)**: Depends on all user stories being complete.

### User Story Dependencies

```
Foundational ──┬── US1 (/stats) ─────────┬── US7 (/serverpulse)
               ├── US2 (/userstats) ─────┼── US9 (/activity)
               ├── US3 (/channelstats)   │
               ├── US4 (/voicestats)     │
               ├── US5 (/growth) ────────┘
               ├── US6 (/peakhours)
               └── US8 (/leaderboard)
                                          └── Polish
```

### Within Each User Story

- Query functions in `database.py` marked [P] can all run in parallel
- Embed enhancements depend on query functions being complete
- New embed depends on enhanced embed (same command, sequential changes)

### Parallel Opportunities

- T002-T009 (rollups + utilities): All [P], can run in parallel after T001
- T010-T015, T018-T025, T028-T033, T036-T038, T041-T042, T045-T046, T050: All query functions marked [P] within their phase
- US1-US6 can run in parallel after Foundational
- US8 can run in parallel with any user story
- T054-T055 (help + README) can run in parallel

---

## Parallel Example: Foundational Phase

```
# All utility functions can be written simultaneously (different function names, same file):
T004: _gini_coefficient in cogs/stats.py
T005: _compute_streak in cogs/stats.py
T006: _consistency_score in cogs/stats.py
T007: _composite_health_score in cogs/stats.py
T008: _day_name in cogs/stats.py
T009: _build_heatmap_bar in cogs/stats.py

# Both rollup updates can run in parallel (different functions, same file):
T002: rollup_user_activity in database.py
T003: rollup_channel_activity in database.py
```

## Parallel Example: US1

```
# All query functions can be written simultaneously:
T010: get_dau_wau_mau in database.py
T011: get_server_word_stats in database.py
T012: get_weekday_weekend_split in database.py
T013: get_channel_growth_trends in database.py
T014: get_activity_diversity in database.py
T015: get_message_velocity in database.py

# Then sequentially:
T016: Enhance Embed 1 (depends on T011, T012)
T017: Add Embed 5 (depends on T010, T013, T014, T015)
```

---

## Implementation Strategy

### MVP First (US1 + US2 Only)

1. Complete Phase 1: Foundational (migration, rollups, utilities)
2. Complete Phase 2: US1 — Enhanced `/stats`
3. Complete Phase 3: US2 — Enhanced `/userstats`
4. **STOP and VALIDATE**: Test both commands in Discord
5. Deploy if ready — users get immediate value from enhanced dashboards

### Incremental Delivery

1. Foundational → Foundation ready
2. US1 (`/stats`) → Enhanced server dashboard (MVP!)
3. US2 (`/userstats`) → Enhanced user profiles
4. US3 (`/channelstats`) → Enhanced channel stats
5. US4 (`/voicestats`) → Enhanced voice stats
6. US5 (`/growth`) → Enhanced growth dashboard
7. US6 (`/peakhours`) → Enhanced peak hours
8. US7 (`/serverpulse`) → Admin health check (requires US1+US5)
9. US8 (`/leaderboard`) → Leaderboard command
10. US9 (`/activity`) → User comparison (requires US2)
11. Polish → Help + README

Each increment adds value without breaking previous work.

---

## Notes

- [P] tasks = different files or independent functions, no dependencies
- [Story] label maps task to specific user story for traceability
- All query functions go in `database.py`, all command/UI code in `cogs/stats.py`
- Every new metric must handle empty data — return 0, "N/A", or skip the field
- The `_gini_coefficient` and `_compute_streak` utilities are used by multiple stories — defined once in Foundational
- `/serverpulse` is the only new command requiring `@has_admin_role()`
- Streak computation iterates Python list, not SQL window functions (SQLite compatibility)
- QuickChart overlay chart in `/activity` should limit to 30 days of data points
