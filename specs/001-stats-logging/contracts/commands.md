# Command Contracts: Stats Logging

**Cog**: `Stats` (`cogs/stats.py`)
**Access**: Open to all server members (no role gating — read-only queries)
**Command type**: Hybrid (`@commands.hybrid_command()`) — available as both prefix (`!stats`) and slash (`/stats`)
**Chart engine**: QuickChart.io (URL-based Chart.js rendering, no dependencies)

## Visual Design System

### Embed Colors
| Context | Hex | Usage |
|---------|-----|-------|
| Positive / Growth | `0x2ECC71` | Net positive trends, gains |
| Neutral / Info | `0x3498DB` | Standard data display |
| Warning / Decline | `0xE74C3C` | Declining trends, losses |
| Voice | `0x9B59B6` | Voice-specific embeds |
| Gemini / AI | `0xF39C12` | AI-generated content |

### Emoji Palette
```
📊 Stats/Overview     💬 Messages         🎤 Voice
📈 Growth/Increase    📉 Decline          ➡️ Stable/Flat
👥 Members            🏆 Leaderboard      ⏱️ Duration
🔥 Hot/Active         ❄️ Cold/Inactive    🤖 AI/Gemini
📅 Date/Time          🕐 Peak Hour        🔄 Reactions
⭐ Top/Best           📌 Channel          🎯 Target
```

### Unicode Bar Chart Function
Render proportional bars in embed fields using block characters:
```
User A  ████████████████████ 847
User B  ██████████████░░░░░░ 612
User C  █████████░░░░░░░░░░░ 389
User D  ██████░░░░░░░░░░░░░░ 241
User E  ████░░░░░░░░░░░░░░░░ 156
```
- Bar width: 20 characters (█ for filled, ░ for empty)
- Scale relative to max value in the set
- Wrapped in code block for monospace alignment

### QuickChart URL Construction
Base URL: `https://quickchart.io/chart?c=`
- Append URL-encoded Chart.js config JSON
- Set `width=500&height=300&backgroundColor=rgb(47,49,54)` (Discord dark theme background)
- Chart text color: `rgb(255,255,255)` (white on dark)
- Grid lines: `rgba(255,255,255,0.1)` (subtle)
- Set as embed `.set_image(url=chart_url)`

---

## Commands

### `/stats` — Server Activity Dashboard

**Purpose**: Comprehensive server-wide activity overview.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| days | int | 7 | Lookback period (1-90) |

**Response**: 4 embeds

**Embed 1 — Overview** (color: `0x3498DB`)
- Thumbnail: Server icon
- Title: `📊 Server Stats — Last {days} Days`
- Description: One-line summary, e.g., `Your server had **2,847 messages** across **42 active users** this week.`
- Inline fields (3 per row):
  ```
  💬 Messages        🎤 Voice Hours      👥 Active Users
  2,847              186.4h              42

  📌 Active Channels  🔄 Reactions       📈 Trend
  18                  1,203              ↑ 12% vs prior
  ```
- Footer: `Data collected since {first_data_date} • {days}-day window`

**Embed 2 — Top Users Leaderboard** (color: `0x3498DB`)
- Title: `🏆 Most Active Users`
- Description (code block):
  ```
  #1  @UserA   ████████████████████ 847 msgs
  #2  @UserB   ██████████████░░░░░░ 612 msgs
  #3  @UserC   █████████░░░░░░░░░░░ 389 msgs
  #4  @UserD   ██████░░░░░░░░░░░░░░ 241 msgs
  #5  @UserE   ████░░░░░░░░░░░░░░░░ 156 msgs
  ```

**Embed 3 — Top Channels Leaderboard** (color: `0x3498DB`)
- Title: `📌 Most Active Channels`
- Description (code block):
  ```
  #1  #general      ████████████████████ 1,204 msgs
  #2  #dev-chat     ████████████░░░░░░░░  723 msgs
  #3  #off-topic    ████████░░░░░░░░░░░░  481 msgs
  #4  #help         █████░░░░░░░░░░░░░░░  289 msgs
  #5  #announcements ███░░░░░░░░░░░░░░░░░  150 msgs
  ```

