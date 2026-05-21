# Implementation Plan: Expanded Stats & Metrics

**Branch**: `main` | **Date**: 2026-05-20 | **Spec**: specs/003-expanded-stats/spec.md
**Input**: Feature specification from `specs/003-expanded-stats/spec.md`

## Summary

Expand existing stats commands (`/stats`, `/userstats`, `/channelstats`, `/voicestats`, `/growth`, `/peakhours`) with significantly more derived metrics — all computed from data already being collected. Add three new commands (`/serverpulse`, `/leaderboard`, `/activity`). No new event listeners, no new tables. Two new columns on existing rollup tables (`total_words`), ~20 new database query functions, 6 Python utility functions, 6 enhanced embeds, and 3 new command handlers.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py (commands extension), google-generativeai, sqlite3 (stdlib)
**Storage**: SQLite via `database.py` — existing tables: `message_events`, `voice_sessions`, `member_events`, `member_snapshots`, `user_activity_daily`, `channel_activity_daily`
**Testing**: Manual testing via Discord bot commands; flake8 lint in CI
**Target Platform**: Windows (Scheduled Task deployment via GitHub Actions)
**Project Type**: Discord bot (cog-modular architecture)
**Performance Goals**: All new queries must return within existing command response time (~1-2s). No heavy aggregations over unbounded data.
**Constraints**: SQLite only (no window functions for streaks — compute in Python). Raw events pruned after `retention_days`, so rollup columns must capture word counts before pruning.
**Scale/Scope**: Single-server bot. Rollup tables bounded by retention config. At most ~365 rows per user per year.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | All changes in existing `Stats` cog (`cogs/stats.py`) and `database.py`. No new cogs. |
| II. Admin Role Gating | PASS | `/serverpulse` gated with `@has_admin_role()`. All other new/enhanced commands are read-only stats — existing stats commands are already ungated per current design (read-only, no destructive actions). |
| III. Audit-First Design | N/A | No audit changes. |
| IV. AI-Augmented Recommendations | N/A | No Gemini changes. Existing `/insights` command unaffected. |
| V. Observability & Structured Logging | PASS | New query functions use existing database patterns. No silent failures — all metrics fall back to 0/N/A gracefully. |

**Post-Design Re-Check**: PASS. No constitution violations. The decision to leave stats commands ungated (except `/serverpulse`) is consistent with existing behavior — `/stats`, `/userstats`, etc. are already open to all members.

## Project Structure

### Documentation (this feature)

```text
specs/003-expanded-stats/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Derived metrics catalog and decisions
├── data-model.md        # Schema changes, rollup updates, query functions
├── quickstart.md        # Implementation guide (6 phases)
├── contracts/
│   └── commands.md      # UI contracts for all enhanced/new commands
└── tasks.md             # Phase 2 output (created by /speckit-tasks)
```

### Source Code (repository root)

```text
database.py              # Add total_words migration, update rollups, add ~20 query functions
cogs/stats.py            # Add 6 utility functions, enhance 5 commands, add 3 new commands
utils/help.py            # Update Stats cog description for new commands
README.md                # Add new commands to command table
```

**Structure Decision**: Single-project flat layout. All stats logic stays in the existing `Stats` cog per Principle I. Database queries go in `database.py` per existing convention.

## Complexity Tracking

No constitution violations to justify.

## Implementation Phases

### Phase 1: Database Migration & Rollup Updates (`database.py`)

**Goal**: Add `total_words` column to rollup tables and update rollup functions.

1. Add migration in `init_db()`:
   - `ALTER TABLE user_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0`
   - `ALTER TABLE channel_activity_daily ADD COLUMN total_words INTEGER DEFAULT 0`
   - Wrapped in try/except for idempotent re-runs

2. Update `rollup_user_activity()` to SUM `word_count` into `total_words`

3. Update `rollup_channel_activity()` to SUM `word_count` into `total_words`

4. Add all new query functions (see data-model.md for signatures):
   - **Server**: `get_dau_wau_mau`, `get_server_word_stats`, `get_weekday_weekend_split`, `get_channel_growth_trends`, `get_activity_diversity`, `get_message_velocity`, `get_server_engagement_score`
   - **User**: `get_user_word_stats`, `get_user_active_hours`, `get_user_streaks`, `get_user_consistency`, `get_user_weekday_split`, `get_user_rank`, `get_user_dormancy`, `get_user_engagement_ratios`
   - **Channel**: `get_channel_word_stats`, `get_channel_hourly_heatmap`, `get_channel_user_concentration`, `get_channel_weekday_split`, `get_channel_density`, `get_channel_growth`
   - **Voice**: `get_voice_session_distribution`, `get_voice_day_of_week`, `get_voice_peak_hours`
   - **Growth**: `get_churn_metrics`, `get_join_day_distribution`
   - **Leaderboard**: `get_leaderboard`, `get_user_comparison`

