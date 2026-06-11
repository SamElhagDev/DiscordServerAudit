import datetime
import logging

import discord
from discord.ext import commands

from utils.permissions import has_admin_role, build_embed

logger = logging.getLogger(__name__)


class Info(commands.Cog):
    """Read-only inspection and listing commands. All restricted to admin role."""

    def __init__(self, bot):
        self.bot = bot

    # -------------------------------------------------------------------------
    # channelinfo — details about a channel
    # -------------------------------------------------------------------------
    @commands.command(name="channelinfo")
    @has_admin_role()
    async def channel_info(self, ctx: commands.Context, channel: discord.abc.GuildChannel):
        """
        Show details about a channel.
        Usage: !channelinfo #channel
        """
        age = (datetime.datetime.now(datetime.timezone.utc) - channel.created_at).days
        embed = discord.Embed(title=f"Channel: {channel.name}", color=discord.Color.blue())
        embed.add_field(name="ID", value=str(channel.id), inline=True)
        embed.add_field(name="Type", value=str(channel.type), inline=True)
        embed.add_field(name="Category", value=channel.category.name if channel.category else "None", inline=True)
        embed.add_field(name="Position", value=str(channel.position), inline=True)
        embed.add_field(name="Created", value=f"{age}d ago", inline=True)
        if isinstance(channel, discord.TextChannel):
            embed.add_field(name="Slowmode", value=f"{channel.slowmode_delay}s", inline=True)
            embed.add_field(name="NSFW", value=str(channel.is_nsfw()), inline=True)
            embed.add_field(name="Topic", value=channel.topic or "None", inline=False)
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # listchannels — all channels grouped by category
    # -------------------------------------------------------------------------
    @commands.command(name="listchannels")
    @has_admin_role()
    async def list_channels(self, ctx: commands.Context):
        """
        List all channels grouped by category.
        Usage: !listchannels
        """
        lines = []
        for category, channels in ctx.guild.by_category():
            cat_name = category.name if category else "No Category"
            ch_names = " ".join(("🔊" if isinstance(c, discord.VoiceChannel) else "#") + c.name for c in channels)
            lines.append(f"**{cat_name}**\n{ch_names}")
        text = "\n\n".join(lines)
        await ctx.send(embed=build_embed("📋 Channels", text[:4000] or "No channels.", discord.Color.blue()))

    # -------------------------------------------------------------------------
    # listroles — all roles with member counts
    # -------------------------------------------------------------------------
    @commands.command(name="listroles")
    @has_admin_role()
    async def list_roles(self, ctx: commands.Context):
        """
        List all roles with their member counts (highest first).
        Usage: !listroles
        """
        roles = sorted([r for r in ctx.guild.roles if not r.is_default()], key=lambda r: -r.position)
        if not roles:
            await ctx.send(embed=build_embed("Roles", "No roles.", discord.Color.greyple()))
            return
        lines = [f"{r.mention} — {len(r.members)} member(s)" for r in roles[:40]]
        overflow = f"\n…and {len(roles) - 40} more" if len(roles) > 40 else ""
        await ctx.send(embed=build_embed(f"📋 Roles ({len(roles)})", "\n".join(lines) + overflow, discord.Color.blue()))

    # -------------------------------------------------------------------------
    # listbans — currently banned members
    # -------------------------------------------------------------------------
    @commands.command(name="listbans")
    @has_admin_role()
    async def list_bans(self, ctx: commands.Context):
        """
        List currently banned members.
        Usage: !listbans
        """
        bans = [e async for e in ctx.guild.bans()]
        if not bans:
            await ctx.send(embed=build_embed("Bans", "No members are currently banned.", discord.Color.green()))
            return
        lines = [f"**{e.user}** — {e.reason or 'No reason'}" for e in bans[:30]]
        overflow = f"\n…and {len(bans) - 30} more" if len(bans) > 30 else ""
        await ctx.send(embed=build_embed(f"🔨 Banned Members ({len(bans)})", "\n".join(lines) + overflow, discord.Color.red()))

    # -------------------------------------------------------------------------
    # listinvites — active invite links
    # -------------------------------------------------------------------------
    @commands.command(name="listinvites")
    @has_admin_role()
    async def list_invites(self, ctx: commands.Context):
        """
        List active invite links with creators and use counts.
        Usage: !listinvites
        """
        invites = await ctx.guild.invites()
        if not invites:
            await ctx.send(embed=build_embed("Invites", "No active invites.", discord.Color.greyple()))
            return
        lines = [f"{i.url} — by {i.inviter}, {i.uses} use(s)" for i in invites[:25]]
        await ctx.send(embed=build_embed(f"🔗 Active Invites ({len(invites)})", "\n".join(lines)[:4000], discord.Color.blue()))

    # -------------------------------------------------------------------------
    # listemojis — custom emojis
    # -------------------------------------------------------------------------
    @commands.command(name="listemojis")
    @has_admin_role()
    async def list_emojis(self, ctx: commands.Context):
        """
        List all custom emojis in the server.
        Usage: !listemojis
        """
        if not ctx.guild.emojis:
            await ctx.send(embed=build_embed("Emojis", "No custom emojis.", discord.Color.greyple()))
            return
        text = " ".join(str(e) for e in ctx.guild.emojis)
        await ctx.send(embed=build_embed(f"😀 Custom Emojis ({len(ctx.guild.emojis)})", text[:4000], discord.Color.blue()))

    # -------------------------------------------------------------------------
    # listadmins — members with Administrator
    # -------------------------------------------------------------------------
    @commands.command(name="listadmins")
    @has_admin_role()
    async def list_admins(self, ctx: commands.Context):
        """
        List members with Administrator permission — a quick privilege review.
        Usage: !listadmins
        """
        admins = [m for m in ctx.guild.members if m.guild_permissions.administrator and not m.bot]
        if not admins:
            await ctx.send(embed=build_embed("Administrators", "No members have Administrator.", discord.Color.greyple()))
            return
        lines = [f"{m.mention} ({m})" for m in admins[:30]]
        overflow = f"\n…and {len(admins) - 30} more" if len(admins) > 30 else ""
        await ctx.send(embed=build_embed(f"🛡️ Administrators ({len(admins)})", "\n".join(lines) + overflow, discord.Color.orange()))

    # -------------------------------------------------------------------------
    # avatar — show a member's avatar
    # -------------------------------------------------------------------------
    @commands.command(name="avatar")
    @has_admin_role()
    async def avatar(self, ctx: commands.Context, member: discord.Member):
        """
        Show a member's avatar at full size.
        Usage: !avatar @User
        """
        embed = discord.Embed(title=f"{member.display_name}'s avatar", color=member.color)
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # membercount — quick membership summary
    # -------------------------------------------------------------------------
    @commands.command(name="membercount")
    @has_admin_role()
    async def member_count(self, ctx: commands.Context):
        """
        Show membership counts (total, humans, bots, currently in voice).
        Usage: !membercount
        """
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        bots = sum(1 for m in g.members if m.bot)
        in_voice = sum(len(vc.members) for vc in g.voice_channels)
        embed = build_embed(
            "👥 Member Count",
            f"**Total:** {g.member_count}\n**Humans:** {humans}\n**Bots:** {bots}\n**In voice:** {in_voice}",
            discord.Color.blue(),
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Info(bot))
