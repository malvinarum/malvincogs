import asyncio
import json
import logging
import requests
from typing import List, Dict, Optional, Set
import discord  # Import Discord for using Embeds

from redbot.core import Config, commands, checks, app_commands, tasks
from redbot.core.utils.chat_formatting import box, pagify, humanize_list

# Setup logging for the cog
log = logging.getLogger("red.freegames")

# --- Helper Class for API Interactions ---

# Base URLs for the APIs
API_GP_BASE_URL = "https://www.gamerpower.com/api"


class GiveawayFetcher:
    """
    Utility class to interact with the GamerPower API for time-limited giveaways.
    This replaces the separate helper file and is internal to the cog.
    """

    def fetch_current_giveaways(self) -> Optional[List[Dict]]:
        """
        Fetches a list of time-limited game giveaways, filtered for full games.
        """
        # Filter by type=game to get only full game giveaways (not loot/keys/beta access)
        # Sort by popularity to get the most relevant ones first
        endpoint = f"{API_GP_BASE_URL}/giveaways?type=game&sort-by=popularity"
        log.debug(f"Fetching limited-time giveaways from: {endpoint}")

        try:
            response = requests.get(endpoint, timeout=10)
            response.raise_for_status()  # Raises an exception for bad status codes (4xx or 5xx)

            data = response.json()
            # GamerPower API returns a list of dictionaries for giveaways
            return data

        except requests.exceptions.RequestException as e:
            log.error(f"An error occurred while fetching giveaway data: {e}")
            return None
        except json.JSONDecodeError:
            log.error("Error: Could not decode JSON response from GamerPower API.")
            return None


# --- RedBot Cog Implementation ---

class FreeGames(commands.Cog):
    """
    A cog to track and notify about time-limited free game giveaways
    from various platforms (via GamerPower API).
    """

    def __init__(self, bot):
        self.bot = bot
        # Use RedBot's config system for persistent storage
        self.config = Config.get_conf(self, identifier=147789053890256247, force_registration=True)

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
        """Formats a single giveaway item into a clean Discord embed with the requested footer."""
        title = giveaway.get('title', 'Unknown Title')
        platform = giveaway.get('platforms', 'N/A')
        end_date = giveaway.get('end_date', 'Ongoing')
        worth = giveaway.get('worth', 'Free')
        open_url = giveaway.get('open_giveaway_url', '#')
        thumbnail = giveaway.get('thumbnail', None)  # Use the thumbnail image from the API

        description_full = giveaway.get('description', 'No description available')

        # Create the embed
        embed = discord.Embed(
            title=title,
            description=description_full[:300] + "..." if len(description_full) > 300 else description_full,
            # Keep description concise
            url=open_url,
            color=0x4CAF50  # Green color for 'free'
        )

        embed.set_author(name="Free Game Alert!", url=open_url)

        # Add fields
        embed.add_field(name="Platform(s)", value=platform, inline=True)
        embed.add_field(name="Value", value=worth, inline=True)
        embed.add_field(name="Ends", value=end_date, inline=False)

        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        # Add the requested footer
        embed.set_footer(text="Game notifier by Malvinarum")

        return embed

    # --- Background Task ---

    @tasks.loop(hours=6)  # Check for new games every 6 hours
    async def giveaway_check(self):
        """
        Background task to periodically check for new giveaways and announce them.
        """
        log.debug("Starting giveaway check task.")

        # 1. Fetch current giveaways from API
        giveaways = self.fetcher.fetch_current_giveaways()
        if not giveaways:
            log.warning("Giveaway check failed: No data retrieved from API.")
            return

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
            newly_announced_ids: List[str] = []

            # 4. Find and announce new giveaways
            for giveaway in giveaways:
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

            # 5. Save the updated list of announced IDs for the guild
            if newly_announced_ids:
                updated_ids = list(announced_ids) + newly_announced_ids
                await self.config.guild(guild).announced_ids.set(updated_ids)
                log.info(f"Announced {len(newly_announced_ids)} new giveaways in guild {guild_id}.")

    @giveaway_check.before_loop
    async def before_giveaway_check(self):
        await self.bot.wait_until_red_ready()

    # --- Commands ---

    @commands.group(name="freegames", invoke_without_command=True)
    @commands.guild_only()
    async def _freegames(self, ctx: commands.Context):
        """Manages the Free Games Giveaway Notifier."""
        await ctx.send_help(ctx.command)

    @_freegames.command(name="setchannel")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def freegames_setchannel(self, ctx: commands.Context, channel: commands.TextChannel):
        """
        Sets the channel for free game giveaway announcements.
        """
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Success! Free game announcements will now be posted in {channel.mention}.")
        log.info(f"Notification channel set to {channel.id} in guild {ctx.guild.id}")

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

    @_freegames.command(name="current")
    @commands.guild_only()
    async def freegames_current(self, ctx: commands.Context):
        """
        Displays all currently active game giveaways (up to 5).
        """
        await ctx.defer()
        giveaways = self.fetcher.fetch_current_giveaways()

        if not giveaways:
            return await ctx.send("I couldn't retrieve any active giveaways right now. The API might be down.")

        await ctx.send("✨ **TOP 5 CURRENT ACTIVE GAME GIVEAWAYS** ✨")

        # Send up to 5 embeds instead of pagified text
        for giveaway in giveaways[:5]:
            embed = self._format_giveaway(giveaway)
            await ctx.send(embed=embed)
            await asyncio.sleep(0.5)  # Small delay to prevent issues with sending multiple messages
