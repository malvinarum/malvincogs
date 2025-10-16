import asyncio
import json
import logging
import requests
from typing import List, Dict, Optional, Set
import discord  # Import Discord for using Embeds

# We import core components from redbot.core
from redbot.core import Config, commands, checks, app_commands
# FIX: Import tasks directly from discord.ext for best compatibility across RedBot versions
from discord.ext import tasks
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

# Setup logging for the cog
log = logging.getLogger("red.freegames")

# --- Helper Class for API Interactions ---

# New Base URL for the FreeStuffBot API
API_FSB_BASE_URL = "https://api.freestuffbot.xyz/v2"


class GiveawayFetcher:
    """
    Utility class to interact with the FreeStuffBot API for time-limited giveaways.
    """

    def fetch_current_giveaways(self, api_key: str) -> Optional[List[Dict]]:
        """
        Fetches a list of time-limited game giveaways using the provided API key.
        """
        if not api_key:
            log.warning("FreeStuffBot API key is not set. Cannot fetch giveaways.")
            return None

        endpoint = f"{API_FSB_BASE_URL}/giveaways"
        headers = {
            # FreeStuffBot API requires the key in the Authorization header
            "Authorization": f"Bearer {api_key}"
        }

        log.debug(f"Fetching limited-time giveaways from: {endpoint}")

        try:
            response = requests.get(endpoint, headers=headers, timeout=10)
            response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)

            data = response.json()
            # FreeStuffBot API returns an object with a 'giveaways' array
            return data.get('giveaways', [])

        except requests.exceptions.RequestException as e:
            log.error(f"An error occurred while fetching giveaway data: {e}")
            return None
        except json.JSONDecodeError:
            log.error("Error: Could not decode JSON response from FreeStuffBot API.")
            return None


# --- RedBot Cog Implementation ---

