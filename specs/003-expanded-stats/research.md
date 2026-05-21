# Research: Expanded Stats & Metrics

## Decision: No New Tables Required

**Rationale**: All proposed metrics can be derived from existing raw tables (`message_events`, `voice_sessions`, `member_events`, `member_snapshots`) and rollup tables (`user_activity_daily`, `channel_activity_daily`). Some metrics benefit from new columns on existing rollup tables to avoid recomputing from raw data on every command invocation.

**Alternatives considered**: Creating dedicated aggregate tables (e.g., `user_streaks`, `hourly_heatmap`) — rejected because the rollup tables already provide the needed granularity and new tables would add schema complexity without meaningful performance benefit at Discord bot scale.

## Decision: Add `word_count` Column to Rollup Tables

**Rationale**: `message_events` already stores `word_count` per message, but the rollup tables (`user_activity_daily`, `channel_activity_daily`) don't aggregate it. Adding `total_words` to both rollup tables enables avg-words-per-message without scanning raw events.

**Alternatives considered**: Computing from raw `message_events` on every query — rejected because raw events are pruned after `retention_days`, so historical word counts would be lost.

## Decision: Add `hour` Distribution Query Pattern

**Rationale**: Peak hour analysis currently uses raw `message_events` with `substr(recorded_at, 12, 2)`. For per-channel and per-user hourly heatmaps, the same pattern works. No schema change needed — the existing `recorded_at` ISO timestamp is sufficient for hour extraction.

## Decision: Use Runtime Discord.py Data for Role/Join Metrics

**Rationale**: Member join dates, account creation dates, current roles, and online status are available from `guild.members` at runtime. These don't need to be stored — they're always current. Combining runtime member data with DB stats gives us tenure, role-based breakdowns, and new-account detection without any new collection.

## Derived Metrics Catalog

### From `message_events` (raw)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Avg words/message (user) | `AVG(word_count) WHERE user_id = ?` | Falls back to rollup after prune |
| Most active hour (user) | `GROUP BY hour ORDER BY COUNT(*) DESC` | Same substr pattern as peak_hours |
| Hourly heatmap (channel) | `GROUP BY hour` for specific channel_id | 24-bucket histogram |
| Weekend vs weekday | `strftime('%w', recorded_at)` → 0=Sun, 6=Sat | Group by weekend/weekday |
| Day-of-week distribution | `strftime('%w', recorded_at)` grouped | 7-bucket histogram |

### From `user_activity_daily` (rollup)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Activity streak | Consecutive days with message_count > 0 | Window function or iterate in Python |
| Consistency score | `STDEV` equivalent (compute in Python) | Lower std dev = more consistent |
| DAU (specific date) | `COUNT(DISTINCT user_id) WHERE date = ?` | |
| WAU | `COUNT(DISTINCT user_id) WHERE date >= 7d ago` | |
| MAU | `COUNT(DISTINCT user_id) WHERE date >= 30d ago` | |
| DAU/WAU ratio | DAU / WAU | Stickiness metric |
| DAU/MAU ratio | DAU / MAU | Engagement depth |
| User rank (messages) | `RANK() OVER (ORDER BY SUM(message_count) DESC)` | |
| User rank (voice) | `RANK() OVER (ORDER BY SUM(voice_minutes) DESC)` | |
| Engagement ratio | reactions_given / message_count | Per user |
| Dormancy (days since last) | `MAX(date)` compared to today | |

### From `channel_activity_daily` (rollup)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Messages per active user | SUM(message_count) / SUM(unique_users) | Conversation density |
| User concentration | Top-3 users' share of total messages | From raw message_events |
| Channel growth trend | Current period SUM vs prior period SUM | Percentage change |
| Top growing channels | ORDER BY (current - prior) DESC | |
| Top declining channels | ORDER BY (current - prior) ASC | |

### From `voice_sessions` (raw)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Median session length | Sort durations, pick middle | Compute in Python |
| Longest session | MAX(duration_seconds) | |
| Sessions by day of week | `strftime('%w', joined_at)` grouped | 7-bucket histogram |
| Session count per user | COUNT(*) WHERE user_id = ? | |
| Voice peak hours | `GROUP BY hour` on joined_at | Similar to message peak hours |

### From `member_events` (raw)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Churn rate | leaves / total_members (from snapshot) | |
| Ban rate | bans / (leaves + bans) | Among departures |
| Busiest join day of week | `strftime('%w', recorded_at)` for joins | |

### From `member_snapshots` (raw)

| Metric | SQL Pattern | Notes |
|--------|-------------|-------|
| Average online ratio | AVG(online_members / total_members) | Over snapshot window |
| Boost trend | Compare boost_count over time | |

### From Discord.py Runtime Data

| Metric | Source | Notes |
|--------|--------|-------|
| Average member tenure | `guild.members[*].joined_at` | Days since join, averaged |
| New account detection | `member.created_at` vs `member.joined_at` | Accounts < 7d old at join |
| Role-based activity | Cross-ref member.roles with user_activity_daily | Group stats by role |
| User rank | Sort all users by messages/voice | Position / total |

## Performance Considerations

- **Streak calculation**: Cannot be done efficiently in SQLite without window functions or recursive CTEs. Compute in Python by iterating `user_activity_daily` rows for the user — the table is bounded by retention days, so this is at most ~365 rows per user.
- **Gini coefficient / Shannon entropy**: Compute in Python from channel message counts. The query returns at most ~200 channels.
- **Concurrent voice users**: Requires overlapping interval queries on `voice_sessions`. This is expensive on large datasets. Compute only when explicitly requested, not in the overview dashboard.
- **User comparison**: Two parallel queries, one per user. Minimal overhead.
- **New rollup columns**: The `word_count` column migration runs once. The rollup function needs a small update to also SUM word_count.

## Implementation Approach

1. **Database migration**: Add `total_words INTEGER DEFAULT 0` to `user_activity_daily` and `channel_activity_daily`. Update `rollup_user_activity` and `rollup_channel_activity` to also aggregate word_count.
2. **New query functions**: Add `get_user_streaks()`, `get_engagement_metrics()`, `get_dau_wau_mau()`, `get_channel_growth_trends()`, etc. to `database.py`.
3. **Enhance existing commands**: Add new embed fields and additional embeds to existing commands.
4. **New commands**: `/serverpulse`, `/leaderboard`, `/activity @User1 @User2` as new hybrid commands in the existing `Stats` cog.
5. **Utility functions**: Add `_gini_coefficient()`, `_compute_streak()`, `_compute_consistency()` to `cogs/stats.py`.
