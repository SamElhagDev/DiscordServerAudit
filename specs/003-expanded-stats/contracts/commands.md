# Interface Contracts: Expanded Stats

**Cog**: `Stats` (`cogs/stats.py`)
**Access**: Hybrid commands, open to all server members (no role gating) except `/serverpulse` (admin-only)
**Chart engine**: QuickChart.io (unchanged)

---

## Enhanced Commands

### `/stats [days]` — Enhanced Server Dashboard

**Current**: 4 embeds. **New**: 5 embeds.

**Embed 1 — Overview** (enhanced)

Add fields to existing overview embed:

```
💬 Messages        🎤 Voice Hours      👥 Active Users
2,847              186.4h              42

📌 Active Channels  🔄 Reactions       📈 Trend
18                  1,203              ↑ 12%

📝 Avg Msg Length   📊 Msgs/Day Avg    📅 Wkday/Wkend
8.3 words           406/day            78% / 22%
```

**Embed 2 — Top Users** (unchanged)

**Embed 3 — Top Channels** (unchanged)

**Embed 4 — Activity Trend** (unchanged)

**Embed 5 — Server Health** (NEW)

```
+----------------------------------------------------------+
| 🏥 Server Health Snapshot                    COLOR_BLUE   |
|----------------------------------------------------------|
| 👥 DAU / WAU / MAU         42 / 68 / 95                  |
| 📊 DAU/MAU Ratio           44% (healthy)                  |
| ⚡ Message Velocity         17.4/hr (↑ 8% vs prior)       |
| 📈 Channel Diversity        0.72 Gini (well distributed)  |
|                                                          |
| 📌 Top Growing Channels:                                 |
|   #dev-chat ↑ 34%  •  #help ↑ 21%                       |
| 📉 Top Declining Channels:                               |
|   #off-topic ↓ 15%  •  #random ↓ 8%                     |
|----------------------------------------------------------|
| Based on {days}-day analysis                              |
+----------------------------------------------------------+
```

---

### `/userstats @User [days]` — Enhanced User Profile

**Current**: 3 embeds. **New**: 4 embeds.

**Embed 1 — Profile** (enhanced)

Add fields to existing profile embed:

```
💬 Messages Sent    🎤 Voice Time       🔄 Reactions Given
389                 24h 17m             156

📌 Top Channel      📅 Daily Average    📈 Trend
#dev-chat           13.0 msgs/day       ↑ 23%

🔄 Reactions Recv   ⏱️ Avg Voice Sess   📝 Avg Msg Length
89                  1h 12m              12.4 words

🏆 Server Rank      🔥 Current Streak   📊 Longest Streak
#3 of 95            14 days             31 days
```

**Embed 2 — Activity Chart** (unchanged)

**Embed 3 — Channel Breakdown** (unchanged)

**Embed 4 — Activity Profile** (NEW)

```
+----------------------------------------------------------+
| 📊 Activity Profile                       COLOR_NEUTRAL   |
|----------------------------------------------------------|
| ⏰ Most Active Hour      3:00 PM EDT                      |
| 📅 Weekday / Weekend     82% / 18%                        |
| 🎯 Consistency Score     78/100 (steady contributor)       |
| 💤 Days Since Last Msg   0 (active today)                  |
| ❤️ Engagement Ratio      0.40 reactions/msg sent           |
| 📈 Active Day %          73% of days since join            |
|                                                          |
| ⏰ Hourly Activity:                                       |
| 00 ░░ 06 ░▂ 12 ▅█ 18 █▇ 23 ▃░                            |
|----------------------------------------------------------|
| {days}-day window                                        |
+----------------------------------------------------------+
```

---

### `/channelstats #channel [days]` — Enhanced Channel Stats

**Current**: 3 embeds. **New**: 4 embeds.

**Embed 1 — Overview** (enhanced)

Add fields:

