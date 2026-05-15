import asyncio
import datetime
import logging
import re
import time
import discord
from discord.ext import commands

from utils.permissions import has_admin_role, build_embed
from utils.planner import build_plan

logger = logging.getLogger(__name__)

_BULK_DELAY = 0.5  # seconds between individual API calls in bulk loops
_OLD_MSG_DELAY = 1.1  # seconds between individual deletes of messages >14 days old
_BULK_DELETE_DELAY = 1.0  # seconds between bulk-delete batches


async def _api(coro_factory, retries: int = 3):
    """Call coro_factory(), retrying on 429 (rate limit) and 5xx (Discord server errors)."""
    for attempt in range(retries):
        try:
            return await coro_factory()
        except discord.HTTPException as exc:
            is_rate_limit = exc.status == 429
            is_server_error = exc.status >= 500
            if (is_rate_limit or is_server_error) and attempt < retries - 1:
                wait = (getattr(exc, "retry_after", None) or 1.0) if is_rate_limit else (2 ** attempt)
                logger.warning(
                    "Discord error %d — waiting %.2fs before retry (attempt %d/%d)",
                    exc.status, wait, attempt + 1, retries,
                )
                await asyncio.sleep(wait)
            else:
                raise


async def _purge_messages(
    channel: discord.TextChannel,
    *,
    limit: int | None = None,
    check=None,
) -> int:
    """
    Delete messages from a channel in a rate-limit-safe way.

    Paginates history manually (0.5s between pages) to avoid bursting the GET
    endpoint, then:
    - Recent messages (≤14 days): bulk-deleted in batches of up to 100
    - Old messages (>14 days): individually deleted with _OLD_MSG_DELAY spacing

    Returns the count of deleted messages.
    """
    cutoff = discord.utils.utcnow() - datetime.timedelta(days=14)
    recent: list[discord.Message] = []
    old: list[discord.Message] = []

    fetched = 0
    before = None
    while True:
        page_size = min(100, limit - fetched) if limit is not None else 100
        page: list[discord.Message] = []
        for attempt in range(3):
            try:
                page = [m async for m in channel.history(limit=page_size, before=before, oldest_first=False)]
                break
            except discord.HTTPException as exc:
                if exc.status >= 500 and attempt < 2:
                    wait = 2 ** attempt
                    logger.warning("Discord %d fetching history #%s — retry in %ds", exc.status, channel.name, wait)
                    await asyncio.sleep(wait)
                else:
                    raise

        if not page:
            break

        for msg in page:
            if check and not check(msg):
                continue
            if msg.created_at > cutoff:
                recent.append(msg)
            else:
                old.append(msg)

        fetched += len(page)
        before = page[-1]

        if len(page) < page_size or (limit is not None and fetched >= limit):
            break

        await asyncio.sleep(0.5)  # pace history GET requests

    deleted = 0

    for i in range(0, len(recent), 100):
        batch = recent[i:i + 100]
        if len(batch) == 1:
            await _api(batch[0].delete)
        else:
            await _api(lambda b=batch: channel.delete_messages(b))
        deleted += len(batch)
        await asyncio.sleep(_BULK_DELETE_DELAY)

    for msg in old:
        await _api(msg.delete)
        deleted += 1
        await asyncio.sleep(_OLD_MSG_DELAY)

    logger.debug(
        "_purge_messages | channel=#%s recent=%d old=%d deleted=%d",
        channel.name, len(recent), len(old), deleted,
    )
    return deleted


# ---------------------------------------------------------------------------
# Resolvers — try ID / mention / exact name / case-insensitive / partial
# ---------------------------------------------------------------------------

def resolve_text_channel(guild: discord.Guild, value: str) -> discord.TextChannel | None:
    value = value.strip().lstrip("#")
    m = re.match(r"<#(\d+)>", value)
    if m:
        return guild.get_channel(int(m.group(1)))
    if value.isdigit():
        return guild.get_channel(int(value))
    ch = discord.utils.get(guild.text_channels, name=value)
    if ch:
        return ch
    low = value.lower()
    for ch in guild.text_channels:
        if ch.name.lower() == low:
            return ch
    for ch in guild.text_channels:
        if low in ch.name.lower():
            return ch
    return None


def resolve_role(guild: discord.Guild, value: str) -> discord.Role | None:
    value = value.strip()
    m = re.match(r"<@&(\d+)>", value)
    if m:
        return guild.get_role(int(m.group(1)))
    if value.isdigit():
        return guild.get_role(int(value))
    role = discord.utils.get(guild.roles, name=value)
    if role:
        return role
    low = value.lower()
    for role in guild.roles:
        if role.name.lower() == low:
            return role
    for role in guild.roles:
        if low in role.name.lower():
            return role
    return None


def resolve_category(guild: discord.Guild, value: str) -> discord.CategoryChannel | None:
    value = value.strip()
    if value.isdigit():
        ch = guild.get_channel(int(value))
        return ch if isinstance(ch, discord.CategoryChannel) else None
    cat = discord.utils.get(guild.categories, name=value)
    if cat:
        return cat
    low = value.lower()
    for cat in guild.categories:
        if cat.name.lower() == low:
            return cat
    for cat in guild.categories:
        if low in cat.name.lower():
            return cat
    return None


