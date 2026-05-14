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

        if plan.get("action") == "unknown":
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

        action = plan["action"]
        params = plan.get("parameters", {})
        summary = plan.get("summary", "")
        risks = plan.get("risks") or "None"
        is_destructive = plan.get("is_destructive", False)

        logger.info(
            "Plan generated | user=%s (ID=%s) action=%r params=%s destructive=%s elapsed=%.2fs",
            ctx.author, ctx.author.id, action, params, is_destructive, plan_elapsed,
        )

        color = discord.Color.red() if is_destructive else discord.Color.green()
        embed = discord.Embed(
            title=f"{'⚠️ Destructive' if is_destructive else '✅ Safe'} — Review Plan",
            color=color,
        )
        embed.add_field(name="Action", value=f"`{action}`", inline=True)
        embed.add_field(name="What will happen", value=summary, inline=False)
        if params:
            param_lines = "\n".join(f"`{k}`: {v}" for k, v in params.items())
            embed.add_field(name="Parameters", value=param_lines, inline=False)
        embed.add_field(name="Risks", value=risks, inline=False)
        embed.set_footer(text="Confirm to execute • Cancel to abort • Expires in 60s")

        view = ConfirmView(author_id=ctx.author.id, is_destructive=is_destructive)
        await status.edit(embed=embed, view=view)

        await view.wait()
        await status.edit(view=None)

        if view.result is True:
            logger.info(
                "Plan confirmed — executing | user=%s (ID=%s) action=%r guild=%r (ID=%s)",
                ctx.author, ctx.author.id, action,
                ctx.guild.name if ctx.guild else "DM",
                ctx.guild.id if ctx.guild else "N/A",
            )
            await ctx.send(embed=build_embed("⚙️ Executing...", summary, discord.Color.blue()))
            t_exec = time.perf_counter()
            result = await self._execute(ctx, action, params)
            exec_elapsed = time.perf_counter() - t_exec
            logger.info(
                "Execution complete | user=%s (ID=%s) action=%r elapsed=%.2fs result=%r",
                ctx.author, ctx.author.id, action, exec_elapsed, result,
            )
            await ctx.send(embed=build_embed("✅ Done", result, discord.Color.green()))

        elif view.result is False:
            logger.info(
                "Plan cancelled by user | user=%s (ID=%s) action=%r",
                ctx.author, ctx.author.id, action,
            )
            await ctx.send(embed=build_embed("❌ Cancelled", "Action was cancelled.", discord.Color.greyple()))

        else:
            logger.info(
                "Plan confirmation timed out | user=%s (ID=%s) action=%r",
                ctx.author, ctx.author.id, action,
            )
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

        logger.error("No executor implemented for action %r", action)
        return f"Executor for `{action}` is not implemented."


async def setup(bot):
    await bot.add_cog(NaturalLanguage(bot))