class FreeGames(commands.Cog):
    """
    A cog to track and notify about time-limited free game giveaways
    from various platforms (via FreeStuffBot API).
    """

    def __init__(self, bot):
        self.bot = bot
        # Use a global identifier for the API key, as it's common across all guilds
        self.config = Config.get_conf(self, identifier=147789053890256247, force_registration=True)

        # Global configuration defaults (for API Key)
        default_global = {
            # The API key is now set to an empty string. Owner must use [p]freegames setkey <key> to configure.
            "api_key": ""
        }
        self.config.register_global(**default_global)

        # Guild configuration defaults
        default_guild = {
            "channel_id": None,  # Channel to send notifications to
            "announced_ids": [],  # List of giveaway IDs that have already been announced
        }
        self.config.register_guild(**default_guild)

        self.fetcher = GiveawayFetcher()
        self.giveaway_check.start()
        log.info("FreeGames cog initialized and task started.")

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        self.giveaway_check.cancel()
        log.info("FreeGames cog task cancelled.")

    # --- Utility Methods ---

    def _format_giveaway(self, giveaway: Dict) -> discord.Embed:
        """
        Formats a single giveaway item (using FreeStuffBot keys)
        into a clean Discord embed, designed to mimic the user's provided example.
        """
        # Mapping FreeStuffBot keys to embed fields
        title = giveaway.get('title', 'Unknown Title')
        platform = giveaway.get('platform', 'N/A')
        end_date = giveaway.get('end_date', 'Ongoing')
        worth = giveaway.get('original_price', 'Free')
        open_url = giveaway.get('link', '#')
        image_url = giveaway.get('image', None)  # Renamed to image_url for clarity

        # Determine the status text (e.g., "Free to keep" vs. "Free until...")
        status_text = ""
        if end_date and end_date.lower() not in ["ongoing", "n/a", "tbd"]:
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

    # --- Background Task ---

    @tasks.loop(hours=6)  # Check for new games every 6 hours
    async def giveaway_check(self):
        """
        Background task to periodically check for new giveaways and announce them.
        """
        log.debug("Starting giveaway check task.")

        api_key = await self.config.api_key()

        # 1. Fetch current giveaways from API
        giveaways = self.fetcher.fetch_current_giveaways(api_key)

        if not giveaways:
            # Added a more specific warning for key status
            if not api_key:
                log.warning("Giveaway check skipped: FreeStuffBot API key is not configured.")
            else:
                log.warning("Giveaway check failed: No data retrieved from API (check key validity or API status).")
            return

        log.debug(f"Successfully fetched {len(giveaways)} active giveaways.")

        # 2. Iterate through all configured guilds
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():

            # Skip if no notification channel is set
            channel_id = guild_data.get("channel_id")
            if not channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None

            if not channel:
                log.warning(f"Notification channel not found for guild {guild_id}. Skipping.")
                continue

            # 3. Get previously announced IDs for this guild
            announced_ids: Set[str] = set(guild_data.get("announced_ids", []))
            log.debug(f"Guild {guild_id} has {len(announced_ids)} IDs in history.")
            newly_announced_ids: List[str] = []

            games_skipped_count = 0

            # 4. Find and announce new giveaways
            for giveaway in giveaways:
                # Assuming 'id' is the unique identifier for persistence
                giveaway_id = str(giveaway.get("id"))

                if giveaway_id not in announced_ids:

                    try:
                        embed = self._format_giveaway(giveaway)  # Get the embed

                        # Announce the game using the embed
                        await channel.send(content="🚨 **NEW FREE GAME ALERT!** 🚨", embed=embed)

                        # Add to the list to be saved
                        newly_announced_ids.append(giveaway_id)

                        # Add a small delay to avoid rate-limiting if many games are new
                        await asyncio.sleep(1)

                    except Exception as e:
                        log.error(f"Failed to announce giveaway {giveaway_id} in channel {channel_id}: {e}")

                else:
                    games_skipped_count += 1

            # 5. Save the updated list of announced IDs for the guild
            if newly_announced_ids:
                updated_ids = list(announced_ids) + newly_announced_ids
                await self.config.guild(guild).announced_ids.set(updated_ids)
                log.info(
                    f"Announced {len(newly_announced_ids)} new giveaways in guild {guild_id}. Skipped {games_skipped_count} existing ones.")
            else:
                log.info(f"No new giveaways found for guild {guild_id}. Skipped {games_skipped_count} existing ones.")

    @giveaway_check.before_loop
    async def before_giveaway_check(self):
        await self.bot.wait_until_red_ready()

    # --- Commands ---

    @commands.group(name="freegames", invoke_without_command=True)
    @commands.guild_only()
    async def _freegames(self, ctx: commands.Context):
        """Manages the Free Games Giveaway Notifier."""
        await ctx.send_help(ctx.command)

    @_freegames.command(name="setkey")
    @checks.is_owner()
    async def freegames_setkey(self, ctx: commands.Context, key: str):
        """
        Sets the FreeStuffBot REST API Key (Owner only).

        This key is required to fetch giveaway data.
        """
        await self.config.api_key.set(key)
        await ctx.send("FreeStuffBot REST API key has been successfully updated.")

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
        Clears the list of previously announced giveaway IDs for this server.

        Use this if you think the bot missed an announcement or want to
        re-post all currently active giveaways.
        """
        await self.config.guild(ctx.guild).announced_ids.set([])
        await ctx.send(
            "Cleared the giveaway announcement history for this server. The next check will treat all active giveaways as new.")

    @_freegames.command(name="checknow")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_checknow(self, ctx: commands.Context):
        """
        Triggers an immediate check for new giveaways.
        Only announces *new* giveaways to the set channel.
        """
        await ctx.send(
            "Checking for new giveaways now. If any are found, they will be posted in the announcement channel.")
        # Running the task immediately
        await self.giveaway_check()
        await ctx.send("Check complete.")

    @_freegames.command(name="testapi")
    @checks.is_owner()
    async def freegames_testapi(self, ctx: commands.Context):
        """
        Tests the connection to the FreeStuffBot API and prints response data structure. (Owner only)
        """
        await ctx.defer()

        api_key = await self.config.api_key()
        if not api_key:
            return await ctx.send(
                "❌ **Error:** The FreeStuffBot API key is not set. Please use `[p]freegames setkey <key>`.")

        await ctx.send("Attempting to connect to FreeStuffBot API...")

        giveaways = self.fetcher.fetch_current_giveaways(api_key)

        if giveaways is None:
            return await ctx.send(
                "❌ **Error:** API request failed. Check your API key and RedBot console logs for connection errors.")

        count = len(giveaways)

        if count == 0:
            return await ctx.send(
                "⚠️ **API Test Successful:** The connection is working, but the API returned **0 active giveaways**.")

        # Successfully received data, show the structure of the first item
        first_giveaway = giveaways[0]

        # Prepare a readable JSON string for the first item
        try:
            sample_data = json.dumps(first_giveaway, indent=2)
            if len(sample_data) > 1900:  # Ensure it fits in a Discord message
                sample_data = sample_data[:1900] + "\n... (truncated)"
        except Exception:
            sample_data = "Could not serialize sample data to JSON."

        await ctx.send(
            f"✅ **API Test Successful:** Found **{count}** active giveaways.\n"
            f"**First Giveaway Item Data Structure:**\n"
            f"{box(sample_data, lang='json')}"
        )

    @_freegames.command(name="current")
    @commands.guild_only()
    async def freegames_current(self, ctx: commands.Context):
        """
        Displays all currently active game giveaways (up to 5).
        """
        await ctx.defer()

        api_key = await self.config.api_key()
        if not api_key:
            return await ctx.send("The FreeStuffBot API key is not set. Please use `[p]freegames setkey <key>`.")

        giveaways = self.fetcher.fetch_current_giveaways(api_key)

        if not giveaways:
            return await ctx.send(
                "I couldn't retrieve any active giveaways right now. The API might be down or your API key is invalid.")

        await ctx.send("✨ **TOP 5 CURRENT ACTIVE GAME GIVEAWAYS** ✨")

        # Send up to 5 embeds instead of pagified text
        for giveaway in giveaways[:5]:
            embed = self._format_giveaway(giveaway)
            await ctx.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay to prevent issues with sending multiple messages
