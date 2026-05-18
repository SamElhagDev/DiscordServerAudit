# Data Model: Comprehensive Server Stats Logging

**Date**: 2026-05-18 | **Branch**: `001-stats-logging`

## Overview

Six new tables added to the existing `bot.db` SQLite database, managed via `database.py`. Tables follow the existing pattern: `init_db()` creates them with `CREATE TABLE IF NOT EXISTS`, all timestamps stored as UTC ISO-8601 strings via `_now()`.

Tables are organized into three tiers:
1. **Raw event tables** — granular, short-retention (default 30 days)
2. **Rollup tables** — daily aggregates, long-retention (indefinite)
3. **Snapshot tables** — periodic state captures, long-retention (indefinite)

## Entity Relationship Diagram

```
member_snapshots (hourly)        member_events (raw)
  guild_id ─────────┐              guild_id ───────┐
  recorded_at       │              user_id          │
  total_members     │              event_type       │
  online_members    │              recorded_at      │
  bot_count         │                               │
  boost_count       │                               │
  boost_tier        │                               │
                    │                               │
                    ▼                               ▼
              ┌──────────┐                   ┌──────────┐
              │  guild   │                   │  guild   │
              │ (Discord)│                   │ (Discord)│
              └──────────┘                   └──────────┘
                    ▲                               ▲
                    │                               │
message_events (raw)│         voice_sessions (raw)  │
  guild_id ─────────┘           guild_id ───────────┘
  channel_id                    channel_id
  user_id                       user_id
  recorded_at                   joined_at
  word_count                    left_at
                                duration_seconds
        │                               │
        ▼ (daily rollup)                ▼ (daily rollup)
channel_activity_daily          user_activity_daily
  guild_id                        guild_id
  channel_id                      user_id
  date                            date
  message_count                   message_count
  unique_users                    voice_minutes
                                  reactions_given
                                  reactions_received
```

## Tables

### 1. `member_snapshots` (Snapshot — hourly)

Periodic captures of guild-level membership metrics for growth tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| recorded_at | TEXT | NOT NULL | UTC ISO-8601 timestamp |
| total_members | INTEGER | NOT NULL | Total member count at snapshot time |
| online_members | INTEGER | DEFAULT 0 | Approximate online count (best-effort) |
| bot_count | INTEGER | DEFAULT 0 | Number of bot accounts |
| boost_count | INTEGER | DEFAULT 0 | Number of active boosts |
| boost_tier | INTEGER | DEFAULT 0 | Current boost tier (0-3) |

**Indexes**:
- `idx_member_snapshots_guild_time` ON (guild_id, recorded_at)

**Retention**: Indefinite (24 rows/day/guild — negligible growth)

### 2. `message_events` (Raw — bounded retention)

Individual message metadata for granular recent-history queries.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| channel_id | INTEGER | NOT NULL | Channel the message was sent in |
| user_id | INTEGER | NOT NULL | Author's Discord user ID |
| recorded_at | TEXT | NOT NULL | UTC ISO-8601 timestamp |
| word_count | INTEGER | DEFAULT 0 | Word count (engagement proxy) |

**Indexes**:
- `idx_message_events_guild_time` ON (guild_id, recorded_at)
- `idx_message_events_user` ON (guild_id, user_id, recorded_at)
- `idx_message_events_channel` ON (guild_id, channel_id, recorded_at)

**Retention**: Configurable, default 30 days. Pruned after daily rollup.

### 3. `voice_sessions` (Raw — bounded retention)

Voice channel usage tracked as start/end session pairs.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| channel_id | INTEGER | NOT NULL | Voice channel ID |
| user_id | INTEGER | NOT NULL | User's Discord ID |
| joined_at | TEXT | NOT NULL | UTC ISO-8601 join timestamp |
| left_at | TEXT | DEFAULT NULL | UTC ISO-8601 leave timestamp (NULL = still in channel) |
| duration_seconds | INTEGER | DEFAULT NULL | Computed on session close |

**Indexes**:
- `idx_voice_sessions_guild_time` ON (guild_id, joined_at)
- `idx_voice_sessions_user` ON (guild_id, user_id, joined_at)
- `idx_voice_sessions_open` ON (guild_id, left_at) — for finding orphaned sessions

**Retention**: Configurable, default 30 days. Pruned after daily rollup.

### 4. `member_events` (Raw — indefinite retention)

Membership lifecycle events (join, leave, ban, unban).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| user_id | INTEGER | NOT NULL | User's Discord ID |
| event_type | TEXT | NOT NULL | One of: `join`, `leave`, `ban`, `unban` |
| recorded_at | TEXT | NOT NULL | UTC ISO-8601 timestamp |

**Indexes**:
- `idx_member_events_guild_time` ON (guild_id, recorded_at)
- `idx_member_events_type` ON (guild_id, event_type, recorded_at)

