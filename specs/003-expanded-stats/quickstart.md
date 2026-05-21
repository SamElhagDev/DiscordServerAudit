# Quickstart: Expanded Stats Implementation

**Estimated effort**: ~8-12 hours

## Implementation Order

Follow this sequence — each phase builds on the previous.

### Phase 1: Database Migration & Rollup Updates (database.py)

1. Add migration to `init_db()` — add `total_words` column to both rollup tables:
   ```python
   # In init_db(), after existing schema:
   try:
       conn.execute("ALTER TABLE user_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0")
   except sqlite3.OperationalError:
       pass  # Column already exists
   try:
       conn.execute("ALTER TABLE channel_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0")
   except sqlite3.OperationalError:
       pass  # Column already exists
   ```

2. Update `rollup_user_activity()` to also SUM word_count into `total_words`

3. Update `rollup_channel_activity()` to also SUM word_count into `total_words`

4. Add all new query functions from data-model.md:
   - Server: `get_dau_wau_mau`, `get_server_word_stats`, `get_weekday_weekend_split`, `get_channel_growth_trends`, `get_activity_diversity`, `get_message_velocity`, `get_server_engagement_score`
   - User: `get_user_word_stats`, `get_user_active_hours`, `get_user_streaks`, `get_user_consistency`, `get_user_weekday_split`, `get_user_rank`, `get_user_dormancy`, `get_user_engagement_ratios`
   - Channel: `get_channel_word_stats`, `get_channel_hourly_heatmap`, `get_channel_user_concentration`, `get_channel_weekday_split`, `get_channel_density`, `get_channel_growth`
   - Voice: `get_voice_session_distribution`, `get_voice_day_of_week`, `get_voice_peak_hours`
   - Growth: `get_churn_metrics`, `get_join_day_distribution`
   - Leaderboard: `get_leaderboard`

**Verify**: Run `python -c "import database; database.init_db()"` — should add columns without errors.

### Phase 2: Utility Functions (cogs/stats.py)

Add to the top of the stats cog file:

1. `_gini_coefficient(values: list[int]) -> float` — inequality measure
2. `_compute_streak(daily_rows: list[dict]) -> tuple[int, int]` — (current, longest)
3. `_consistency_score(daily_counts: list[int]) -> float` — 0-100 scale
4. `_composite_health_score(metrics: dict) -> int` — 0-100 scale
5. `_day_name(day_num: int) -> str` — SQLite day number to name
6. `_build_heatmap_bar(hours_data: list, width: int = 24) -> str` — visual bar

### Phase 3: Enhance Existing Commands (cogs/stats.py)

Update one command at a time, test each after modifying:

1. **`/stats`** — Add Embed 5 (Server Health) with DAU/WAU/MAU, velocity, diversity, growing/declining channels, avg message length, weekday/weekend split
2. **`/userstats`** — Add Embed 4 (Activity Profile) with active hour, streaks, consistency, dormancy, engagement ratio, hourly heatmap, server rank
3. **`/channelstats`** — Add Embed 4 (Channel Profile) with avg words, user concentration, weekday/weekend, growth, hourly heatmap
4. **`/voicestats`** — Add Embed 5 (Session Analysis) with session distribution histogram, day-of-week breakdown, median/longest session, voice peak hours
5. **`/growth`** — Add Embed 5 (Member Lifecycle) with churn rate, ban rate, avg tenure, new accounts, join day distribution
6. **`/peakhours`** — Add Embed 2 (Channel Breakdown) with per-channel peak hours, weekday vs weekend hourly comparison, trend vs prior period

### Phase 4: New Commands (cogs/stats.py)

1. **`/serverpulse`** — Single embed quick health check (admin-only)
2. **`/leaderboard [days] [category]`** — Multi-category with 10-user bar chart
3. **`/activity @User1 @User2 [days]`** — Side-by-side comparison + overlay chart

### Phase 5: Update Help System (utils/help.py)

1. Update `_COG_DESCRIPTIONS["Stats"]` to mention new commands
2. Add new command descriptions/help strings

### Phase 6: Update README

1. Add `/serverpulse`, `/leaderboard`, `/activity` to command table
2. Update Stats feature description

## Key Patterns

- **No new event listeners** — all data already collected
- **Hybrid commands** — `@commands.hybrid_command()` for all new commands
- **No admin gating** on stats commands except `/serverpulse`
- **Fallback for no data** — every new metric should have a graceful "N/A" or "0" fallback
- **Word stats** — use rolled-up `total_words` column, fall back to raw `message_events.word_count` for un-rolled dates
- **Streak calculation** — iterate Python list, don't use SQL window functions (SQLite compatibility)
- **Gini coefficient** — compute in Python from a list of values, not in SQL

## Files Modified

| File | Change |
|------|--------|
| `database.py` | Add `total_words` column migration, update rollup functions, add ~20 new query functions |
| `cogs/stats.py` | Add utility functions, enhance 6 existing commands (add 1 embed each), add 3 new commands |
| `utils/help.py` | Update Stats cog description |
| `README.md` | Add new commands to command table |
