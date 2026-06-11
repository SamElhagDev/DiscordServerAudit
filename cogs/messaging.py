import asyncio
import datetime
import json
import logging

import discord
from discord.ext import commands

import database
from utils.permissions import has_admin_role, build_embed

logger = logging.getLogger(__name__)


def _guild_ctx(ctx: commands.Context) -> str:
    guild = f"{ctx.guild.name!r} (ID={ctx.guild.id})" if ctx.guild else "DM"
    return f"user={ctx.author} (ID={ctx.author.id}) guild={guild}"


async def _confirm(ctx: commands.Context, embed: discord.Embed) -> bool:
    """Post a reaction-confirm prompt. Returns True if confirmed, False otherwise."""
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id

    try:
        reaction, _ = await ctx.bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await msg.edit(embed=build_embed("Cancelled", "Timed out.", discord.Color.greyple()))
        return False

    if str(reaction.emoji) == "❌":
        await msg.edit(embed=build_embed("Cancelled", "Action cancelled.", discord.Color.greyple()))
        return False

    return True


class Messaging(commands.Cog):
    """Messaging, moderation, and channel action commands. All restricted to admin role."""

    def __init__(self, bot):
        self.bot = bot

    # -------------------------------------------------------------------------
    # say — send a plain text message to a channel
    # -------------------------------------------------------------------------
    @commands.command(name="say")
    @has_admin_role()
    async def say(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        """
        Send a plain message to a channel as the bot.
        Usage: !say #channel Your message here
        """
        await channel.send(message)
        logger.info("say | %s | channel=#%s msg=%r", _guild_ctx(ctx), channel.name, message[:80])
        database.log_bulk_task("say", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "message": message[:200]}))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # sayembed — send a custom embed to a channel
    # -------------------------------------------------------------------------
    @commands.command(name="sayembed")
    @has_admin_role()
    async def say_embed(self, ctx: commands.Context, channel: discord.TextChannel, title: str, *, description: str):
        """
        Send a custom embed to a channel.
        Usage: !sayembed #channel "Title" Description text here
        """
        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
        await channel.send(embed=embed)
        logger.info("sayembed | %s | channel=#%s title=%r", _guild_ctx(ctx), channel.name, title)
        database.log_bulk_task("say_embed", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "title": title}))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # announce — formatted announcement with optional @everyone ping
    # -------------------------------------------------------------------------
    @commands.command(name="announce")
    @has_admin_role()
    async def announce(self, ctx: commands.Context, channel: discord.TextChannel, ping: bool, title: str, *, body: str):
        """
        Send a formatted announcement embed, optionally @everyone.
        Usage: !announce #channel true "Title" Body text here
        """
        embed = discord.Embed(
            title=f"📢  {title}",
            description=body,
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Announced by {ctx.author.display_name}")
        content = "@everyone" if ping else None
        await channel.send(content=content, embed=embed)
        logger.info("announce | %s | channel=#%s ping=%s title=%r", _guild_ctx(ctx), channel.name, ping, title)
        database.log_bulk_task("announce", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "ping": ping, "title": title}))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # timed — send a self-deleting message
    # -------------------------------------------------------------------------
    @commands.command(name="timed")
    @has_admin_role()
    async def timed_message(self, ctx: commands.Context, channel: discord.TextChannel, seconds: int, *, message: str):
        """
        Send a message that auto-deletes after N seconds.
        Usage: !timed #channel 30 This will disappear in 30 seconds
        """
        seconds = max(5, min(seconds, 3600))
        await channel.send(message, delete_after=seconds)
        logger.info("timed | %s | channel=#%s seconds=%d", _guild_ctx(ctx), channel.name, seconds)
        database.log_bulk_task("timed_message", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "seconds": seconds, "message": message[:200]}))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # dm — send a DM to a member
    # -------------------------------------------------------------------------
    @commands.command(name="dm")
    @has_admin_role()
    async def dm_member(self, ctx: commands.Context, member: discord.Member, *, message: str):
        """
        Send a direct message to a member.
        Usage: !dm @User Your message here
        """
        try:
            await member.send(message)
        except discord.Forbidden:
            await ctx.send(embed=build_embed("❌ DM Failed", f"Could not DM {member.mention} — they may have DMs disabled.", discord.Color.red()))
            return
        logger.info("dm | %s | target=%s (ID=%s)", _guild_ctx(ctx), member, member.id)
        database.log_bulk_task("dm", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "message": message[:200]}))
        await ctx.send(embed=build_embed("✅ DM Sent", f"Message delivered to {member.mention}.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # bulkdm — DM every member with a role
    # -------------------------------------------------------------------------
    @commands.command(name="bulkdm")
    @has_admin_role()
    async def bulk_dm(self, ctx: commands.Context, role: discord.Role, *, message: str):
        """
        DM every member that has a given role.
        Usage: !bulkdm @RoleName Your message here
        """
        targets = [m for m in role.members if not m.bot]
        if not targets:
            await ctx.send(embed=build_embed("Nothing to do", f"No non-bot members have **{role.name}**.", discord.Color.greyple()))
            return

        confirmed = await _confirm(ctx, build_embed(
            "⚠️ Confirm Bulk DM",
            f"Send a DM to **{len(targets)}** members with **{role.name}**?\nReact ✅ to confirm or ❌ to cancel.",
            discord.Color.orange(),
        ))
        if not confirmed:
            return

        progress = await ctx.send(f"Sending DMs... 0/{len(targets)}")
        sent = failed = 0
        for i, member in enumerate(targets):
            try:
                await member.send(message)
                sent += 1
            except discord.Forbidden:
                failed += 1
                logger.debug("bulkdm skipped %s (DMs closed)", member)
            if i % 5 == 0:
                await progress.edit(content=f"Sending DMs... {i + 1}/{len(targets)}")
            await asyncio.sleep(0.5)

        logger.info("bulkdm complete | role=%r sent=%d failed=%d | %s", role.name, sent, failed, _guild_ctx(ctx))
        database.log_bulk_task("bulk_dm", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"role": str(role.id), "sent": sent, "failed": failed}))
        await progress.delete()
        await ctx.send(embed=build_embed(
            "✅ Bulk DM Complete",
            f"Sent to **{sent}** members. **{failed}** skipped (DMs closed).",
            discord.Color.green(),
        ))

    # -------------------------------------------------------------------------
    # react — add a reaction to a message
    # -------------------------------------------------------------------------
    @commands.command(name="react")
    @has_admin_role()
    async def react(self, ctx: commands.Context, channel: discord.TextChannel, message_id: int, emoji: str):
        """
        Add a reaction to a message.
        Usage: !react #channel 123456789 👍
        """
        try:
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Found", "Message not found.", discord.Color.red()))
            return
        except discord.HTTPException as e:
            await ctx.send(embed=build_embed("❌ Failed", f"Could not add reaction: {e}", discord.Color.red()))
            return
        logger.info("react | %s | channel=#%s msg=%d emoji=%s", _guild_ctx(ctx), channel.name, message_id, emoji)
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # clearreactions — remove all reactions from a message
    # -------------------------------------------------------------------------
    @commands.command(name="clearreactions")
    @has_admin_role()
    async def clear_reactions(self, ctx: commands.Context, channel: discord.TextChannel, message_id: int):
        """
        Remove all reactions from a message.
        Usage: !clearreactions #channel 123456789
        """
        try:
            msg = await channel.fetch_message(message_id)
            await msg.clear_reactions()
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Found", "Message not found.", discord.Color.red()))
            return
        logger.info("clearreactions | %s | channel=#%s msg=%d", _guild_ctx(ctx), channel.name, message_id)
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # pin — pin a message by ID
    # -------------------------------------------------------------------------
    @commands.command(name="pin")
    @has_admin_role()
    async def pin_message(self, ctx: commands.Context, channel: discord.TextChannel, message_id: int):
        """
        Pin a message in a channel.
        Usage: !pin #channel 123456789
        """
        try:
            msg = await channel.fetch_message(message_id)
            await msg.pin(reason=f"Pinned by {ctx.author}")
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Found", "Message not found.", discord.Color.red()))
            return
        logger.info("pin | %s | channel=#%s msg=%d", _guild_ctx(ctx), channel.name, message_id)
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # unpin — unpin a message by ID
    # -------------------------------------------------------------------------
    @commands.command(name="unpin")
    @has_admin_role()
    async def unpin_message(self, ctx: commands.Context, channel: discord.TextChannel, message_id: int):
        """
        Unpin a message in a channel.
        Usage: !unpin #channel 123456789
        """
        try:
            msg = await channel.fetch_message(message_id)
            await msg.unpin(reason=f"Unpinned by {ctx.author}")
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Found", "Message not found.", discord.Color.red()))
            return
        logger.info("unpin | %s | channel=#%s msg=%d", _guild_ctx(ctx), channel.name, message_id)
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # edit — edit a bot-authored message
    # -------------------------------------------------------------------------
    @commands.command(name="editmsg")
    @has_admin_role()
    async def edit_message(self, ctx: commands.Context, channel: discord.TextChannel, message_id: int, *, new_content: str):
        """
        Edit the content of a message the bot sent.
        Usage: !editmsg #channel 123456789 New message content
        """
        try:
            msg = await channel.fetch_message(message_id)
            if msg.author.id != self.bot.user.id:
                await ctx.send(embed=build_embed("❌ Forbidden", "I can only edit my own messages.", discord.Color.red()))
                return
            await msg.edit(content=new_content)
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Found", "Message not found.", discord.Color.red()))
            return
        logger.info("editmsg | %s | channel=#%s msg=%d", _guild_ctx(ctx), channel.name, message_id)
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # slowmode — set slowmode on a channel
    # -------------------------------------------------------------------------
    @commands.command(name="slowmode")
    @has_admin_role()
    async def slowmode(self, ctx: commands.Context, channel: discord.TextChannel, seconds: int):
        """
        Set slowmode on a channel (0 to disable, max 21600).
        Usage: !slowmode #channel 10
        """
        seconds = max(0, min(seconds, 21600))
        await channel.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author}")
        status = f"{seconds}s" if seconds else "disabled"
        logger.info("slowmode | %s | channel=#%s delay=%d", _guild_ctx(ctx), channel.name, seconds)
        database.log_bulk_task("slowmode", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "seconds": seconds}))
        await ctx.send(embed=build_embed("✅ Slowmode Updated", f"Slowmode in {channel.mention}: **{status}**.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # lock — deny @everyone from sending messages in a channel
    # -------------------------------------------------------------------------
    @commands.command(name="lock")
    @has_admin_role()
    async def lock_channel(self, ctx: commands.Context, channel: discord.TextChannel, *, reason: str = "No reason given"):
        """
        Lock a channel so @everyone cannot send messages.
        Usage: !lock #channel Optional reason here
        """
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Locked by {ctx.author}: {reason}")
        logger.info("lock | %s | channel=#%s reason=%r", _guild_ctx(ctx), channel.name, reason)
        database.log_bulk_task("lock_channel", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "reason": reason}))
        await channel.send(embed=build_embed("🔒 Channel Locked", f"This channel has been locked. Reason: {reason}", discord.Color.red()))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # unlock — restore @everyone send permissions
    # -------------------------------------------------------------------------
    @commands.command(name="unlock")
    @has_admin_role()
    async def unlock_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Unlock a channel, restoring @everyone send permissions.
        Usage: !unlock #channel
        """
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author}")
        logger.info("unlock | %s | channel=#%s", _guild_ctx(ctx), channel.name)
        database.log_bulk_task("unlock_channel", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id)}))
        await channel.send(embed=build_embed("🔓 Channel Unlocked", "This channel has been unlocked.", discord.Color.green()))
        await ctx.message.add_reaction("✅")

    # -------------------------------------------------------------------------
    # rename — rename a channel
    # -------------------------------------------------------------------------
    @commands.command(name="rename")
    @has_admin_role()
    async def rename_channel(self, ctx: commands.Context, channel: discord.TextChannel, *, new_name: str):
        """
        Rename a text channel.
        Usage: !rename #channel new-channel-name
        """
        old_name = channel.name
        await channel.edit(name=new_name, reason=f"Renamed by {ctx.author}")
        logger.info("rename | %s | #%s → #%s", _guild_ctx(ctx), old_name, new_name)
        database.log_bulk_task("rename_channel", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "old": old_name, "new": new_name}))
        await ctx.send(embed=build_embed("✅ Channel Renamed", f"**#{old_name}** → **#{new_name}**", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # topic — set a channel topic
    # -------------------------------------------------------------------------
    @commands.command(name="topic")
    @has_admin_role()
    async def set_topic(self, ctx: commands.Context, channel: discord.TextChannel, *, topic: str):
        """
        Set the topic of a text channel.
        Usage: !topic #channel New topic text here
        """
        await channel.edit(topic=topic, reason=f"Topic set by {ctx.author}")
        logger.info("topic | %s | channel=#%s topic=%r", _guild_ctx(ctx), channel.name, topic[:80])
        database.log_bulk_task("set_topic", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "topic": topic[:500]}))
        await ctx.send(embed=build_embed("✅ Topic Updated", f"{channel.mention}: {topic}", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # nsfw — toggle NSFW on a channel
    # -------------------------------------------------------------------------
    @commands.command(name="nsfw")
    @has_admin_role()
    async def toggle_nsfw(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Toggle NSFW flag on a channel.
        Usage: !nsfw #channel
        """
        new_state = not channel.is_nsfw()
        await channel.edit(nsfw=new_state, reason=f"NSFW toggled by {ctx.author}")
        state_str = "enabled" if new_state else "disabled"
        logger.info("nsfw | %s | channel=#%s nsfw=%s", _guild_ctx(ctx), channel.name, new_state)
        database.log_bulk_task("toggle_nsfw", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "nsfw": new_state}))
        await ctx.send(embed=build_embed("✅ NSFW Updated", f"NSFW {state_str} in {channel.mention}.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # movechannel — move a channel to a different category
    # -------------------------------------------------------------------------
    @commands.command(name="movechannel")
    @has_admin_role()
    async def move_channel(self, ctx: commands.Context, channel: discord.TextChannel, category: discord.CategoryChannel):
        """
        Move a text channel to a different category.
        Usage: !movechannel #channel "Category Name"
        """
        old_cat = channel.category.name if channel.category else "none"
        await channel.edit(category=category, reason=f"Moved by {ctx.author}")
        logger.info("movechannel | %s | #%s: %r → %r", _guild_ctx(ctx), channel.name, old_cat, category.name)
        database.log_bulk_task("move_channel", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"channel": str(channel.id), "old_category": old_cat, "new_category": category.name}))
        await ctx.send(embed=build_embed("✅ Channel Moved", f"{channel.mention} moved to **{category.name}**.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # nick — set a member's nickname
    # -------------------------------------------------------------------------
    @commands.command(name="nick")
    @has_admin_role()
    async def set_nick(self, ctx: commands.Context, member: discord.Member, *, nickname: str):
        """
        Set a member's server nickname.
        Usage: !nick @User New Nickname
        """
        old = member.display_name
        await member.edit(nick=nickname, reason=f"Nick set by {ctx.author}")
        logger.info("nick | %s | target=%s old=%r new=%r", _guild_ctx(ctx), member, old, nickname)
        database.log_bulk_task("set_nick", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "old": old, "new": nickname}))
        await ctx.send(embed=build_embed("✅ Nickname Set", f"{member.mention}: **{old}** → **{nickname}**", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # clearnick — reset a member's nickname
    # -------------------------------------------------------------------------
    @commands.command(name="clearnick")
    @has_admin_role()
    async def clear_nick(self, ctx: commands.Context, member: discord.Member):
        """
        Clear a member's server nickname, reverting to their username.
        Usage: !clearnick @User
        """
        old = member.display_name
        await member.edit(nick=None, reason=f"Nick cleared by {ctx.author}")
        logger.info("clearnick | %s | target=%s old=%r", _guild_ctx(ctx), member, old)
        database.log_bulk_task("clear_nick", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "old": old}))
        await ctx.send(embed=build_embed("✅ Nickname Cleared", f"{member.mention}'s nickname reset to **{member.name}**.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # timeout — mute a member for N minutes
    # -------------------------------------------------------------------------
    @commands.command(name="timeout")
    @has_admin_role()
    async def timeout_member(self, ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str = "No reason given"):
        """
        Timeout (mute) a member for N minutes (max 40320 = 28 days).
        Usage: !timeout @User 30 Spamming
        """
        minutes = max(1, min(minutes, 40320))
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=f"Timeout by {ctx.author}: {reason}")
        logger.info("timeout | %s | target=%s minutes=%d reason=%r", _guild_ctx(ctx), member, minutes, reason)
        database.log_bulk_task("timeout", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "minutes": minutes, "reason": reason}))
        await ctx.send(embed=build_embed(
            "🔇 Member Timed Out",
            f"{member.mention} timed out for **{minutes} min**.\nReason: {reason}",
            discord.Color.orange(),
        ))

    # -------------------------------------------------------------------------
    # untimeout — remove a member's timeout
    # -------------------------------------------------------------------------
    @commands.command(name="untimeout")
    @has_admin_role()
    async def untimeout_member(self, ctx: commands.Context, member: discord.Member):
        """
        Remove a timeout from a member.
        Usage: !untimeout @User
        """
        await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
        logger.info("untimeout | %s | target=%s", _guild_ctx(ctx), member)
        database.log_bulk_task("untimeout", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id)}))
        await ctx.send(embed=build_embed("✅ Timeout Removed", f"{member.mention} can speak again.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # move — move a member to a different voice channel
    # -------------------------------------------------------------------------
    @commands.command(name="movemember")
    @has_admin_role()
    async def move_member(self, ctx: commands.Context, member: discord.Member, channel: discord.VoiceChannel):
        """
        Move a member to a different voice channel.
        Usage: !movemember @User "Voice Channel Name"
        """
        if not member.voice:
            await ctx.send(embed=build_embed("❌ Not in Voice", f"{member.mention} is not in a voice channel.", discord.Color.red()))
            return
        old_ch = member.voice.channel.name
        await member.move_to(channel, reason=f"Moved by {ctx.author}")
        logger.info("movemember | %s | target=%s %r → %r", _guild_ctx(ctx), member, old_ch, channel.name)
        database.log_bulk_task("move_member", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "from": old_ch, "to": channel.name}))
        await ctx.send(embed=build_embed("✅ Member Moved", f"{member.mention}: **{old_ch}** → **{channel.name}**", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # vcmute / vcunmute — server-mute a member in voice
    # -------------------------------------------------------------------------
    @commands.command(name="vcmute")
    @has_admin_role()
    async def vc_mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """
        Server-mute a member in their current voice channel.
        Usage: !vcmute @User Disruptive
        """
        if not member.voice:
            await ctx.send(embed=build_embed("❌ Not in Voice", f"{member.mention} is not in a voice channel.", discord.Color.red()))
            return
        await member.edit(mute=True, reason=f"VC mute by {ctx.author}: {reason}")
        logger.info("vcmute | %s | target=%s reason=%r", _guild_ctx(ctx), member, reason)
        database.log_bulk_task("vc_mute", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "reason": reason}))
        await ctx.send(embed=build_embed("🔇 VC Muted", f"{member.mention} server-muted. Reason: {reason}", discord.Color.orange()), delete_after=10)

    @commands.command(name="vcunmute")
    @has_admin_role()
    async def vc_unmute(self, ctx: commands.Context, member: discord.Member):
        """
        Remove a server-mute from a member in voice.
        Usage: !vcunmute @User
        """
        await member.edit(mute=False, reason=f"VC unmute by {ctx.author}")
        logger.info("vcunmute | %s | target=%s", _guild_ctx(ctx), member)
        database.log_bulk_task("vc_unmute", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id)}))
        await ctx.send(embed=build_embed("✅ VC Unmuted", f"{member.mention} can speak in voice again.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # vcdeafen / vcundeafen — server-deafen in voice
    # -------------------------------------------------------------------------
    @commands.command(name="vcdeafen")
    @has_admin_role()
    async def vc_deafen(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """
        Server-deafen a member in their current voice channel.
        Usage: !vcdeafen @User Reason
        """
        if not member.voice:
            await ctx.send(embed=build_embed("❌ Not in Voice", f"{member.mention} is not in a voice channel.", discord.Color.red()))
            return
        await member.edit(deafen=True, reason=f"VC deafen by {ctx.author}: {reason}")
        logger.info("vcdeafen | %s | target=%s reason=%r", _guild_ctx(ctx), member, reason)
        database.log_bulk_task("vc_deafen", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "reason": reason}))
        await ctx.send(embed=build_embed("🔇 VC Deafened", f"{member.mention} server-deafened. Reason: {reason}", discord.Color.orange()), delete_after=10)

    @commands.command(name="vcundeafen")
    @has_admin_role()
    async def vc_undeafen(self, ctx: commands.Context, member: discord.Member):
        """
        Remove a server-deafen from a member in voice.
        Usage: !vcundeafen @User
        """
        await member.edit(deafen=False, reason=f"VC undeafen by {ctx.author}")
        logger.info("vcundeafen | %s | target=%s", _guild_ctx(ctx), member)
        database.log_bulk_task("vc_undeafen", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id)}))
        await ctx.send(embed=build_embed("✅ VC Undeafened", f"{member.mention} can hear voice again.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # disconnect — kick a member from their voice channel
    # -------------------------------------------------------------------------
    @commands.command(name="vcdisconnect")
    @has_admin_role()
    async def vc_disconnect(self, ctx: commands.Context, member: discord.Member):
        """
        Disconnect a member from their current voice channel.
        Usage: !vcdisconnect @User
        """
        if not member.voice:
            await ctx.send(embed=build_embed("❌ Not in Voice", f"{member.mention} is not in a voice channel.", discord.Color.red()))
            return
        ch = member.voice.channel.name
        await member.move_to(None, reason=f"VC disconnect by {ctx.author}")
        logger.info("vcdisconnect | %s | target=%s from=%r", _guild_ctx(ctx), member, ch)
        database.log_bulk_task("vc_disconnect", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "from": ch}))
        await ctx.send(embed=build_embed("✅ Disconnected", f"{member.mention} removed from **{ch}**.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # roleinfo — show details about a role
    # -------------------------------------------------------------------------
    @commands.command(name="roleinfo")
    @has_admin_role()
    async def role_info(self, ctx: commands.Context, role: discord.Role):
        """
        Show details about a role: colour, permissions, member count.
        Usage: !roleinfo @RoleName
        """
        perms = [name for name, value in role.permissions if value]
        embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
        embed.add_field(name="ID", value=str(role.id), inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Colour", value=str(role.color), inline=True)
        embed.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
        embed.add_field(name="Hoisted", value=str(role.hoist), inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Permissions", value=", ".join(perms) if perms else "None", inline=False)
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # userinfo — show details about a member
    # -------------------------------------------------------------------------
    @commands.command(name="userinfo")
    @has_admin_role()
    async def user_info(self, ctx: commands.Context, member: discord.Member):
        """
        Show detailed info about a member.
        Usage: !userinfo @User
        """
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        embed = discord.Embed(title=f"User Info: {member.display_name}", color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Username", value=str(member), inline=True)
        embed.add_field(name="Bot", value=str(member.bot), inline=True)
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "N/A", inline=True)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        timed_out = discord.utils.format_dt(member.timed_out_until, "R") if member.timed_out_until and member.timed_out_until > discord.utils.utcnow() else "No"
        embed.add_field(name="Timed Out Until", value=timed_out, inline=True)
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # serverinfo — show a summary of the server
    # -------------------------------------------------------------------------
    @commands.command(name="serverinfo")
    @has_admin_role()
    async def server_info(self, ctx: commands.Context):
        """
        Show a summary of the current server.
        Usage: !serverinfo
        """
        g = ctx.guild
        embed = discord.Embed(title=g.name, color=discord.Color.blurple())
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=g.owner.mention if g.owner else "N/A", inline=True)
        embed.add_field(name="Members", value=str(g.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Emojis", value=str(len(g.emojis)), inline=True)
        embed.add_field(name="Boosts", value=str(g.premium_subscription_count), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"), inline=True)
        embed.add_field(name="Verification Level", value=str(g.verification_level).title(), inline=True)
        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # kick — remove a member (they can rejoin)
    # -------------------------------------------------------------------------
    @commands.command(name="kick")
    @has_admin_role()
    async def kick_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """
        Kick a member from the server. They can rejoin with a new invite.
        Usage: !kick @User Reason here
        """
        await member.kick(reason=f"Kicked by {ctx.author}: {reason}")
        logger.info("kick | %s | target=%s reason=%r", _guild_ctx(ctx), member, reason)
        database.log_bulk_task("kick", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "reason": reason}))
        await ctx.send(embed=build_embed("👢 Member Kicked", f"{member.mention} was kicked. Reason: {reason}", discord.Color.orange()))

    # -------------------------------------------------------------------------
    # ban — permanently ban a member (with confirmation)
    # -------------------------------------------------------------------------
    @commands.command(name="ban")
    @has_admin_role()
    async def ban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """
        Ban a member from the server (requires confirmation).
        Usage: !ban @User Reason here
        """
        confirmed = await _confirm(ctx, build_embed(
            "⚠️ Confirm Ban",
            f"Ban {member.mention}? They cannot rejoin until manually unbanned.\nReason: {reason}",
            discord.Color.red(),
        ))
        if not confirmed:
            return
        await member.ban(reason=f"Banned by {ctx.author}: {reason}", delete_message_seconds=0)
        logger.info("ban | %s | target=%s reason=%r", _guild_ctx(ctx), member, reason)
        database.log_bulk_task("ban", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "reason": reason}))
        await ctx.send(embed=build_embed("🔨 Member Banned", f"{member.mention} was banned. Reason: {reason}", discord.Color.red()))

    # -------------------------------------------------------------------------
    # unban — lift a ban by user ID or name
    # -------------------------------------------------------------------------
    @commands.command(name="unban")
    @has_admin_role()
    async def unban_user(self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason given"):
        """
        Unban a previously banned user by ID or username.
        Usage: !unban 123456789012345678
        """
        try:
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}: {reason}")
        except discord.NotFound:
            await ctx.send(embed=build_embed("❌ Not Banned", f"{user} is not currently banned.", discord.Color.red()))
            return
        logger.info("unban | %s | target=%s", _guild_ctx(ctx), user)
        database.log_bulk_task("unban", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(user.id)}))
        await ctx.send(embed=build_embed("✅ Member Unbanned", f"{user.mention} was unbanned.", discord.Color.green()))

    # -------------------------------------------------------------------------
    # softban — ban+unban to purge a member's recent messages
    # -------------------------------------------------------------------------
    @commands.command(name="softban")
    @has_admin_role()
    async def softban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason given"):
        """
        Ban then immediately unban a member to delete their recent messages.
        Usage: !softban @User Reason here
        """
        await member.ban(reason=f"Softban by {ctx.author}: {reason}", delete_message_seconds=86400)
        await ctx.guild.unban(member, reason="Softban — immediate unban")
        logger.info("softban | %s | target=%s reason=%r", _guild_ctx(ctx), member, reason)
        database.log_bulk_task("softban", str(ctx.author.id), ctx.guild.id,
                               json.dumps({"target": str(member.id), "reason": reason}))
        await ctx.send(embed=build_embed("🧹 Member Softbanned", f"{member.mention}'s recent messages were purged. They may rejoin. Reason: {reason}", discord.Color.orange()))

    # -------------------------------------------------------------------------
    # addrole / removerole — single-member role management
    # -------------------------------------------------------------------------
    @commands.command(name="addrole")
    @has_admin_role()
    async def add_role(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """
        Add a role to a single member.
        Usage: !addrole @User @Role
        """
        await member.add_roles(role, reason=f"Added by {ctx.author}")
        logger.info("addrole | %s | target=%s role=%r", _guild_ctx(ctx), member, role.name)
        await ctx.send(embed=build_embed("✅ Role Added", f"Added **{role.name}** to {member.mention}.", discord.Color.green()), delete_after=10)

    @commands.command(name="removerole")
    @has_admin_role()
    async def remove_role(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """
        Remove a role from a single member.
        Usage: !removerole @User @Role
        """
        await member.remove_roles(role, reason=f"Removed by {ctx.author}")
        logger.info("removerole | %s | target=%s role=%r", _guild_ctx(ctx), member, role.name)
        await ctx.send(embed=build_embed("✅ Role Removed", f"Removed **{role.name}** from {member.mention}.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # createrole / deleterole
    # -------------------------------------------------------------------------
    @commands.command(name="createrole")
    @has_admin_role()
    async def create_role(self, ctx: commands.Context, name: str, color: str = None):
        """
        Create a new role with an optional hex colour.
        Usage: !createrole "Role Name" #ff0000
        """
        kwargs = {"name": name, "reason": f"Created by {ctx.author}"}
        if color:
            try:
                kwargs["color"] = discord.Color(int(color.lstrip("#"), 16))
            except ValueError:
                pass
        role = await ctx.guild.create_role(**kwargs)
        logger.info("createrole | %s | role=%r", _guild_ctx(ctx), role.name)
        await ctx.send(embed=build_embed("✅ Role Created", f"Created {role.mention}.", discord.Color.green()), delete_after=10)

    @commands.command(name="deleterole")
    @has_admin_role()
    async def delete_role(self, ctx: commands.Context, role: discord.Role):
        """
        Delete a role from the server.
        Usage: !deleterole @Role
        """
        name = role.name
        await role.delete(reason=f"Deleted by {ctx.author}")
        logger.info("deleterole | %s | role=%r", _guild_ctx(ctx), name)
        await ctx.send(embed=build_embed("✅ Role Deleted", f"Deleted role **{name}**.", discord.Color.green()), delete_after=10)

    # -------------------------------------------------------------------------
    # createinvite — generate an invite link
    # -------------------------------------------------------------------------
    @commands.command(name="createinvite")
    @has_admin_role()
    async def create_invite(self, ctx: commands.Context, channel: discord.TextChannel, max_age_hours: int = 24, max_uses: int = 0):
        """
        Create an invite link for a channel (0 hours = never expires, 0 uses = unlimited).
        Usage: !createinvite #channel 24 0
        """
        invite = await channel.create_invite(
            max_age=max(0, max_age_hours) * 3600,
            max_uses=max(0, max_uses),
            reason=f"Created by {ctx.author}",
        )
        logger.info("createinvite | %s | channel=#%s", _guild_ctx(ctx), channel.name)
        await ctx.send(embed=build_embed("✅ Invite Created", invite.url, discord.Color.green()))

    # -------------------------------------------------------------------------
    # vcmuteall / vcunmuteall — bulk voice mute
    # -------------------------------------------------------------------------
    @commands.command(name="vcmuteall")
    @has_admin_role()
    async def vc_mute_all(self, ctx: commands.Context, channel: discord.VoiceChannel):
        """
        Server-mute everyone currently in a voice channel.
        Usage: !vcmuteall "Voice Channel Name"
        """
        count = 0
        for m in list(channel.members):
            try:
                await m.edit(mute=True, reason=f"Mute-all by {ctx.author}")
                count += 1
            except discord.HTTPException as e:
                logger.warning("vcmuteall failed for %s: %s", m, e)
            await asyncio.sleep(0.3)
        logger.info("vcmuteall | %s | vc=%r affected=%d", _guild_ctx(ctx), channel.name, count)
        await ctx.send(embed=build_embed("🔇 Voice Muted", f"Muted **{count}** member(s) in **{channel.name}**.", discord.Color.orange()))

    @commands.command(name="vcunmuteall")
    @has_admin_role()
    async def vc_unmute_all(self, ctx: commands.Context, channel: discord.VoiceChannel):
        """
        Remove server-mute from everyone in a voice channel.
        Usage: !vcunmuteall "Voice Channel Name"
        """
        count = 0
        for m in list(channel.members):
            try:
                await m.edit(mute=False, reason=f"Unmute-all by {ctx.author}")
                count += 1
            except discord.HTTPException as e:
                logger.warning("vcunmuteall failed for %s: %s", m, e)
            await asyncio.sleep(0.3)
        logger.info("vcunmuteall | %s | vc=%r affected=%d", _guild_ctx(ctx), channel.name, count)
        await ctx.send(embed=build_embed("🔊 Voice Unmuted", f"Unmuted **{count}** member(s) in **{channel.name}**.", discord.Color.green()))

    # -------------------------------------------------------------------------
    # vckickall — disconnect everyone from a voice channel
    # -------------------------------------------------------------------------
    @commands.command(name="vckickall")
    @has_admin_role()
    async def vc_kick_all(self, ctx: commands.Context, channel: discord.VoiceChannel):
        """
        Disconnect every member from a voice channel.
        Usage: !vckickall "Voice Channel Name"
        """
        members = list(channel.members)
        if not members:
            await ctx.send(embed=build_embed("Nothing to do", f"**{channel.name}** is empty.", discord.Color.greyple()))
            return
        confirmed = await _confirm(ctx, build_embed(
            "⚠️ Confirm Disconnect All",
            f"Disconnect all **{len(members)}** member(s) from **{channel.name}**?",
            discord.Color.orange(),
        ))
        if not confirmed:
            return
        count = 0
        for m in members:
            try:
                await m.move_to(None, reason=f"VC kick-all by {ctx.author}")
                count += 1
            except discord.HTTPException as e:
                logger.warning("vckickall failed for %s: %s", m, e)
            await asyncio.sleep(0.3)
        logger.info("vckickall | %s | vc=%r affected=%d", _guild_ctx(ctx), channel.name, count)
        await ctx.send(embed=build_embed("✅ Voice Cleared", f"Disconnected **{count}** member(s) from **{channel.name}**.", discord.Color.green()))


async def setup(bot):
    await bot.add_cog(Messaging(bot))
