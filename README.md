# Discord Admin Bot

A Discord bot for bulk automation, security audits, server health recommendations, activity analytics, natural language server management, and AI-powered fact-checking.

## Features

- **Bulk Tasks** — Message purging, member pruning, bulk role assign/remove, bulk channel create/delete
- **Security Audits** — Checks permissions, @everyone overrides, dangerous bot permissions, channel overwrites, 2FA enforcement
- **Server Audits** — Dead channel detection, missing onboarding channels, role hierarchy issues, branding gaps, channel organization
- **Stats & Analytics** — Server activity dashboards, per-user and per-channel stats, voice tracking, member growth trends, peak hour analysis, history backfill scanning
- **Natural Language** — Describe admin tasks in plain English (via `!ask`), review an AI-generated execution plan, then confirm to run it. Supports 30+ actions including moderation, channel/role management, and server queries
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
  retention_days: 30
  exclude_bots: true
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
| `/stats [days]` | Server activity dashboard (messages, voice, top users/channels, trend chart) |
| `/userstats @User [days]` | Activity profile for a specific user |
| `/channelstats #channel [days]` | Activity report for a specific channel |
| `/voicestats [days]` | Voice activity dashboard (leaderboard, channel usage, trends) |
| `/growth [days]` | Member growth trends (joins vs leaves, retention, growth chart) |
| `/peakhours [days]` | Message activity distribution by hour |
| `/insights [days]` | AI-powered server trend analysis (requires Gemini) |
| `/scan [days]` | Backfill the stats database with server message history |
| `/dbcheck` | Diagnose stats tables for duplicates and consistency |
| `/dbclean` | Remove duplicate rows from all stats tables |

Stats commands are hybrid (work as both `!command` and `/command`).

### Natural Language

| Command | Description |
|---|---|
| `!ask <query>` | Describe an admin task in plain English; review the AI plan, then confirm to execute |

The `!ask` command supports 30+ actions including: bulk message delete, prune members, role management (add/remove/create/delete/rename/list), channel management (create/delete/rename/move/lock/unlock/topic/NSFW/slowmode), member moderation (kick/ban/unban/timeout/move voice/disconnect), server queries (server info, member info, find inactive channels, find roleless members, find new members, list bans), and running audits.

### Fact-Check

| Command | Description |
|---|---|
| `/factcheck` | Show fact-check configuration and session stats |
| React with the configured emoji | Triggers an AI fact-check on the reacted message |

Fact-checking is triggered by reacting to any message with the configured emoji (default: magnifying glass). The bot replies with a detailed breakdown of each claim, per-claim verdicts, an overall verdict, and an analysis paragraph. Includes per-message cooldown and per-user rate limiting.

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

### What the workflow does
1. Lints with flake8
2. Robocopy's source to `C:\apps\DiscordServerAudit\src`
3. Creates/updates a venv at `C:\apps\DiscordServerAudit\.venv`
4. Sets all secrets as machine-level env vars via `setx`
5. Stops any running instance
6. Registers/restarts a Scheduled Task (`DiscordServerAudit`) that runs at startup under SYSTEM with auto-restart on failure
