# Feature Spec: Expanded Stats & Metrics

## Goal

Expand the existing `/stats`, `/userstats`, `/channelstats`, `/voicestats`, `/growth`, and `/peakhours` commands with significantly more derived metrics — all computed from data already being collected. No new event listeners, no new database columns, no new data collection. Pure query-side expansion.

## Clarifications

### Session 2026-05-20

- Q: Solo vs group voice sessions — intentionally dropped or missed from contracts? → A: Dropped. Overlapping interval queries are expensive per research; omitted from contracts/data-model intentionally.
- Q: Should `/peakhours` be enhanced, left as-is, or deprecated? → A: Enhance it with additional metrics (not just left as-is despite new heatmaps on other commands).
- Q: Should all members be able to view all other members' detailed stats? → A: Open to all, consistent with current behavior. No restrictions or opt-out.

## Constraints

- **No new data collection**: All metrics must be derivable from existing tables: `message_events`, `voice_sessions`, `member_events`, `member_snapshots`, `user_activity_daily`, `channel_activity_daily`
- **No new event listeners**: The existing `on_message`, `on_voice_state_update`, `on_member_join/remove/ban/unban`, `on_raw_reaction_add/remove` listeners are sufficient
- **No new database tables**: New indexes are acceptable; new columns on existing rollup tables are acceptable if populated from existing raw data
- **Runtime Discord.py data**: guild/member attributes (roles, join date, account age, status, etc.) are available at query time and can supplement DB queries

## Scope

### Enhanced Server Dashboard (`/stats`)

Add to the existing 4-embed layout:
- Messages per day average
- Average message length (words)
- Weekend vs weekday activity split
- DAU/WAU/MAU ratios (from user_activity_daily unique users by date range)
- Message velocity trend (messages/hour recent vs prior)
- Activity diversity — how evenly distributed activity is across channels (Gini coefficient or similar)
- Top growing and declining channels (comparing current vs prior period)

### Enhanced User Profile (`/userstats`)

Add to the existing 3-embed layout:
- Average message length (words per message)
- Most active hour of day
- Activity streak (consecutive days with messages)
- Current streak and longest streak
- Days since last message (dormancy)
- Engagement ratio (reactions given / messages sent)
- Consistency score (std deviation of daily messages — lower = more consistent)
- Weekday vs weekend breakdown
- Rank among server members by messages and voice

### Enhanced Channel Stats (`/channelstats`)

Add to the existing 3-embed layout:
- Average words per message
- Messages per active user (conversation density)
- User concentration — how dominated by top users (top-3 share %)
- Hourly activity heatmap (all 24 hours for this channel, not just peak)
- Weekend vs weekday split
- Growth trend (current period vs prior)

### Enhanced Voice Stats (`/voicestats`)

Add to the existing 4-embed layout:
- Median session length
- Longest single session
- Sessions by day of week
- Most common voice hours (peak voice activity times)

### Enhanced Growth Dashboard (`/growth`)

Add to the existing 4-embed layout:
- Churn rate (leaves / total members)
- Average member tenure (from join dates of current members)
- Ban rate (bans / total leaves)
- New account detection (accounts created < 7 days before joining)
- Busiest join day of week

### Enhanced Peak Hours (`/peakhours`)

Add to the existing layout:
- Weekday vs weekend hourly comparison
- Per-channel peak hour breakdown (top 5 channels)
- Trend comparison (current period peak hours vs prior period)
- Message volume overlay on the hourly chart

### New Command: `/serverpulse`

A quick single-embed snapshot of server health combining the most important metrics into one view:
- Activity score (0-100 composite)
- Messages today vs 7-day average
- Voice users now vs average
- Member change today
- Top channel right now
- Server mood (based on reaction ratio)

### New Command: `/leaderboard`

A dedicated leaderboard view with multiple categories:
- Top messagers (total messages)
- Top voice users (total voice time)
- Most consistent (longest current streak)
- Most engaging (highest reaction-received-per-message ratio)
- Most social (highest reactions-given count)

### New Command: `/activity @User1 @User2`

Compare two users side-by-side with a versus-style embed showing all key metrics for both.

## Non-Goals

- No new data collection mechanisms
- No changes to the scan command
- No changes to the rollup logic (unless adding columns to rollup tables)
- No external API calls for stats (Gemini insights already exists separately)