**Embed 4 — Activity Trend Chart** (color: `0x3498DB`)
- Title: `📈 Daily Activity`
- Image: QuickChart line chart with:
  - X-axis: dates (last N days)
  - Y-axis (left): message count (blue line, filled area)
  - Y-axis (right): voice hours (purple line)
  - Legend: "Messages" / "Voice Hours"
  - Dark theme background matching Discord

**Error cases**:
- No data: Single embed with description "📊 No stats data collected yet. The bot is now tracking activity — check back in a day!"

---

### `/userstats` — User Activity Profile

**Purpose**: Detailed activity profile for a specific user.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| member | discord.Member | (required) | The user to query |
| days | int | 30 | Lookback period (1-90) |

**Response**: 3 embeds

**Embed 1 — User Profile** (color: trend-dependent)
- Thumbnail: User's avatar
- Title: `📊 Stats for {display_name}`
- Description: `Activity summary for the last **{days} days**`
- Inline fields (3 per row):
  ```
  💬 Messages Sent    🎤 Voice Time       🔄 Reactions Given
  389                 24h 17m             156

  📌 Top Channel      📅 Daily Average    📈 Trend
  #dev-chat           13.0 msgs/day       ↑ 23% vs prior
  ```
- Additional fields:
  ```
  🔄 Reactions Received    ⏱️ Avg Voice Session
  89                        1h 12m
  ```

**Embed 2 — Activity Sparkline** (color: same)
- Title: `📈 Daily Activity`
- Description (code block — text sparkline for last 14 days):
  ```
  Messages per day (last 14 days):
  Mon ▂▅████▇▅▃▁▂▄██▆
  Avg: 13.0 | Peak: 31 (May 14) | Quiet: 2 (May 7)
  ```
- Image: QuickChart bar chart:
  - X-axis: last 14 days (date labels)
  - Y-axis: messages per day (blue bars)
  - Dark theme

**Embed 3 — Channel Breakdown** (color: same)
- Title: `📌 Channel Activity`
- Description (code block):
  ```
  #dev-chat     ████████████████████  156 msgs (40%)
  #general      ██████████░░░░░░░░░░   98 msgs (25%)
  #help         ██████░░░░░░░░░░░░░░   62 msgs (16%)
  #off-topic    ████░░░░░░░░░░░░░░░░   44 msgs (11%)
  #other (3)    ███░░░░░░░░░░░░░░░░░   29 msgs  (8%)
  ```

**Error cases**:
- User not found: `MemberNotFound` (discord.py built-in)
- No data: Single embed with `No activity recorded for {display_name} in the last {days} days.`

---

### `/channelstats` — Channel Activity Report

**Purpose**: Detailed activity breakdown for a specific channel.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| channel | discord.TextChannel | (required) | The channel to query |
| days | int | 30 | Lookback period (1-90) |

**Response**: 3 embeds

**Embed 1 — Channel Overview** (color: trend-dependent)
- Thumbnail: Server icon
- Title: `📌 Stats for #{channel_name}`
- Description: `Activity summary for the last **{days} days**`
- Inline fields (3 per row):
  ```
  💬 Total Messages   👥 Unique Users     📅 Daily Average
  1,204               34                  40.1 msgs/day

  🕐 Peak Hour        📈 Trend            🔥 Busiest Day
  3:00 PM UTC         ↑ 8% vs prior       Wednesday
  ```

**Embed 2 — Top Contributors** (color: same)
- Title: `🏆 Top Contributors`
- Description (code block):
  ```
  #1  @UserA   ████████████████████  312 msgs (26%)
  #2  @UserB   ██████████████░░░░░░  245 msgs (20%)
  #3  @UserC   ██████████░░░░░░░░░░  198 msgs (16%)
  #4  @UserD   ██████░░░░░░░░░░░░░░  124 msgs (10%)
  #5  @UserE   ████░░░░░░░░░░░░░░░░   87 msgs  (7%)
  ```
- Footer: `{remaining_count} other users contributed {remaining_pct}%`

**Embed 3 — Activity Trend** (color: same)
- Title: `📈 Daily Message Volume`
- Image: QuickChart bar chart:
  - X-axis: dates
  - Y-axis: message count
  - Bars colored by volume intensity (gradient green → blue)
  - Dark theme