**Retention**: Indefinite (low volume — a few events per day)

**Validation**: `event_type` must be one of `join`, `leave`, `ban`, `unban`.

### 5. `user_activity_daily` (Rollup — indefinite retention)

Daily per-user aggregated activity metrics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| user_id | INTEGER | NOT NULL | User's Discord ID |
| date | TEXT | NOT NULL | Date string `YYYY-MM-DD` |
| message_count | INTEGER | DEFAULT 0 | Messages sent that day |
| voice_minutes | INTEGER | DEFAULT 0 | Total voice time in minutes |
| reactions_given | INTEGER | DEFAULT 0 | Reactions added by user |
| reactions_received | INTEGER | DEFAULT 0 | Reactions received on user's messages |

**Indexes**:
- `idx_user_activity_guild_date` ON (guild_id, date)
- `idx_user_activity_user_date` ON (guild_id, user_id, date)

**Constraints**: UNIQUE (guild_id, user_id, date) — one row per user per day, upserted during rollup.

**Retention**: Indefinite (one row per active user per day)

### 6. `channel_activity_daily` (Rollup — indefinite retention)

Daily per-channel aggregated activity metrics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | Row ID |
| guild_id | INTEGER | NOT NULL | Discord guild ID |
| channel_id | INTEGER | NOT NULL | Channel's Discord ID |
| date | TEXT | NOT NULL | Date string `YYYY-MM-DD` |
| message_count | INTEGER | DEFAULT 0 | Messages sent in channel that day |
| unique_users | INTEGER | DEFAULT 0 | Distinct users who sent messages |

**Indexes**:
- `idx_channel_activity_guild_date` ON (guild_id, date)
- `idx_channel_activity_channel_date` ON (guild_id, channel_id, date)

**Constraints**: UNIQUE (guild_id, channel_id, date) — one row per channel per day.

**Retention**: Indefinite (one row per active channel per day)

## Database Helper Functions

New functions to add to `database.py`:

### Write operations (called from event handlers)
- `log_message_event(guild_id, channel_id, user_id, word_count)` — insert into `message_events`
- `start_voice_session(guild_id, channel_id, user_id)` — insert into `voice_sessions` with `left_at=NULL`
- `end_voice_session(guild_id, user_id)` — update open session with `left_at` and `duration_seconds`
- `close_orphaned_voice_sessions()` — close all sessions with `left_at IS NULL` (called on bot restart)
- `log_member_event(guild_id, user_id, event_type)` — insert into `member_events`
- `save_member_snapshot(guild_id, total, online, bots, boosts, tier)` — insert into `member_snapshots`

### Rollup operations (called from daily scheduler)
- `rollup_user_activity(guild_id, date)` — aggregate `message_events` + `voice_sessions` into `user_activity_daily`
- `rollup_channel_activity(guild_id, date)` — aggregate `message_events` into `channel_activity_daily`
- `prune_old_events(days)` — delete `message_events` and `voice_sessions` older than retention period

### Read operations (called from stats commands)
- `get_member_growth(guild_id, days)` — return member snapshots for growth chart data
- `get_top_users(guild_id, days, limit)` — return top users by message count from rollup
- `get_top_channels(guild_id, days, limit)` — return top channels by message count from rollup
- `get_voice_leaderboard(guild_id, days, limit)` — return top users by voice minutes
- `get_user_stats(guild_id, user_id, days)` — return activity summary for one user
- `get_channel_stats(guild_id, channel_id, days)` — return activity summary for one channel
- `get_server_stats_summary(guild_id, days)` — return aggregate stats for server overview
- `get_peak_hours(guild_id, days)` — return message counts grouped by hour-of-day from raw events
- `get_daily_activity(guild_id, days)` — return daily totals for activity trend

## Config Keys

New keys under `stats` section in `config.yaml`:

```yaml
stats:
  enabled: true                    # Master toggle for stats collection
  snapshot_interval_hours: 1       # How often to capture member snapshots
  retention_days: 30               # How long to keep raw event data
  rollup_hour_utc: 4               # Hour (UTC) to run daily aggregation
  track_reactions: true            # Whether to count reactions
  excluded_channels: []            # Channel IDs to exclude from tracking
  excluded_users: []               # User IDs to exclude (e.g., bots)
  exclude_bots: true               # Auto-exclude bot users from message/voice tracking
```

## Migration Notes

- All tables use `CREATE TABLE IF NOT EXISTS` — safe to run on existing databases
- No changes to existing tables — fully additive
- WAL mode enabled via `PRAGMA journal_mode=WAL` in `init_db()` (benefits all tables, not just new ones)
- Indexes created with `CREATE INDEX IF NOT EXISTS` — idempotent
