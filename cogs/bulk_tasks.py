import asyncio
import json
import logging
import time
import discord
from discord.ext import commands

import database
from utils.permissions import has_admin_role, build_embed

logger = logging.getLogger(__name__)


def _guild_ctx(ctx: commands.Context) -> str:
    """Compact guild+user string for log lines."""
    guild = f"{ctx.guild.name!r} (ID={ctx.guild.id})" if ctx.guild else "DM"
    return f"user={ctx.author} (ID={ctx.author.id}) guild={guild}"


class BulkTasks(commands.Cog):
    """Bulk automation commands. All restricted to admin role."""

    def __init__(self, bot):
        self.bot = bot

    # -------------------------------------------------------------------------
    # Bulk Message Delete
    # -------------------------------------------------------------------------
    @commands.command(name="bulkdelete")
    @has_admin_role()
    async def bulk_delete(self, ctx: commands.Context, channel: discord.TextChannel, limit: int):
        """
        Bulk delete messages in a channel.
        Usage: !bulkdelete #channel 50
        Max 100 per invocation (Discord API limit for bulk delete).
        """
        limit = min(limit, 100)
        logger.info("bulkdelete requested | %s | channel=#%s (ID=%s) limit=%d", _guild_ctx(ctx), channel.name, channel.id, limit)

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Delete",
                f"Delete the last **{limit}** messages in {channel.mention}?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange(),
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            logger.info("bulkdelete timed out waiting for confirmation | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            logger.info("bulkdelete cancelled by user | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Bulk delete cancelled.", discord.Color.greyple()))
            return

        logger.info("bulkdelete confirmed — purging #%s limit=%d | %s", channel.name, limit, _guild_ctx(ctx))
        t0 = time.perf_counter()
        deleted = await channel.purge(limit=limit)
        elapsed = time.perf_counter() - t0

        logger.info(
            "bulkdelete complete | channel=#%s deleted=%d elapsed=%.2fs | %s",
            channel.name, len(deleted), elapsed, _guild_ctx(ctx),
        )
        database.log_bulk_task(
            "bulk_delete", str(ctx.author.id), ctx.guild.id,
            json.dumps({"channel": str(channel.id), "requested": limit, "deleted": len(deleted)}),
        )
        await ctx.send(
            embed=build_embed("✅ Bulk Delete Complete", f"Deleted **{len(deleted)}** messages in {channel.mention}.", discord.Color.green()),
            delete_after=10,
        )

    # -------------------------------------------------------------------------
    # Prune Inactive Members
    # -------------------------------------------------------------------------
    @commands.command(name="prunembers")
    @has_admin_role()
    async def prune_members(self, ctx: commands.Context, days: int = 30):
        """
        Prune members with no roles who have been inactive for N days (default 30).
        Usage: !prunembers 30
        """
        days = max(1, min(days, 30))
        estimated = await ctx.guild.estimate_pruned_members(days=days)
        logger.info(
            "prunembers requested | %s | days=%d estimated=%d",
            _guild_ctx(ctx), days, estimated,
        )

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Member Prune",
                f"This will kick approximately **{estimated}** members inactive for **{days}+ days** with no roles.\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange(),
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            logger.info("prunembers timed out waiting for confirmation | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            logger.info("prunembers cancelled by user | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Prune cancelled.", discord.Color.greyple()))
            return

        logger.info("prunembers confirmed — pruning days=%d | %s", days, _guild_ctx(ctx))
        t0 = time.perf_counter()
        pruned = await ctx.guild.prune_members(days=days, reason=f"Bulk prune by {ctx.author}")
        elapsed = time.perf_counter() - t0

        logger.info("prunembers complete | pruned=%d elapsed=%.2fs | %s", pruned, elapsed, _guild_ctx(ctx))
        database.log_bulk_task(
            "prune_members", str(ctx.author.id), ctx.guild.id,
            json.dumps({"days": days, "pruned": pruned}),
        )
        await ctx.send(
            embed=build_embed("✅ Prune Complete", f"Pruned **{pruned}** inactive members.", discord.Color.green())
        )

    # -------------------------------------------------------------------------
    # Bulk Assign Role
    # -------------------------------------------------------------------------
    @commands.command(name="bulkroleadd")
    @has_admin_role()
    async def bulk_role_add(self, ctx: commands.Context, role: discord.Role):
        """
        Assign a role to all members currently without it.
        Usage: !bulkroleadd @RoleName
        """
        targets = [m for m in ctx.guild.members if role not in m.roles and not m.bot]
        logger.info(
            "bulkroleadd requested | %s | role=%r (ID=%s) targets=%d",
            _guild_ctx(ctx), role.name, role.id, len(targets),
        )

        if not targets:
            logger.info("bulkroleadd — nothing to do, all members already have role %r", role.name)
            await ctx.send(embed=build_embed("Nothing to do", f"All members already have **{role.name}**.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Role Add",
                f"Add **{role.name}** to **{len(targets)}** members?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange(),
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            logger.info("bulkroleadd timed out waiting for confirmation | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            logger.info("bulkroleadd cancelled by user | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Role assignment cancelled.", discord.Color.greyple()))
            return

        logger.info("bulkroleadd confirmed — assigning role %r to %d members | %s", role.name, len(targets), _guild_ctx(ctx))
        progress = await ctx.send(f"Assigning role... 0/{len(targets)}")
        failed = 0
        t0 = time.perf_counter()

        for i, member in enumerate(targets):
            try:
                await member.add_roles(role, reason=f"Bulk role add by {ctx.author}")
            except discord.HTTPException as e:
                failed += 1
                logger.warning("bulkroleadd failed for member %s (ID=%s): %s", member, member.id, e)
            if i % 10 == 0:
                await progress.edit(content=f"Assigning role... {i + 1}/{len(targets)}")
            await asyncio.sleep(0.3)

        elapsed = time.perf_counter() - t0
        assigned = len(targets) - failed
        logger.info(
            "bulkroleadd complete | role=%r assigned=%d failed=%d elapsed=%.2fs | %s",
            role.name, assigned, failed, elapsed, _guild_ctx(ctx),
        )
        database.log_bulk_task(
            "bulk_role_add", str(ctx.author.id), ctx.guild.id,
            json.dumps({"role": str(role.id), "targets": len(targets), "failed": failed}),
        )
        await progress.delete()
        await ctx.send(
            embed=build_embed(
                "✅ Bulk Role Add Complete",
                f"Assigned **{role.name}** to **{assigned}** members. ({failed} failed)",
                discord.Color.green(),
            )
        )

    # -------------------------------------------------------------------------
    # Bulk Remove Role
    # -------------------------------------------------------------------------
    @commands.command(name="bulkroleremove")
    @has_admin_role()
    async def bulk_role_remove(self, ctx: commands.Context, role: discord.Role):
        """
        Remove a role from all members who have it.
        Usage: !bulkroleremove @RoleName
        """
        targets = [m for m in ctx.guild.members if role in m.roles]
        logger.info(
            "bulkroleremove requested | %s | role=%r (ID=%s) targets=%d",
            _guild_ctx(ctx), role.name, role.id, len(targets),
        )

        if not targets:
            logger.info("bulkroleremove — nothing to do, no members have role %r", role.name)
            await ctx.send(embed=build_embed("Nothing to do", f"No members have **{role.name}**.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Role Remove",
                f"Remove **{role.name}** from **{len(targets)}** members?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange(),
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            logger.info("bulkroleremove timed out waiting for confirmation | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            logger.info("bulkroleremove cancelled by user | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Role removal cancelled.", discord.Color.greyple()))
            return

        logger.info("bulkroleremove confirmed — removing role %r from %d members | %s", role.name, len(targets), _guild_ctx(ctx))
        failed = 0
        t0 = time.perf_counter()

        for member in targets:
            try:
                await member.remove_roles(role, reason=f"Bulk role remove by {ctx.author}")
            except discord.HTTPException as e:
                failed += 1
                logger.warning("bulkroleremove failed for member %s (ID=%s): %s", member, member.id, e)
            await asyncio.sleep(0.3)

        elapsed = time.perf_counter() - t0
        removed = len(targets) - failed
        logger.info(
            "bulkroleremove complete | role=%r removed=%d failed=%d elapsed=%.2fs | %s",
            role.name, removed, failed, elapsed, _guild_ctx(ctx),
        )
        database.log_bulk_task(
            "bulk_role_remove", str(ctx.author.id), ctx.guild.id,
            json.dumps({"role": str(role.id), "targets": len(targets), "failed": failed}),
        )
        await ctx.send(
            embed=build_embed(
                "✅ Bulk Role Remove Complete",
                f"Removed **{role.name}** from **{removed}** members. ({failed} failed)",
                discord.Color.green(),
            )
        )

    # -------------------------------------------------------------------------
    # Bulk Create Channels
    # -------------------------------------------------------------------------
    @commands.command(name="bulkcreatechannels")
    @has_admin_role()
    async def bulk_create_channels(self, ctx: commands.Context, category: discord.CategoryChannel, *, channel_names: str):
        """
        Bulk create text channels in a category.
        Usage: !bulkcreatechannels "Category Name" channel-one channel-two channel-three
        """
        names = channel_names.split()
        logger.info(
            "bulkcreatechannels requested | %s | category=%r (ID=%s) count=%d names=%s",
            _guild_ctx(ctx), category.name, category.id, len(names), names,
        )

        t0 = time.perf_counter()
        created = []
        for name in names:
            ch = await ctx.guild.create_text_channel(name, category=category)
            created.append(ch.mention)
            logger.debug("Created channel #%s (ID=%s) in category %r", ch.name, ch.id, category.name)
            await asyncio.sleep(0.5)

        elapsed = time.perf_counter() - t0
        logger.info(
            "bulkcreatechannels complete | category=%r created=%d elapsed=%.2fs | %s",
            category.name, len(created), elapsed, _guild_ctx(ctx),
        )
        database.log_bulk_task(
            "bulk_create_channels", str(ctx.author.id), ctx.guild.id,
            json.dumps({"category": str(category.id), "channels": names}),
        )
        await ctx.send(
            embed=build_embed(
                "✅ Channels Created",
                f"Created {len(created)} channels in **{category.name}**:\n" + "\n".join(created),
                discord.Color.green(),
            )
        )

    # -------------------------------------------------------------------------
    # Bulk Delete Channels
    # -------------------------------------------------------------------------
    @commands.command(name="bulkdeletechannels")
    @has_admin_role()
    async def bulk_delete_channels(self, ctx: commands.Context, category: discord.CategoryChannel):
        """
        Delete all channels in a category (not the category itself).
        Usage: !bulkdeletechannels "Category Name"
        """
        channels = category.channels
        logger.info(
            "bulkdeletechannels requested | %s | category=%r (ID=%s) channels=%d",
            _guild_ctx(ctx), category.name, category.id, len(channels),
        )

        if not channels:
            logger.info("bulkdeletechannels — nothing to do, category %r is empty", category.name)
            await ctx.send(embed=build_embed("Nothing to do", "No channels in that category.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Channel Delete",
                f"Delete **{len(channels)}** channels in **{category.name}**? This cannot be undone.\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.red(),
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            logger.info("bulkdeletechannels timed out waiting for confirmation | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            logger.info("bulkdeletechannels cancelled by user | %s", _guild_ctx(ctx))
            await confirm_msg.edit(embed=build_embed("Cancelled", "Channel deletion cancelled.", discord.Color.greyple()))
            return

        deleted_names = [c.name for c in channels]
        logger.info(
            "bulkdeletechannels confirmed — deleting %d channels from %r | %s",
            len(channels), category.name, _guild_ctx(ctx),
        )
        t0 = time.perf_counter()

        for channel in channels:
            await channel.delete(reason=f"Bulk delete by {ctx.author}")
            logger.debug("Deleted channel #%s from category %r", channel.name, category.name)
            await asyncio.sleep(0.5)

        elapsed = time.perf_counter() - t0
        logger.info(
            "bulkdeletechannels complete | category=%r deleted=%d elapsed=%.2fs | %s",
            category.name, len(deleted_names), elapsed, _guild_ctx(ctx),
        )
        database.log_bulk_task(
            "bulk_delete_channels", str(ctx.author.id), ctx.guild.id,
            json.dumps({"category": str(category.id), "deleted": deleted_names}),
        )
        await ctx.send(
            embed=build_embed(
                "✅ Channels Deleted",
                f"Deleted **{len(deleted_names)}** channels from **{category.name}**.",
                discord.Color.green(),
            )
        )

    # -------------------------------------------------------------------------
    # Audit Log
    # -------------------------------------------------------------------------
    @commands.command(name="tasklogs")
    @has_admin_role()
    async def task_logs(self, ctx: commands.Context, limit: int = 10):
        """Show recent bulk task log entries."""
        logger.debug("tasklogs requested | %s | limit=%d", _guild_ctx(ctx), limit)
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT task_type, performed_by, details, performed_at FROM bulk_task_log WHERE guild_id = ? ORDER BY performed_at DESC LIMIT ?",
                (ctx.guild.id, limit),
            ).fetchall()

        if not rows:
            await ctx.send(embed=build_embed("Task Logs", "No tasks logged yet.", discord.Color.greyple()))
            return

        lines = [
            f"**{r['task_type']}** by <@{r['performed_by']}> at `{r['performed_at'][:19]}`"
            for r in rows
        ]
        await ctx.send(embed=build_embed("📋 Recent Bulk Tasks", "\n".join(lines), discord.Color.blue()))


async def setup(bot):
    await bot.add_cog(BulkTasks(bot))
