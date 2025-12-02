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
            "whitelist_role_ids": [],  # CHANGED: Now a list of IDs
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

        # 1. Check Whitelist (Updated for Multiple Roles)
        whitelist_ids = settings["whitelist_role_ids"]
        if whitelist_ids:
            # Check if user has ANY of the whitelisted roles
            # We use set intersection for efficiency
            user_role_ids = {r.id for r in after.roles}
            has_whitelisted_role = bool(user_role_ids.intersection(whitelist_ids))

            if not has_whitelisted_role:
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
                embed = discord.Embed(
                    title=f"🔴 {after.display_name} is Now Live!",
                    description=f"**Playing:** {after_stream.game}\n**Title:** {after_stream.name}",
                    url=after_stream.url,
                    color=discord.Color.purple()
                )
                embed.set_thumbnail(url=after.display_avatar.url)

                if "twitch.tv" in after_stream.url:
                    embed.set_footer(text="Twitch.tv", icon_url="https://i.imgur.com/v9E8D6p.png")

                try:
                    await alert_channel.send(content=f"Hey @here, {after.mention} is live!", embed=embed)
                except discord.Forbidden:
                    pass

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

    # --- UPDATED WHITELIST COMMANDS ---

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

    @streamset.command(name="settings")
    async def streamset_show(self, ctx: commands.Context):
        """Show current configuration."""
        data = await self.config.guild(ctx.guild).all()

        live_role = ctx.guild.get_role(data['live_role_id']) if data['live_role_id'] else "None"
        alert_ch = ctx.guild.get_channel(data['alert_channel_id']) if data['alert_channel_id'] else "None"
        clip_ch = ctx.guild.get_channel(data['clip_channel_id']) if data['clip_channel_id'] else "None"

        # Format Whitelist
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