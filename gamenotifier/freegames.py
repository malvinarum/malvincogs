import asyncio
import json
import logging
import requests
from typing import List, Dict, Optional, Set
import discord  # Import Discord for using Embeds

# We import core components from redbot.core
from redbot.core import Config, commands, checks
# FIX: Import tasks directly from discord.ext for best compatibility across RedBot versions
# Since we are moving to webhooks, the `tasks` and `app_commands` imports are no longer strictly needed for this file.
# We will use the redbot webserver instead.
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

# Import Red's web server components
from redbot.core.app_commands import AppCommand
from redbot.core.bot import Red
from redbot.core.errors import CogLoadError

# Setup logging for the cog
log = logging.getLogger("red.freegames")


# --- Webhook Handler (No longer need GiveawayFetcher class) ---
# We are removing the GiveawayFetcher class and the API_FSB_BASE_URL constant.

# --- RedBot Cog Implementation ---

class FreeGames(commands.Cog):
    """
    A cog to track and notify about time-limited free game giveaways
    via a FreeStuffBot webhook relay.

    This cog uses Red's webserver component to listen for incoming webhooks.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Use a global identifier for the API key, as it's common across all guilds
        self.config = Config.get_conf(self, identifier=147789053890256247, force_registration=True)

        # Global configuration defaults (for Webhook Secret)
        default_global = {
            # The API key is replaced by the webhook secret used for validation
            "webhook_secret": "",
            "webhook_url": None  # Red's public URL for the cog's endpoint
        }
        self.config.register_global(**default_global)

        # Guild configuration defaults
        default_guild = {
            "channel_id": None,  # Channel to send notifications to
            # We don't need announced_ids in the webhook model as the source should only send new data.
        }
        self.config.register_guild(**default_guild)

        # Ensure webserver is loaded
        if not self.bot.get_cog("Webserver"):
            raise CogLoadError("The Webserver cog is not loaded. This cog requires it to receive webhooks.")

        # Register the webhook handler
        self.bot.get_cog("Webserver").register_routes(self.app_commands)

        log.info("FreeGames cog initialized and webhook route registered.")

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        # Unregister the webhook handler
        if self.bot.get_cog("Webserver"):
            self.bot.get_cog("Webserver").unregister_routes(self.app_commands)
        log.info("FreeGames cog webhook route unregistered.")

    # --- Webhook Routes (Using Red's app_commands) ---
    @property
    def app_commands(self) -> List[AppCommand]:
        """Defines the public web routes for this cog."""
        return [
            AppCommand(
                "POST",
                "/freegames/webhook/{guild_id}",
                self.handle_webhook,
                json_body=True,
                secret=self.config.webhook_secret  # Use the configured secret for signing/validation
            )
        ]

    async def handle_webhook(self, data: Dict, guild_id: int):
        """
        Handles incoming webhook payloads from FreeStuffBot.
        The data payload is expected to be a list of giveaway objects.
        """
        log.debug(f"Received webhook for guild {guild_id}. Data type: {type(data)}")

        if not isinstance(data, list):
            log.warning(f"Webhook data for guild {guild_id} was not a list. Skipping.")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.warning(f"Received webhook for non-existent guild ID: {guild_id}. Skipping.")
            return

        guild_data = await self.config.guild(guild).all()
        channel_id = guild_data.get("channel_id")

        if not channel_id:
            log.info(f"Guild {guild_id} has no announcement channel set. Skipping webhook processing.")
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            log.warning(f"Announcement channel {channel_id} not found in guild {guild_id}. Skipping.")
            return

        # Process all giveaways in the payload
        for giveaway in data:
            try:
                embed = self._format_giveaway(giveaway)
                await channel.send(content="🚨 **NEW FREE GAME ALERT!** 🚨", embed=embed)
                await asyncio.sleep(0.5)  # Small delay
            except Exception as e:
                log.error(f"Failed to announce webhook giveaway in channel {channel_id}: {e}")

        log.info(f"Successfully processed {len(data)} giveaways via webhook for guild {guild_id}.")
        return {"status": "ok", "message": f"Processed {len(data)} giveaways."}, 200  # Return standard success response

    # --- Utility Methods ---

    def _format_giveaway(self, giveaway: Dict) -> discord.Embed:
        """
        Formats a single giveaway item (using FreeStuffBot keys)
        into a clean Discord embed, designed to mimic the user's provided example.
        (This method remains mostly unchanged as the payload keys are likely the same)
        """
        # Mapping FreeStuffBot keys to embed fields
        title = giveaway.get('title', 'Unknown Title')
        platform = giveaway.get('platform', 'N/A')
        # FIX: The end_date might be null or missing, so we safely handle that and convert it to a string if present.
        end_date = str(giveaway.get('end_date', 'Ongoing'))
        worth = giveaway.get('original_price', 'Free')
        open_url = giveaway.get('link', '#')
        image_url = giveaway.get('image', None)  # Renamed to image_url for clarity

        # Determine the status text (e.g., "Free to keep" vs. "Free until...")
        status_text = ""
        # Check if end_date is meaningful
        if end_date and end_date.lower() not in ["ongoing", "n/a", "tbd", "none"]:
            # If there's an end date, make it prominent
            status_text = f"**Free until:** {end_date}"
        elif worth and worth.lower() == 'free':
            # Assume if it's 'Free' and no end date, it's 'Free to Keep'
            status_text = "**Free to Keep Forever!**"
        else:
            status_text = "**Active Giveaway**"

        # Create the embed
        embed = discord.Embed(
            # Title is the game name
            title=title,
            # Use a neutral dark color to match a sleek Discord look
            color=0x2F3136,
            url=open_url  # URL for the title link
        )

        # Set Author to be the alert message
        embed.set_author(name="🚨 NEW FREE GAME ALERT!", icon_url="https://i.imgur.com/8Q0N6jY.png")  # Simple alert icon

        # Use the description to display the status and link information
        embed.description = (
            f"{status_text}\n"
            f"Platform: **{platform}** | Value: **{worth}**\n\n"
            f"[**Click here to claim this giveaway!**]({open_url})"
        )

        # Use set_image for the primary visual (matching the user's example)
        if image_url:
            embed.set_image(url=image_url)

        # Add the requested footer
        embed.set_footer(text=f"Game notifier by Malvinarum | Source: FreeStuffBot")

        return embed

    # --- Commands (Modified for Webhook setup) ---

    @commands.group(name="freegames", invoke_without_command=True)
    @commands.guild_only()
    async def _freegames(self, ctx: commands.Context):
        """Manages the Free Games Giveaway Webhook Notifier."""
        await ctx.send_help(ctx.command)

    @_freegames.command(name="setsecret")
    @checks.is_owner()
    async def freegames_setsecret(self, ctx: commands.Context, secret: str):
        """
        Sets the webhook secret for validating incoming FreeStuffBot webhooks (Owner only).

        This secret must match the one configured on the FreeStuffBot service.
        """
        await self.config.webhook_secret.set(secret)
        await ctx.send("FreeStuffBot Webhook Secret has been successfully updated.")

    @_freegames.command(name="geturl")
    @checks.is_owner()
    async def freegames_geturl(self, ctx: commands.Context):
        """
        Gets the full public URL for the FreeStuffBot webhook. (Owner only)

        You must copy this URL into the FreeStuffBot settings.
        """
        webserver_cog = self.bot.get_cog("Webserver")
        if not webserver_cog or not webserver_cog.public_url:
            return await ctx.send(
                "❌ **Error:** Webserver cog is not configured or its public URL is unknown. Cannot generate the endpoint URL.")

        # The URL for this guild is the public URL + the route defined in app_commands
        guild_id = ctx.guild.id
        webhook_path = f"/freegames/webhook/{guild_id}"
        full_url = webserver_cog.public_url.rstrip('/') + webhook_path

        await ctx.send(
            f"✅ **Your Guild's Webhook URL (for FreeStuffBot):**\n"
            f"You need to copy this URL and paste it into your FreeStuffBot configuration.\n"
            f"{box(full_url)}"
        )

    @_freegames.command(name="setchannel")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the channel for free game giveaway announcements.
        """
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(
            f"Success! Free game announcements will now be posted in {channel.mention} when a webhook is received.")
        log.info(f"Notification channel set to {channel.id} in guild {ctx.guild.id}")

    @_freegames.command(name="reset")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_reset(self, ctx: commands.Context):
        """
        This command is no longer strictly necessary with webhooks, but remains
        as a placeholder for maintenance.
        """
        await ctx.send("The webhook model does not rely on a history list, but this command is kept for compatibility.")

    @_freegames.command(name="checknow")
    @checks.is_owner()
    async def freegames_checknow(self, ctx: commands.Context):
        """
        Since we are using webhooks, this command is now a manual API fetch for testing purposes.
        (Owner only)
        """
        await ctx.send(
            "⚠️ Since this cog is now webhook-based, this command performs a **one-time REST API fetch** for testing the data format only.")

        # We need a temporary fetcher for this test command only.

        # NOTE: We must use the V1 API for testing as it was confirmed to exist.
        # Since the cog no longer stores the API key, we must ask the owner for it if they want to use this command.
        return await ctx.send(
            "❌ **Test Skipped:** Please re-run this command with your REST API Key as an argument, e.g., `[p]freegames checknow <your_rest_api_key>` if you need to test the data structure.")

    @_freegames.command(name="checknow", hidden=True)
    @checks.is_owner()
    async def freegames_checknow_with_key(self, ctx: commands.Context, key: str):
        """
        Manual test fetch using the REST API (Owner only).
        """
        await ctx.defer()

        # Temporary fetcher class implementation just for this command
        class TempFetcher:
            def _make_request(self, endpoint: str, api_key: str) -> tuple[Optional[List], Optional[str]]:
                headers = {"Authorization": f"Bearer {api_key}"}
                try:
                    response = requests.get(endpoint, headers=headers, timeout=10)
                    if response.status_code >= 400:
                        return None, f"HTTP Error {response.status_code}: {response.reason}. Endpoint: {endpoint}"
                    data = response.json()
                    if isinstance(data, list):
                        return data, None
                    if isinstance(data, dict):
                        # Check common keys: 'games', 'giveaways', 'deals'
                        return data.get('games', data.get('giveaways', data.get('deals', []))), None
                    return [], None
                except requests.exceptions.RequestException as e:
                    return None, f"Connection Error: {e.__class__.__name__}: {e}"
                except json.JSONDecodeError:
                    return None, "JSON Decoding Error: The API returned non-JSON data."

        temp_fetcher = TempFetcher()
        API_FSB_BASE_URL = "https://api.freestuffbot.xyz/v1"
        endpoint = f"{API_FSB_BASE_URL}/free"

        await ctx.send("Attempting manual connection to FreeStuffBot API for format check...")

        giveaways, error = temp_fetcher._make_request(endpoint, key)

        if error:
            return await ctx.send(f"❌ **Error:** Manual API request failed. **Reason:** {error}")

        count = len(giveaways)

        if count == 0:
            return await ctx.send(
                "⚠️ **API Test Successful:** The connection is working, but the API returned **0 active giveaways**.")

        first_giveaway = giveaways[0]
        try:
            sample_data = json.dumps(first_giveaway, indent=2)
            if len(sample_data) > 1900:
                sample_data = sample_data[:1900] + "\n... (truncated)"
        except Exception:
            sample_data = "Could not serialize sample data to JSON."

        await ctx.send(
            f"✅ **Manual Test Successful:** Found **{count}** active giveaways.\n"
            f"**First Giveaway Item Data Structure:**\n"
            f"{box(sample_data, lang='json')}"
        )

    @_freegames.command(name="current")
    @commands.guild_only()
    async def freegames_current(self, ctx: commands.Context):
        """
        The 'current' command is **not available** in the webhook model.

        The webhook only pushes *new* giveaways. To view current giveaways,
        you should use the original REST API (e.g., in a browser or API tool).
        """
        await ctx.send(
            "⚠️ This command is disabled in the webhook-based cog. The bot relies on the FreeStuffBot service to push *new* giveaways only.")
