# Tasks: Emoji-Triggered Fact-Check

**Input**: Design documents from `specs/002-fact-check/`
**Prerequisites**: plan.md, research.md, data-model.md, contracts/commands.md, quickstart.md

**Tests**: Not requested — no test tasks generated.

**Organization**: Tasks grouped by user story derived from plan.md and contracts/commands.md:
- **US1**: Reaction-triggered fact-check (core feature)
- **US2**: Abuse protection (rate limit + cooldown)
- **US3**: Admin status command (`!factcheck`)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths included in all descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration, registration, and help system updates

- [X] T001 [P] Add `factcheck` config section to config.yaml with keys: enabled, emoji, model, rate_limit, cooldown_seconds per data-model.md
- [X] T002 [P] Add `DiscordServerAudit_FACTCHECK_EMOJI` env override to `_env_overrides` dict in config.py
- [X] T003 [P] Add `"cogs.fact_check"` to the `COGS` list in bot.py
- [X] T004 [P] Add FactCheck cog colour, label, and description to `_COG_COLORS`, `_COG_LABELS`, and `_COG_DESCRIPTIONS` dicts in utils/help.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cog skeleton and Gemini integration that all user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Create cogs/fact_check.py with FactCheck cog class skeleton: `__init__`, `cog_load`, logger, config reads for `factcheck.enabled`, `factcheck.emoji`, `factcheck.model`
- [X] T006 Add `_match_emoji` helper method to cogs/fact_check.py that compares a `discord.PartialEmoji` against the configured emoji (handles both unicode chars and custom emoji names)
- [X] T007 Add `_build_prompt` helper method to cogs/fact_check.py that takes message text and returns the fact-check prompt string per the Gemini Prompt Design section in plan.md
- [X] T008 Add `_call_gemini` async method to cogs/fact_check.py that sends the prompt to the configured model via `utils.gemini.get_client()` wrapped in `asyncio.wait_for` with `factcheck.timeout_seconds` (default 30s), parses JSON response, and returns a dict with keys `verdict`, `confidence`, `analysis`, `claims` (returns None on error or timeout)
- [X] T009 Add `_build_embed` helper method to cogs/fact_check.py that takes the parsed Gemini response dict and returns a `discord.Embed` with colour-coded verdict (green/yellow/red/grey per contracts/commands.md), claims list, and AI disclaimer footer
- [X] T010 Add async `setup` function at module level in cogs/fact_check.py: `await bot.add_cog(FactCheck(bot))`

**Checkpoint**: Cog loads without errors, all helpers defined but not yet wired to any listener

---

## Phase 3: User Story 1 - Reaction-Triggered Fact-Check (Priority: P1) MVP

**Goal**: User reacts with configured emoji on a text message, bot replies with a fact-check verdict embed

**Independent Test**: Post a message with a verifiable claim, react with the configured emoji, observe bot reply with verdict embed within 2-5 seconds

### Implementation for User Story 1

- [X] T011 [US1] Add `on_raw_reaction_add` listener to FactCheck cog in cogs/fact_check.py: filter for guild-only, non-bot, enabled check, then call `_match_emoji` to check if reaction matches configured emoji
- [X] T012 [US1] In the `on_raw_reaction_add` handler in cogs/fact_check.py, fetch the target message via `bot.get_channel(payload.channel_id)` then `channel.fetch_message(payload.message_id)`, skip if message has no text content (empty or None)
- [X] T013 [US1] In the `on_raw_reaction_add` handler in cogs/fact_check.py, first reply to the original message with a temporary "Checking..." embed (grey, magnifying glass emoji), then call `_build_prompt` and `_call_gemini`, then edit the reply in-place with the final verdict embed from `_build_embed`. On Gemini failure, edit the reply to a brief error embed instead
- [X] T014 [US1] Add structured logging to the `on_raw_reaction_add` flow in cogs/fact_check.py: log at INFO when a fact-check is triggered (guild, channel, user, message_id), log at INFO when result is posted (verdict, elapsed time), log at ERROR on Gemini failure

**Checkpoint**: Core fact-check flow works end-to-end — react to a message, get a verdict reply

---

## Phase 4: User Story 2 - Abuse Protection (Priority: P2)

**Goal**: Prevent spam by rate-limiting fact-checks per user and adding a cooldown per message

**Independent Test**: React to the same message twice (second is silently ignored). React to 6 different messages in quick succession (6th gets a temporary rate-limit reply that auto-deletes)

### Implementation for User Story 2