**Error cases**:
- Channel not found: `ChannelNotFound` (discord.py built-in)
- No data: Single embed with `No activity recorded for #{channel_name} in the last {days} days.`

---

### `/voicestats` — Voice Activity Dashboard

**Purpose**: Comprehensive voice channel usage overview.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| days | int | 7 | Lookback period (1-90) |

**Response**: 4 embeds

**Embed 1 — Voice Overview** (color: `0x9B59B6`)
- Thumbnail: Server icon
- Title: `🎤 Voice Stats — Last {days} Days`
- Description: Summary sentence
- Inline fields (3 per row):
  ```
  ⏱️ Total Time       👥 Unique Users     📊 Sessions
  186h 24m            28                  347

  ⏱️ Avg Session      🔥 Peak Concurrent  🟢 Currently In
  32m                  12                  5
  ```

**Embed 2 — Top Voice Users** (color: `0x9B59B6`)
- Title: `🏆 Voice Leaderboard`
- Description (code block):
  ```
  #1  @UserA   ████████████████████  48h 12m
  #2  @UserB   ██████████████░░░░░░  34h 45m
  #3  @UserC   █████████░░░░░░░░░░░  22h 08m
  #4  @UserD   ██████░░░░░░░░░░░░░░  13h 33m
  #5  @UserE   ████░░░░░░░░░░░░░░░░   8h 56m
  ```

**Embed 3 — Top Voice Channels** (color: `0x9B59B6`)
- Title: `📌 Channel Usage`
- Description (code block):
  ```
  #1  🔊 General Voice  ████████████████████  89h 14m
  #2  🔊 Gaming         ██████████████░░░░░░  52h 30m
  #3  🔊 Music          ████████░░░░░░░░░░░░  28h 11m
  #4  🔊 Study Room     █████░░░░░░░░░░░░░░░  12h 29m
  #5  🔊 AFK            ███░░░░░░░░░░░░░░░░░   4h 00m
  ```

**Embed 4 — Voice Activity Trend** (color: `0x9B59B6`)
- Title: `📈 Daily Voice Hours`
- Image: QuickChart line chart:
  - X-axis: dates
  - Y-axis: voice hours per day
  - Purple line with filled area
  - Dark theme

**Error cases**:
- No data: Single embed with `🎤 No voice data collected yet. Join a voice channel to start tracking!`

---

### `/growth` — Member Growth Dashboard

**Purpose**: Member count trends, join/leave tracking, and retention metrics.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| days | int | 30 | Lookback period (1-365) |

**Response**: 4 embeds

**Embed 1 — Growth Summary** (color: trend-dependent green/red)
- Thumbnail: Server icon
- Title: `👥 Member Growth — Last {days} Days`
- Description: Bold summary, e.g., `Your server grew by **+23 members** (4.2%) this month.`
- Inline fields (3 per row):
  ```
  👥 Current          👥 {days}d Ago       📈 Net Change
  573                 550                 +23 (↑ 4.2%)

  ✅ Joins            ❌ Leaves            📊 Retention
  31                  8                   74.2%
  ```

**Embed 2 — Growth Chart** (color: same)
- Title: `📈 Member Count Over Time`
- Image: QuickChart line chart:
  - X-axis: dates (from snapshots)
  - Y-axis: total member count
  - Green line with filled area
  - Min Y-axis slightly below minimum to show variation
  - Annotations for significant join/leave events if detectable
  - Dark theme

**Embed 3 — Daily Breakdown** (color: same)
- Title: `📅 Daily Activity (Last 7 Days)`
- Description (code block — formatted table):
  ```
  Date         Joins  Leaves  Net    Members
  ─────────────────────────────────────────
  May 18 (Sun)   3      1    +2      573
  May 17 (Sat)   5      0    +5      571
  May 16 (Fri)   2      2     0      566
  May 15 (Thu)   1      0    +1      566
  May 14 (Wed)   4      1    +3      565
  May 13 (Tue)   0      2    -2      562
  May 12 (Mon)   2      0    +2      564
  ─────────────────────────────────────────
  Total         17      6   +11
  ```

**Embed 4 — Join/Leave Distribution** (color: same)
- Title: `📊 Joins vs Leaves`
- Image: QuickChart stacked bar chart:
  - X-axis: dates
  - Stacked bars: green (joins) on top, red (leaves) below
  - Dark theme

