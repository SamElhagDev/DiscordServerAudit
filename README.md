# Discord Admin Bot

A Discord bot for bulk automation, messaging and moderation, security audits, server health recommendations, activity analytics, natural language server management, and AI-powered fact-checking.

## Features

- **Bulk Tasks** — Message purging, member pruning, bulk role assign/remove, bulk channel create/delete
- **Messaging & Moderation** — Send messages, embeds, announcements, DMs and bulk DMs; react, pin, and edit; lock channels and set slowmode; kick, ban, softban, and timeout members; manage roles and nicknames; create invites; create custom emojis from avatars; and full voice moderation (mute/deafen/disconnect, individually or in bulk)
- **Info & Inspection** — Inspect and list channels, roles, bans, invites, emojis, and admins; view avatars and member counts
- **Security Audits** — Checks permissions, @everyone overrides, dangerous bot permissions, channel overwrites, 2FA enforcement
- **Server Audits** — Dead channel detection, missing onboarding channels, role hierarchy issues, branding gaps, channel organization
- **Stats & Analytics** — Server activity dashboards, per-user and per-channel stats, voice tracking, member growth trends, peak hour analysis, server health pulse, multi-category leaderboards, user-vs-user comparisons, history backfill scanning
- **Natural Language** — Describe admin tasks in plain English (via `!ask`), review an AI-generated execution plan, then confirm to run it. Supports 70+ actions spanning messaging, moderation, channel/role management, voice control, info queries, and emoji creation
- **Fact-Check** — React to any message with a configurable emoji to get an AI-powered fact-check with per-claim breakdowns, verdicts, and detailed analysis
- **AI-Powered** — Gemini integration provides AI action plans for audit findings, server trend insights, and natural language command planning
- **Scheduled** — Security audits, server audits, and stats snapshots run automatically on configurable intervals
- **Role-gated** — All commands restricted to a single configured admin role, no exceptions

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your bot
- Go to https://discord.com/developers/applications
- Create a New Application → Bot
- Enable **Server Members Intent**, **Message Content Intent**, and **Voice State Intent** under Bot > Privileged Gateway Intents
- *(Optional)* Enable **Presence Intent** as well if you want online-member metrics (`/growth` online ratio, `/serverpulse` online count). It also requires `stats.track_presence: true` in config; while off, those metrics show **N/A** instead of a misleading 0.
- Copy your bot token

### 3. Configure
Edit `config.yaml`:
```yaml
bot:
  prefix: "!"
  token: "YOUR_BOT_TOKEN_HERE"

admin_role: "Bot Admin"        # Exact role name — everyone needs this including server owner

log_channel_id: 123456789      # Paste your channel IDs here
audit_channel_id: 123456789

gemini_key: "YOUR_GEMINI_KEY"  # Required for AI features (insights, fact-check, natural language)

intervals:
  security_audit: 24           # Hours between security audits
  server_audit: 168            # Hours between server audits (168 = weekly)

stats:
  enabled: true
  snapshot_interval_hours: 1
  retention_days: 90             # Raw events kept this long; daily rollups keep aggregates longer
  exclude_bots: true
  track_presence: false          # true + Presence Intent enables online-member metrics
  # excluded_channels: []
  # excluded_users: []

factcheck:
  enabled: true
  emoji: "\U0001F50D"          # Magnifying glass — react with this to trigger
  model: "gemini-2.5-flash"
  rate_limit: 5                # Max checks per user per hour
  cooldown_seconds: 300        # Cooldown per message before it can be re-checked
  timeout_seconds: 30
```

All config values can be overridden via environment variables prefixed with `DiscordServerAudit_` (e.g. `DiscordServerAudit_GEMINI_KEY`).

### 4. Invite the bot
Generate an invite URL in the Discord Developer Portal with these permissions:
- Manage Roles
- Manage Channels
- Kick Members
- Ban Members
- Manage Messages
- Read Message History
- Send Messages
- View Channels
- Moderate Members (for timeouts)
- Move Members (for voice moves and disconnects)
- Mute Members / Deafen Members (for voice moderation)
- Create Instant Invite (for `!createinvite` and `!ask` invite creation)
- Manage Expressions (for creating custom emojis from avatars)

### 5. Create the admin role
In your Discord server, create a role named exactly as set in `admin_role` in config.yaml and assign it to yourself and any other admins.

### 6. Run
```bash
python bot.py
```

---

## Commands

### Admin Utilities

| Command | Description |
|---|---|
| `!config` | Show the current effective runtime configuration |
| `!shutdown` | Gracefully stop the bot (finalises open voice sessions and closes the database first) |
| `!help` | Rich embed-based help with per-category and per-command detail |

### Bulk Operations

| Command | Description |
|---|---|
| `!bulkdelete #channel 50` | Delete last N messages in a channel (max 100) |
| `!prunembers 30` | Prune roleless inactive members (up to 30 days) |
| `!bulkroleadd @Role` | Add a role to all members who don't have it |
| `!bulkroleremove @Role` | Remove a role from all members who have it |
| `!bulkcreatechannels "Category" name1 name2` | Create multiple channels in a category |
| `!bulkdeletechannels "Category"` | Delete all channels in a category |
| `!tasklogs` | Show recent bulk task history |

