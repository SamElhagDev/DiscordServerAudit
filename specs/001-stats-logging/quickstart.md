# Quickstart: Implementing Stats Logging

**Branch**: `001-stats-logging` | **Estimated effort**: ~6-8 hours

## Implementation Order

Follow this sequence — each step builds on the previous:

### Step 1: Database Schema (database.py)

1. Add WAL pragma to `init_db()` (before table creation):
   ```python
   conn.execute("PRAGMA journal_mode=WAL")
   ```
2. Add 6 new `CREATE TABLE IF NOT EXISTS` statements (see data-model.md)
3. Add `CREATE INDEX IF NOT EXISTS` statements for all indexes
4. Add all write helper functions (`log_message_event`, `start_voice_session`, etc.)
5. Add all read helper functions (`get_top_users`, `get_server_stats_summary`, etc.)
6. Add rollup functions (`rollup_user_activity`, `rollup_channel_activity`, `prune_old_events`)

**Verify**: Run `python -c "import database; database.init_db()"` — should create tables without errors.

### Step 2: Config Keys (config.yaml)

Add the `stats` section with all keys from data-model.md. Add a `config.get()` call test to confirm values load.

### Step 3: Bot Setup (bot.py)

1. Add `intents.voice_states = True` in `AdminBot.__init__`
2. Add `await self.tree.sync()` at the end of `setup_hook()` to register slash commands with Discord

### Step 4: Gemini Extension (utils/gemini.py)

Add `analyze_trends(stats_summary: dict) -> str` function following the existing `summarize_findings()` pattern. Accept a dict, format a prompt, call Gemini, return the text response. Handle missing API key and API errors gracefully.

### Step 5: Stats Cog (cogs/stats.py)

Build in this order within the cog:

1. **Utility functions first** (used by all commands):
   - `_build_bar_chart(items, max_width=20)` — Unicode bar chart renderer
   - `_build_quickchart_url(chart_config)` — QuickChart URL constructor with dark theme defaults
   - `_trend_indicator(current, previous)` — Returns `📈 ↑ X%` / `📉 ↓ X%` / `➡️ ─ 0%`
   - `_format_duration(minutes)` — Converts to `Xh Ym` format
   - `_embed_color_for_trend(current, previous)` — Green (0x2ECC71) / Blue (0x3498DB) / Red (0xE74C3C)

2. **Event listeners** (passive collection — no commands needed to test):
   - `on_message` → `database.log_message_event()`
   - `on_voice_state_update` → `database.start_voice_session()` / `end_voice_session()`
   - `on_member_join/remove/ban/unban` → `database.log_member_event()`
   - `on_raw_reaction_add/remove` → increment/decrement `user_activity_daily` counters

3. **Scheduler registration** in `cog_load`:
   - Register `stats_snapshot_{guild_id}` task (hourly)
   - Register `stats_rollup_{guild_id}` task (daily)
   - Call `close_orphaned_voice_sessions()` on cog load

4. **Hybrid commands** (one at a time, test each via both prefix and slash):
   - Use `@commands.hybrid_command()` instead of `@commands.command()`
   - No `@has_admin_role()` — open to all users
   - Each command sends **multiple embeds** (see contracts/commands.md for exact layout)
   - Use `await ctx.send(embeds=[embed1, embed2, embed3, embed4])` for multi-embed responses
   - QuickChart URLs go in `embed.set_image(url=chart_url)`
   - Unicode bar charts go in description wrapped in `` ```\n...\n``` `` code blocks
   - Build order:
     1. `/stats` — server dashboard (4 embeds) — validates all utility functions
     2. `/userstats` — user profile (3 embeds)
     3. `/channelstats` — channel report (3 embeds)
     4. `/voicestats` — voice dashboard (4 embeds)
     5. `/growth` — growth dashboard (4 embeds)
     6. `/insights` — Gemini analysis (2-3 embeds, last — depends on Gemini)

**QuickChart dark theme defaults** (apply to all charts):
```python
CHART_DEFAULTS = {
    "width": 500,
    "height": 300,
    "backgroundColor": "rgb(47,49,54)",
    "options": {
        "legend": {"labels": {"fontColor": "white"}},
        "scales": {
            "xAxes": [{"ticks": {"fontColor": "white"}, "gridLines": {"color": "rgba(255,255,255,0.1)"}}],
            "yAxes": [{"ticks": {"fontColor": "white"}, "gridLines": {"color": "rgba(255,255,255,0.1)"}}],
        },
    },
}
```

### Step 6: Register Cog (bot.py)

Add `"cogs.stats"` to the `COGS` list.

### Step 7: Test End-to-End

1. Start bot, verify cog loads and slash commands sync (check logs for "Synced N commands")
2. Send messages in a test channel → verify `message_events` rows appear in database
3. Join/leave voice → verify `voice_sessions` rows with duration
4. Wait for snapshot interval → verify `member_snapshots` row
5. Test `/stats` via slash command → verify 4 embeds display with chart image
6. Test `!stats` via prefix → verify same output
7. Run `/userstats @yourself` → verify user profile with avatar thumbnail and bar charts
8. Run `/channelstats #test` → verify channel report with contributor leaderboard
9. Run `/voicestats` → verify voice dashboard with purple-themed embeds
10. Run `/growth` → verify growth chart and daily breakdown table
11. Verify QuickChart images load (dark theme, correct data)
12. Verify Unicode bar charts align properly in code blocks
13. If Gemini configured: run `/insights` → verify AI analysis embed with gold theme

## Key Patterns to Follow

- **Event handlers**: Use `@commands.Cog.listener()` decorator, NOT `@bot.event`
- **Database calls**: Use existing `with get_conn() as conn:` pattern
- **Embeds**: Use `build_embed()` from `utils/permissions.py`
- **Hybrid commands**: Use `@commands.hybrid_command()` — no admin gating (open to all users)
- **App command sync**: `await bot.tree.sync()` in `setup_hook()` registers slash commands
- **Logging**: `logger = logging.getLogger(__name__)` at module top
- **Config access**: `config.get("stats.enabled", True)` with defaults
- **Scheduler**: Use `self.bot.scheduler.register()` matching existing pattern in `bot.py:on_ready`

## Files Modified

| File | Change |
|------|--------|
| `database.py` | Add 6 tables, indexes, ~15 new functions |
| `config.yaml` | Add `stats:` section |
| `bot.py` | Add voice intent + cog registration + `tree.sync()` for slash commands |
| `utils/gemini.py` | Add `analyze_trends()` |
| `cogs/stats.py` | **New file** — full cog implementation |
