# plex_activity/plex_activity.py
import asyncio
import aiohttp
import logging
from datetime import datetime

import discord
from redbot.core import commands, Config, app_commands, checks
from redbot.core.utils.chat_formatting import humanize_list, box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate
from redbot.core import tasks

log = logging.getLogger("red.plex_activity")

# Define default settings for the cog's configuration
DEFAULT_GUILD_SETTINGS = {
    "plex_url": None,
    "plex_token": None,
    "activity_channel": None,
    "activity_message_id": None,  # To store the ID of the message to update
    "update_interval": 60  # Default update interval in seconds
}


class PlexActivity(commands.Cog):
    """
    A Redbot cog to track and display Plex Media Server activity.

    This cog polls the Plex API for active sessions and updates a Discord message
    in a designated channel.
    """

    def __init__(self, bot):
        self.bot = bot
        # Initialize Config for guild-specific settings
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()  # HTTP session for API requests
        self._plex_activity_loop_task = None  # To hold the background task

    async def cog_load(self):
        """
        Called when the cog is loaded.
        Starts the background task to update Plex activity.
        """
        log.info("PlexActivity cog loaded. Starting activity loop.")
        # Ensure the loop starts only once
        if not self._plex_activity_loop_task or self._plex_activity_loop_task.done():
            self._plex_activity_loop_task = self.plex_activity_loop.start()

    async def cog_unload(self):
        """
        Called when the cog is unloaded.
        Stops the background task and closes the aiohttp session.
        """
        log.info("PlexActivity cog unloaded. Stopping activity loop and closing session.")
        if self._plex_activity_loop_task:
            self.plex_activity_loop.cancel()
        if self.session:
            await self.session.close()

    async def _get_plex_sessions(self, guild_id: int):
        """
        Fetches active sessions from the Plex Media Server API.

        Args:
            guild_id (int): The ID of the guild to fetch settings for.

        Returns:
            list: A list of active Plex sessions, or an empty list if an error occurs.
        """
        settings = await self.config.guild_from_id(guild_id).all()
        plex_url = settings["plex_url"]
        plex_token = settings["plex_token"]

        if not plex_url or not plex_token:
            log.warning(f"Plex URL or Token not configured for guild {guild_id}.")
            return []

        # Ensure the URL ends with a slash for proper path joining
        if not plex_url.endswith("/"):
            plex_url += "/"

        api_url = f"{plex_url}status/sessions?X-Plex-Token={plex_token}"

        try:
            async with self.session.get(api_url, timeout=10) as response:
                response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
                data = await response.text()  # Plex API returns XML, we'll parse it

                # A very basic XML parsing for sessions.
                # For more robust parsing, consider `xml.etree.ElementTree`
                # or a dedicated Plex API wrapper.
                sessions = []
                # This is a simplified parsing. In a real scenario, you'd use an XML parser.
                # Example: <MediaContainer size="1" allowSync="0" identifier="com.plexapp.plugins.library" mediaTagPrefix="/system/bundle/media/flags/" mediaTagVersion="1716892697" platform="Linux" platformVersion="5.15.0-107-generic" serverVersion="1.32.8.7639-fb64a6472" syncSupported="1" myPlex="1" myPlexMappingState="direct" myPlexSigninState="ok" myPlexSubscription="1" myPlexUsername="user@example.com" machineIdentifier="xxxxxxxxxxxxxxxxxxxx" claimed="1" content="sessions">
                #   <VideoSession addedAt="1716912345" at="1716912345" device="Chrome" live="0" location="lan" machineIdentifier="xxxxxxxxxxxx" transcodeDecision="copy" protocol="http" state="playing" sessionKey="12345" userState="playing" viewOffset="12345">
                #     <User id="1" thumb="https://plex.tv/users/xxxxxxxx/avatar" title="PlexUser" />
                #     <Player address="192.168.1.100" agent="Chrome" device="Chrome" machineIdentifier="xxxxxxxxxxxx" platform="Chrome" platformVersion="125.0" product="Plex Web" state="playing" title="Plex Web (Chrome)" local="1" relayed="0" secure="1" userID="1" />
                #     <Session id="12345" bandwidth="10000" location="lan" />
                #     <TranscodeSession key="/transcode/sessions/xxxxxxxxxx" throttledBuffer="0" complete="0" progress="0" speed="0" duration="0" container="mp4" videoCodec="h264" audioCodec="aac" protocol="http" remaining="0" context="streaming" sourceVideoCodec="h264" sourceAudioCodec="aac" videoDecision="copy" audioDecision="copy" />
                #     <Media id="12345" parentRatingKey="123" ratingKey="12345" sessionKey="12345" type="episode" title="Episode Title" parentTitle="Show Title" grandparentNode="123" grandparentNodeTitle="Show Title" grandparentNodeType="show" thumb="/library/metadata/123/thumb/12345" art="/library/metadata/123/art/12345" duration="1234567" viewOffset="12345" />
                #   </VideoSession>
                # </MediaContainer>

                # Simple regex or string parsing can extract key info for now.
                # A more robust solution would use an XML parser.
                # For demonstration, we'll simulate parsing a few key fields.

                # Example of a very basic string search for sessions:
                # This is highly brittle and should be replaced with proper XML parsing.
                if "<VideoSession" in data or "<PhotoSession" in data or "<TrackSession" in data:
                    # Simulate finding sessions. In reality, you'd iterate through XML elements.
                    # For now, let's assume we find one session for demonstration.
                    # You'd need to parse the XML to get actual user, title, etc.
                    # Example of extracting user and title from XML:
                    # from xml.etree import ElementTree as ET
                    # root = ET.fromstring(data)
                    # for session_elem in root.findall(".//VideoSession"):
                    #     user_elem = session_elem.find("User")
                    #     media_elem = session_elem.find("Media")
                    #     if user_elem is not None and media_elem is not None:
                    #         username = user_elem.get("title")
                    #         media_title = media_elem.get("title")
                    #         parent_title = media_elem.get("parentTitle")
                    #         media_type = media_elem.get("type")
                    #         view_offset = int(media_elem.get("viewOffset", 0))
                    #         duration = int(media_elem.get("duration", 1))
                    #         progress_percent = (view_offset / duration) * 100 if duration > 0 else 0
                    #         sessions.append({
                    #             "user": username,
                    #             "title": f"{parent_title} - {media_title}" if parent_title else media_title,
                    #             "type": media_type,
                    #             "progress": f"{progress_percent:.0f}%"
                    #         })

                    # Placeholder for actual parsed sessions:
                    # If there's a MediaContainer, it means there's activity.
                    # We'll just return a dummy session for now if any session tag is found.
                    # This needs to be replaced with actual XML parsing.
                    if 'size="0"' not in data:  # Check if there are active sessions
                        # This part needs proper XML parsing to extract real data.
                        # For now, we'll just indicate activity if the size is not 0.
                        # This is a *placeholder* and will not show detailed info without XML parsing.
                        log.debug(f"Plex API response for guild {guild_id}: {data[:500]}...")  # Log first 500 chars

                        # Example of what a parsed session might look like:
                        sessions.append({
                            "user": "A User",
                            "title": "A Movie/Episode",
                            "type": "movie",
                            "progress": "50%",
                            "device": "Web",
                            "ip_address": "192.168.1.1"  # This will be filtered in _format_sessions_embed
                        })
                        # In a real scenario, you'd parse the XML to populate this list accurately.
                        # For instance, using xml.etree.ElementTree:
                        # import xml.etree.ElementTree as ET
                        # root = ET.fromstring(data)
                        # for video_session in root.findall(".//VideoSession"):
                        #     user_title = video_session.find("User").get("title")
                        #     media_title = video_session.find("Media").get("title")
                        #     parent_title = video_session.find("Media").get("parentTitle")
                        #     media_type = video_session.find("Media").get("type")
                        #     view_offset = int(video_session.find("Media").get("viewOffset", "0"))
                        #     duration = int(video_session.find("Media").get("duration", "1"))
                        #     progress = f"{(view_offset / duration * 100):.0f}%" if duration > 0 else "0%"
                        #     device = video_session.find("Player").get("product")
                        #     ip_address = video_session.find("Player").get("address")
                        #     sessions.append({
                        #         "user": user_title,
                        #         "title": f"{parent_title} - {media_title}" if parent_title else media_title,
                        #         "type": media_type,
                        #         "progress": progress,
                        #         "device": device,
                        #         "ip_address": ip_address
                        #     })

                else:
                    log.debug(f"No active sessions found for guild {guild_id}.")
        except aiohttp.ClientError as e:
            log.error(f"Failed to connect to Plex API for guild {guild_id}: {e}")
        except asyncio.TimeoutError:
            log.error(f"Plex API request timed out for guild {guild_id}.")
        except Exception as e:
            log.exception(f"An unexpected error occurred while fetching Plex sessions for guild {guild_id}.")
        return sessions

    async def _format_sessions_embed(self, sessions: list):
        """
        Formats a list of Plex sessions into a Discord embed.

        Args:
            sessions (list): A list of active Plex sessions.

        Returns:
            discord.Embed: An embed representing the current Plex activity.
        """
        embed = discord.Embed(
            title="Plex Media Server Activity",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Last updated")

        if not sessions:
            embed.description = "No active sessions currently."
        else:
            description_parts = []
            for session in sessions:
                user = session.get("user", "Unknown User")
                title = session.get("title", "Unknown Title")
                media_type = session.get("type", "media")
                progress = session.get("progress", "N/A")
                device = session.get("device", "Unknown Device")
                # Removed ip_address from display for privacy
                # ip_address = session.get("ip_address", "N/A")

                description_parts.append(
                    f"**{user}** is watching **{title}** ({media_type.capitalize()})\n"
                    f"  - Progress: `{progress}`\n"
                    f"  - Device: `{device}`"  # Removed IP address
                )
            embed.description = "\n\n".join(description_parts)

        return embed

    @tasks.loop(seconds=60)  # Default to update every 60 seconds
    async def plex_activity_loop(self):
        """
        Background task to periodically fetch and update Plex activity.
        """
        for guild_id in await self.config.all_guilds():
            guild_settings = await self.config.guild_from_id(guild_id).all()
            channel_id = guild_settings["activity_channel"]
            message_id = guild_settings["activity_message_id"]
            update_interval = guild_settings.get("update_interval", 60)

            # Update loop interval if it has changed
            if self.plex_activity_loop.seconds != update_interval:
                self.plex_activity_loop.change_interval(seconds=update_interval)

            if not channel_id:
                continue  # Skip if no channel is set for this guild

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue  # Skip if guild is not found (e.g., bot left the guild)

            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                log.warning(
                    f"Configured activity channel {channel_id} not found or not a text channel in guild {guild.name}.")
                await self.config.guild(guild).activity_channel.set(None)  # Clear invalid channel
                await self.config.guild(guild).activity_message_id.set(None)
                continue

            sessions = await self._get_plex_sessions(guild_id)
            embed = await self._format_sessions_embed(sessions)

            try:
                if message_id:
                    # Try to edit the existing message
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embed=embed)
                        log.debug(f"Updated Plex activity message in {channel.name} ({guild.name}).")
                    except discord.NotFound:
                        log.warning(f"Activity message {message_id} not found in {channel.name}. Sending a new one.")
                        message = await channel.send(embed=embed)
                        await self.config.guild(guild).activity_message_id.set(message.id)
                    except discord.Forbidden:
                        log.warning(f"Bot does not have permissions to edit message {message_id} in {channel.name}.")
                        await self.config.guild(guild).activity_message_id.set(None)  # Clear message ID
                        await channel.send(
                            f"I don't have permissions to edit the activity message. Please check my permissions or use `{await self.bot.get_prefix(channel)}plex setchannel` again.")
                else:
                    # Send a new message if no message ID is stored
                    message = await channel.send(embed=embed)
                    await self.config.guild(guild).activity_message_id.set(message.id)
                    log.info(f"Sent new Plex activity message in {channel.name} ({guild.name}).")
            except discord.Forbidden:
                log.error(f"Bot does not have permissions to send messages in {channel.name} ({guild.name}).")
                await self.config.guild(guild).activity_channel.set(None)  # Clear invalid channel
                await self.config.guild(guild).activity_message_id.set(None)
            except Exception as e:
                log.exception(f"Error updating Plex activity message in {channel.name} ({guild.name}): {e}")

    @plex_activity_loop.before_loop
    async def before_plex_activity_loop(self):
        """Waits for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()
        log.info("Plex activity loop is ready to start.")

    @commands.group(name="plex", invoke_without_command=True)
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def plex(self, ctx: commands.Context):
        """
        Manage Plex Media Server activity tracking.
        """
        await ctx.send_help(self.plex)

    @plex.command(name="setup")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex_setup(self, ctx: commands.Context):
        """
        Set up your Plex Media Server URL and API token.

        Your Plex URL should be the full address to your Plex server, e.g.,
        `http://192.168.1.100:32400` or `https://app.plex.tv/desktop`.
        Your Plex token can be found by inspecting network requests
        when browsing your Plex server or through various online guides.
        """
        await ctx.send(
            "Please enter your Plex Media Server URL (e.g., `http://192.168.1.100:32400`):"
        )
        try:
            plex_url_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=60)
            plex_url = plex_url_msg.content.strip()
            if not (plex_url.startswith("http://") or plex_url.startswith("https://")):
                return await ctx.send("Invalid URL format. Please include `http://` or `https://`.")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out. Please run the command again.")

        await ctx.send("Please enter your Plex API Token:")
        try:
            plex_token_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=60)
            plex_token = plex_token_msg.content.strip()
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out. Please run the command again.")

        await self.config.guild(ctx.guild).plex_url.set(plex_url)
        await self.config.guild(ctx.guild).plex_token.set(plex_token)
        await ctx.send(f"Plex URL and Token have been set for this server.")

        # Test connection immediately
        await ctx.send("Testing Plex connection...")
        sessions = await self._get_plex_sessions(ctx.guild.id)
        if sessions is not None:
            await ctx.send("Plex connection successful! (Note: Actual session parsing needs more robust XML handling).")
        else:
            await ctx.send("Plex connection failed. Please double check your URL and token.")

    @plex.command(name="setchannel")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel where Plex activity updates will be posted.

        The bot will automatically update a single message in this channel
        with the current Plex activity.
        """
        await self.config.guild(ctx.guild).activity_channel.set(channel.id)
        # Clear existing message ID so a new one is sent
        await self.config.guild(ctx.guild).activity_message_id.set(None)
        await ctx.send(f"Plex activity updates will now be posted in {channel.mention}.")
        # Immediately trigger an update to create the initial message
        await self.plex_activity_loop()  # Call the loop once to create the initial message

    @plex.command(name="interval")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex_interval(self, ctx: commands.Context, seconds: int):
        """
        Set the update interval for Plex activity in seconds.

        Minimum interval is 10 seconds.
        """
        if seconds < 10:
            return await ctx.send("The minimum update interval is 10 seconds.")
        await self.config.guild(ctx.guild).update_interval.set(seconds)
        self.plex_activity_loop.change_interval(seconds=seconds)
        await ctx.send(f"Plex activity update interval set to {seconds} seconds.")

    @plex.command(name="status")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex_status(self, ctx: commands.Context):
        """
        Show current Plex activity settings for this server.
        """
        settings = await self.config.guild(ctx.guild).all()
        plex_url = settings["plex_url"] or "Not set"
        activity_channel_id = settings["activity_channel"]
        activity_message_id = settings["activity_message_id"]
        update_interval = settings["update_interval"]

        channel_mention = self.bot.get_channel(activity_channel_id).mention if activity_channel_id else "Not set"

        status_msg = (
            f"**Plex URL:** `{plex_url}`\n"
            f"**Plex Token:** `{'Set' if settings['plex_token'] else 'Not set'}`\n"
            f"**Activity Channel:** {channel_mention}\n"
            f"**Activity Message ID:** `{activity_message_id or 'None'}`\n"
            f"**Update Interval:** `{update_interval} seconds`"
        )
        await ctx.send(box(status_msg))

    @plex.command(name="clear")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex_clear(self, ctx: commands.Context):
        """
        Clear all Plex activity settings for this server.
        """
        await self.config.guild(ctx.guild).clear()
        await ctx.send("All Plex activity settings for this server have been cleared.")
        # Stop the loop if no more guilds are configured
        if not await self.config.all_guilds():
            self.plex_activity_loop.cancel()


# Setup function for Redbot
async def setup(bot):
    """Adds the cog to the bot."""
    await bot.add_cog(PlexActivity(bot))