### Messaging & Actions

**Send & manage messages**

| Command | Description |
|---|---|
| `!say #channel <message>` | Send a plain message as the bot |
| `!sayembed #channel "Title" <description>` | Send a custom embed |
| `!announce #channel true "Title" <body>` | Formatted announcement, optionally pinging @everyone |
| `!timed #channel 30 <message>` | Send a message that auto-deletes after N seconds |
| `!dm @User <message>` | Send a direct message to a member |
| `!bulkdm @Role <message>` | DM every member with a role (confirms first) |
| `!react #channel <messageID> 👍` | Add a reaction to a message |
| `!clearreactions #channel <messageID>` | Remove all reactions from a message |
| `!pin #channel <messageID>` / `!unpin ...` | Pin / unpin a message |
| `!editmsg #channel <messageID> <text>` | Edit a message the bot sent |

**Moderation**

| Command | Description |
|---|---|
| `!kick @User [reason]` | Kick a member (they can rejoin) |
| `!ban @User [reason]` | Ban a member (confirms first) |
| `!unban <userID> [reason]` | Lift a ban |
| `!softban @User [reason]` | Ban + unban to purge a member's recent messages |
| `!timeout @User 30 [reason]` | Timeout (mute) a member for N minutes |
| `!untimeout @User` | Remove a member's timeout |
| `!nick @User <nickname>` / `!clearnick @User` | Set / reset a member's nickname |

**Channels**

| Command | Description |
|---|---|
| `!lock #channel [reason]` / `!unlock #channel` | Lock / unlock a channel for @everyone |
| `!slowmode #channel 10` | Set slowmode in seconds (0 disables) |
| `!rename #channel <new-name>` | Rename a channel |
| `!topic #channel <text>` | Set a channel's topic |
| `!nsfw #channel` | Toggle a channel's NSFW flag |
| `!movechannel #channel "Category"` | Move a channel to a category |

**Roles**

| Command | Description |
|---|---|
| `!addrole @User @Role` / `!removerole @User @Role` | Add / remove a role on a member |
| `!createrole "Name" [#hex]` | Create a role with an optional colour |
| `!deleterole @Role` | Delete a role |

**Voice**

| Command | Description |
|---|---|
| `!movemember @User "VC"` | Move a member to a voice channel |
| `!vcmute @User` / `!vcunmute @User` | Server-mute / unmute a member |
| `!vcdeafen @User` / `!vcundeafen @User` | Server-deafen / undeafen a member |
| `!vcdisconnect @User` | Disconnect a member from voice |
| `!vcmuteall "VC"` / `!vcunmuteall "VC"` | Mute / unmute everyone in a voice channel |
| `!vckickall "VC"` | Disconnect everyone from a voice channel (confirms) |

**Invites**

| Command | Description |
|---|---|
| `!createinvite #channel [hours] [uses]` | Create an invite link (0 hours = never, 0 uses = unlimited) |

### Info & Inspection

| Command | Description |
|---|---|
| `!serverinfo` | Server summary: members, channels, roles, boosts |
| `!userinfo @User` | Member details: roles, join date, timeout status |
| `!roleinfo @Role` | Role colour, member count, permissions |
| `!channelinfo #channel` | Channel category, topic, slowmode, NSFW, age |
| `!listchannels` | All channels grouped by category |
| `!listroles` | All roles with member counts |
| `!listbans` | Currently banned members |
| `!listinvites` | Active invite links with creators and use counts |
| `!listemojis` | Custom server emojis |
| `!listadmins` | Members with Administrator permission |
| `!avatar @User` | Show a member's avatar at full size |
| `!membercount` | Total / human / bot / in-voice counts |

### Security Audit

| Command | Description |
|---|---|
| `!securityaudit` | Manually run a security audit |
| `!lastaudit` | Show last security audit findings |

### Server Audit

| Command | Description |
|---|---|
| `!serveraudit` | Manually run a server audit |
| `!lastserveraudit` | Show last server audit findings |

### Stats & Analytics

| Command | Description |
|---|---|
| `/stats [days]` | Server activity dashboard with health metrics, DAU/MAU, channel diversity |
| `/userstats @User [days]` | Activity profile with active hours, dormancy, weekday/weekend split, hourly heatmap |
| `/channelstats #channel [days]` | Channel report with user concentration, word stats, growth trends |
| `/voicestats [days]` | Voice dashboard with session distribution, day-of-week analysis |
| `/growth [days]` | Member growth with churn rate, ban rate, member lifecycle |
| `/peakhours [days]` | Hourly distribution with per-channel breakdown, weekday vs weekend |
| `/serverpulse` | Quick server health check with composite score (admin only) |
| `/leaderboard [days] [category]` | Top 10 users by messages, voice, engagement, or social |
| `/activity @User1 @User2 [days]` | Head-to-head user comparison with overlay chart |
| `/insights [days]` | AI-powered server trend analysis (requires Gemini) |
| `/scan [days]` | Backfill the stats database with server message history |
| `/dbcheck` | Diagnose stats tables for duplicates and consistency |
| `/dbclean` | Remove duplicate rows from all stats tables |

