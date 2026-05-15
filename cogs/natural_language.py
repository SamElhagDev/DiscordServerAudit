import datetime
import logging
import time
import discord
from discord.ext import commands

from utils.permissions import has_admin_role, build_embed
from utils.planner import build_plan

logger = logging.getLogger(__name__)


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
            channel_name = params.get("channel_name", "")
            count = max(1, min(int(params.get("count", 100)), 100))
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                logger.warning("bulk_delete: channel %r not found | guild=%r (ID=%s)", channel_name, guild.name, guild.id)
                return f"Channel `#{channel_name}` not found."
            logger.info("Executing bulk_delete | channel=#%s count=%d | guild=%r (ID=%s)", channel_name, count, guild.name, guild.id)
            deleted = await channel.purge(limit=count)
            return f"Deleted {len(deleted)} messages from `#{channel_name}`."

        if action == "prune_members":
            days = max(1, min(int(params.get("days", 7)), 30))
            logger.info("Executing prune_members | days=%d | guild=%r (ID=%s)", days, guild.name, guild.id)
            pruned = await guild.prune_members(days=days, compute_prune_count=True)
            return f"Pruned {pruned} inactive members ({days}+ days with no roles)."

        if action == "bulk_role_add":
            role_name = params.get("role_name", "")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                logger.warning("bulk_role_add: role %r not found | guild=%r (ID=%s)", role_name, guild.name, guild.id)
                return f"Role `{role_name}` not found."
            count = 0
            logger.info("Executing bulk_role_add | role=%r | guild=%r (ID=%s)", role_name, guild.name, guild.id)
            for member in guild.members:
                if role not in member.roles:
                    await member.add_roles(role)
                    count += 1
            return f"Added `{role_name}` to {count} members."

        if action == "bulk_role_remove":
            role_name = params.get("role_name", "")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                logger.warning("bulk_role_remove: role %r not found | guild=%r (ID=%s)", role_name, guild.name, guild.id)
                return f"Role `{role_name}` not found."
            count = 0
            logger.info("Executing bulk_role_remove | role=%r | guild=%r (ID=%s)", role_name, guild.name, guild.id)
            for member in guild.members:
                if role in member.roles:
                    await member.remove_roles(role)
                    count += 1
            return f"Removed `{role_name}` from {count} members."

        if action == "bulk_create_channels":
            category_name = params.get("category_name", "")
            names = [n.strip() for n in params.get("channel_names", "").split(",") if n.strip()]
            if not names:
                return "No channel names provided."
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                category = await guild.create_category(category_name)
                logger.info("Created category %r | guild=%r (ID=%s)", category_name, guild.name, guild.id)
            logger.info("Executing bulk_create_channels | category=%r names=%s | guild=%r (ID=%s)", category_name, names, guild.name, guild.id)
            for name in names:
                await guild.create_text_channel(name, category=category)
            return f"Created {len(names)} channels in `{category_name}`."

        if action == "bulk_delete_channels":
            category_name = params.get("category_name", "")
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                logger.warning("bulk_delete_channels: category %r not found | guild=%r (ID=%s)", category_name, guild.name, guild.id)
                return f"Category `{category_name}` not found."
            count = len(category.channels)
            logger.info("Executing bulk_delete_channels | category=%r channels=%d | guild=%r (ID=%s)", category_name, count, guild.name, guild.id)
            for ch in list(category.channels):
                await ch.delete()
            return f"Deleted {count} channels from `{category_name}`."

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
            role_name = params.get("role_name", "")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return f"Role `{role_name}` not found."
            members = [m for m in role.members if not m.bot]
            if not members:
                return f"No members have the `{role_name}` role."
            names = ", ".join(m.display_name for m in members[:20])
            overflow = f" (+{len(members) - 20} more)" if len(members) > 20 else ""
            return f"**{len(members)} members with `{role_name}`:** {names}{overflow}"

        if action == "kick_member":
            member_name = params.get("member_name", "").lower()
            reason = params.get("reason") or f"Kicked by {ctx.author}"
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            await member.kick(reason=reason)
            logger.info("Kicked %s (ID=%s) | reason=%r | guild=%r", member, member.id, reason, guild.name)
            return f"Kicked **{member.display_name}**. Reason: {reason}"

        if action == "ban_member":
            member_name = params.get("member_name", "").lower()
            reason = params.get("reason") or f"Banned by {ctx.author}"
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            await member.ban(reason=reason)
            logger.info("Banned %s (ID=%s) | reason=%r | guild=%r", member, member.id, reason, guild.name)
            return f"Banned **{member.display_name}**. Reason: {reason}"

        if action == "set_slowmode":
            channel_name = params.get("channel_name", "")
            seconds = max(0, min(int(params.get("seconds", 0)), 21600))
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.edit(slowmode_delay=seconds)
            msg = f"Slowmode on `#{channel_name}` set to {seconds}s." if seconds else f"Slowmode disabled on `#{channel_name}`."
            logger.info("set_slowmode | channel=#%s seconds=%d | guild=%r", channel_name, seconds, guild.name)
            return msg

        if action == "lock_channel":
            channel_name = params.get("channel_name", "")
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.set_permissions(guild.default_role, send_messages=False)
            logger.info("lock_channel | channel=#%s | guild=%r", channel_name, guild.name)
            return f"🔒 `#{channel_name}` is now locked — @everyone cannot send messages."

        if action == "unlock_channel":
            channel_name = params.get("channel_name", "")
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.set_permissions(guild.default_role, send_messages=None)
            logger.info("unlock_channel | channel=#%s | guild=%r", channel_name, guild.name)
            return f"🔓 `#{channel_name}` is now unlocked — @everyone permissions restored."

        if action == "rename_channel":
            channel_name = params.get("channel_name", "")
            new_name = params.get("new_name", "")
            if not new_name:
                return "No new name provided."
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.edit(name=new_name)
            logger.info("rename_channel | #%s → #%s | guild=%r", channel_name, new_name, guild.name)
            return f"Renamed `#{channel_name}` → `#{new_name}`."

        if action == "set_channel_topic":
            channel_name = params.get("channel_name", "")
            topic = params.get("topic", "")
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.edit(topic=topic)
            logger.info("set_channel_topic | channel=#%s | guild=%r", channel_name, guild.name)
            return f"Topic updated for `#{channel_name}`."

        if action == "delete_user_messages":
            member_name = params.get("member_name", "").lower()
            channel_name = params.get("channel_name", "").strip().lstrip("#")
            scan_limit = max(1, min(int(params.get("scan_limit", 500)), 1000))

            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."

            channels = (
                [discord.utils.get(guild.text_channels, name=channel_name)]
                if channel_name
                else list(guild.text_channels)
            )
            channels = [c for c in channels if c is not None]
            if not channels:
                return f"Channel `#{channel_name}` not found."

            total_deleted = 0
            for ch in channels:
                try:
                    deleted = await ch.purge(
                        limit=scan_limit,
                        check=lambda m, uid=member.id: m.author.id == uid,
                    )
                    total_deleted += len(deleted)
                except discord.Forbidden:
                    logger.warning("No permission to purge #%s (ID=%s)", ch.name, ch.id)

            scope = f"`#{channel_name}`" if channel_name else "all channels"
            logger.info(
                "delete_user_messages | member=%s (ID=%s) scope=%s deleted=%d | guild=%r",
                member, member.id, scope, total_deleted, guild.name,
            )
            return f"Deleted **{total_deleted}** messages from **{member.display_name}** across {scope}."

        # ── Member management ──────────────────────────────────────────────────

        if action == "rename_member":
            member_name = params.get("member_name", "").lower()
            nickname = params.get("nickname", "")
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            await member.edit(nick=nickname)
            logger.info("rename_member | %s → %r | guild=%r", member, nickname, guild.name)
            return f"Renamed **{member.name}** → **{nickname}**."

        if action == "clear_nickname":
            member_name = params.get("member_name", "").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            await member.edit(nick=None)
            logger.info("clear_nickname | %s | guild=%r", member, guild.name)
            return f"Cleared nickname for **{member.name}** — now shows as their username."

        if action == "timeout_member":
            member_name = params.get("member_name", "").lower()
            minutes = max(1, min(int(params.get("duration_minutes", 60)), 40320))
            reason = params.get("reason") or f"Timed out by {ctx.author}"
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            await member.timeout(until, reason=reason)
            logger.info("timeout_member | %s (ID=%s) minutes=%d | guild=%r", member, member.id, minutes, guild.name)
            return f"⏱️ **{member.display_name}** timed out for {minutes} minute(s). Reason: {reason}"

        if action == "remove_timeout":
            member_name = params.get("member_name", "").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            await member.timeout(None)
            logger.info("remove_timeout | %s (ID=%s) | guild=%r", member, member.id, guild.name)
            return f"Timeout removed from **{member.display_name}**."

        if action == "unban_member":
            member_name = params.get("member_name", "").lower()
            async for ban_entry in guild.bans():
                if ban_entry.user.name.lower() == member_name:
                    await guild.unban(ban_entry.user)
                    logger.info("unban_member | %s (ID=%s) | guild=%r", ban_entry.user, ban_entry.user.id, guild.name)
                    return f"Unbanned **{ban_entry.user.name}**."
            return f"No banned member named `{member_name}` found."

        if action == "member_info":
            member_name = params.get("member_name", "").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
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
            member_name = params.get("member_name", "").lower()
            vc_name = params.get("voice_channel_name", "")
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            if not member.voice:
                return f"**{member.display_name}** is not in a voice channel."
            vc = discord.utils.get(guild.voice_channels, name=vc_name)
            if not vc:
                return f"Voice channel `{vc_name}` not found."
            await member.move_to(vc)
            logger.info("move_to_voice | %s → %r | guild=%r", member, vc_name, guild.name)
            return f"Moved **{member.display_name}** to **{vc_name}**."

        if action == "disconnect_from_voice":
            member_name = params.get("member_name", "").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            if not member.voice:
                return f"**{member.display_name}** is not in a voice channel."
            await member.move_to(None)
            logger.info("disconnect_from_voice | %s | guild=%r", member, guild.name)
            return f"Disconnected **{member.display_name}** from voice."

        # ── Role management ────────────────────────────────────────────────────

        if action == "assign_role":
            member_name = params.get("member_name", "").lower()
            role_name = params.get("role_name", "")
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return f"Role `{role_name}` not found."
            await member.add_roles(role)
            logger.info("assign_role | %s → role=%r | guild=%r", member, role_name, guild.name)
            return f"Assigned **{role_name}** to **{member.display_name}**."

        if action == "remove_role":
            member_name = params.get("member_name", "").lower()
            role_name = params.get("role_name", "")
            member = discord.utils.find(
                lambda m: m.name.lower() == member_name or m.display_name.lower() == member_name,
                guild.members,
            )
            if not member:
                return f"Member `{member_name}` not found."
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return f"Role `{role_name}` not found."
            await member.remove_roles(role)
            logger.info("remove_role | %s role=%r | guild=%r", member, role_name, guild.name)
            return f"Removed **{role_name}** from **{member.display_name}**."

        if action == "create_role":
            role_name = params.get("role_name", "")
            color_hex = params.get("color", "").strip().lstrip("#")
            color = discord.Color(int(color_hex, 16)) if color_hex else discord.Color.default()
            role = await guild.create_role(name=role_name, color=color)
            logger.info("create_role | role=%r color=%r | guild=%r", role_name, color_hex, guild.name)
            return f"Created role **{role.name}**."

        if action == "delete_role":
            role_name = params.get("role_name", "")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return f"Role `{role_name}` not found."
            await role.delete()
            logger.info("delete_role | role=%r | guild=%r", role_name, guild.name)
            return f"Deleted role **{role_name}**."

        if action == "rename_role":
            role_name = params.get("role_name", "")
            new_name = params.get("new_name", "")
            if not new_name:
                return "No new name provided."
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return f"Role `{role_name}` not found."
            await role.edit(name=new_name)
            logger.info("rename_role | %r → %r | guild=%r", role_name, new_name, guild.name)
            return f"Renamed role **{role_name}** → **{new_name}**."

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
            category_name = params.get("category_name", "").strip()
            category = discord.utils.get(guild.categories, name=category_name) if category_name else None
            ch = await guild.create_text_channel(channel_name, category=category)
            logger.info("create_channel | #%s category=%r | guild=%r", channel_name, category_name, guild.name)
            return f"Created {ch.mention}."

        if action == "create_voice_channel":
            channel_name = params.get("channel_name", "")
            category_name = params.get("category_name", "").strip()
            category = discord.utils.get(guild.categories, name=category_name) if category_name else None
            ch = await guild.create_voice_channel(channel_name, category=category)
            logger.info("create_voice_channel | %r category=%r | guild=%r", channel_name, category_name, guild.name)
            return f"Created voice channel **{ch.name}**."

        if action == "create_category":
            category_name = params.get("category_name", "")
            cat = await guild.create_category(category_name)
            logger.info("create_category | %r | guild=%r", category_name, guild.name)
            return f"Created category **{cat.name}**."

        if action == "delete_channel":
            channel_name = params.get("channel_name", "")
            channel = discord.utils.get(guild.channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.delete()
            logger.info("delete_channel | #%s | guild=%r", channel_name, guild.name)
            return f"Deleted channel **#{channel_name}**."

        if action == "move_channel":
            channel_name = params.get("channel_name", "")
            category_name = params.get("category_name", "")
            channel = discord.utils.get(guild.channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                return f"Category `{category_name}` not found."
            await channel.edit(category=category)
            logger.info("move_channel | #%s → category=%r | guild=%r", channel_name, category_name, guild.name)
            return f"Moved **#{channel_name}** to **{category_name}**."

        if action == "set_channel_nsfw":
            channel_name = params.get("channel_name", "")
            enabled = str(params.get("enabled", "true")).lower() != "false"
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                return f"Channel `#{channel_name}` not found."
            await channel.edit(nsfw=enabled)
            state = "marked as NSFW 🔞" if enabled else "unmarked as NSFW"
            logger.info("set_channel_nsfw | #%s enabled=%s | guild=%r", channel_name, enabled, guild.name)
            return f"`#{channel_name}` {state}."

        if action == "slowmode_all_channels":
            seconds = max(0, min(int(params.get("seconds", 0)), 21600))
            count = 0
            for ch in guild.text_channels:
                try:
                    await ch.edit(slowmode_delay=seconds)
                    count += 1
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
                    await member.timeout(until, reason="Mass timeout — raid containment")
                    count += 1
                except discord.Forbidden:
                    pass
            logger.info("mass_timeout | targets=%d timed_out=%d minutes=%d | guild=%r", len(targets), count, minutes, guild.name)
            return f"⏱️ Timed out {count} roleless members for {minutes} minute(s)."

        logger.error("No executor implemented for action %r", action)
        return f"Executor for `{action}` is not implemented."


async def setup(bot):
    await bot.add_cog(NaturalLanguage(bot))
