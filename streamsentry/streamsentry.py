import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box
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
            "whitelist_role_id": None,  # If set, only users with this role trigger alerts
            "enabled": False,
            "cleanup_on_offline": True  # Remove role when offline
        }
        self.config.register_guild(**default_guild)

        # Regex for common clip URLs
        self.clip_regex = re.compile(
            r"(https?://(?:www\.)?(?:twitch\.tv/\w+/clip/[^ \n]+|clips\.twitch\.tv/[^ \n]+|"
            r"medal\.tv/games/[^ \n]+|youtube\.com/clip/[^ \n]+))"
        )

    async def cog_load(self):
        log.info("StreamSentry loaded.")

    # --- EVENTS ---

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """
        Detects when a member starts or stops streaming.
        """
        guild = after.guild
        if before.bot or after.bot:
            return

        settings = await self.config.guild(guild).all()
        if not settings["enabled"]:
            return

        # 1. Check Whitelist (Optional)
        if settings["whitelist_role_id"]:
            whitelist_role = guild.get_role(settings["whitelist_role_id"])
            if whitelist_role and whitelist_role not in after.roles:
                return

        # 2. Determine Stream State
        # We look for an activity of type Streaming
        before_stream = next((a for a in before.activities if a.type == discord.ActivityType.streaming), None)
        after_stream = next((a for a in after.activities if a.type == discord.ActivityType.streaming), None)

        live_role = guild.get_role(settings["live_role_id"]) if settings["live_role_id"] else None
        alert_channel = guild.get_channel(settings["alert_channel_id"]) if settings["alert_channel_id"] else None

        # --- CASE: STARTED STREAMING ---
        if not before_stream and after_stream:
            # Assign Role
            if live_role:
                try:
                    await after.add_roles(live_role, reason="StreamSentry: User went live")
                except discord.Forbidden:
                    log.warning(f"StreamSentry: Missing permissions to add role in {guild.name}")

            # Send Alert
            if alert_channel and after_stream.url:
                embed = discord.Embed(
                    title=f"🔴 {after.display_name} is Now Live!",
                    description=f"**Playing:** {after_stream.game}\n**Title:** {after_stream.name}",
                    url=after_stream.url,
                    color=discord.Color.purple()
                )
                embed.set_thumbnail(url=after.display_avatar.url)

                # If it's Twitch, we can try to guess the thumbnail/preview
                # (Simple heuristic, not perfect without API)
                if "twitch.tv" in after_stream.url:
                    embed.set_footer(text="Twitch.tv", icon_url="https://i.imgur.com/v9E8D6p.png")

                try:
                    await alert_channel.send(content=f"Hey @here, {after.mention} is live!", embed=embed)
                except discord.Forbidden:
                    pass

        # --- CASE: STOPPED STREAMING ---
        elif before_stream and not after_stream:
            if live_role and settings["cleanup_on_offline"]:
                try:
                    await after.remove_roles(live_role, reason="StreamSentry: User went offline")
                except discord.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Watches for clips and reposts them to the vault.
        """
        if message.author.bot or not message.guild:
            return

        clip_channel_id = await self.config.guild(message.guild).clip_channel_id()
        if not clip_channel_id or message.channel.id == clip_channel_id:
            return

        # Check if the message contains a clip URL
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
                    await message.add_reaction("💾")  # Ack that we saved it
                except Exception as e:
                    log.error(f"Failed to archive clip: {e}")

    # --- COMMANDS ---

    @commands.group(name="streamset")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def streamset(self, ctx: commands.Context):
        """Configure StreamSentry settings."""
        pass

    @streamset.command(name="toggle")
    async def streamset_toggle(self, ctx: commands.Context):
        """Enable or disable the entire cog system."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        await ctx.send(f"StreamSentry is now **{'Enabled' if new_state else 'Disabled'}**.")

    @streamset.command(name="role")
    async def streamset_role(self, ctx: commands.Context, role: discord.Role):
        """Set the 'Now Live' role to assign to streamers."""
        await self.config.guild(ctx.guild).live_role_id.set(role.id)
        await ctx.send(f"✅ Live role set to: {role.mention}")

    @streamset.command(name="alertchannel")
    async def streamset_alert(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where live notifications are posted."""
        await self.config.guild(ctx.guild).alert_channel_id.set(channel.id)
        await ctx.send(f"✅ Alerts will promote streamers in: {channel.mention}")

    @streamset.command(name="clipvault")
    async def streamset_clips(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where clips detected in chat are archived."""
        await self.config.guild(ctx.guild).clip_channel_id.set(channel.id)
        await ctx.send(f"✅ Clips will be archived to: {channel.mention}")

    @streamset.command(name="whitelist")
    async def streamset_whitelist(self, ctx: commands.Context, role: discord.Role = None):
        """
        Set a required role to trigger alerts.
        If set, only users with this role will get the Live Role/Alerts.
        Leave empty to clear.
        """
        if role:
            await self.config.guild(ctx.guild).whitelist_role_id.set(role.id)
            await ctx.send(f"🔒 StreamSentry locked to role: {role.mention}")
        else:
            await self.config.guild(ctx.guild).whitelist_role_id.set(None)
            await ctx.send
            "🔓 StreamSentry is open to **everyone**."

    @streamset.command(name="settings")
    async def streamset_show(self, ctx: commands.Context):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()

        live_role = ctx.guild.get_role(data['live_role_id']) if data['live_role_id'] else "None"
        alert_ch = ctx.guild.get_channel(data['alert_channel_id']) if data['alert_channel_id'] else "None"
        clip_ch = ctx.guild.get_channel(data['clip_channel_id']) if data['clip_channel_id'] else "None"
        whitelist = ctx.guild.get_role(data['whitelist_role_id']) if data['whitelist_role_id'] else "None"

        status = "🟢 Enabled" if data['enabled'] else "🔴 Disabled"

        msg = (
            f"**StreamSentry Config**\n"
            f"Status: {status}\n\n"
            f"🎭 **Live Role:** {live_role.mention if isinstance(live_role, discord.Role) else live_role}\n"
            f"📢 **Alert Channel:** {alert_ch.mention if isinstance(alert_ch, discord.TextChannel) else alert_ch}\n"
            f"🎬 **Clip Vault:** {clip_ch.mention if isinstance(clip_ch, discord.TextChannel) else clip_ch}\n"
            f"🔐 **Whitelist:** {whitelist.mention if isinstance(whitelist, discord.Role) else whitelist}\n"
        )
        await ctx.send(msg)