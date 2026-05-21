# Data Model: Expanded Stats

## Schema Changes

### Modified Table: `user_activity_daily`

Add one column:

```sql
ALTER TABLE user_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0;
```

### Modified Table: `channel_activity_daily`

Add one column:

```sql
ALTER TABLE channel_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0;
```

### No New Tables

All metrics derive from existing tables. The two new columns capture word counts during the daily rollup so they survive raw event pruning.

---

## Rollup Changes

### `rollup_user_activity()`

Update the message aggregation query to also SUM word_count:

```sql
INSERT INTO user_activity_daily (guild_id, user_id, date, message_count, total_words, voice_minutes)
SELECT me.guild_id, me.user_id, ?, COUNT(*), COALESCE(SUM(me.word_count), 0), 0
FROM message_events me
WHERE me.guild_id = ? AND DATE(me.recorded_at) = ?
GROUP BY me.guild_id, me.user_id
ON CONFLICT(guild_id, user_id, date) DO UPDATE
    SET message_count = excluded.message_count,
        total_words = excluded.total_words
```

### `rollup_channel_activity()`

Update to also SUM word_count:

```sql
INSERT INTO channel_activity_daily (guild_id, channel_id, date, message_count, unique_users, total_words)
SELECT guild_id, channel_id, ?, COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(word_count), 0)
FROM message_events
WHERE guild_id = ? AND DATE(recorded_at) = ?
GROUP BY guild_id, channel_id
ON CONFLICT(guild_id, channel_id, date) DO UPDATE
    SET message_count = excluded.message_count,
        unique_users = excluded.unique_users,
        total_words = excluded.total_words
```

---

## New Query Functions

### Server-Level

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_dau_wau_mau(guild_id, days)` | `{dau: int, wau: int, mau: int, dau_wau: float, dau_mau: float}` | user_activity_daily |
| `get_server_word_stats(guild_id, days)` | `{total_words: int, avg_words_per_msg: float}` | user_activity_daily (total_words) |
| `get_weekday_weekend_split(guild_id, days)` | `{weekday_msgs: int, weekend_msgs: int, ratio: float}` | message_events |
| `get_channel_growth_trends(guild_id, days)` | `[{channel_id, current, previous, change_pct}]` | channel_activity_daily |
| `get_activity_diversity(guild_id, days)` | `{gini: float, top3_share: float}` | channel_activity_daily |
| `get_message_velocity(guild_id, days)` | `{current_rate: float, prior_rate: float, change_pct: float}` | user_activity_daily |
| `get_server_engagement_score(guild_id, days)` | `{score: int, components: dict}` | Multiple |

### User-Level

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_user_word_stats(guild_id, user_id, days)` | `{total_words: int, avg_words: float}` | user_activity_daily (total_words) |
| `get_user_active_hours(guild_id, user_id, days)` | `[{hour: int, count: int}]` (24 entries) | message_events |
| `get_user_streaks(guild_id, user_id, days)` | `{current: int, longest: int, active_days: int, total_days: int}` | user_activity_daily |
| `get_user_consistency(guild_id, user_id, days)` | `{mean: float, std_dev: float, score: float}` | user_activity_daily |
| `get_user_weekday_split(guild_id, user_id, days)` | `{weekday: int, weekend: int}` | message_events |
| `get_user_rank(guild_id, user_id, days)` | `{msg_rank: int, voice_rank: int, total_users: int}` | user_activity_daily |
| `get_user_dormancy(guild_id, user_id)` | `{days_since_last: int, last_date: str}` | user_activity_daily |
| `get_user_engagement_ratios(guild_id, user_id, days)` | `{reaction_per_msg: float, received_per_msg: float}` | user_activity_daily |

### Channel-Level

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_channel_word_stats(guild_id, channel_id, days)` | `{total_words: int, avg_words: float}` | channel_activity_daily (total_words) |
| `get_channel_hourly_heatmap(guild_id, channel_id, days)` | `[{hour: int, count: int}]` (24 entries) | message_events |
| `get_channel_user_concentration(guild_id, channel_id, days)` | `{top3_share: float, gini: float}` | message_events |
| `get_channel_weekday_split(guild_id, channel_id, days)` | `{weekday: int, weekend: int}` | message_events |
| `get_channel_density(guild_id, channel_id, days)` | `{msgs_per_user: float}` | channel_activity_daily |
| `get_channel_growth(guild_id, channel_id, days)` | `{current: int, previous: int, change_pct: float}` | channel_activity_daily |

### Voice-Level

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_voice_session_distribution(guild_id, days)` | `{median: int, p25: int, p75: int, max: int, count: int}` | voice_sessions |
| `get_voice_day_of_week(guild_id, days)` | `[{day: int, sessions: int, minutes: int}]` (7 entries) | voice_sessions |
| `get_voice_peak_hours(guild_id, days)` | `[{hour: int, sessions: int}]` (24 entries) | voice_sessions |

### Growth-Level

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_churn_metrics(guild_id, days)` | `{churn_rate: float, ban_rate: float, turnover: float}` | member_events, member_snapshots |
| `get_join_day_distribution(guild_id, days)` | `[{day: int, count: int}]` (7 entries) | member_events |

### Leaderboard / Comparison

| Function | Returns | Source Tables |
|----------|---------|--------------|
| `get_leaderboard(guild_id, days, category, limit)` | `[{user_id, value}]` | user_activity_daily |
| `get_user_comparison(guild_id, user1_id, user2_id, days)` | `{user1: stats, user2: stats}` | Calls get_user_stats for each |

---

## Utility Functions (Python, in cogs/stats.py)

| Function | Input | Output | Notes |
|----------|-------|--------|-------|
| `_gini_coefficient(values)` | `list[int]` | `float` (0-1) | 0 = equal, 1 = concentrated |
| `_compute_streak(daily_rows)` | `list[dict]` with `date`, `message_count` | `(current, longest)` | Iterate consecutive dates |
| `_consistency_score(daily_counts)` | `list[int]` | `float` (0-100) | 100 = perfectly consistent |
| `_composite_score(metrics)` | `dict` of metric values | `int` (0-100) | Weighted composite |
| `_day_name(day_num)` | `int` (0-6) | `str` ("Sunday"-"Saturday") | SQLite strftime('%w') mapping |
| `_build_heatmap_bar(hours_data)` | `list[dict]` with `hour`, `count` | `str` | 24-bar visual |
