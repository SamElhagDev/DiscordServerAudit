import discord
from discord.ext import commands
import json
import asyncio
import datetime

import database
from utils.permissions import has_admin_role, build_embed


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
        Usage: /bulkdelete #channel 50
        Max 100 per invocation (Discord API limit for bulk delete).
        """
        limit = min(limit, 100)
        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Delete",
                f"Delete the last **{limit}** messages in {channel.mention}?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange()
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(embed=build_embed("Cancelled", "Bulk delete cancelled.", discord.Color.greyple()))
            return

        deleted = await channel.purge(limit=limit)
        database.log_bulk_task(
            "bulk_delete", str(ctx.author.id), ctx.guild.id,
            json.dumps({"channel": str(channel.id), "requested": limit, "deleted": len(deleted)})
        )
        await ctx.send(
            embed=build_embed("✅ Bulk Delete Complete", f"Deleted **{len(deleted)}** messages in {channel.mention}.", discord.Color.green()),
            delete_after=10
        )

    # -------------------------------------------------------------------------
    # Prune Inactive Members
    # -------------------------------------------------------------------------
    @commands.command(name="prunembers")
    @has_admin_role()
    async def prune_members(self, ctx: commands.Context, days: int = 30):
        """
        Prune members with no roles who have been inactive for N days (default 30).
        Usage: /prunembers 30
        """
        days = max(1, min(days, 30))  # Discord API clamps at 30
        count = await ctx.guild.estimate_pruned_members(days=days)
        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Member Prune",
                f"This will kick approximately **{count}** members inactive for **{days}+ days** with no roles.\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange()
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(embed=build_embed("Cancelled", "Prune cancelled.", discord.Color.greyple()))
            return

        pruned = await ctx.guild.prune_members(days=days, reason=f"Bulk prune by {ctx.author}")
        database.log_bulk_task(
            "prune_members", str(ctx.author.id), ctx.guild.id,
            json.dumps({"days": days, "pruned": pruned})
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
        Usage: /bulkroleadd @RoleName
        """
        targets = [m for m in ctx.guild.members if role not in m.roles and not m.bot]
        if not targets:
            await ctx.send(embed=build_embed("Nothing to do", f"All members already have **{role.name}**.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Role Add",
                f"Add **{role.name}** to **{len(targets)}** members?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange()
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(embed=build_embed("Cancelled", "Role assignment cancelled.", discord.Color.greyple()))
            return

        progress = await ctx.send(f"Assigning role... 0/{len(targets)}")
        failed = 0
        for i, member in enumerate(targets):
            try:
                await member.add_roles(role, reason=f"Bulk role add by {ctx.author}")
            except discord.HTTPException:
                failed += 1
            if i % 10 == 0:
                await progress.edit(content=f"Assigning role... {i+1}/{len(targets)}")
            await asyncio.sleep(0.3)  # Rate limit safety

        database.log_bulk_task(
            "bulk_role_add", str(ctx.author.id), ctx.guild.id,
            json.dumps({"role": str(role.id), "targets": len(targets), "failed": failed})
        )
        await progress.delete()
        await ctx.send(
            embed=build_embed(
                "✅ Bulk Role Add Complete",
                f"Assigned **{role.name}** to **{len(targets) - failed}** members. ({failed} failed)",
                discord.Color.green()
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
        Usage: /bulkroleremove @RoleName
        """
        targets = [m for m in ctx.guild.members if role in m.roles]
        if not targets:
            await ctx.send(embed=build_embed("Nothing to do", f"No members have **{role.name}**.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Role Remove",
                f"Remove **{role.name}** from **{len(targets)}** members?\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.orange()
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(embed=build_embed("Cancelled", "Role removal cancelled.", discord.Color.greyple()))
            return

        failed = 0
        for member in targets:
            try:
                await member.remove_roles(role, reason=f"Bulk role remove by {ctx.author}")
            except discord.HTTPException:
                failed += 1
            await asyncio.sleep(0.3)

        database.log_bulk_task(
            "bulk_role_remove", str(ctx.author.id), ctx.guild.id,
            json.dumps({"role": str(role.id), "targets": len(targets), "failed": failed})
        )
        await ctx.send(
            embed=build_embed(
                "✅ Bulk Role Remove Complete",
                f"Removed **{role.name}** from **{len(targets) - failed}** members. ({failed} failed)",
                discord.Color.green()
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
        Usage: /bulkcreatechannels "Category Name" channel-one channel-two channel-three
        Channel names should be space-separated.
        """
        names = channel_names.split()
        created = []
        for name in names:
            ch = await ctx.guild.create_text_channel(name, category=category)
            created.append(ch.mention)
            await asyncio.sleep(0.5)

        database.log_bulk_task(
            "bulk_create_channels", str(ctx.author.id), ctx.guild.id,
            json.dumps({"category": str(category.id), "channels": names})
        )
        await ctx.send(
            embed=build_embed(
                "✅ Channels Created",
                f"Created {len(created)} channels in **{category.name}**:\n" + "\n".join(created),
                discord.Color.green()
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
        Usage: /bulkdeletechannels "Category Name"
        """
        channels = category.channels
        if not channels:
            await ctx.send(embed=build_embed("Nothing to do", "No channels in that category.", discord.Color.greyple()))
            return

        confirm_msg = await ctx.send(
            embed=build_embed(
                "⚠️ Confirm Bulk Channel Delete",
                f"Delete **{len(channels)}** channels in **{category.name}**? This cannot be undone.\nReact ✅ to confirm or ❌ to cancel.",
                discord.Color.red()
            )
        )
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await confirm_msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
            return

        if str(reaction.emoji) == "❌":
            await confirm_msg.edit(embed=build_embed("Cancelled", "Channel deletion cancelled.", discord.Color.greyple()))
            return

        deleted_names = [c.name for c in channels]
        for channel in channels:
            await channel.delete(reason=f"Bulk delete by {ctx.author}")
            await asyncio.sleep(0.5)

        database.log_bulk_task(
            "bulk_delete_channels", str(ctx.author.id), ctx.guild.id,
            json.dumps({"category": str(category.id), "deleted": deleted_names})
        )
        await ctx.send(
            embed=build_embed(
                "✅ Channels Deleted",
                f"Deleted **{len(deleted_names)}** channels from **{category.name}**.",
                discord.Color.green()
            )
        )

    # -------------------------------------------------------------------------
    # Audit Log
    # -------------------------------------------------------------------------
    @commands.command(name="tasklogs")
    @has_admin_role()
    async def task_logs(self, ctx: commands.Context, limit: int = 10):
        """Show recent bulk task log entries."""
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT task_type, performed_by, details, performed_at FROM bulk_task_log WHERE guild_id = ? ORDER BY performed_at DESC LIMIT ?",
                (ctx.guild.id, limit)
            ).fetchall()

        if not rows:
            await ctx.send(embed=build_embed("Task Logs", "No tasks logged yet.", discord.Color.greyple()))
            return

        lines = []
        for r in rows:
            lines.append(f"**{r['task_type']}** by <@{r['performed_by']}> at `{r['performed_at'][:19]}`")

        await ctx.send(embed=build_embed("📋 Recent Bulk Tasks", "\n".join(lines), discord.Color.blue()))


async def setup(bot):
    await bot.add_cog(BulkTasks(bot))
