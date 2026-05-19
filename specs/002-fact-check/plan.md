# Implementation Plan: Emoji-Triggered Fact-Check

**Branch**: `main` | **Date**: 2026-05-19 | **Spec**: `specs/002-fact-check/`
**Input**: User description — add fact-check functionality triggered by emoji
reaction on a message, using Gemini, with configurable emoji via env var.

## Summary

Add a new `FactCheck` cog that listens for a configurable emoji reaction on
any text message, sends the message content to Gemini for claim verification,
and replies with a colour-coded verdict embed. The emoji is configurable via
`config.yaml` or a `DiscordServerAudit_FACTCHECK_EMOJI` environment variable
(compatible with GitHub secrets). Uses `gemini-2.5-flash` for best
accuracy-to-cost ratio. Includes per-message cooldown and per-user rate
limiting to prevent abuse.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: discord.py 2.3.2 (`on_raw_reaction_add` listener),
  google-genai SDK (existing `utils/gemini.py` client)
**Storage**: None (stateless — in-memory cooldown cache only)
**Testing**: Manual testing via Discord
**Target Platform**: Windows Scheduled Task on self-hosted runner
**Project Type**: Discord bot cog (`cogs/fact_check.py`)
**Performance Goals**: Respond within 2-5 seconds of emoji reaction
**Constraints**: Gemini API rate limits; Discord reaction event delivery;
  must coexist with stats cog's `on_raw_reaction_add` listener
**Scale/Scope**: Single-server; ~5-20 fact-checks/day expected

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Cog-Modular Architecture | PASS | New `cogs/fact_check.py` cog, self-contained, registered in `bot.py:COGS`, loaded via `setup_hook` |
| II. Admin Role Gating (NON-NEGOTIABLE) | JUSTIFIED DEVIATION | The reaction trigger is open to all users — it must be, since reactions are the interface. The `!factcheck` admin command is gated with `@has_admin_role()`. Justified: the reaction trigger is read-only (posts an informational reply), non-destructive, and rate-limited. |
| III. Audit-First Design | N/A | Not an audit feature |
| IV. AI-Augmented Recommendations (Gemini) | PASS | Gemini used for claim verification; graceful degradation if API key absent; advisory output only with disclaimer; model configurable via `config.yaml` |
| V. Observability & Structured Logging | PASS | All events logged (trigger, API call, result, errors); rate-limit hits logged at INFO |

**Additional constitution requirements checked:**
- Config keys documented in `config.yaml` with sensible defaults: PASS
- Cog added to `bot.py:COGS`: PASS (implementation task)
- No destructive commands: PASS (read-only fact-check response)
- Rate limiting: PASS (per-user hourly limit + per-message cooldown)
- Hybrid commands: `!factcheck` status command uses `@commands.hybrid_command()`

**Gate result: PASS — one justified deviation (Principle II) documented.**

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Reaction trigger ungated (Principle II) | Reactions are the core UX — gating defeats the purpose. Any user should be able to request a fact-check. | Admin-only reactions would make the feature useless for community self-moderation. Rate limits + cooldowns provide abuse protection instead. |

## Project Structure

### Documentation (this feature)

```text
specs/002-fact-check/
+-- plan.md              # This file
+-- research.md          # Model selection, viability analysis
+-- data-model.md        # In-memory state, config keys
+-- quickstart.md        # Setup and testing scenarios
+-- contracts/
|   +-- commands.md      # Reaction trigger + admin command contracts
+-- tasks.md             # Implementation tasks (via /speckit-tasks)
```

### Source Code (changes to repository root)

```text
cogs/
+-- fact_check.py        # NEW — FactCheck cog (listener + admin command)

bot.py                   # MODIFIED — add "cogs.fact_check" to COGS list
config.py                # MODIFIED — add factcheck emoji env override
config.yaml              # MODIFIED — add factcheck section
utils/help.py            # MODIFIED — add FactCheck cog color/label/description
```

## Design Decisions

### D1: Model Selection
Use `gemini-2.5-flash` (not the bot's current `gemini-3.1-flash-lite`).
Fact-checking requires reasoning accuracy over speed. See `research.md#R1`.

### D2: Reaction vs Command
Emoji reaction is the trigger because it's contextual (attached to a specific
message) and low-friction. A `!factcheck` command would require quoting or
replying to the target message, adding UX complexity.

### D3: Reply vs Thread
Reply to the original message (not a thread). Threads add friction and clutter
the thread list for a single response. See `research.md#R4`.

### D4: No Database Persistence
Fact-check results are not stored in SQLite. They're posted as Discord messages
(which Discord persists). Adding a table would create storage growth with no
clear query use-case. The in-memory cooldown cache is sufficient.

### D5: Coexistence with Stats Reaction Listener
Both `cogs/stats.py` and `cogs/fact_check.py` register `on_raw_reaction_add`
listeners. discord.py dispatches events to all listeners — they don't conflict.
The stats listener counts all reactions; the fact-check listener only acts on
the configured emoji. No coordination needed.

## Clarifications

### Session 2026-05-19

- Q: Should certain channels be excluded from fact-checking? → A: No exclusions — fact-check works in all channels.
- Q: What should happen when a message exceeds a reasonable length? → A: No limit — send the full message to Gemini regardless of length.
- Q: Should the bot show a visual indicator while waiting for Gemini? → A: Bot posts a temporary "Checking..." reply, then edits it to the final result embed.
- Q: What timeout should the Gemini API call have? → A: 30 seconds. On timeout, edit the "Checking..." reply to a timeout error embed.

## Gemini Prompt Design

The prompt must:
1. Identify discrete claims in the message
2. Assess each claim's verifiability and likely accuracy
3. Provide context and reasoning for each claim assessment
4. Produce a structured verdict (Mostly True / Mixed / Mostly False /
   Unverifiable / Not a Factual Claim)
5. Include a confidence level and brief sourcing notes
6. Decline to check opinions, jokes, and subjective statements

```text
You are an expert fact-checker providing detailed, educational analysis.
Analyze the following message from a Discord server.

1. Identify each discrete factual claim in the message.
2. For each claim:
   - State the claim clearly
   - Assess whether it is True, False, Partially True, or Unverifiable
   - Provide a brief explanation of WHY (cite the correct fact, the common
     misconception, or why it cannot be verified)
3. Provide an overall verdict: one of "Mostly True", "Mixed",
   "Mostly False", "Unverifiable", or "Not a Factual Claim".
4. Write a detailed analysis paragraph (4-6 sentences) that explains
   the key findings, provides important context or nuance, and notes
   any caveats. Be educational — help the reader understand the topic
   better, not just whether the claim is right or wrong.
5. Rate your overall confidence: "High", "Medium", or "Low".
6. If the message contains only opinions, jokes, or subjective statements,
   use verdict "Not a Factual Claim" and explain why it is not checkable.

Respond in this exact JSON format (no markdown fences):
{
  "verdict": "<one of the five verdict options>",
  "confidence": "<High/Medium/Low>",
  "analysis": "<4-6 sentence detailed analysis with context and nuance>",
  "claims": [
    {
      "claim": "<extracted claim>",
      "assessment": "<True/False/Partially True/Unverifiable>",
      "explanation": "<1-2 sentence explanation with the correct fact or reasoning>"
    }
  ]
}

Message to check:
"{message_content}"
```