Stats commands are hybrid (work as both `!command` and `/command`).

### Natural Language

| Command | Description |
|---|---|
| `!ask <query>` | Describe an admin task in plain English; review the AI plan, then confirm to execute |

The `!ask` command supports 70+ actions including: messaging (send messages, embeds, announcements, DMs, bulk DMs, reactions, pins), bulk message delete, prune members, role management (add/remove/create/delete/rename/list), channel management (create/delete/rename/move/lock/unlock/topic/NSFW/slowmode), member moderation (kick/ban/unban/softban/timeout), voice control (mute/deafen/disconnect individually or in bulk), invite management (create/list), creating custom emojis from a member's avatar, server queries (server/role/channel info, member info, find inactive channels, find roleless/new members, list bans/channels/emojis/admins), and running audits.

### Fact-Check

| Command | Description |
|---|---|
| `/factcheck` | Show fact-check configuration, context/grounding status, and session stats |
| `/factcheckrefresh` | Backfill the context store from existing channel history (admin) |
| React with the configured emoji | Triggers an AI fact-check on the reacted message |

Fact-checking is triggered by reacting to any message with the configured emoji (default: magnifying glass). The bot replies with a detailed breakdown of each claim, per-claim verdicts, an overall verdict, and an analysis paragraph. Includes per-message cooldown and per-user rate limiting.

**Web grounding (`factcheck.grounding`).** When enabled, fact-checks use Gemini's Google Search grounding so claims are verified against live web results instead of the model's training data — this fixes cases where a real, recent article was wrongly dismissed as nonexistent. Grounded verdicts list their sources. A guardrail (`require_source_for_negative`) downgrades a "Mostly False" verdict to "Unverifiable" when no source backs it, so the bot never asserts an uncorroborated denial. Note: Google Search grounding is billed per grounded request by the Gemini API — check current pricing before enabling on a busy server.

**Conversational context (`factcheck.context`).** The bot stores recent message text locally (SQLite; storing costs no tokens) so verdicts can resolve references like "that article" or "he said". Two tiers feed each check: a *recency* window (recent server-wide messages) and a *relevance* tier that uses a SQLite FTS5 index to pull the most relevant messages from **all retained history** — so a request can reference something posted long ago at a bounded per-request cost. Context is used only to understand the message; the verdict is always about the message you reacted to.

- `storage_retention_days` is the sole delete horizon and defaults to **`0` = keep forever**. This retains all message text indefinitely (maximizes recall); set a finite number of days for a tighter privacy posture, or `0` to keep everything. It is independent of `recency_window_hours` (a query window that never deletes).
- Set `excluded_channels` / `excluded_users` to keep private channels or specific users out of the context store entirely (applied to both live capture and backfill).
- Run `/factcheckrefresh` after enabling context to seed the store from existing history — it scans every messageable channel (text, voice/stage text chat, and active threads), idempotent, admin-only, rate-limited, and preserves each message's original timestamp.
- The relevance tier requires SQLite FTS5; if unavailable it disables gracefully and the recency tier still works.

All commands require the admin role configured in `config.yaml`.

---

## GitHub Actions Deployment

The workflow deploys to your self-hosted Windows runner as a Windows Scheduled Task (same pattern as DiscordVoiceDatabase).

### Required GitHub Secrets
Set these under **Settings → Secrets and variables → Actions**:

| Secret | Required | Description |
|---|---|---|
| `DiscordServerAudit_TOKEN` | Yes | Your Discord bot token |
| `DiscordServerAudit_ADMIN_ROLE` | Yes | The admin role name (e.g. `Bot Admin`) |
| `DiscordServerAudit_LOG_CHANNEL_ID` | Yes | Discord channel ID for general bot logs |
| `DiscordServerAudit_AUDIT_CHANNEL_ID` | Yes | Discord channel ID for audit results |
| `DiscordServerAudit_GEMINI_KEY` | No | Google Gemini API key (enables AI features: insights, fact-check, natural language, audit action plans) |
| `DiscordServerAudit_FACTCHECK_EMOJI` | No | Custom emoji for fact-check trigger (default: magnifying glass) |
| `DiscordServerAudit_PREFIX` | No | Bot command prefix (default: `!`) |
| `DiscordServerAudit_LOG_FILE` | No | Log file name (default: `DiscordServerAudit.log`) |
| `DiscordServerAudit_VERSION` | No | Version string shown at startup and in help footer (default: `1.0.0`) |

### What the workflow does
1. Lints with flake8
2. Robocopy's source to `C:\apps\DiscordServerAudit\src`
3. Creates/updates a venv at `C:\apps\DiscordServerAudit\.venv`
4. Sets all secrets as machine-level env vars via `setx`
5. Stops any running instance
6. Registers/restarts a Scheduled Task (`DiscordServerAudit`) that runs at startup under SYSTEM with auto-restart on failure
