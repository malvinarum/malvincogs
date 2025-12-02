import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, humanize_list
import logging
import re

log = logging.getLogger("red.malvincogs.streamsentry")


class StreamSentry(commands.Cog):
    """
    StreamSentry: Automated Streamer Promotion
    - Auto-assigns 'Now Live' roles.
    - Posts go-live notifications.
    - Archives game clips to a vault channel.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1029384756, force_registration=True)
        default_guild = {
            "live_role_id": None,
            "alert_channel_id": None,
            "clip_channel_id": None,
            "whitelist_role_ids": [],
            "enabled": False,
            "cleanup_on_offline": True
        }
        self.config.register_guild(**default_guild)

        self.clip_regex = re.compile(
            r"(https?://(?:www\.)?(?:twitch\.tv/\w+/clip/[^ \n]+|clips\.twitch\.tv/[^ \n]+|"
            r"medal\.tv/games/[^ \n]+|youtube\.com/clip/[^ \n]+))"
        )

    async def cog_load(self):
        log.info("StreamSentry loaded.")

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if before.bot or after.bot:
            return

        settings = await self.config.guild(guild).all()
        if not settings["enabled"]:
            return

        # 1. Check Whitelist
        whitelist_ids = settings["whitelist_role_ids"]
        if whitelist_ids:
            user_role_ids = {r.id for r in after.roles}
            if not user_role_ids.intersection(whitelist_ids):
                return

        # 2. Determine Stream State
        before_stream = next((a for a in before.activities if a.type == discord.ActivityType.streaming), None)
        after_stream = next((a for a in after.activities if a.type == discord.ActivityType.streaming), None)

        live_role = guild.get_role(settings["live_role_id"]) if settings["live_role_id"] else None
        alert_channel = guild.get_channel(settings["alert_channel_id"]) if settings["alert_channel_id"] else None

        # --- STARTED STREAMING ---
        if not before_stream and after_stream:
            if live_role:
                try:
                    await after.add_roles(live_role, reason="StreamSentry: User went live")
                except discord.Forbidden:
                    pass

            if alert_channel and after_stream.url:
                await self._send_alert(alert_channel, after, after_stream)

        # --- STOPPED STREAMING ---
        elif before_stream and not after_stream:
            if live_role and settings["cleanup_on_offline"]:
                try:
                    await after.remove_roles(live_role, reason="StreamSentry: User went offline")
                except discord.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        clip_channel_id = await self.config.guild(message.guild).clip_channel_id()
        if not clip_channel_id or message.channel.id == clip_channel_id:
            return

        found_clips = self.clip_regex.findall(message.content)
        if found_clips:
            clip_channel = message.guild.get_channel(clip_channel_id)
            if not clip_channel:
                return

            for clip_url in found_clips:
                embed = discord.Embed(
                    title="🎬 New Clip Archived",
                    description=f"**Clipper:** {message.author.mention}\n**Source:** [Watch Clip]({clip_url})",
                    color=discord.Color.gold(),
                    timestamp=message.created_at
                )
                embed.set_footer(text=f"From #{message.channel.name}")

                try:
                    await clip_channel.send(embed=embed)
                    await message.add_reaction("💾")
                except Exception:
                    pass

    # --- HELPERS ---

    async def _send_alert(self, channel, member, activity):
        """Shared alert sending logic."""
        embed = discord.Embed(
            title=f"🔴 {member.display_name} is Now Live!",
            description=f"**Playing:** {activity.game}\n**Title:** {activity.name}",
            url=activity.url,
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if "twitch.tv" in activity.url:
            embed.set_footer(text="Twitch.tv", icon_url="https://i.imgur.com/v9E8D6p.png")

        try:
            await channel.send(content=f"Hey @here, {member.mention} is live!", embed=embed)
        except discord.Forbidden:
            pass

    # --- COMMANDS ---

    @commands.group(name="streamset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def streamset(self, ctx: commands.Context):
        """Configure StreamSentry settings."""
        pass

    @streamset.command(name="toggle")
    async def streamset_toggle(self, ctx: commands.Context):
        """Enable or disable the system."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        await ctx.send(f"StreamSentry is now **{'Enabled' if new_state else 'Disabled'}**.")

    @streamset.command(name="role")
    async def streamset_role(self, ctx: commands.Context, role: discord.Role):
        """Set the 'Now Live' role."""
        await self.config.guild(ctx.guild).live_role_id.set(role.id)
        await ctx.send(f"✅ Live role set to: {role.mention}")

    @streamset.command(name="alertchannel")
    async def streamset_alert(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the live notification channel."""
        await self.config.guild(ctx.guild).alert_channel_id.set(channel.id)
        await ctx.send(f"✅ Alerts will post in: {channel.mention}")

    @streamset.command(name="clipvault")
    async def streamset_clips(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the clip archive channel."""
        await self.config.guild(ctx.guild).clip_channel_id.set(channel.id)
        await ctx.send(f"✅ Clips will be archived to: {channel.mention}")

    @streamset.group(name="whitelist")
    async def streamset_whitelist(self, ctx: commands.Context):
        """Manage the role whitelist."""
        pass

    @streamset_whitelist.command(name="add")
    async def whitelist_add(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the whitelist."""
        async with self.config.guild(ctx.guild).whitelist_role_ids() as ids:
            if role.id in ids:
                return await ctx.send(f"⚠️ {role.name} is already whitelisted.")
            ids.append(role.id)
        await ctx.send(f"🔐 Added **{role.name}** to the whitelist.")

    @streamset_whitelist.command(name="remove")
    async def whitelist_remove(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the whitelist."""
        async with self.config.guild(ctx.guild).whitelist_role_ids() as ids:
            if role.id not in ids:
                return await ctx.send(f"⚠️ {role.name} is not in the whitelist.")
            ids.remove(role.id)
        await ctx.send(f"🔓 Removed **{role.name}** from the whitelist.")

    @streamset_whitelist.command(name="clear")
    async def whitelist_clear(self, ctx: commands.Context):
        """Clear all whitelisted roles (Open to everyone)."""
        await self.config.guild(ctx.guild).whitelist_role_ids.set([])
        await ctx.send("🔓 Whitelist cleared. StreamSentry is now open to **everyone**.")

    @streamset.command(name="check")
    async def streamset_check(self, ctx: commands.Context, member: discord.Member = None):
        """
        Manually trigger the live check for a user with DEBUG output.
        """
        if not member:
            member = ctx.author

        # 1. Fetch fresh member object from cache to ensure Activity list is up to date
        member = ctx.guild.get_member(member.id)

        # 2. Debug Activities
        detected_activities = []
        for act in member.activities:
            detected_activities.append(f"{act.type.name}: {act.name}")

        stream_activity = next((a for a in member.activities if a.type == discord.ActivityType.streaming), None)

        if not stream_activity:
            debug_str = "\n".join(detected_activities) if detected_activities else "None"
            return await ctx.send(
                f"❌ {member.display_name} is not currently streaming.\n"
                f"**Bot sees these activities:**\n{box(debug_str)}"
            )

        # 3. Whitelist Check
        settings = await self.config.guild(ctx.guild).all()
        whitelist_ids = settings["whitelist_role_ids"]

        if whitelist_ids:
            user_role_ids = {r.id for r in member.roles}
            if not user_role_ids.intersection(whitelist_ids):
                return await ctx.send(f"❌ {member.display_name} does not have a whitelisted role.")

        # 4. Apply Role & Alert
        live_role = ctx.guild.get_role(settings["live_role_id"]) if settings["live_role_id"] else None
        alert_channel = ctx.guild.get_channel(settings["alert_channel_id"]) if settings["alert_channel_id"] else None

        actions = []

        # Role
        if live_role:
            if live_role not in member.roles:
                try:
                    await member.add_roles(live_role, reason="StreamSentry: Manual Check")
                    actions.append("assigned role")
                except discord.Forbidden:
                    actions.append("failed to assign role (perms)")
            else:
                actions.append("already has role")

        # Alert
        if alert_channel:
            try:
                await self._send_alert(alert_channel, member, stream_activity)
                actions.append("sent alert")
            except Exception as e:
                actions.append(f"failed alert ({e})")

        await ctx.send(f"✅ Manual check complete: {', '.join(actions)}.")

    @streamset.command(name="settings")
    async def streamset_show(self, ctx: commands.Context):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()

        live_role = ctx.guild.get_role(data['live_role_id']) if data['live_role_id'] else "None"
        alert_ch = ctx.guild.get_channel(data['alert_channel_id']) if data['alert_channel_id'] else "None"
        clip_ch = ctx.guild.get_channel(data['clip_channel_id']) if data['clip_channel_id'] else "None"

        whitelist_ids = data['whitelist_role_ids']
        if whitelist_ids:
            roles = [ctx.guild.get_role(rid).mention for rid in whitelist_ids if ctx.guild.get_role(rid)]
            whitelist_str = humanize_list(roles) if roles else "None (IDs invalid)"
        else:
            whitelist_str = "Everyone (No restrictions)"

        status = "🟢 Enabled" if data['enabled'] else "🔴 Disabled"

        msg = (
            f"**StreamSentry Config**\n"
            f"Status: {status}\n\n"
            f"🎭 **Live Role:** {live_role.mention if isinstance(live_role, discord.Role) else live_role}\n"
            f"📢 **Alert Channel:** {alert_ch.mention if isinstance(alert_ch, discord.TextChannel) else alert_ch}\n"
            f"🎬 **Clip Vault:** {clip_ch.mention if isinstance(clip_ch, discord.TextChannel) else clip_ch}\n"
            f"🔐 **Allowed Roles:** {whitelist_str}\n"
        )
        await ctx.send(msg)