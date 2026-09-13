# Discord Admin Bot

A Discord bot for bulk automation, messaging and moderation, security audits, server health recommendations, activity analytics, natural language server management, and AI-powered fact-checking that understands the surrounding conversation.

## Features

- **Bulk Tasks** — Message purging, member pruning, bulk role assign/remove, and bulk channel create/delete, with ✅/❌ confirmation prompts on destructive operations and a persistent task log
- **Messaging & Moderation** — Send messages, embeds, announcements, DMs and bulk DMs; react, pin, and edit; lock channels and set slowmode; kick, ban, softban, and timeout members; manage roles and nicknames; create invites; create custom emojis from avatars; and full voice moderation (mute/deafen/disconnect, individually or in bulk)
- **Info & Inspection** — Inspect and list channels, roles, bans, invites, emojis, and admins; view avatars and member counts
- **Security Audits** — 2FA enforcement, too many Administrator roles, dangerous `@everyone` permissions, dangerous roles held by most members, over-privileged bots, risky channel overwrites, and unused roles
- **Server Audits** — Dead channels, channels without topics, missing onboarding channels, role hierarchy problems, verification level, missing system channel, branding gaps, channel bloat, and uncategorised channels
- **Stats & Analytics** — Server activity dashboards, per-user and per-channel stats, voice tracking, member growth trends, peak hour analysis, server health pulse, multi-category leaderboards, user-vs-user comparisons, and history backfill scanning
- **Natural Language** — Describe admin tasks in plain English (via `!ask`), review an AI-generated multi-step plan with destructive steps flagged, then confirm to run it. Supports 70 actions spanning messaging, moderation, channel/role management, raid response, voice control, info queries, and emoji creation
- **Fact-Check** — React to any message with a configurable emoji to get an AI fact-check of its text, images, and link previews, with per-claim verdicts, an overall verdict, detailed analysis, and web sources from Google Search grounding
- **Context-Aware Retrieval** — Fact-checks draw on locally stored server history through three tiers (recent messages, SQLite FTS5 keyword search, and Gemini-embedding semantic search), so references to older messages resolve even when they share no words with the message being checked
- **AI-Powered** — Gemini provides audit action plans, server trend insights, natural language command planning, fact-checks, and embeddings
- **Scheduled** — Security audits, server audits, member snapshots, and daily stats rollups run automatically; last-run times are stored in SQLite, so restarts don't reset schedules
- **Role-gated** — Admin, moderation, audit, and natural language commands require a single configured admin role, with no bypass for the server owner. Stats dashboards and the fact-check reaction are open to all members — see [Access control](#access-control)
- **Resilient** — SQLite in WAL mode with database work kept off the event loop, batched writes, a capped gateway reconnect backoff, and a graceful shutdown that flushes pending writes and closes open voice sessions

---

## Setup

### 1. Install dependencies
Python **3.10** is the supported version (CI and the deployed venv both use it).
```bash
pip install -r requirements.txt
```

### 2. Create your bot
- Go to https://discord.com/developers/applications
- Create a New Application → Bot
- Under **Bot → Privileged Gateway Intents**, enable **Server Members Intent** and **Message Content Intent** (voice state events are a standard intent and need no toggle)
- *(Optional)* Enable **Presence Intent** as well if you want online-member metrics (`/growth` online ratio, `/serverpulse` online count). It also requires `stats.track_presence: true` in config; while off, those metrics show **N/A** instead of a misleading 0.
- Copy your bot token

### 3. Configure
The bot token is read **only** from the `DiscordServerAudit_TOKEN` environment variable — it is not read from `config.yaml`, and the bot refuses to start without it. The Gemini key is optional; without it, all AI features (fact-checks, `!ask`, `/insights`, audit action plans, semantic retrieval) are disabled.

```bash
export DiscordServerAudit_TOKEN="YOUR_BOT_TOKEN"
export DiscordServerAudit_GEMINI_KEY="YOUR_GEMINI_KEY"
```

On Windows PowerShell, use `$env:DiscordServerAudit_TOKEN = "YOUR_BOT_TOKEN"` instead.

Everything else lives in `config.yaml`, which ships with working defaults and inline comments. At minimum, set:

```yaml
bot:
  prefix: "!"

admin_role: "Bot Admin"   # Exact, case-sensitive role name — everyone needs this, including the server owner

log_channel_id: 0         # Paste your channel IDs here
audit_channel_id: 0       # Audit results are posted here

intervals:
  security_audit: 168     # Hours between security audits
  server_audit: 168       # Hours between server audits (168 = weekly)
```

See [Configuration](#configuration) for every setting and the environment variables that can override them.

### 4. Invite the bot
Generate an invite URL in the Discord Developer Portal with these permissions:
- View Channels
- Send Messages
- Embed Links (almost every response is an embed)
- Add Reactions (confirmation prompts and `!react`)
- Read Message History
- Manage Messages
- Manage Channels
- Manage Roles
- Manage Server (for `!listinvites` and invite cleanup via `!ask`)
- Kick Members
- Ban Members
- Moderate Members (for timeouts)
- Move Members (for voice moves and disconnects)
- Mute Members / Deafen Members (for voice moderation)
- Create Instant Invite (for `!createinvite` and `!ask` invite creation)
- Manage Expressions (for creating custom emojis from avatars)

The bot can only manage roles below its own highest role, so move its role above any roles it should assign. Expect the security audit to list this bot under bot permissions — it needs several permissions that the audit treats as sensitive.

### 5. Create the admin role
In your Discord server, create a role named exactly as set in `admin_role` and assign it to yourself and any other admins. The name is resolved to a role and compared by ID; if the role is missing, or more than one role has that name, admin commands are refused until it's fixed.

### 6. Run
```bash
python bot.py
```

On startup the bot creates or migrates its SQLite database (`bot.db` in the working directory unless `DiscordServerAudit_DB_PATH` is set), loads all cogs, and syncs slash commands. Logs go to the console (INFO and above) and to `logs/DiscordServerAudit.log` (DEBUG and above, rotated at 5 MB with 5 backups kept).

### 7. Seed history (optional)
- `/scan [days]` backfills the stats database from message history (up to 365 days). On a database that already has data, it replaces the window's message and member events — see [Stats & Analytics](#stats--analytics).
- `/factcheckrefresh` backfills the fact-check context store. The embedding reconciler then embeds those messages in the background; `/factcheck` shows its progress.

---

## Configuration

### Environment variables

| Variable | Description |
|---|---|
| `DiscordServerAudit_TOKEN` | **Required.** Discord bot token (environment only) |
| `DiscordServerAudit_GEMINI_KEY` | Gemini API key; overrides `gemini_key` |
| `DiscordServerAudit_ADMIN_ROLE` | Overrides `admin_role` |
| `DiscordServerAudit_LOG_CHANNEL_ID` | Overrides `log_channel_id` |
| `DiscordServerAudit_AUDIT_CHANNEL_ID` | Overrides `audit_channel_id` |
| `DiscordServerAudit_PREFIX` | Overrides `bot.prefix` |
| `DiscordServerAudit_FACTCHECK_EMOJI` | Overrides `factcheck.emoji` |
| `DiscordServerAudit_DB_PATH` | SQLite database path (default: `bot.db` in the working directory) |
| `DiscordServerAudit_APP_ROOT` | Directory that holds `logs/` (default: the directory containing `bot.py`) |
| `DiscordServerAudit_LOG_FILE` | Log file name (default: `DiscordServerAudit.log`) |
| `DiscordServerAudit_VERSION` | Version string shown at startup and in the help footer (default: `1.0.0`) |

Only these variables are read. Every other setting comes from `config.yaml`.

### Configuration reference

`config.yaml` holds the shipped values with inline comments; this reference covers what each key controls.

**Core**

| Key | Description |
|---|---|
| `bot.prefix` | Command prefix |
| `admin_role` | Name of the role required for gated commands |
| `audit_channel_id` | Channel where audit results are posted. Scheduled audits are skipped for any server that doesn't contain this channel |
| `log_channel_id` | Log channel ID. Shown in `!config` and protected from `!ask` channel recreation; the bot doesn't currently post to it |
| `gemini_key` | Gemini API key. Prefer the environment variable so the key stays out of source control |
| `gemini.model` | Model used for audit action plans, `/insights`, and `!ask` planning |
| `intervals.security_audit` / `intervals.server_audit` | Hours between scheduled audits |

**Security audit (`security.*`)**

| Key | Description |
|---|---|
| `max_admin_roles` | Raise a critical finding when more roles than this have Administrator |
| `max_bot_permissions_threshold`, `warn_no_2fa`, `warn_unverified_bots` | Present in `config.yaml` but not currently read: the 2FA check always runs, and bot permissions are checked against a built-in list |

**Server audit (`server.*`)**

| Key | Description |
|---|---|
| `inactive_channel_days` | Days without a message before a text channel counts as dead |
| `max_empty_channels` | Flag when more text channels than this have no topic |
| `min_onboarding_channels` | Channel names that should exist (case-insensitive partial match) |

**Stats (`stats.*`)**

| Key | Description |
|---|---|
| `enabled` | Master toggle for stats collection |
| `snapshot_interval_hours` | How often member-count snapshots are taken |
| `retention_days` | Days of raw `message_events` and `voice_sessions` rows to keep; pruned during the daily rollup, while daily aggregates are kept |
| `track_reactions` | Count reactions given and received |
| `track_presence` | Count online members (also requires the Presence Intent) |
| `exclude_bots` | Ignore bot accounts |
| `excluded_channels` / `excluded_users` | Channel or user IDs to leave out of tracking and `/scan` |

**Fact-check (`factcheck.*`)**

| Key | Description |
|---|---|
| `enabled` | Master toggle for fact-checking |
| `emoji` | Trigger emoji — a unicode character, or a custom emoji's name |
| `model` | Gemini model for fact-checks |
| `rate_limit` | Max fact-checks per user per hour |
| `cooldown_seconds` | Seconds before the same message can be checked again |
| `timeout_seconds` | Max seconds to wait for Gemini |
| `max_images` / `max_image_bytes` / `image_download_timeout` | Limits on images taken from attachments, stickers, video thumbnails, and embeds |
| `grounding.enabled` | Verify claims with Google Search grounding |
| `grounding.max_sources` | Max source links shown in the verdict |
| `grounding.require_source_for_negative` | Downgrade a "Mostly False" verdict with no sources to "Unverifiable" |

**Fact-check context (`factcheck.context.*`)**

| Key | Description |
|---|---|
| `enabled` | Store message text and use it as fact-check context |
| `storage_retention_days` | Delete horizon for stored text; `0` = keep forever |
| `max_messages_per_channel` | Per-channel row cap; `0` = unlimited |
| `max_stored_chars` | Truncate stored messages to this many characters |
| `excluded_channels` / `excluded_users` | Channel or user IDs to keep out of the context store. Not in the shipped file — add them under `factcheck.context` |
| `recency_window_hours` | What counts as "recent" for the recency tier (query window only; never deletes) |
| `max_context_messages` / `same_channel_limit` | Max recent messages injected per check / how many trigger-channel messages are taken first |
| `history_relevance.enabled` | Keyword relevance tier (turns itself off if SQLite lacks FTS5) |
| `history_relevance.lookback_days` | How far back keyword search reaches; `0` = all history |
| `history_relevance.archive_max_messages` | Size of the relevance slot shared by keyword and semantic results |
| `history_relevance.min_score` | bm25 score floor; `0` = no floor |
| `backfill_messages_per_channel` | Messages pulled per channel by `/factcheckrefresh`; `0` = unlimited |
| `backfill_channel_delay` | Seconds to pause between channels during backfill |

**Semantic retrieval (`factcheck.context.semantic.*`)**

| Key | Description |
|---|---|
| `enabled` | Master toggle for embeddings; `false` stops all embedding calls |
| `model` / `dimensions` | Embedding model and vector size. Changing either re-embeds all stored history in the background |
| `max_messages` | Semantic candidates considered before fusion |
| `lookback_days` | How far back semantic results may reach; `0` = all embedded history |
| `min_similarity` | Cosine similarity floor |
| `query_context_messages` | Recent same-channel messages folded into the query embedding |
| `fusion_k` | Reciprocal-rank-fusion constant |
| `embed_batch_size` / `embed_interval_seconds` / `max_pending_per_sweep` | Background reconciler batch size, sweep interval, and per-sweep cap (throttles backfill) |
| `embed_backoff_max_seconds` | Ceiling on the sweep interval after consecutive embedding failures |

**Performance (`performance.*`)**

| Key | Description |
|---|---|
| `batch_writes.enabled` | Buffer message-event and context rows and write them in batches. A hard crash can lose up to one flush interval of rows; graceful shutdown always flushes |
| `batch_writes.max_rows` / `batch_writes.max_interval_seconds` | Flush when this many rows are pending or this many seconds have passed |
| `fast_factcheck.enabled` | Fetch images, reply context, and context tiers concurrently (same results, lower latency) |
| `health.log_latency` | Log gateway latency every hour |

---

## Commands

### Admin Utilities

| Command | Description |
|---|---|
| `!config` | Show the effective runtime configuration (prefix, admin role, channels, intervals, audit thresholds) |
| `!shutdown` | Gracefully stop the bot (flushes pending writes, finalises open voice sessions, and closes the database first) |
| `!help [category or command]` | Rich embed-based help with per-category and per-command detail |

### Bulk Operations

| Command | Description |
|---|---|
| `!bulkdelete #channel 50` | Delete the last N messages in a channel (max 100; confirms first). Messages older than 14 days are deleted one at a time |
| `!prunembers [days]` | Prune roleless members inactive for N days (1–30, default 30; confirms first) |
| `!bulkroleadd @Role` | Add a role to all members who don't have it (confirms first) |
| `!bulkroleremove @Role` | Remove a role from all members who have it (confirms first) |
| `!bulkcreatechannels "Category" name1 name2` | Create multiple text channels in a category (space-separated names) |
| `!bulkdeletechannels "Category"` | Delete all channels in a category (confirms first) |
| `!tasklogs [limit]` | Show recent bulk task history (default 10) |

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
| `!softban @User [reason]` | Ban + unban to purge a member's last 24 hours of messages |
| `!timeout @User 30 [reason]` | Timeout (mute) a member for N minutes (max 40320 = 28 days) |
| `!untimeout @User` | Remove a member's timeout |
| `!nick @User <nickname>` / `!clearnick @User` | Set / reset a member's nickname |

**Channels**

| Command | Description |
|---|---|
| `!lock #channel [reason]` / `!unlock #channel` | Lock / unlock a channel for @everyone |
| `!slowmode #channel 10` | Set slowmode in seconds (0 disables, max 21600) |
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
| `!vcmute @User [reason]` / `!vcunmute @User` | Server-mute / unmute a member |
| `!vcdeafen @User [reason]` / `!vcundeafen @User` | Server-deafen / undeafen a member |
| `!vcdisconnect @User` | Disconnect a member from voice |
| `!vcmuteall "VC"` / `!vcunmuteall "VC"` | Mute / unmute everyone in a voice channel |
| `!vckickall "VC"` | Disconnect everyone from a voice channel (confirms first) |

**Invites**

| Command | Description |
|---|---|
| `!createinvite #channel [hours] [uses]` | Create an invite link (defaults: 24 hours, unlimited uses; 0 hours = never expires, 0 uses = unlimited) |

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

Audit results are posted to the audit channel as severity-coloured embeds, followed by a Gemini action plan when a key is configured. Every run and finding is stored in the database.

### Stats & Analytics

| Command | Description |
|---|---|
| `/stats [days]` | Server activity dashboard with health metrics, DAU/MAU, channel diversity (default 7 days) |
| `/userstats @User [days]` | Activity profile with active hours, dormancy, weekday/weekend split, hourly heatmap (default 30) |
| `/channelstats #channel [days]` | Channel report with user concentration, word stats, growth trends (default 30) |
| `/voicestats [days]` | Voice dashboard with session distribution, day-of-week analysis (default 7) |
| `/growth [days]` | Member growth with churn rate, ban rate, member lifecycle (default 30) |
| `/peakhours [days]` | All 24 hours ranked by message count, with per-channel breakdown and weekday vs weekend (default 30) |
| `/serverpulse` | Quick server health check with a composite 0–100 score (**admin only**) |
| `/leaderboard [days] [category]` | Top 10 users by `messages`, `voice`, `engagement`, or `social` (defaults: 7 days, `messages`) |
| `/activity @User1 @User2 [days]` | Head-to-head user comparison with overlay chart (default 30) |
| `/insights [days]` | AI-powered server trend analysis (requires Gemini; default 7) |
| `/scan [days]` | Rebuild stats from message history for the last N days (1–365, default 30). Deletes the window's message events and **all** member events, then re-ingests messages and current members' joins and re-runs daily rollups — leave, ban, and unban events in the window are not restored |
| `/dbcheck` | Diagnose stats tables for duplicates and raw-vs-rollup consistency |
| `/dbclean` | Remove duplicate rows from `message_events`, `voice_sessions`, and `member_events` |

Stats commands are hybrid (they work as both `!command` and `/command`) and, except for `/serverpulse`, are available to every server member. Times are displayed in US Eastern time. Charts are rendered by [QuickChart](https://quickchart.io), so chart data is sent to that service: daily counts and labels, including the two members' display names for `/activity`.

### Natural Language

| Command | Description |
|---|---|
| `!ask <query>` | Describe an admin task in plain English; review the AI plan, then confirm to execute |

Gemini maps the request to one or more of 70 actions, run in order. The review embed lists each step with its parameters and risks and flags destructive steps. Only the person who ran `!ask` can press **Confirm** or **Cancel**, and the plan expires after 60 seconds.

Supported actions cover:
- **Messaging** — send messages, embeds, announcements, DMs, bulk DMs; add reactions; pin/unpin
- **Message cleanup** — bulk-delete channel messages, delete a member's recent messages
- **Roles** — assign/remove, create/delete/rename, list, bulk add/remove, find members with a role
- **Channels** — create text/voice channels and categories; delete, rename, move, lock/unlock, set topic, NSFW, or slowmode (per channel or server-wide); bulk create/delete in a category
- **Moderation** — kick, ban, unban, softban, timeout/remove timeout, nicknames, prune members
- **Raid response** — delete all invites, mass-timeout roleless members, find new accounts and new members
- **Voice** — move, mute/unmute, deafen/undeafen, or disconnect a member; mute, unmute, or disconnect everyone in a voice channel
- **Invites & emojis** — create/list invites, create a custom emoji from a member's avatar
- **Queries & audits** — server/role/channel/member info, inactive channels, roleless members, bans, channels, emojis, admins; run either audit

For "delete all messages" (or any count of 500 or more), `!ask` recreates the channel instead of purging it: the new channel keeps the old one's settings but gets a new ID. The configured audit and log channels are refused on that path. Smaller counts purge only messages from the last 14 days.

### Fact-Check

| Command | Description |
|---|---|
| `/factcheck` | Show fact-check configuration, context/grounding status, semantic retrieval status (model, embedded vs pending), and session stats (admin) |
| `/factcheckrefresh` | Backfill the context store from existing channel history (admin) |
| React with the configured emoji | Fact-check the reacted message (any member; rate-limited) |

Reacting with the trigger emoji (default: magnifying glass) posts a "Checking…" placeholder. The bot gathers the message's text, link-preview text, the message it replies to, and up to `max_images` images from attachments, PNG/APNG stickers, video thumbnails, and embeds. It then edits the placeholder into a verdict: per-claim assessments, an overall verdict (Mostly True, Mixed, Mostly False, Unverifiable, or Not a Factual Claim), a confidence rating, an analysis paragraph, and sources. Includes per-message cooldown and per-user rate limiting. Reactions from bots are ignored, and so is every reaction when no Gemini key is configured.

**Web grounding (`factcheck.grounding`).** When enabled, fact-checks use Gemini's Google Search grounding so claims are verified against live web results instead of the model's training data — this fixes cases where a real, recent article was wrongly dismissed as nonexistent. Grounded verdicts list their sources. A guardrail (`require_source_for_negative`) downgrades a "Mostly False" verdict to "Unverifiable" when no source backs it, so the bot never asserts an uncorroborated denial. Note: Google Search grounding is billed per grounded request by the Gemini API — check current pricing before enabling on a busy server.

**Conversational context (`factcheck.context`).** The bot stores the text of human messages locally in SQLite so verdicts can resolve references like "that article" or "he said". Three tiers feed each check:

1. **Recency** — messages from the last `recency_window_hours`: up to `same_channel_limit` from the trigger channel first, then the newest from across the server, capped at `max_context_messages`
2. **Keyword relevance** — an SQLite FTS5 index ranked by bm25 pulls matching messages from **all retained history**
3. **Semantic relevance** — messages with similar meaning, even when they share no words with the trigger (see below)

Keyword and semantic results are merged by reciprocal-rank fusion into a single relevance slot capped at `archive_max_messages`. Semantic search changes *which* older messages fill that slot without enlarging the prompt, so the per-request cost stays bounded however much history is stored. Context is used only to understand the message; the verdict is always about the message you reacted to.

- `storage_retention_days` is the sole delete horizon and defaults to **`0` = keep forever**. This retains all message text indefinitely (maximizes recall); set a finite number of days for a tighter privacy posture. It is independent of `recency_window_hours` (a query window that never deletes).
- Set `excluded_channels` / `excluded_users` to keep private channels or specific users out of the context store.
- Run `/factcheckrefresh` after enabling context to seed the store from existing history. It scans every messageable channel (text, voice/stage text chat, and active threads) the bot can read, is idempotent, preserves each message's original timestamp, pauses between channels, and applies the retention and per-channel caps when it finishes.
- The keyword tier requires SQLite FTS5; if unavailable it disables gracefully and the other tiers still work.

**Semantic retrieval (`factcheck.context.semantic`).** A background reconciler embeds stored messages with Gemini (`gemini-embedding-001` at 768 dimensions by default), working through un-embedded rows in batches every `embed_interval_seconds`. The same loop embeds history added by `/factcheckrefresh`, and `/factcheck` reports embedded vs pending counts. Vectors are stored as normalised float32 blobs in the `message_embeddings` table, deleted automatically along with their source message, and mirrored in an in-memory NumPy index, so a search is a single matrix multiply. At check time the bot embeds the reacted message together with its reply target and a few recent same-channel messages. That lets low-text triggers — an image, a bare link, "is this true?" — still find relevant history. Results are always re-scoped to the current server.

- **Cost & privacy:** embedding sends the text of every stored message to the Gemini embedding API, billed separately from fact-check generation. Set `semantic.enabled: false` to stop all embedding calls; retrieval then uses recency and keyword tiers only.
- **Changing `model` or `dimensions`** marks every stored message as pending again, and history is re-embedded in the background.
- **Failures back off:** consecutive embedding failures (a bad key, an exhausted quota) double the sweep interval up to `embed_backoff_max_seconds`, and the first success resets it. Any semantic failure at check time falls back to recency + keyword results.

---

## Access control

Gated commands use the `@has_admin_role()` check in `utils/permissions.py`. It resolves the configured `admin_role` name to a role in the current server and compares by role ID; it fails closed if the role is missing or the name is ambiguous, and it only works in servers (not DMs).

| Access | Commands |
|---|---|
| Admin role | Everything under Admin Utilities (except `!help`), Bulk Operations, Messaging & Actions, Info & Inspection, Security Audit, Server Audit, and Natural Language, plus `/serverpulse`, `/factcheck`, and `/factcheckrefresh` |
| Any server member | `!help`; all other Stats & Analytics commands, including `/scan`, `/dbcheck`, and `/dbclean`; and the fact-check reaction (rate-limited per user) |

Opening the stats commands to all members is a deliberate choice recorded in `specs/001-stats-logging/plan.md` and `specs/003-expanded-stats/`.

---

## Data storage

Everything is kept in one SQLite database in WAL mode. Synchronous database calls run in worker threads with thread-local connections, so queries don't block the Discord event loop.

| Area | Tables |
|---|---|
| Audits & scheduling | `audit_runs`, `audit_findings`, `bulk_task_log`, `scheduler_state` |
| Stats (raw) | `message_events` (metadata and word counts only — no message text), `voice_sessions`, `member_events`, `member_snapshots` |
| Stats (daily rollups) | `user_activity_daily`, `channel_activity_daily` |
| Fact-check context | `message_context` (message text), `message_context_fts` (FTS5 index), `message_embeddings` (vectors) |

The schema is created on startup, and one-time data migrations are gated by `PRAGMA user_version`. Triggers keep the FTS index and stored vectors in sync when context rows are deleted.

External services: Discord, the Gemini API (fact-checks, planning, insights, audit summaries, embeddings), and QuickChart (stats chart images).

---

## Project structure

```text
bot.py                    Entry point: logging, intents, cog loading, scheduled audits, graceful shutdown
config.py                 Loads config.yaml and applies environment-variable overrides
config.yaml               Runtime configuration (shipped defaults with inline comments)
cogs/
  admin.py                !config, !shutdown
  bulk_tasks.py           Bulk message/member/role/channel operations and task log
  messaging.py            Messaging, moderation, channel, role, voice, invite, and server/user/role info commands
  info.py                 Channel/role/ban/invite/emoji/admin listings, avatars, member counts
  security_audit.py       Security audit checks and manual trigger
  server_audit.py         Server health audit checks and manual trigger
  stats.py                Activity listeners, snapshots and rollups, stats dashboards, /scan
  natural_language.py     !ask: plan review, confirmation, and execution
  fact_check.py           Reaction fact-checks, context capture/backfill, semantic retrieval and reconciler
database/                 SQLite persistence; every function is re-exported as database.<name>
  connection.py           Thread-local WAL connections, database.run() worker-thread hop, FTS5 probe
  schema.py               Tables, indexes, FTS5 index, embeddings table, migrations
  audit.py                Audit runs/findings, bulk task log, scheduler state
  activity.py             Message/voice/member/reaction events, rollups, pruning
  stats_read.py           Read-side queries behind the stats dashboards
  factcheck.py            Context store, keyword retrieval, embedding storage
  common.py               Timestamp helpers, Gini coefficient
utils/
  permissions.py          @has_admin_role() check and shared embed builders
  help.py                 Embed-based !help command
  scheduler.py            Interval scheduler with last-run times persisted in SQLite
  gemini.py               Shared Gemini client and audit action-plan summaries
  planner.py              !ask action catalogue and Gemini plan builder
  embeddings.py           Gemini document/query embeddings with L2 normalisation
  vector_index.py         In-memory cosine index and reciprocal-rank fusion
  write_buffer.py         Size/time-flushed batch writer
  backoff.py              Capped exponential backoff for the embedding reconciler
  reconnect.py            Caps discord.py's gateway reconnect backoff (~32 s)
  stats_embeds.py         Stats formatting: charts, sparklines, heatmaps, health score, Eastern time
  media.py                Avatar-to-custom-emoji helpers
tests/                    Standard-library unittest suite
scripts/verify_backoff.py Offline check that the reconnect backoff cap holds
specs/                    Spec Kit feature docs (001–007): spec, plan, research, and tasks per feature
.github/workflows/        CI/CD: lint, test, and deploy to a self-hosted Windows runner
```

---

## Development

Run the unit tests (the same command CI runs):
```bash
python -m unittest discover -s tests -t .
```

The suite uses only the standard library's `unittest` and needs no Discord connection or Gemini key; database tests run against a temporary SQLite file. It covers the vector index and rank fusion, the context and embedding SQL, the write buffer, the backoff helper, and the stats formatting helpers.

Lint the way CI does (the first command fails the build on syntax errors and undefined names; the second is informational):
```bash
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics
```

Verify the reconnect backoff cap without a token or network access:
```bash
python scripts/verify_backoff.py
```

Feature specifications, plans, and task lists live in `specs/`, written with Spec Kit; `CLAUDE.md` points to the current plan.

---

## GitHub Actions Deployment

The workflow (`.github/workflows/python-app.yml`) runs on every push to `main` on a self-hosted Windows runner and deploys the bot as a Windows Scheduled Task (same pattern as DiscordVoiceDatabase).

### Required GitHub Secrets
Set these under **Settings → Secrets and variables → Actions**:

| Secret | Required | Description |
|---|---|---|
| `DiscordServerAudit_TOKEN` | Yes | Your Discord bot token |
| `DiscordServerAudit_ADMIN_ROLE` | Yes | The admin role name (e.g. `Bot Admin`) |
| `DiscordServerAudit_LOG_CHANNEL_ID` | Yes | Log channel ID |
| `DiscordServerAudit_AUDIT_CHANNEL_ID` | Yes | Discord channel ID for audit results |
| `DiscordServerAudit_GEMINI_KEY` | No | Google Gemini API key (enables AI features: fact-check, natural language, insights, audit action plans, semantic retrieval) |
| `DiscordServerAudit_FACTCHECK_EMOJI` | No | Custom emoji for fact-check trigger (default: magnifying glass) |
| `DiscordServerAudit_PREFIX` | No | Bot command prefix (default: `!`) |
| `DiscordServerAudit_VERSION` | No | Version string shown at startup and in help footer (default: `1.0.0`) |

The workflow also sets `DiscordServerAudit_DB_PATH` to `C:\apps\DiscordServerAudit\data\bot.db`, keeping the database outside the deployed source folder. It does not pass `DiscordServerAudit_LOG_FILE` or `DiscordServerAudit_APP_ROOT` to the running bot, so unless those are set on the host, logs are written to `C:\apps\DiscordServerAudit\src\logs\DiscordServerAudit.log`.

### What the workflow does
1. Sets up Python 3.10 and installs `requirements.txt` plus flake8
2. Lints with flake8 (syntax errors and undefined names fail the build)
3. Runs the unit tests — a failure stops the job before anything is deployed
4. Mirrors the source to `C:\apps\DiscordServerAudit\src` with robocopy (excluding `.git`, `.github`, `.venv`, and caches)
5. Creates or updates the venv at `C:\apps\DiscordServerAudit\.venv`, rebuilding it with Python 3.10 if it's broken
6. Writes `C:\apps\DiscordServerAudit\run-bot.ps1`, the launcher the Scheduled Task runs
7. Sets the secrets as machine-level env vars via `setx /M`; a missing required secret fails the deploy
8. Stops the running Scheduled Task and any leftover `bot.py` process
9. Re-registers and starts the `DiscordServerAudit` Scheduled Task, which runs at startup under SYSTEM, restarts up to 5 times at 1-minute intervals on failure, and has no time limit