def resolve_member(guild: discord.Guild, value: str) -> discord.Member | None:
    value = value.strip()
    m = re.match(r"<@!?(\d+)>", value)
    if m:
        return guild.get_member(int(m.group(1)))
    if value.isdigit():
        return guild.get_member(int(value))
    member = discord.utils.find(lambda mem: mem.name == value or mem.display_name == value, guild.members)
    if member:
        return member
    low = value.lower()
    for mem in guild.members:
        if mem.name.lower() == low or mem.display_name.lower() == low:
            return mem
    for mem in guild.members:
        if low in mem.name.lower() or low in mem.display_name.lower():
            return mem
    return None


def resolve_voice_channel(guild: discord.Guild, value: str) -> discord.VoiceChannel | None:
    value = value.strip()
    m = re.match(r"<#(\d+)>", value)
    if m:
        return guild.get_channel(int(m.group(1)))
    if value.isdigit():
        return guild.get_channel(int(value))
    vc = discord.utils.get(guild.voice_channels, name=value)
    if vc:
        return vc
    low = value.lower()
    for vc in guild.voice_channels:
        if vc.name.lower() == low:
            return vc
    for vc in guild.voice_channels:
        if low in vc.name.lower():
            return vc
    return None


def resolve_any_channel(guild: discord.Guild, value: str) -> discord.abc.GuildChannel | None:
    value = value.strip().lstrip("#")
    m = re.match(r"<#(\d+)>", value)
    if m:
        return guild.get_channel(int(m.group(1)))
    if value.isdigit():
        return guild.get_channel(int(value))
    ch = discord.utils.get(guild.channels, name=value)
    if ch:
        return ch
    low = value.lower()
    for ch in guild.channels:
        if ch.name.lower() == low:
            return ch
    for ch in guild.channels:
        if low in ch.name.lower():
            return ch
    return None


async def resolve_ban(guild: discord.Guild, value: str):
    """Resolve a banned user by ID, mention, exact username, or case-insensitive username."""
    value = value.strip()
    m = re.match(r"<@!?(\d+)>", value)
    target_id = int(m.group(1)) if m else (int(value) if value.isdigit() else None)
    low = value.lower()
    async for entry in guild.bans():
        if target_id and entry.user.id == target_id:
            return entry
        if entry.user.name == value:
            return entry
        if entry.user.name.lower() == low:
            return entry
    # partial match as last resort
    async for entry in guild.bans():
        if low in entry.user.name.lower():
            return entry
    return None


def _member_not_found(guild: discord.Guild, raw: str) -> str:
    sample = ", ".join(m.display_name for m in list(guild.members)[:8])
    return f"Member `{raw}` not found. Sample members: {sample}"


def _role_not_found(guild: discord.Guild, raw: str) -> str:
    sample = ", ".join(r.name for r in guild.roles[1:9])
    return f"Role `{raw}` not found. Available: {sample}"


def _channel_not_found(guild: discord.Guild, raw: str) -> str:
    sample = ", ".join("#" + c.name for c in guild.text_channels[:8])
    return f"Channel `#{raw}` not found. Available: {sample}"


def _category_not_found(guild: discord.Guild, raw: str) -> str:
    sample = ", ".join(c.name for c in guild.categories[:8])
    return f"Category `{raw}` not found. Available: {sample}"


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, is_destructive: bool):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.result = None  # True=confirmed, False=cancelled, None=timed out

        style = discord.ButtonStyle.danger if is_destructive else discord.ButtonStyle.success
        confirm_btn = discord.ui.Button(label="Confirm", style=style)
        confirm_btn.callback = self._on_confirm
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._on_cancel
        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            logger.debug(
                "ConfirmView interaction rejected — wrong user | expected=%s got=%s (ID=%s)",
                self.author_id, interaction.user, interaction.user.id,
            )
            await interaction.response.send_message("Only the person who ran this command can confirm.", ephemeral=True)
            return False
        return True

    async def _on_confirm(self, interaction: discord.Interaction):
        self.result = True
        self.stop()
        await interaction.response.defer()

    async def _on_cancel(self, interaction: discord.Interaction):
        self.result = False
        self.stop()
        await interaction.response.defer()


