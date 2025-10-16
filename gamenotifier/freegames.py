import asyncio
import json
import logging
import requests
from typing import List, Dict, Optional, Set
import discord  # Import Discord for using Embeds
from discord.ext import tasks  # Re-import tasks for polling

# We import core components from redbot.core
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

# Setup logging for the cog
log = logging.getLogger("red.freegames")

# Constants
API_FSB_BASE_URL = "https://api.freestuffbot.xyz/v1"
POLL_INTERVAL_MINUTES = 30


# --- Giveaway Fetcher (Reintroduced for Polling) ---

class GiveawayFetcher:
    """Handles API interaction and tracking of announced giveaways."""

    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

    async def _get_api_key(self) -> Optional[str]:
        """Retrieves the globally stored API key."""
        return await self.config.api_key()

    def _make_request(self, endpoint: str, api_key: str) -> tuple[Optional[List], Optional[str]]:
        """Makes the authenticated API request to FreeStuffBot."""
        # Authenticated request requires the Bearer token
        headers = {"Authorization": f"Bearer {api_key}"}
        full_url = f"{API_FSB_BASE_URL}/{endpoint}"

        try:
            response = requests.get(full_url, headers=headers, timeout=10)

            # Handle non-success status codes (4xx/5xx)
            if response.status_code == 401:
                return None, f"HTTP Error 401: Unauthorized. Please check your API key is correct and valid."
            if response.status_code >= 400:
                return None, f"HTTP Error {response.status_code}: {response.reason}. Endpoint: {full_url}"

            data = response.json()
            # The /v1/free endpoint returns a list of giveaways
            if isinstance(data, list):
                return data, None
            # If it's a dict, try to extract the list from common keys
            if isinstance(data, dict):
                return data.get('games', data.get('giveaways', data.get('deals', []))), None

            return [], None

        except requests.exceptions.RequestException as e:
            return None, f"Connection Error: {e.__class__.__name__}: {e}"
        except json.JSONDecodeError:
            return None, "JSON Decoding Error: The API returned non-JSON data."

    async def fetch_new_giveaways(self) -> List[Dict]:
        """Fetches new giveaways and updates announced history."""
        api_key = await self._get_api_key()
        if not api_key:
            log.warning("FreeGames cog: API key not set. Skipping fetch.")
            return []

        giveaways, error = await asyncio.to_thread(self._make_request, "free", api_key)

        if error:
            log.error(f"FreeGames API Fetch Error: {error}")
            return []

        if not giveaways:
            return []

        new_giveaways = []

        # Get all announced IDs from all guilds
        all_announced_ids = set()
        for guild_id in await self.config.all_guilds():
            guild_data = await self.config.guild_from_id(guild_id).all()
            all_announced_ids.update(guild_data.get("announced_ids", []))

        # Check for new giveaways
        for giveaway in giveaways:
            giveaway_id = str(giveaway.get("id"))
            if giveaway_id and giveaway_id not in all_announced_ids:
                new_giveaways.append(giveaway)

        # Update history for all guilds with the new IDs
        if new_giveaways:
            new_ids = {str(g.get("id")) for g in new_giveaways}
            for guild_id in await self.config.all_guilds():
                async with self.config.guild_from_id(guild_id).announced_ids() as announced_ids:
                    announced_ids.extend(list(new_ids))

        return new_giveaways


# --- RedBot Cog Implementation ---

