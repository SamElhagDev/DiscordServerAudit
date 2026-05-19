# Data Model: Emoji-Triggered Fact-Check

## Overview

This feature requires **no new database tables**. The fact-check is stateless
from a persistence standpoint — results are posted as Discord messages and not
stored in SQLite. The only state is an in-memory cooldown cache to prevent
duplicate checks on the same message.

## In-Memory State

### FactCheck Cooldown Cache

| Field | Type | Description |
|-------|------|-------------|
| message_id | int | Discord message snowflake (dict key) |
| checked_at | float | `time.monotonic()` timestamp of the check |

**Structure**: `dict[int, float]` — maps `message_id` to monotonic timestamp.

**Eviction**: Entries older than `cooldown_seconds` (default 300 = 5 min) are
lazily evicted on next access. No background cleanup needed — the dict stays
small since only fact-checked messages are tracked.

**Purpose**: Prevents the same message from being fact-checked multiple times
if several users react with the emoji. Only the first reaction triggers the
check.

### User Rate Limit Cache

| Field | Type | Description |
|-------|------|-------------|
| user_id | int | Discord user snowflake (dict key) |
| timestamps | list[float] | Monotonic timestamps of recent fact-checks |

**Structure**: `dict[int, list[float]]` — maps `user_id` to a sliding window
of check timestamps.

**Window**: Configurable via `factcheck.rate_limit` (default 5 checks per
hour). Oldest entries beyond the window are pruned on access.

## Configuration Keys

Added to `config.yaml` under `factcheck:` section:

| Key | Type | Default | Env Override | Description |
|-----|------|---------|-------------|-------------|
| `factcheck.enabled` | bool | `true` | — | Master toggle |
| `factcheck.emoji` | str | `"\U0001F50D"` | `DiscordServerAudit_FACTCHECK_EMOJI` | Emoji name or unicode char |
| `factcheck.model` | str | `"gemini-2.5-flash"` | — | Gemini model for checks |
| `factcheck.rate_limit` | int | `5` | — | Max checks per user per hour |
| `factcheck.cooldown_seconds` | int | `300` | — | Seconds before same message can be re-checked |
| `factcheck.timeout_seconds` | int | `30` | — | Max seconds to wait for Gemini response |

## Existing Tables — No Changes

The following tables are untouched:
- `message_events`, `voice_sessions`, `member_events` — raw event tables
- `user_activity_daily`, `channel_activity_daily` — rollup tables
- `audit_runs`, `audit_findings` — audit tables
- `bulk_task_log`, `scheduler_state` — operational tables