```
💬 Total Messages   👥 Unique Users     📅 Daily Average
1,204               34                  40.1 msgs/day

🕐 Peak Hour        📈 Trend            🔥 Busiest Day
3:00 PM EDT         ↑ 8%                Wednesday

📝 Avg Words/Msg    💬 Msgs/User        📊 Top-3 Share
9.2 words           35.4 msgs/user      62%
```

**Embed 2 — Top Contributors** (unchanged)

**Embed 3 — Activity Trend** (unchanged)

**Embed 4 — Channel Profile** (NEW)

```
+----------------------------------------------------------+
| 📊 Channel Profile                       COLOR_NEUTRAL    |
|----------------------------------------------------------|
| 📅 Weekday / Weekend     85% / 15%                        |
| 📈 Growth vs Prior       ↑ 12% more messages              |
| 📊 User Concentration    0.45 Gini (moderately diverse)    |
|                                                          |
| ⏰ Hourly Activity:                                       |
| ░░░░░░▁▂▃▅▆██████▇▆▅▃▂▁░                                 |
| 00    06    12    18    23                                |
|----------------------------------------------------------|
| {days}-day window                                        |
+----------------------------------------------------------+
```

---

### `/voicestats [days]` — Enhanced Voice Dashboard

**Current**: 4 embeds. **New**: 5 embeds.

**Embed 1 — Overview** (enhanced)

Add fields:

```
⏱️ Total Time       👥 Unique Users     📊 Sessions
186h 24m            28                  347

⏱️ Avg Session      ⏱️ Median Session   🏆 Longest Session
32m                 18m                 4h 23m

🟢 Currently In     🔥 Peak Hour        📅 Busiest Day
5                   8:00 PM EDT         Saturday
```

**Embed 2 — User Leaderboard** (unchanged)

**Embed 3 — Channel Usage** (unchanged)

**Embed 4 — Voice Trend** (unchanged)

**Embed 5 — Session Distribution** (NEW)

```
+----------------------------------------------------------+
| 📊 Session Analysis                       COLOR_VOICE     |
|----------------------------------------------------------|
| Session Length Distribution:                              |
|   <5m  ██████████░░░░░░░░░░  89 (26%)                    |
|   5-15m ████████░░░░░░░░░░░░  72 (21%)                    |
|   15-30m ██████░░░░░░░░░░░░░░  58 (17%)                    |
|   30-60m █████░░░░░░░░░░░░░░░  48 (14%)                    |
|   1-2h  ████░░░░░░░░░░░░░░░░  41 (12%)                    |
|   2h+   ███░░░░░░░░░░░░░░░░░  39 (11%)                    |
|                                                          |
| Sessions by Day of Week:                                 |
|   Mon ▃  Tue ▅  Wed ▆  Thu ▇  Fri █  Sat █  Sun ▅       |
|----------------------------------------------------------|
| {days}-day window                                        |
+----------------------------------------------------------+
```

---

### `/growth [days]` — Enhanced Growth Dashboard

**Current**: 4 embeds. **New**: 5 embeds.

**Embed 1 — Summary** (enhanced)

Add fields:

```
👥 Current          👥 {days}d Ago       📈 Net Change
573                 550                 +23 (↑ 4.2%)

✅ Joins            ❌ Leaves            📊 Retention
31                  8                   74.2%

📉 Churn Rate       🔨 Ban Rate         👥 Avg Tenure
1.4%                12.5% of leaves     142 days
```

**Embed 2 — Growth Chart** (unchanged)

**Embed 3 — Daily Breakdown** (unchanged)

**Embed 4 — Joins vs Leaves** (unchanged)

**Embed 5 — Member Lifecycle** (NEW)

