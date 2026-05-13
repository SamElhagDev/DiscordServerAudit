<!--
SYNC IMPACT REPORT
==================
Version change: [TEMPLATE] → 1.0.0 (initial constitution — MAJOR: first adoption, all placeholders resolved)

Modified principles:
  [PRINCIPLE_1_NAME] → I. Cog-Modular Architecture
  [PRINCIPLE_2_NAME] → II. Admin Role Gating (NON-NEGOTIABLE)
  [PRINCIPLE_3_NAME] → III. Audit-First Design
  [PRINCIPLE_4_NAME] → IV. AI-Augmented Recommendations (Gemini)
  [PRINCIPLE_5_NAME] → V. Observability & Structured Logging

Added sections:
  - Technology Stack & Constraints
  - Development Workflow

Removed sections: none

Templates requiring updates:
  ✅ .specify/memory/constitution.md — this file (written now)
  ✅ .specify/templates/plan-template.md — Constitution Check section references updated principles
  ✅ .specify/templates/spec-template.md — no structural changes required
  ✅ .specify/templates/tasks-template.md — no structural changes required

Follow-up TODOs:
  TODO(RATIFICATION_DATE): set to first commit date 2026-05-12
-->

# Discord Server Audit Bot Constitution

## Core Principles

### I. Cog-Modular Architecture

Every capability MUST be implemented as a self-contained discord.py `Cog` (module). Cogs MUST be:
independently loadable/unloadable without restarting the bot; independently testable in isolation;
registered in `bot.py:COGS` and loaded via `setup_hook`. Cross-cog dependencies MUST be accessed
through `bot.get_cog()`, never via direct import. New features MUST each live in their own cog file
under `cogs/`.

**Rationale**: The cog pattern is the bot's primary extension mechanism. Violating it couples
unrelated concerns together, making the bot fragile under partial failures and harder to extend.

### II. Admin Role Gating (NON-NEGOTIABLE)

Every command MUST be decorated with `@has_admin_role()` from `utils/permissions.py`. No command
MAY be accessible without this check, including manual triggers, diagnostic commands, and any future
slash commands. The admin role name MUST be sourced exclusively from `config.yaml:admin_role`.
Hard-coding role names or IDs in command code is forbidden.

**Rationale**: A Discord admin bot with unguarded commands is a server security liability. The
single chokepoint in `utils/permissions.py` ensures access control can be audited and changed in
one place.

### III. Audit-First Design

Both SecurityAudit and ServerAudit cogs MUST follow a two-phase model:
1. `run_audit(guild, triggered_by)` — pure logic returning `list[dict]` with `severity`,
   `category`, and `description` keys; persists findings to SQLite via `database.py`.
2. `post_audit_results(guild, findings, triggered_by)` — presentation layer posting embeds to the
   configured audit channel.

Scheduled and manual audit runs MUST use the same `run_audit` logic path. Severity levels are
`critical`, `warning`, and `info` only. All findings MUST be persisted before posting. New audit
checks MUST be added to the appropriate existing cog, not a new top-level module, unless the audit
domain is genuinely distinct.

**Rationale**: Separating logic from presentation enables testing without Discord API calls and
ensures the scheduler and manual trigger produce identical, auditable results.

### IV. AI-Augmented Recommendations (Gemini)

Gemini (Google Generative AI SDK) MUST be used to generate natural-language recommendations from
raw audit findings. Gemini calls MUST be:
- Optional/gracefully degraded (bot MUST function fully if the API key is absent or the call fails);
- Triggered after `run_audit` completes, receiving the structured `findings` list;
- Capped at a reasonable token limit to avoid runaway API costs;
- Configured via `config.yaml` (API key, model name, enabled flag).

Gemini MUST NOT be used to make security decisions or perform destructive actions. Its role is
advisory output only — surfacing recommendations to admins in the audit channel.

**Rationale**: Gemini adds value by translating raw permission flags into actionable, human-readable
advice. Keeping it optional and advisory prevents over-reliance on AI judgement for consequential
server changes.

### V. Observability & Structured Logging

All bot startup events, cog loads, scheduled task registrations, and audit runs MUST be logged via
Python's `logging` module at the appropriate level (`INFO` for lifecycle events, `WARNING` for
recoverable issues, `ERROR` for failures). Log messages MUST identify the guild ID and trigger
source. Silent failures (bare `except: pass`) are forbidden; every caught exception MUST be logged.
The database MUST be the system of record for all audit findings and bulk task history.

**Rationale**: The bot runs as a background Windows Scheduled Task with no interactive console.
Log-and-database observability is the only way to diagnose issues post-deployment.

## Technology Stack & Constraints

- **Language**: Python 3.11+
- **Discord library**: discord.py (commands extension + Intents.members + Intents.message_content)
- **AI**: Google Generative AI SDK (`google-generativeai`), model `gemini-pro` (or configured)
- **Database**: SQLite via Python stdlib `sqlite3`, schema managed in `database.py`
- **Config**: `config.yaml` loaded via `config.py`; secrets injected as environment variables in CI
- **Deployment**: Windows Scheduled Task on self-hosted runner, deployed via GitHub Actions
- **Linting**: flake8 (enforced in CI)
- **Rate limiting**: All bulk operations MUST include `asyncio.sleep` delays between Discord API
  calls to avoid hitting rate limits (minimum 0.3 s between member mutations, 0.5 s between channel
  mutations)
- **Destructive commands** (bulk delete, prune, channel wipe) MUST require a reaction confirmation
  from the invoking admin before executing

## Development Workflow

- All new features MUST start as a `/speckit-specify` spec before implementation
- Cogs MUST be added to `bot.py:COGS` as part of the same PR that introduces them
- Config keys added by a feature MUST be documented in `config.yaml` with sensible defaults
- All bulk/destructive operations MUST be logged to `database.bulk_task_log` via
  `database.log_bulk_task()`
- CI MUST pass (flake8 lint + deploy workflow) before merging to `master`
- The audit channel (`audit_channel_id`) and log channel (`log_channel_id`) are distinct;
  audit findings go to audit channel, bot lifecycle events to log channel

## Governance

This constitution supersedes all other informal conventions in the codebase. Amendments require:
1. A written rationale explaining why the change is necessary
2. Update to this file with version bump per semantic versioning policy
3. Propagation check across `.specify/templates/` and dependent docs

**Versioning policy**:
- MAJOR: removal or redefinition of an existing principle
- MINOR: new principle, new section, or materially expanded guidance
- PATCH: wording clarifications, typo fixes, non-semantic refinements

All PRs that add commands, cogs, or audit checks MUST include a Constitution Check confirming
compliance with Principles I–V. Complexity beyond what the principles permit MUST be justified
in the PR description with a Complexity Tracking entry.

**Version**: 1.0.0 | **Ratified**: 2026-05-12 | **Last Amended**: 2026-05-12