class NaturalLanguage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ask")
    @has_admin_role()
    async def ask(self, ctx: commands.Context, *, query: str):
        """Describe a server management task in plain English, review the plan, then confirm to execute."""
        logger.info(
            "ask query received | user=%s (ID=%s) guild=%r (ID=%s) query=%r",
            ctx.author, ctx.author.id,
            ctx.guild.name if ctx.guild else "DM",
            ctx.guild.id if ctx.guild else "N/A",
            query,
        )

        status = await ctx.send(embed=build_embed("🤔 Planning...", f'"{query}"', discord.Color.blue()))

        t0 = time.perf_counter()
        plan = await build_plan(query)
        plan_elapsed = time.perf_counter() - t0

        if plan is None:
            logger.error(
                "build_plan returned None (Gemini error) | user=%s (ID=%s) query=%r elapsed=%.2fs",
                ctx.author, ctx.author.id, query, plan_elapsed,
            )
            await status.edit(embed=build_embed(
                "⚠️ Gemini error",
                "Failed to reach Gemini. Check that `DiscordServerAudit_GEMINI_KEY` is set and valid, then check the bot logs for details.",
                discord.Color.red(),
            ))
            return

        steps = plan.get("steps", [])

        if not steps or steps[0].get("action") == "unknown":
            logger.info(
                "build_plan returned unknown action | user=%s (ID=%s) query=%r elapsed=%.2fs",
                ctx.author, ctx.author.id, query, plan_elapsed,
            )
            await status.edit(embed=build_embed(
                "❓ No matching action",
                "I couldn't map that to a supported action. Try rephrasing, or use `!help` to see available commands.",
                discord.Color.orange(),
            ))
            return

        overall_summary = plan.get("overall_summary", "")
        is_destructive = plan.get("is_destructive", False)

        logger.info(
            "Plan generated | user=%s (ID=%s) steps=%d destructive=%s elapsed=%.2fs",
            ctx.author, ctx.author.id, len(steps), is_destructive, plan_elapsed,
        )

        # Build review embed — one field per step
        color = discord.Color.red() if is_destructive else discord.Color.green()
        title = f"{'⚠️ Destructive' if is_destructive else '✅ Safe'} — Review Plan ({len(steps)} step{'s' if len(steps) != 1 else ''})"
        embed = discord.Embed(title=title, description=overall_summary, color=color)

        for i, step in enumerate(steps, 1):
            action = step.get("action", "unknown")
            summary = step.get("summary", "")
            risks = step.get("risks") or "None"
            params = step.get("parameters", {})
            param_str = "  ".join(f"`{k}`: {v}" for k, v in params.items()) if params else "—"
            destructive_tag = " 🔴" if step.get("is_destructive") else " 🟢"
            embed.add_field(
                name=f"Step {i}{destructive_tag}  `{action}`",
                value=f"{summary}\n**Params:** {param_str}\n**Risks:** {risks}",
                inline=False,
            )

        embed.set_footer(text="Confirm to execute all steps • Cancel to abort • Expires in 60s")

        view = ConfirmView(author_id=ctx.author.id, is_destructive=is_destructive)
        await status.edit(embed=embed, view=view)

        await view.wait()
        await status.edit(view=None)

        if view.result is True:
            logger.info(
                "Plan confirmed — executing %d steps | user=%s (ID=%s) guild=%r (ID=%s)",
                len(steps), ctx.author, ctx.author.id,
                ctx.guild.name if ctx.guild else "DM",
                ctx.guild.id if ctx.guild else "N/A",
            )

            results = []
            for i, step in enumerate(steps, 1):
                action = step["action"]
                params = step.get("parameters", {})
                summary = step.get("summary", "")
                await ctx.send(embed=build_embed(f"⚙️ Step {i}/{len(steps)}", summary, discord.Color.blue()))
                t_exec = time.perf_counter()
                result = await self._execute(ctx, action, params)
                exec_elapsed = time.perf_counter() - t_exec
                logger.info(
                    "Step %d/%d complete | action=%r elapsed=%.2fs result=%r",
                    i, len(steps), action, exec_elapsed, result,
                )
                results.append(f"**Step {i} (`{action}`):** {result}")

            await ctx.send(embed=build_embed(
                "✅ All steps complete",
                "\n".join(results),
                discord.Color.green(),
            ))

        elif view.result is False:
            logger.info("Plan cancelled by user | user=%s (ID=%s)", ctx.author, ctx.author.id)
            await ctx.send(embed=build_embed("❌ Cancelled", "Action was cancelled.", discord.Color.greyple()))

        else:
            logger.info("Plan confirmation timed out | user=%s (ID=%s)", ctx.author, ctx.author.id)
            await ctx.send(embed=build_embed("⏰ Timed out", "No confirmation received. Action was not executed.", discord.Color.greyple()))

    async def _execute(self, ctx: commands.Context, action: str, params: dict) -> str:
        guild = ctx.guild

        if action == "bulk_delete":
            raw = params.get("channel_name", "")
            raw_count = params.get("count", "all")
            # None / "all" / 0 / missing → delete everything; otherwise parse as int
            if str(raw_count).strip().lower() in ("all", "none", "", "0"):
                count = None
            else:
                try:
                    count = max(1, int(raw_count))
                except (ValueError, TypeError):
                    count = None
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("bulk_delete: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return f"Channel `#{raw}` not found. Available: {', '.join('#' + c.name for c in guild.text_channels[:10])}"
            if count is None:
                # Recreate the channel — preserves all settings in 2 API calls instead of
                # thousands of individual deletes (required for messages older than 14 days).
                logger.info(
                    "Executing bulk_delete (recreate) | channel=#%s (ID=%s) | guild=%r (ID=%s)",
                    channel.name, channel.id, guild.name, guild.id,
                )
                position = channel.position
                clone = await channel.clone(reason=f"Bulk delete requested by {ctx.author}")
                await clone.edit(position=position)
                await channel.delete(reason=f"Bulk delete requested by {ctx.author}")
                return f"Cleared `#{clone.name}` (channel recreated — all messages removed)."
            else:
                logger.info(
                    "Executing bulk_delete | channel=#%s (ID=%s) count=%d | guild=%r (ID=%s)",
                    channel.name, channel.id, count, guild.name, guild.id,
                )
                deleted = await _purge_messages(channel, limit=count)
                return f"Deleted {deleted}/{count} messages from `#{channel.name}`."

        if action == "prune_members":
            days = max(1, min(int(params.get("days", 7)), 30))
            logger.info("Executing prune_members | days=%d | guild=%r (ID=%s)", days, guild.name, guild.id)
            pruned = await guild.prune_members(days=days, compute_prune_count=True)
            return f"Pruned {pruned} inactive members ({days}+ days with no roles)."

        if action == "bulk_role_add":
            raw = params.get("role_name", "")
            role = resolve_role(guild, raw)
            if not role:
                logger.warning("bulk_role_add: role %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return f"Role `{raw}` not found. Available: {', '.join(r.name for r in guild.roles[1:11])}"
            count = 0
            logger.info("Executing bulk_role_add | role=%r (ID=%s) | guild=%r (ID=%s)", role.name, role.id, guild.name, guild.id)
            for member in guild.members:
                if role not in member.roles:
                    await _api(lambda m=member: m.add_roles(role))
                    count += 1
                    await asyncio.sleep(_BULK_DELAY)
            return f"Added `{role.name}` to {count} members."

        if action == "bulk_role_remove":
            raw = params.get("role_name", "")
            role = resolve_role(guild, raw)
            if not role:
                logger.warning("bulk_role_remove: role %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return f"Role `{raw}` not found. Available: {', '.join(r.name for r in guild.roles[1:11])}"
            count = 0
            logger.info("Executing bulk_role_remove | role=%r (ID=%s) | guild=%r (ID=%s)", role.name, role.id, guild.name, guild.id)
            for member in guild.members:
                if role in member.roles:
                    await _api(lambda m=member: m.remove_roles(role))
                    count += 1
                    await asyncio.sleep(_BULK_DELAY)
            return f"Removed `{role.name}` from {count} members."

        if action == "bulk_create_channels":
            raw_cat = params.get("category_name", "")
            names = [n.strip() for n in params.get("channel_names", "").split(",") if n.strip()]
            if not names:
                return "No channel names provided."
            category = resolve_category(guild, raw_cat)
            if not category:
                category = await guild.create_category(raw_cat)
                logger.info("Created category %r | guild=%r (ID=%s)", raw_cat, guild.name, guild.id)
            logger.info("Executing bulk_create_channels | category=%r (ID=%s) names=%s | guild=%r (ID=%s)", category.name, category.id, names, guild.name, guild.id)
            for name in names:
                await _api(lambda n=name: guild.create_text_channel(n, category=category))
                await asyncio.sleep(_BULK_DELAY)
            return f"Created {len(names)} channels in `{category.name}`."

        if action == "bulk_delete_channels":
            raw = params.get("category_name", "")
            category = resolve_category(guild, raw)
            if not category:
                logger.warning("bulk_delete_channels: category %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return f"Category `{raw}` not found. Available: {', '.join(c.name for c in guild.categories[:10])}"
            count = len(category.channels)
            logger.info("Executing bulk_delete_channels | category=%r (ID=%s) channels=%d | guild=%r (ID=%s)", category.name, category.id, count, guild.name, guild.id)
            for ch in list(category.channels):
                await _api(ch.delete)
                await asyncio.sleep(_BULK_DELAY)
            return f"Deleted {count} channels from `{category.name}`."

        if action == "security_audit":
            cog = self.bot.get_cog("SecurityAudit")
            if not cog:
                logger.error("security_audit action: SecurityAudit cog not loaded")
                return "SecurityAudit cog is not loaded."
            findings = await cog.run_audit(guild, triggered_by=str(ctx.author.id))
            await cog.post_audit_results(guild, findings, triggered_by=ctx.author.mention)
            return f"Security audit complete — {len(findings)} findings posted to the audit channel."

        if action == "server_audit":
            cog = self.bot.get_cog("ServerAudit")
            if not cog:
                logger.error("server_audit action: ServerAudit cog not loaded")
                return "ServerAudit cog is not loaded."
            findings = await cog.run_audit(guild, triggered_by=str(ctx.author.id))
            await cog.post_audit_results(guild, findings, triggered_by=ctx.author.mention)
            return f"Server audit complete — {len(findings)} recommendations posted to the audit channel."

        if action == "find_inactive_channels":
            days = int(params.get("days", 14))
            now = datetime.datetime.utcnow()
            logger.info("Executing find_inactive_channels | days=%d | guild=%r (ID=%s)", days, guild.name, guild.id)
            results = []
            for channel in guild.text_channels:
                try:
                    msgs = [m async for m in channel.history(limit=1)]
                    if not msgs:
                        results.append(f"#{channel.name} (no messages)")
                    else:
                        delta = now - msgs[0].created_at.replace(tzinfo=None)
                        if delta.days >= days:
                            results.append(f"#{channel.name} ({delta.days}d ago)")
                except discord.Forbidden:
                    logger.debug("No access to channel history for #%s (ID=%s)", channel.name, channel.id)
            logger.info("find_inactive_channels complete | found=%d | guild=%r (ID=%s)", len(results), guild.name, guild.id)
            if not results:
                return f"No channels inactive for {days}+ days."
            overflow = f"\n…and {len(results) - 20} more" if len(results) > 20 else ""
            return f"**{len(results)} inactive channels** ({days}+ days):\n" + "\n".join(results[:20]) + overflow

        if action == "find_roleless_members":
            logger.info("Executing find_roleless_members | guild=%r (ID=%s)", guild.name, guild.id)
            roleless = [m for m in guild.members if len(m.roles) == 1 and not m.bot]
            logger.info("find_roleless_members complete | found=%d | guild=%r (ID=%s)", len(roleless), guild.name, guild.id)
            if not roleless:
                return "No members without roles."
            names = ", ".join(m.display_name for m in roleless[:20])
            overflow = f" (+{len(roleless) - 20} more)" if len(roleless) > 20 else ""
            return f"**{len(roleless)} members with no roles:** {names}{overflow}"

        if action == "find_members_with_role":
            raw = params.get("role_name", "")
            role = resolve_role(guild, raw)
            if not role:
                logger.warning("find_members_with_role: role %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _role_not_found(guild, raw)
            members = [m for m in role.members if not m.bot]
            if not members:
                return f"No members have the `{role.name}` role."
            names = ", ".join(m.display_name for m in members[:20])
            overflow = f" (+{len(members) - 20} more)" if len(members) > 20 else ""
            return f"**{len(members)} members with `{role.name}`:** {names}{overflow}"

        if action == "kick_member":
            raw = params.get("member_name", "")
            reason = params.get("reason") or f"Kicked by {ctx.author}"
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("kick_member: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            await member.kick(reason=reason)
            logger.info("Kicked %s (ID=%s) | reason=%r | guild=%r", member, member.id, reason, guild.name)
            return f"Kicked **{member.display_name}**. Reason: {reason}"

        if action == "ban_member":
            raw = params.get("member_name", "")
            reason = params.get("reason") or f"Banned by {ctx.author}"
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("ban_member: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            await member.ban(reason=reason)
            logger.info("Banned %s (ID=%s) | reason=%r | guild=%r", member, member.id, reason, guild.name)
            return f"Banned **{member.display_name}**. Reason: {reason}"

        if action == "set_slowmode":
            raw = params.get("channel_name", "")
            seconds = max(0, min(int(params.get("seconds", 0)), 21600))
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("set_slowmode: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            await channel.edit(slowmode_delay=seconds)
            msg = f"Slowmode on `#{channel.name}` set to {seconds}s." if seconds else f"Slowmode disabled on `#{channel.name}`."
            logger.info("set_slowmode | channel=#%s seconds=%d | guild=%r", channel.name, seconds, guild.name)
            return msg

        if action == "lock_channel":
            raw = params.get("channel_name", "")
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("lock_channel: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            await channel.set_permissions(guild.default_role, send_messages=False)
            logger.info("lock_channel | channel=#%s | guild=%r", channel.name, guild.name)
            return f"🔒 `#{channel.name}` is now locked — @everyone cannot send messages."

        if action == "unlock_channel":
            raw = params.get("channel_name", "")
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("unlock_channel: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            await channel.set_permissions(guild.default_role, send_messages=None)
            logger.info("unlock_channel | channel=#%s | guild=%r", channel.name, guild.name)
            return f"🔓 `#{channel.name}` is now unlocked — @everyone permissions restored."

        if action == "rename_channel":
            raw = params.get("channel_name", "")
            new_name = params.get("new_name", "")
            if not new_name:
                return "No new name provided."
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("rename_channel: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            old_name = channel.name
            await channel.edit(name=new_name)
            logger.info("rename_channel | #%s → #%s | guild=%r", old_name, new_name, guild.name)
            return f"Renamed `#{old_name}` → `#{new_name}`."

        if action == "set_channel_topic":
            raw = params.get("channel_name", "")
            topic = params.get("topic", "")
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("set_channel_topic: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            await channel.edit(topic=topic)
            logger.info("set_channel_topic | channel=#%s | guild=%r", channel.name, guild.name)
            return f"Topic updated for `#{channel.name}`."

        if action == "delete_user_messages":
            raw_member = params.get("member_name", "")
            raw_channel = params.get("channel_name", "").strip().lstrip("#")
            scan_limit = params.get("scan_limit")
            limit = None if not scan_limit or str(scan_limit).strip() in ("", "0", "all", "none") else max(1, min(int(scan_limit), 1000))

            member = resolve_member(guild, raw_member)
            if not member:
                logger.warning("delete_user_messages: member %r not found | guild=%r (ID=%s)", raw_member, guild.name, guild.id)
                return _member_not_found(guild, raw_member)

            if raw_channel:
                resolved_ch = resolve_text_channel(guild, raw_channel)
                if not resolved_ch:
                    return _channel_not_found(guild, raw_channel)
                channels = [resolved_ch]
            else:
                channels = list(guild.text_channels)

            total_deleted = 0
            for i, ch in enumerate(channels):
                try:
                    total_deleted += await _purge_messages(
                        ch,
                        limit=limit,
                        check=lambda m, uid=member.id: m.author.id == uid,
                    )
                except discord.Forbidden:
                    logger.warning("No permission to purge #%s (ID=%s)", ch.name, ch.id)
                if i < len(channels) - 1:
                    await asyncio.sleep(1.0)  # pace between channels

            scope = f"`#{channels[0].name}`" if raw_channel else "all channels"
            logger.info(
                "delete_user_messages | member=%s (ID=%s) scope=%s deleted=%d | guild=%r",
                member, member.id, scope, total_deleted, guild.name,
            )
            return f"Deleted **{total_deleted}** messages from **{member.display_name}** across {scope}."

        # ── Member management ──────────────────────────────────────────────────

        if action == "rename_member":
            raw = params.get("member_name", "")
            nickname = params.get("nickname", "")
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("rename_member: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            await member.edit(nick=nickname)
            logger.info("rename_member | %s → %r | guild=%r", member, nickname, guild.name)
            return f"Renamed **{member.name}** → **{nickname}**."

        if action == "clear_nickname":
            raw = params.get("member_name", "")
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("clear_nickname: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            await member.edit(nick=None)
            logger.info("clear_nickname | %s | guild=%r", member, guild.name)
            return f"Cleared nickname for **{member.name}** — now shows as their username."

        if action == "timeout_member":
            raw = params.get("member_name", "")
            minutes = max(1, min(int(params.get("duration_minutes", 60)), 40320))
            reason = params.get("reason") or f"Timed out by {ctx.author}"
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("timeout_member: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            await member.timeout(until, reason=reason)
            logger.info("timeout_member | %s (ID=%s) minutes=%d | guild=%r", member, member.id, minutes, guild.name)
            return f"⏱️ **{member.display_name}** timed out for {minutes} minute(s). Reason: {reason}"

        if action == "remove_timeout":
            raw = params.get("member_name", "")
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("remove_timeout: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            await member.timeout(None)
            logger.info("remove_timeout | %s (ID=%s) | guild=%r", member, member.id, guild.name)
            return f"Timeout removed from **{member.display_name}**."

        if action == "unban_member":
            raw = params.get("member_name", "")
            entry = await resolve_ban(guild, raw)
            if not entry:
                logger.warning("unban_member: no ban found for %r | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return f"No banned member matching `{raw}` found."
            await guild.unban(entry.user)
            logger.info("unban_member | %s (ID=%s) | guild=%r", entry.user, entry.user.id, guild.name)
            return f"Unbanned **{entry.user.name}**."

        if action == "member_info":
            raw = params.get("member_name", "")
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("member_info: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            now = datetime.datetime.now(datetime.timezone.utc)
            joined_days = (now - member.joined_at).days if member.joined_at else "?"
            account_days = (now - member.created_at).days
            roles = [r.name for r in member.roles if not r.is_default()]
            timed_out = "Yes" if member.is_timed_out() else "No"
            return (
                f"**{member.display_name}** (`{member.name}`)\n"
                f"Joined server: {joined_days}d ago\n"
                f"Account age: {account_days}d\n"
                f"Roles: {', '.join(roles) if roles else 'None'}\n"
                f"Timed out: {timed_out}\n"
                f"Bot: {'Yes' if member.bot else 'No'}"
            )

        if action == "find_new_members":
            days = int(params.get("days", 7))
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            new_members = [m for m in guild.members if m.joined_at and m.joined_at >= cutoff and not m.bot]
            if not new_members:
                return f"No new members in the last {days} days."
            new_members.sort(key=lambda m: m.joined_at, reverse=True)
            lines = [f"{m.display_name} (joined {(datetime.datetime.now(datetime.timezone.utc) - m.joined_at).days}d ago)" for m in new_members[:20]]
            overflow = f"\n…and {len(new_members) - 20} more" if len(new_members) > 20 else ""
            return f"**{len(new_members)} new members** (last {days}d):\n" + "\n".join(lines) + overflow

        if action == "find_new_accounts":
            days = int(params.get("days", 7))
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            new_accounts = [m for m in guild.members if m.created_at >= cutoff and not m.bot]
            if not new_accounts:
                return f"No members with accounts newer than {days} days."
            lines = [f"{m.display_name} (account {(datetime.datetime.now(datetime.timezone.utc) - m.created_at).days}d old)" for m in new_accounts[:20]]
            overflow = f"\n…and {len(new_accounts) - 20} more" if len(new_accounts) > 20 else ""
            return f"**{len(new_accounts)} members with accounts under {days}d old:**\n" + "\n".join(lines) + overflow

        if action == "move_to_voice":
            raw_member = params.get("member_name", "")
            raw_vc = params.get("voice_channel_name", "")
            member = resolve_member(guild, raw_member)
            if not member:
                logger.warning("move_to_voice: member %r not found | guild=%r (ID=%s)", raw_member, guild.name, guild.id)
                return _member_not_found(guild, raw_member)
            if not member.voice:
                return f"**{member.display_name}** is not in a voice channel."
            vc = resolve_voice_channel(guild, raw_vc)
            if not vc:
                logger.warning("move_to_voice: voice channel %r not found | guild=%r (ID=%s)", raw_vc, guild.name, guild.id)
                sample = ", ".join(v.name for v in guild.voice_channels[:8])
                return f"Voice channel `{raw_vc}` not found. Available: {sample}"
            await member.move_to(vc)
            logger.info("move_to_voice | %s → %r | guild=%r", member, vc.name, guild.name)
            return f"Moved **{member.display_name}** to **{vc.name}**."

        if action == "disconnect_from_voice":
            raw = params.get("member_name", "")
            member = resolve_member(guild, raw)
            if not member:
                logger.warning("disconnect_from_voice: member %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _member_not_found(guild, raw)
            if not member.voice:
                return f"**{member.display_name}** is not in a voice channel."
            await member.move_to(None)
            logger.info("disconnect_from_voice | %s | guild=%r", member, guild.name)
            return f"Disconnected **{member.display_name}** from voice."

        # ── Role management ────────────────────────────────────────────────────

        if action == "assign_role":
            raw_member = params.get("member_name", "")
            raw_role = params.get("role_name", "")
            member = resolve_member(guild, raw_member)
            if not member:
                logger.warning("assign_role: member %r not found | guild=%r (ID=%s)", raw_member, guild.name, guild.id)
                return _member_not_found(guild, raw_member)
            role = resolve_role(guild, raw_role)
            if not role:
                logger.warning("assign_role: role %r not found | guild=%r (ID=%s)", raw_role, guild.name, guild.id)
                return _role_not_found(guild, raw_role)
            await member.add_roles(role)
            logger.info("assign_role | %s → role=%r | guild=%r", member, role.name, guild.name)
            return f"Assigned **{role.name}** to **{member.display_name}**."

        if action == "remove_role":
            raw_member = params.get("member_name", "")
            raw_role = params.get("role_name", "")
            member = resolve_member(guild, raw_member)
            if not member:
                logger.warning("remove_role: member %r not found | guild=%r (ID=%s)", raw_member, guild.name, guild.id)
                return _member_not_found(guild, raw_member)
            role = resolve_role(guild, raw_role)
            if not role:
                logger.warning("remove_role: role %r not found | guild=%r (ID=%s)", raw_role, guild.name, guild.id)
                return _role_not_found(guild, raw_role)
            await member.remove_roles(role)
            logger.info("remove_role | %s role=%r | guild=%r", member, role.name, guild.name)
            return f"Removed **{role.name}** from **{member.display_name}**."

        if action == "create_role":
            role_name = params.get("role_name", "")
            color_hex = params.get("color", "").strip().lstrip("#")
            color = discord.Color(int(color_hex, 16)) if color_hex else discord.Color.default()
            role = await guild.create_role(name=role_name, color=color)
            logger.info("create_role | role=%r color=%r | guild=%r", role_name, color_hex, guild.name)
            return f"Created role **{role.name}**."

        if action == "delete_role":
            raw = params.get("role_name", "")
            role = resolve_role(guild, raw)
            if not role:
                logger.warning("delete_role: role %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _role_not_found(guild, raw)
            await role.delete()
            logger.info("delete_role | role=%r | guild=%r", role.name, guild.name)
            return f"Deleted role **{role.name}**."

        if action == "rename_role":
            raw = params.get("role_name", "")
            new_name = params.get("new_name", "")
            if not new_name:
                return "No new name provided."
            role = resolve_role(guild, raw)
            if not role:
                logger.warning("rename_role: role %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _role_not_found(guild, raw)
            old_name = role.name
            await role.edit(name=new_name)
            logger.info("rename_role | %r → %r | guild=%r", old_name, new_name, guild.name)
            return f"Renamed role **{old_name}** → **{new_name}**."

        if action == "list_roles":
            roles = sorted([r for r in guild.roles if not r.is_default()], key=lambda r: -r.position)
            if not roles:
                return "No roles found."
            lines = [f"**{r.name}** — {len(r.members)} member(s)" for r in roles[:25]]
            overflow = f"\n…and {len(roles) - 25} more" if len(roles) > 25 else ""
            return "\n".join(lines) + overflow

        # ── Channel management ─────────────────────────────────────────────────

        if action == "create_channel":
            channel_name = params.get("channel_name", "")
            raw_cat = params.get("category_name", "").strip()
            category = resolve_category(guild, raw_cat) if raw_cat else None
            ch = await guild.create_text_channel(channel_name, category=category)
            logger.info("create_channel | #%s category=%r | guild=%r", channel_name, raw_cat or None, guild.name)
            return f"Created {ch.mention}."

        if action == "create_voice_channel":
            channel_name = params.get("channel_name", "")
            raw_cat = params.get("category_name", "").strip()
            category = resolve_category(guild, raw_cat) if raw_cat else None
            ch = await guild.create_voice_channel(channel_name, category=category)
            logger.info("create_voice_channel | %r category=%r | guild=%r", channel_name, raw_cat or None, guild.name)
            return f"Created voice channel **{ch.name}**."

        if action == "create_category":
            category_name = params.get("category_name", "")
            cat = await guild.create_category(category_name)
            logger.info("create_category | %r | guild=%r", category_name, guild.name)
            return f"Created category **{cat.name}**."

        if action == "delete_channel":
            raw = params.get("channel_name", "")
            channel = resolve_any_channel(guild, raw)
            if not channel:
                logger.warning("delete_channel: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            name = channel.name
            await channel.delete()
            logger.info("delete_channel | #%s | guild=%r", name, guild.name)
            return f"Deleted channel **#{name}**."

        if action == "move_channel":
            raw_ch = params.get("channel_name", "")
            raw_cat = params.get("category_name", "")
            channel = resolve_any_channel(guild, raw_ch)
            if not channel:
                logger.warning("move_channel: channel %r not found | guild=%r (ID=%s)", raw_ch, guild.name, guild.id)
                return _channel_not_found(guild, raw_ch)
            category = resolve_category(guild, raw_cat)
            if not category:
                logger.warning("move_channel: category %r not found | guild=%r (ID=%s)", raw_cat, guild.name, guild.id)
                return _category_not_found(guild, raw_cat)
            await channel.edit(category=category)
            logger.info("move_channel | #%s → category=%r | guild=%r", channel.name, category.name, guild.name)
            return f"Moved **#{channel.name}** to **{category.name}**."

        if action == "set_channel_nsfw":
            raw = params.get("channel_name", "")
            enabled = str(params.get("enabled", "true")).lower() != "false"
            channel = resolve_text_channel(guild, raw)
            if not channel:
                logger.warning("set_channel_nsfw: channel %r not found | guild=%r (ID=%s)", raw, guild.name, guild.id)
                return _channel_not_found(guild, raw)
            await channel.edit(nsfw=enabled)
            state = "marked as NSFW 🔞" if enabled else "unmarked as NSFW"
            logger.info("set_channel_nsfw | #%s enabled=%s | guild=%r", channel.name, enabled, guild.name)
            return f"`#{channel.name}` {state}."

        if action == "slowmode_all_channels":
            seconds = max(0, min(int(params.get("seconds", 0)), 21600))
            count = 0
            for ch in guild.text_channels:
                try:
                    await _api(lambda c=ch: c.edit(slowmode_delay=seconds))
                    count += 1
                    await asyncio.sleep(_BULK_DELAY)
                except discord.Forbidden:
                    pass
            msg = f"Set slowmode to {seconds}s" if seconds else "Disabled slowmode"
            logger.info("slowmode_all_channels | seconds=%d channels=%d | guild=%r", seconds, count, guild.name)
            return f"{msg} on {count} channels."

        # ── Server management ──────────────────────────────────────────────────

        if action == "delete_invites":
            invites = await guild.invites()
            for invite in invites:
                await invite.delete()
            logger.info("delete_invites | deleted=%d | guild=%r", len(invites), guild.name)
            return f"Deleted {len(invites)} active invite(s)."

        if action == "list_bans":
            bans = [entry async for entry in guild.bans()]
            if not bans:
                return "No members are currently banned."
            lines = [f"**{e.user.name}** — {e.reason or 'No reason'}" for e in bans[:20]]
            overflow = f"\n…and {len(bans) - 20} more" if len(bans) > 20 else ""
            return f"**{len(bans)} banned member(s):**\n" + "\n".join(lines) + overflow

        if action == "server_info":
            now = datetime.datetime.now(datetime.timezone.utc)
            age_days = (now - guild.created_at).days
            humans = sum(1 for m in guild.members if not m.bot)
            bots = sum(1 for m in guild.members if m.bot)
            return (
                f"**{guild.name}**\n"
                f"Owner: {guild.owner}\n"
                f"Created: {age_days}d ago\n"
                f"Members: {humans} humans, {bots} bots\n"
                f"Channels: {len(guild.text_channels)} text, {len(guild.voice_channels)} voice\n"
                f"Roles: {len(guild.roles) - 1}\n"
                f"Boost level: {guild.premium_tier} ({guild.premium_subscription_count} boosts)\n"
                f"Verification: {guild.verification_level}"
            )

        if action == "mass_timeout":
            minutes = max(1, min(int(params.get("duration_minutes", 60)), 40320))
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            targets = [m for m in guild.members if len(m.roles) == 1 and not m.bot]
            count = 0
            for member in targets:
                try:
                    await _api(lambda m=member: m.timeout(until, reason="Mass timeout — raid containment"))
                    count += 1
                    await asyncio.sleep(_BULK_DELAY)
                except discord.Forbidden:
                    pass
            logger.info("mass_timeout | targets=%d timed_out=%d minutes=%d | guild=%r", len(targets), count, minutes, guild.name)
            return f"⏱️ Timed out {count} roleless members for {minutes} minute(s)."

        logger.error("No executor implemented for action %r", action)
        return f"Executor for `{action}` is not implemented."


async def setup(bot):
    await bot.add_cog(NaturalLanguage(bot))