```
+----------------------------------------------------------+
| 📊 Member Lifecycle                       COLOR_NEUTRAL   |
|----------------------------------------------------------|
| 📅 Busiest Join Day       Saturday                        |
| 👶 New Accounts (<7d)     3 members (accounts < 7d old)   |
| 📊 Online Ratio (avg)     38% of members online           |
|                                                          |
| Joins by Day of Week:                                    |
|   Mon ▃  Tue ▅  Wed ▅  Thu ▆  Fri ▇  Sat █  Sun ▇       |
|----------------------------------------------------------|
| {days}-day window                                        |
+----------------------------------------------------------+
```

---

### `/peakhours [days]` — Enhanced Peak Hours

**Current**: 1 embed (hourly bar chart). **New**: 2 embeds.

**Embed 1 — Hourly Distribution** (enhanced)

Add fields to existing chart embed:

```
📊 Peak Hour         📉 Quietest Hour     📈 Trend
3:00 PM EDT          4:00 AM EDT          Peak shifted +1hr

📅 Weekday Peak      📅 Weekend Peak      📊 Volume Change
2:00 PM EDT          8:00 PM EDT          ↑ 12% vs prior
```

**Embed 2 — Channel Breakdown** (NEW)

```
+----------------------------------------------------------+
| 📊 Peak Hours by Channel                  COLOR_NEUTRAL   |
|----------------------------------------------------------|
| Top 5 Channels by Activity:                              |
|   #general      ██████████████████████  Peak: 3 PM       |
|   #dev-chat     ████████████████░░░░░░  Peak: 2 PM       |
|   #help         ██████████░░░░░░░░░░░░  Peak: 11 AM      |
|   #off-topic    ████████░░░░░░░░░░░░░░  Peak: 9 PM       |
|   #voice-text   ██████░░░░░░░░░░░░░░░░  Peak: 8 PM       |
|                                                          |
| Weekday vs Weekend:                                      |
| Wkday ░░░░░░▁▂▃▅▆██████▇▆▅▃▂▁░                          |
| Wkend ░░░░░░░░▁▁▂▃▃▄▅▅▆▇██▇▅▃                           |
| 00    06    12    18    23                                |
|----------------------------------------------------------|
| {days}-day window                                        |
+----------------------------------------------------------+
```

---

## New Commands

### `/serverpulse` — Quick Server Health Check

**Type**: Hybrid command
**Access**: `@has_admin_role()` required (provides operational metrics)
**Parameters**: None
**Response**: 1 embed

```
+----------------------------------------------------------+
| 💓 Server Pulse                           COLOR_POSITIVE  |
|----------------------------------------------------------|
| 🏥 Health Score         82/100                            |
|                                                          |
| 📊 Today vs 7d Avg                                       |
|   💬 Messages           142 vs 134/day avg (↑ 6%)        |
|   🎤 Voice Users        8 vs 6 avg                       |
|   👥 Member Change      +2 today                         |
|                                                          |
| 🔥 Right Now                                             |
|   📌 Top Channel        #general (23 msgs today)          |
|   🎤 In Voice           5 members                         |
|   🟢 Online             42 / 95 members                   |
|                                                          |
| 📊 Engagement                                            |
|   DAU/MAU: 44%  •  Avg Msg Length: 8.3 words             |
|   Reactions/Msg: 0.42  •  Voice/Text Ratio: 3.2          |
|----------------------------------------------------------|
| Snapshot at {timestamp}                                    |
+----------------------------------------------------------+
```

**Health score components** (each 0-20, summed):
1. **Activity**: DAU/MAU ratio × 20 (capped at 20)
2. **Engagement**: reaction_intensity × messages_per_active_user (normalized)
3. **Retention**: (1 - churn_rate) × 20
4. **Voice**: voice_participation_rate × 20
5. **Growth**: net_member_change > 0 → 20, == 0 → 10, < 0 → 5

---

### `/leaderboard [days] [category]` — Multi-Category Leaderboard

**Type**: Hybrid command
**Access**: Open to all (read-only)
**Parameters**:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| days | int | 7 | Lookback period |
| category | str (choice) | "messages" | One of: messages, voice, streaks, engagement, social |

**Response**: 1 embed per category

