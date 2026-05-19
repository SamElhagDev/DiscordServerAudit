# Interface Contracts: Fact-Check

## Reaction Trigger (primary interface)

**Trigger**: User adds emoji reaction matching `factcheck.emoji` config to any
text message in a guild channel.

**Preconditions**:
- `factcheck.enabled` is `true`
- Gemini API key is configured (`gemini_key`)
- Reacting user is not a bot
- Message has not been fact-checked within `cooldown_seconds`
- User has not exceeded `rate_limit` checks per hour

**Response**: Bot replies to the original message with a rich embed:

```
+----------------------------------------------------------+
| [Verdict emoji] Fact-Check: [Verdict]   VERDICT_COLOR    |
|----------------------------------------------------------|
|                                                          |
| [4-6 sentence detailed analysis paragraph with context,  |
|  nuance, and educational explanation from Gemini]        |
|                                                          |
| --- Claims Breakdown ---                    (embed field) |
|                                                          |
| [status emoji] Claim 1                                   |
| > "extracted claim text"                                 |
| Explanation of why this is true/false/etc.               |
|                                                          |
| [status emoji] Claim 2                                   |
| > "extracted claim text"                                 |
| Explanation with the correct fact or reasoning.          |
|                                                          |
| --- Details ---                             (embed field) |
| Confidence: [High/Medium/Low]                            |
| Claims checked: [N]                                      |
|----------------------------------------------------------|
| AI-generated - verify important claims independently     |
| Powered by Gemini                                        |
+----------------------------------------------------------+
```

**Verdict values and colours**:
- `Mostly True` — green embed, checkmark emoji
- `Mixed` — yellow embed, warning emoji
- `Mostly False` — red embed, cross emoji
- `Unverifiable` — grey embed, question mark emoji
- `Not a Factual Claim` — grey embed, speech bubble emoji

**Claim assessment emojis**:
- True: green circle
- Partially True: yellow circle
- False: red circle
- Unverifiable: white circle

**Loading state**:
Bot immediately replies to the original message with a "Checking..." embed
(minimal, grey, with a magnifying glass emoji). Once Gemini responds, the bot
edits that message in-place to the full verdict embed. On failure, it edits
to an error embed instead of posting a second message.

**Error cases**:
- Gemini API key missing: silent (no response, logged at DEBUG)
- Gemini call fails: bot edits the "Checking..." reply to a brief error embed, logs ERROR
- Gemini call exceeds 30s timeout: bot edits the "Checking..." reply to a timeout error embed, logs WARNING
- Rate limit exceeded: bot sends ephemeral-style reply to user, deletes after 10s
- Message has no text content (image-only, embed-only): silent skip

## Admin Command: `!factcheck` / `/factcheck`

**Type**: Hybrid command (prefix + slash)
**Access**: `@has_admin_role()` required
**Parameters**: None
**Purpose**: Show fact-check configuration and usage stats for current session

**Response embed**:
```
+--------------------------------------------------+
| Fact-Check Status                     COLOR_BLUE  |
|--------------------------------------------------|
| Enabled: Yes                                      |
| Emoji: [configured emoji]                         |
| Model: gemini-2.5-flash                          |
| Rate limit: 5/hour per user                       |
| Cooldown: 300s per message                        |
| Checks this session: [count]                      |
+--------------------------------------------------+
```
