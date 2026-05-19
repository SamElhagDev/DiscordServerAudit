# Research: Emoji-Triggered Fact-Check

## R1: Which Gemini model for fact-checking?

**Decision**: `gemini-2.5-flash` (default, configurable via `config.yaml`)

**Rationale**: Fact-checking requires stronger reasoning than the bot's current
summarisation tasks (which use `gemini-3.1-flash-lite`). The model needs to
distinguish verifiable claims from opinions, assess plausibility, and cite
reasoning — cheap speed-optimised models produce more hallucinations on tasks
that demand accuracy. `gemini-2.5-flash` offers the best accuracy-to-cost ratio
for this workload:

| Model | Input $/1M | Output $/1M | Reasoning | Speed | Verdict |
|-------|-----------|------------|-----------|-------|---------|
| gemini-3.1-flash-lite | $0.25 | $1.50 | Adequate for summaries | Fastest | Too shallow for claim verification |
| gemini-2.5-flash | $0.30 | $2.50 | Strong (Flash-class) | Fast | **Best fit** — good reasoning, low cost |
| gemini-2.5-pro | $1.25–2.50 | $10–15 | Excellent | Moderate | Overkill for single-message checks |
| gemini-3.1-pro | $2.00 | $12.00 | Best available | Slowest | 40x cost for marginal accuracy gain |

At ~300 tokens per fact-check request and ~500 tokens per response (rich
detail mode), a single check costs ~$0.0014 with `gemini-2.5-flash`. Even 100 fact-checks/day is
under $0.08/day ($2.40/month).

**Alternatives considered**:
- `gemini-3.1-flash-lite`: Already used by the bot. Rejected because Flash-Lite
  is optimised for throughput, not accuracy — it tends to be more confident in
  wrong answers, which is the worst trait for a fact-checker.
- `gemini-3.1-pro`: Best reasoning but 40x the cost. The marginal improvement
  doesn't justify it for checking Discord messages (typically 1-3 sentences).

## R2: Is AI fact-checking a good idea?

**Decision**: Yes, with prominent disclaimers and abuse protection.

**Rationale — Pros**:
- Community engagement: turns passive reading into interactive verification
- Educational: exposes claims to scrutiny, encourages critical thinking
- Low-stakes: Discord messages are informal — a wrong fact-check is embarrassing
  but not consequential (unlike, say, medical or legal advice)
- Opt-in: triggered only by explicit emoji reaction, not automatic

**Rationale — Risks and mitigations**:

| Risk | Severity | Mitigation |
|------|----------|------------|
| AI hallucination (confidently wrong) | Medium | Every response includes a disclaimer: "AI-generated — verify important claims independently" |
| Abuse / spam (users react on everything) | Medium | Per-message cooldown (only first reaction triggers); per-user rate limit (configurable, default 5/hour) |
| Toxic use (weaponising fact-checks against people) | Low | Only checks factual claims; prompt instructs model to decline opinion/subjective content |
| Cost runaway | Low | Rate limits cap usage; model is cheap ($0.0008/check) |
| Privacy (sending message content to Google) | Low | Same trust boundary as existing Gemini features (audit summaries, NL planner). Document in bot description. |

**Conclusion**: The feature is viable as long as: (1) results are clearly
labelled as AI-generated, (2) rate limits prevent abuse, and (3) the prompt
guides the model to say "I cannot verify this" rather than guessing.

## R3: Emoji configuration via environment variable

**Decision**: New env var `DiscordServerAudit_FACTCHECK_EMOJI` overrides
`config.yaml` key `factcheck.emoji`. Default: `factcheck` (a custom server
emoji name) with fallback to unicode `\U0001F50D` (magnifying glass).

**Rationale**: Following the existing config pattern in `config.py` where
`_env_overrides` maps env vars to config dot-keys. GitHub Actions secrets
inject env vars — so setting the emoji via a GitHub secret is just adding
`DiscordServerAudit_FACTCHECK_EMOJI` to the repository secrets and referencing
it in the workflow.

**Format**: The config value is the emoji name (for custom emojis) or the
unicode character (for built-in emojis). The cog matches against
`payload.emoji.name` which works for both custom and unicode reactions.

## R4: Response format

**Decision**: Reply to the original message in the same channel (not a thread).

**Rationale**:
- Threads add friction (users have to click into them) and clutter the thread
  list for a single fact-check response
- A direct reply keeps the context visible and allows follow-up discussion
- The embed format matches the bot's existing style (coloured embeds with
  footer disclaimer)
- If the same message is reacted to again, the bot does not re-check (cooldown
  per message_id stored in an in-memory dict with TTL)