**Error cases**:
- No snapshot data: Single embed with `👥 No growth data yet — member snapshots begin after the first hour of tracking.`
- Insufficient history: Show available data with footer noting when collection started

---

### `/insights` — AI-Powered Analysis

**Purpose**: Gemini-generated natural language trend analysis and recommendations.

**Arguments**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| days | int | 7 | Lookback period (1-90) |

**Response**: 2-3 embeds

**Embed 1 — AI Analysis** (color: `0xF39C12`)
- Thumbnail: Gemini/AI icon or server icon
- Title: `🤖 AI Insights — Last {days} Days`
- Description: Gemini-generated analysis (up to 4096 chars), covering:
  - Overall activity assessment
  - Notable patterns and anomalies
  - User engagement observations
  - Channel health assessment
  - Actionable recommendations (3-5 bullet points)
- Footer: `Powered by Gemini • Analysis based on {total_messages} messages, {total_voice_hours}h voice, {member_events} member events`

**Embed 2 — Key Metrics Context** (color: `0xF39C12`)
- Title: `📊 Data Summary`
- Description: The raw stats fed to Gemini, so users can verify the AI's claims
- Inline fields with the key numbers used in analysis:
  ```
  💬 Messages         🎤 Voice Hours      👥 Active Users
  2,847               186.4h              42

  📈 Growth           🔥 Peak Day         ❄️ Quietest Day
  +23 members         Wednesday           Sunday
  ```

**Embed 3 — Recommendations** (color: `0xF39C12`, optional — only if Gemini output is long)
- Title: `🎯 Recommendations`
- Description: Extracted action items from Gemini, formatted as a numbered list

**Error cases**:
- Gemini not configured: Single embed (color: `0xE74C3C`): `🤖 Gemini API key not configured. Set gemini_key in config.yaml to enable AI insights. Your stats are still available via /stats, /growth, and other commands.`
- Gemini API failure: Single embed (color: `0xE74C3C`): `🤖 Could not generate insights — Gemini API error. Your stats data is still available via other commands.`
- No data: Single embed: `🤖 Not enough data to generate meaningful insights. Try again after the bot has been collecting data for a few days.`

---

## Event Handlers (Passive — No User Interaction)

These fire automatically and do not produce user-visible output.

| Event | Handler | Action |
|-------|---------|--------|
| `on_message` | `_on_message` | Insert into `message_events` (skip bots if configured, skip excluded channels) |
| `on_voice_state_update` | `_on_voice_state_update` | Start/end voice sessions in `voice_sessions` |
| `on_member_join` | `_on_member_join` | Insert `join` event into `member_events` |
| `on_member_remove` | `_on_member_remove` | Insert `leave` event into `member_events` |
| `on_member_ban` | `_on_member_ban` | Insert `ban` event into `member_events` |
| `on_member_unban` | `_on_member_unban` | Insert `unban` event into `member_events` |
| `on_raw_reaction_add` | `_on_reaction_add` | Increment daily reaction counters (if tracking enabled) |
| `on_raw_reaction_remove` | `_on_reaction_remove` | Decrement daily reaction counters (if tracking enabled) |

## Scheduled Tasks

| Task Key | Interval | Action |
|----------|----------|--------|
| `stats_snapshot_{guild_id}` | Configurable (default 1 hour) | Capture member snapshot |
| `stats_rollup_{guild_id}` | Daily (at configured UTC hour) | Run daily aggregation + prune old raw events |

## Utility Functions (cogs/stats.py)

### `_build_bar_chart(items, max_width=20) -> str`
Render a Unicode bar chart from a list of `(label, value)` tuples. Returns a formatted code block string.

### `_build_quickchart_url(chart_config) -> str`
URL-encode a Chart.js config dict and return a full QuickChart URL with dark-theme defaults.

### `_trend_indicator(current, previous) -> str`
Return `📈 ↑ X%` / `📉 ↓ X%` / `➡️ ─ 0%` string based on percentage change.

### `_format_duration(minutes) -> str`
Convert minutes to `Xh Ym` string (e.g., `24h 17m`).

### `_embed_color_for_trend(current, previous) -> int`
Return green/neutral/red hex color based on trend direction.