- [X] T015 [US2] Add `_checked_messages: dict[int, float]` in-memory cooldown cache to FactCheck `__init__` in cogs/fact_check.py, with `_is_on_cooldown(message_id)` method that checks/evicts entries older than `factcheck.cooldown_seconds`
- [X] T016 [US2] Add `_user_limits: dict[int, list[float]]` rate-limit cache to FactCheck `__init__` in cogs/fact_check.py, with `_is_rate_limited(user_id)` method that checks sliding window against `factcheck.rate_limit` per hour
- [X] T017 [US2] Wire cooldown check into the `on_raw_reaction_add` handler in cogs/fact_check.py: call `_is_on_cooldown(payload.message_id)` before fetching the message, return early if on cooldown
- [X] T018 [US2] Wire rate-limit check into the `on_raw_reaction_add` handler in cogs/fact_check.py: call `_is_rate_limited(payload.user_id)` before calling Gemini, send a temporary reply that auto-deletes after 10 seconds if rate-limited
- [X] T019 [US2] After a successful fact-check in the handler in cogs/fact_check.py, record the message_id in `_checked_messages` and append the timestamp to `_user_limits[user_id]`
- [X] T020 [US2] Add logging for abuse protection in cogs/fact_check.py: log at DEBUG for cooldown hits, log at INFO for rate-limit hits (include user_id and current count)

**Checkpoint**: Cooldown prevents duplicate checks, rate limit caps per-user usage

---

## Phase 5: User Story 3 - Admin Status Command (Priority: P3)

**Goal**: Admin can run `!factcheck` to see configuration and session usage stats

**Independent Test**: Run `!factcheck` as an admin, see embed with enabled status, configured emoji, model name, rate limit, cooldown, and session check count

### Implementation for User Story 3

- [X] T021 [US3] Add `_session_check_count: int` counter to FactCheck `__init__` in cogs/fact_check.py, increment in the handler after each successful fact-check
- [X] T022 [US3] Add `factcheck_cmd` hybrid command to FactCheck cog in cogs/fact_check.py: `@commands.hybrid_command(name="factcheck")`, `@commands.guild_only()`, `@has_admin_role()`, builds an embed showing all config values and session stats per contracts/commands.md
- [X] T023 [US3] Import `has_admin_role` from `utils.permissions` in cogs/fact_check.py (add to existing imports at top of file)

**Checkpoint**: Admin status command works, shows live session stats

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup

- [X] T024 Verify cog loads without errors by checking bot startup logs for "Loaded cog: cogs.fact_check"
- [X] T025 Run quickstart.md validation scenarios: happy path, opinion rejection, cooldown, rate limit, no Gemini key, bot reaction
- [X] T026 Verify coexistence with stats cog `on_raw_reaction_add` listener — confirm both listeners fire independently (stats counts the reaction, fact-check triggers the check)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — all 4 tasks are parallel
- **Foundational (Phase 2)**: Depends on T003 (cog registered in bot.py) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 completion (cog skeleton + all helpers)
- **US2 (Phase 4)**: Depends on T013 (handler exists to wire into)
- **US3 (Phase 5)**: Depends on T019 (session counter incremented)
- **Polish (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no dependencies on other stories
- **US2 (P2)**: Depends on US1 handler being in place (T013) — adds protection around it
- **US3 (P3)**: Depends on US2 session counter (T021 references T019 flow) — but can be implemented after US1 if counter is added early

### Within Each User Story

- T011 → T012 → T013 → T014 (sequential within US1: filter → fetch → process → log)
- T015, T016 parallel (independent caches), then T017, T018 sequential (wire into handler)
- T021 → T022 → T023 (counter → command → import)

### Parallel Opportunities

- **Phase 1**: All 4 tasks (T001–T004) are parallel — different files
- **Phase 2**: T006, T007 parallel (independent helpers); T008, T009 parallel (independent helpers); all depend on T005 (skeleton)
- **Phase 4**: T015, T016 parallel (independent cache implementations)

---

## Parallel Example: Phase 1

```bash
# All setup tasks modify different files — launch together:
Task: "T001 Add factcheck config section to config.yaml"
Task: "T002 Add FACTCHECK_EMOJI env override to config.py"
Task: "T003 Add cogs.fact_check to COGS list in bot.py"
Task: "T004 Add FactCheck cog to help.py dicts"
```

## Parallel Example: Phase 2

```bash
# Independent helper methods — launch together after T005:
Task: "T006 Add _match_emoji helper to cogs/fact_check.py"
Task: "T007 Add _build_prompt helper to cogs/fact_check.py"

# Then these depend on T006/T007:
Task: "T008 Add _call_gemini method to cogs/fact_check.py"
Task: "T009 Add _build_embed method to cogs/fact_check.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (4 parallel tasks)
2. Complete Phase 2: Foundational (cog skeleton + helpers)
3. Complete Phase 3: US1 — Reaction-triggered fact-check
4. **STOP and VALIDATE**: React to a message, verify verdict embed appears
5. Deploy if ready — abuse protection and admin command can follow

### Incremental Delivery

1. Setup + Foundational → Cog loads, helpers ready
2. Add US1 → Fact-check works → Deploy (MVP!)
3. Add US2 → Cooldown + rate limiting active → Deploy
4. Add US3 → Admin visibility → Deploy
5. Each story adds safety without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- No database migrations needed — feature is stateless
- Cog uses existing `utils/gemini.py` client, no new Gemini wrapper needed
- Model defaults to `gemini-2.5-flash` (different from bot's existing `gemini-3.1-flash-lite`)
- Do NOT commit after each phase — leave all changes uncommitted for one final commit at the end