```
+----------------------------------------------------------+
| 🏆 Leaderboard — Messages (Last 7 Days)   COLOR_NEUTRAL  |
|----------------------------------------------------------|
| 🥇 @UserA     ████████████████████  847 msgs              |
| 🥈 @UserB     ██████████████░░░░░░  612 msgs              |
| 🥉 @UserC     █████████░░░░░░░░░░░  389 msgs              |
| #4 @UserD     ██████░░░░░░░░░░░░░░  241 msgs              |
| #5 @UserE     ████░░░░░░░░░░░░░░░░  156 msgs              |
| #6 @UserF     ███░░░░░░░░░░░░░░░░░  128 msgs              |
| #7 @UserG     ██░░░░░░░░░░░░░░░░░░   94 msgs              |
| #8 @UserH     ██░░░░░░░░░░░░░░░░░░   82 msgs              |
| #9 @UserI     █░░░░░░░░░░░░░░░░░░░   67 msgs              |
| #10 @UserJ    █░░░░░░░░░░░░░░░░░░░   51 msgs              |
|----------------------------------------------------------|
| Categories: messages • voice • streaks • engagement       |
+----------------------------------------------------------+
```

**Category definitions**:
- **messages**: `SUM(message_count)` from user_activity_daily
- **voice**: `SUM(voice_minutes)` from user_activity_daily
- **streaks**: Current consecutive-day streak (computed in Python)
- **engagement**: `SUM(reactions_received) / SUM(message_count)` ratio
- **social**: `SUM(reactions_given)` from user_activity_daily

---

### `/activity @User1 @User2 [days]` — User Comparison

**Type**: Hybrid command
**Access**: Open to all (read-only)
**Parameters**:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| user1 | discord.Member | (required) | First user |
| user2 | discord.Member | (required) | Second user |
| days | int | 30 | Lookback period |

**Response**: 2 embeds

**Embed 1 — Head-to-Head**

```
+----------------------------------------------------------+
| ⚔️ @UserA vs @UserB — Last 30 Days       COLOR_NEUTRAL   |
|----------------------------------------------------------|
|                    UserA          UserB                    |
| 💬 Messages        389  ◄█████    ███►   241              |
| 🎤 Voice           24h  ◄████     █████► 31h              |
| 🔄 Reactions Given  156  ◄███      ████► 189              |
| 🔄 Reactions Recv   89  ◄████     ██►    52              |
| 📝 Avg Words        12   ◄██       ████► 18              |
| 🔥 Current Streak   14d  ◄████     ██►   8d              |
| 🎯 Consistency      78   ◄████     ███►  65              |
| 📌 Top Channel      #dev          #help                  |
|----------------------------------------------------------|
| {days}-day comparison                                    |
+----------------------------------------------------------+
```

**Embed 2 — Daily Overlay Chart**

QuickChart line chart showing both users' daily message counts on the same axes, different colors.

---

## Shared Patterns

### Heatmap Rendering

All hourly heatmaps use the same rendering function:

```
⏰ Hourly Activity:
░░░░░░▁▂▃▅▆██████▇▆▅▃▂▁░
00    06    12    18    23
```

Characters: `░▁▂▃▄▅▆▇█` mapped proportionally to min-max values.

### Consistency Score

Scale 0-100:
- 100 = perfectly even daily activity (std_dev = 0)
- 0 = all activity concentrated in one day
- Formula: `100 × (1 - (std_dev / mean))` clamped to [0, 100]

### Health Score

Scale 0-100, 5 equal components (0-20 each):
- **Activity** (DAU/MAU ratio normalized to 0-20)
- **Engagement** (reactions per message, normalized)
- **Retention** (inverse churn rate)
- **Voice** (voice participation rate)
- **Growth** (net member change direction)

Labels: 0-20 "Critical", 21-40 "Needs Attention", 41-60 "Average", 61-80 "Healthy", 81-100 "Thriving"