### Phase 2: Utility Functions (`cogs/stats.py`)

**Goal**: Add shared helper functions used by multiple commands.

1. `_gini_coefficient(values: list[int]) -> float` — inequality measure (0=equal, 1=concentrated)
2. `_compute_streak(daily_rows: list[dict]) -> tuple[int, int]` — (current, longest) streak
3. `_consistency_score(daily_counts: list[int]) -> float` — 0-100 scale
4. `_composite_health_score(metrics: dict) -> int` — 0-100 scale, 5 components (20 each)
5. `_day_name(day_num: int) -> str` — SQLite day number to name
6. `_build_heatmap_bar(hours_data: list, width: int = 24) -> str` — 24-char visual bar

### Phase 3: Enhance Existing Commands (`cogs/stats.py`)

**Goal**: Add one new embed to each of the 5 existing stats commands.

1. `/stats` — **Embed 5: Server Health** — DAU/WAU/MAU, velocity, diversity, growing/declining channels
2. `/userstats` — **Embed 4: Activity Profile** — active hour, streaks, consistency, dormancy, heatmap
3. `/channelstats` — **Embed 4: Channel Profile** — avg words, user concentration, weekday/weekend, heatmap
4. `/voicestats` — **Embed 5: Session Analysis** — session distribution histogram, day-of-week breakdown
5. `/growth` — **Embed 5: Member Lifecycle** — churn rate, ban rate, avg tenure, join day distribution
6. `/peakhours` — **Embed 2: Channel Breakdown** — per-channel peak hours, weekday vs weekend hourly comparison, trend vs prior

### Phase 4: New Commands (`cogs/stats.py`)

**Goal**: Add 3 new hybrid commands.

1. `/serverpulse` — Single embed quick health check (admin-only, `@has_admin_role()`)
2. `/leaderboard [days] [category]` — Multi-category with 10-user bar chart (open to all)
3. `/activity @User1 @User2 [days]` — Side-by-side comparison + overlay chart (open to all)

### Phase 5: Update Help System (`utils/help.py`)

1. Update `_COG_DESCRIPTIONS["Stats"]` to mention new commands
2. Add descriptions for `/serverpulse`, `/leaderboard`, `/activity`

### Phase 6: Update README

1. Add `/serverpulse`, `/leaderboard`, `/activity` to command table
2. Update Stats feature description

## Key Design Decisions

- **No new tables**: All metrics derived from existing raw + rollup tables
- **`total_words` on rollup tables**: Survives raw event pruning, avoids scanning `message_events` for historical data
- **Streaks in Python**: SQLite compatibility — iterate `user_activity_daily` rows (bounded by retention)
- **Gini in Python**: Compute from channel message counts (at most ~200 channels)
- **Hybrid commands**: `@commands.hybrid_command()` for all new commands (consistent with existing pattern)
- **Graceful fallbacks**: Every metric has a 0/N/A fallback for missing data
- **No admin gating on read-only stats**: Only `/serverpulse` requires admin (operational metrics)

## Dependencies Between Phases

```
Phase 1 (database) → Phase 2 (utilities) → Phase 3 (enhance) → Phase 4 (new commands)
                                                                        ↓
                                                              Phase 5 (help) + Phase 6 (README)
```

Phases 5 and 6 are independent of each other but depend on Phase 4 being complete.

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Rollup column migration fails on existing DB | Low | Wrapped in try/except, idempotent |
| Performance on large message_events table | Low | Most queries use rolled-up daily tables; raw event queries are bounded by days parameter |
| Embed character limits exceeded | Medium | Test with realistic data; truncate long lists |
| QuickChart URL too long for comparison chart | Low | Limit to 30 days of data points for overlay charts |

## Files Modified

| File | Changes | Lines Est. |
|------|---------|-----------|
| `database.py` | Migration, rollup updates, ~20 new query functions | +400-500 |
| `cogs/stats.py` | 6 utility functions, 5 enhanced commands, 3 new commands | +600-800 |
| `utils/help.py` | Update cog description | +5-10 |
| `README.md` | Add new commands | +10-15 |

**Estimated total effort**: ~8-12 hours
