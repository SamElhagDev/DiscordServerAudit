# Quickstart: Fact-Check Feature

## Setup

1. Ensure `gemini_key` is configured (same key used by existing Gemini features)
2. Add fact-check config to `config.yaml`:
   ```yaml
   factcheck:
     enabled: true
     emoji: "\U0001F50D"       # or a custom emoji name like "factcheck"
     model: "gemini-2.5-flash"
     rate_limit: 5             # max checks per user per hour
     cooldown_seconds: 300     # cooldown per message
   ```
3. Optionally set `DiscordServerAudit_FACTCHECK_EMOJI` env var (overrides config)

## Usage

1. Find a message you want fact-checked
2. React to it with the configured emoji (default: magnifying glass)
3. Bot replies to the message with a fact-check embed
4. Read the analysis, but remember it's AI-generated

## Testing Scenarios

### Happy path
- Post a message with a verifiable fact (e.g., "The Eiffel Tower is 300m tall")
- React with the configured emoji
- Expect: Bot replies with verdict embed within 2-5 seconds

### Opinion rejection
- Post an opinion (e.g., "Pizza is the best food")
- React with emoji
- Expect: Bot replies with "Not a Factual Claim" verdict

### Cooldown
- React to the same message twice
- Expect: Only one fact-check response; second reaction is silently ignored

### Rate limit
- React to 6 different messages within an hour
- Expect: 5 succeed; 6th gets a temporary "rate limit" reply

### No Gemini key
- Unset `gemini_key` and restart bot
- React to a message
- Expect: No response, DEBUG log entry

### Bot reaction
- Have another bot react with the emoji
- Expect: Ignored (bot users filtered out)
