# Implementation Plan: Comprehensive Server Stats Logging

**Branch**: `001-stats-logging` | **Date**: 2026-05-18 | **Spec**: `specs/001-stats-logging/spec.md`
**Input**: User description — comprehensive stats logging for Discord server: user activity, messages, voice channel use, member count over time, with Gemini integration for trend analysis.

## Summary

Add a new `Stats` cog that passively collects server activity data (messages, voice sessions, member joins/leaves) via discord.py event listeners, stores it in new SQLite tables in the existing `database.py`, and exposes hybrid commands (prefix + slash) accessible to all users to query stats, view growth trends, and optionally get AI-powered insights via the existing Gemini integration. A periodic scheduler task captures hourly member count snapshots for time-series growth tracking.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py 2.3.2 (commands extension, Intents.members, Intents.message_content, Intents.voice_states)
**Storage**: SQLite via Python stdlib `sqlite3`, schema managed in `database.py`
**Testing**: Manual testing via Discord (no automated test framework in project)
**Target Platform**: Windows Scheduled Task on self-hosted runner
**Project Type**: Discord bot (discord.py cog-based architecture)
**Performance Goals**: Event handlers must be non-blocking; database writes batched where possible to avoid I/O overhead on high-traffic servers
**Constraints**: Rate-limit compliance (0.3s between member queries); SQLite single-writer constraint; Gemini calls optional and capped
**Scale/Scope**: Single-server deployment; expected ~100-500 members, ~1000 messages/day

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | New `cogs/stats.py` cog, self-contained, registered in `bot.py:COGS`, loaded via `setup_hook` |
| II. Admin Role Gating | JUSTIFIED DEVIATION | Stats commands are open to all users (no `@has_admin_role()`). Justified: these are read-only queries with no destructive or sensitive actions. User explicitly requested public access. |
| III. Audit-First Design | N/A | Stats logging is not an audit feature; however, we follow the same separation of logic (collection) from presentation (embeds) |
| IV. AI-Augmented Recommendations (Gemini) | PASS | Gemini used optionally for trend analysis; graceful degradation if API key absent; advisory output only |
| V. Observability & Structured Logging | PASS | All event handlers, scheduler tasks, and database operations logged via `logging` module; database is system of record |

**Additional constitution requirements checked:**
- Config keys documented in `config.yaml` with sensible defaults: PASS
- Cog added to `bot.py:COGS`: PASS (implementation task)
- No destructive commands in this feature: PASS (read-only stats)
- Rate limiting: PASS (no bulk Discord API calls needed; event-driven collection)
- Hybrid commands: All commands use `@commands.hybrid_command()` for both prefix and slash support
- App command sync: `bot.tree.sync()` called in `setup_hook` or `on_ready` to register slash commands

**Gate result: PASS — one justified deviation (Principle II) documented in Complexity Tracking.**

## Project Structure

### Documentation (this feature)

```text
specs/001-stats-logging/
├── plan.md              # This file
├── research.md          # Phase 0: research findings
├── data-model.md        # Phase 1: database schema design
├── quickstart.md        # Phase 1: implementation quickstart
├── contracts/           # Phase 1: command interface contracts
│   └── commands.md      # Stats command definitions
└── tasks.md             # Phase 2: implementation tasks (via /speckit-tasks)
```

### Source Code (repository root)

```text
# Existing structure — new/modified files marked with [NEW] or [MOD]
bot.py                   # [MOD] Add "cogs.stats" to COGS list
config.py                # (unchanged)
config.yaml              # [MOD] Add stats section with config keys
database.py              # [MOD] Add 6 new tables and helper functions
requirements.txt         # (unchanged — no new dependencies)

cogs/
├── admin.py             # (unchanged)
├── bulk_tasks.py        # (unchanged)
├── natural_language.py  # (unchanged)
├── security_audit.py    # (unchanged)
├── server_audit.py      # (unchanged)
└── stats.py             # [NEW] Stats collection cog — event handlers + commands

utils/
├── gemini.py            # [MOD] Add analyze_trends() function
├── help.py              # (unchanged — auto-discovers new cog)
├── permissions.py       # (unchanged)
├── planner.py           # (unchanged)
└── scheduler.py         # (unchanged — reused for snapshot scheduling)
```

**Structure Decision**: Single new cog file + extensions to existing `database.py` and `utils/gemini.py`. No new directories needed beyond the spec artifacts. This follows the established cog-modular pattern exactly.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Principle II: No admin role gating on stats commands | Stats are read-only queries — any server member should see server activity. User explicitly requested public access. | Gating behind admin role would prevent regular members from checking their own stats or server activity, defeating the purpose of community-facing stats. |