class FreeGames(commands.Cog):
    """
    A cog to track and notify about time-limited free game giveaways
    via the FreeStuffBot REST API.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=147789053890256247, force_registration=True)

        # Global configuration defaults (for API Key)
        default_global = {
            "api_key": ""  # REST API Key for polling
        }
        self.config.register_global(**default_global)

        # Guild configuration defaults
        default_guild = {
            "channel_id": None,  # Channel to send notifications to
            "announced_ids": []  # List of giveaway IDs already announced
        }
        self.config.register_guild(**default_guild)

        self.fetcher = GiveawayFetcher(bot, self.config)

        # Start the polling loop
        self.giveaway_poller.start()
        log.info("FreeGames cog initialized and polling task started.")

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        # Stop the polling loop
        self.giveaway_poller.stop()
        log.info("FreeGames cog polling task stopped.")

    @tasks.loop(minutes=POLL_INTERVAL_MINUTES)
    async def giveaway_poller(self):
        """The main loop that checks the API for new giveaways."""
        await self.bot.wait_until_ready()

        log.debug("Starting giveaway check...")

        new_giveaways = await self.fetcher.fetch_new_giveaways()

        if not new_giveaways:
            log.debug("No new giveaways found.")
            return

        log.info(f"Found {len(new_giveaways)} new giveaways. Announcing...")

        # Announce new giveaways in all configured channels
        all_guilds_data = await self.config.all_guilds()

        for guild_id, guild_data in all_guilds_data.items():
            channel_id = guild_data.get("channel_id")
            if not channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            channel = guild.get_channel(channel_id)
            if not channel or not channel.permissions_for(guild.me).send_messages:
                log.warning(f"Cannot send messages in channel {channel_id} in guild {guild_id}.")
                continue

            for giveaway in new_giveaways:
                try:
                    embed = self._format_giveaway(giveaway)
                    await channel.send(content="🚨 **NEW FREE GAME ALERT!** 🚨", embed=embed)
                    await asyncio.sleep(0.5)  # Small delay to respect rate limits
                except Exception as e:
                    log.error(f"Failed to announce giveaway {giveaway.get('id')} in channel {channel_id}: {e}")

    # --- Utility Methods ---

    def _format_giveaway(self, giveaway: Dict) -> discord.Embed:
        """
        Formats a single giveaway item into a clean Discord embed.
        """
        # Mapping FreeStuffBot keys to embed fields
        title = giveaway.get('title', 'Unknown Title')
        platform = giveaway.get('platform', 'N/A')
        # FIX: The end_date might be null or missing, so we safely handle that and convert it to a string if present.
        end_date = str(giveaway.get('end_date', 'Ongoing'))
        worth = giveaway.get('original_price', 'Free')
        open_url = giveaway.get('link', '#')
        image_url = giveaway.get('image', None)

        # Determine the status text (e.g., "Free to keep" vs. "Free until...")
        status_text = ""
        # Check if end_date is meaningful
        if end_date and end_date.lower() not in ["ongoing", "n/a", "tbd", "none"]:
            status_text = f"**Free until:** {end_date}"
        elif worth and worth.lower() == 'free':
            status_text = "**Free to Keep Forever!**"
        else:
            status_text = "**Active Giveaway**"

        # Create the embed
        embed = discord.Embed(
            title=title,
            color=0x2F3136,
            url=open_url
        )

        embed.set_author(name="🚨 NEW FREE GAME ALERT!", icon_url="https://i.imgur.com/8Q0N6jY.png")

        embed.description = (
            f"{status_text}\n"
            f"Platform: **{platform}** | Value: **{worth}**\n\n"
            f"[**Click here to claim this giveaway!**]({open_url})"
        )

        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=f"Game notifier by Malvinarum | Source: FreeStuffBot")

        return embed

    # --- Commands (Restored for REST API setup) ---

    @commands.group(name="freegames", invoke_without_command=True)
    @commands.guild_only()
    async def _freegames(self, ctx: commands.Context):
        """Manages the Free Games Giveaway Polling Notifier."""
        await ctx.send_help(ctx.command)

    @_freegames.command(name="setkey")
    @checks.is_owner()
    async def freegames_setkey(self, ctx: commands.Context, key: str):
        """
        Sets the FreeStuffBot REST API key (Owner only).

        This key is required for the bot to poll the API for new giveaways.
        """
        await self.config.api_key.set(key)
        await ctx.send("FreeStuffBot REST API Key has been successfully updated.")

    @_freegames.command(name="setchannel")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the channel for free game giveaway announcements.
        """
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Success! Free game announcements will now be posted in {channel.mention}.")
        log.info(f"Notification channel set to {channel.id} in guild {ctx.guild.id}")

    @_freegames.command(name="reset")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_reset(self, ctx: commands.Context):
        """
        Clears the list of already-announced giveaways for this server.

        The cog will re-announce all currently active giveaways after this.
        """
        await self.config.guild(ctx.guild).announced_ids.set([])
        await ctx.send(
            "The announced game history for this server has been cleared. The bot will re-announce all currently active giveaways on the next check.")

    @_freegames.command(name="checkapi")
    @checks.is_owner()
    async def freegames_checkapi(self, ctx: commands.Context, key: Optional[str] = None):
        """
        Manually tests the REST API connection and checks the data format.

        Optionally provide the API key as an argument for a one-time test:
        `[p]freegames checkapi <your_rest_api_key>` (Owner only).
        If no key is provided, it uses the key set with `[p]freegames setkey`.
        """
        await ctx.defer()

        api_key = key or await self.config.api_key()

        if not api_key:
            return await ctx.send(
                "❌ **Test Skipped:** No API key provided or set. Use `[p]freegames setkey <key>` first, or provide the key as an argument.")

        await ctx.send("Attempting manual connection to FreeStuffBot API for format check...")

        # Use the fetcher class's request method
        giveaways, error = await asyncio.to_thread(self.fetcher._make_request, "free", api_key)

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
        The 'current' command is no longer necessary, as the polling will
        continuously update. This command is kept as a placeholder.
        """
        await ctx.send(
            "⚠️ This command is deprecated. The cog automatically polls the API every 30 minutes for new giveaways.")
