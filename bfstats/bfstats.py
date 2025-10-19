import aiohttp
import asyncio
import discord
from redbot.core import commands
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

# Define the base URL for the GameTools API
API_BASE_URL = "https://api.gametools.network/bfstats/"


class BattlefieldStats(commands.Cog):
    """
    Retrieves and displays Battlefield player statistics from GameTools Network API.
    Supports BF1, BFV, and BF2042.
    """

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        # Clean up the aiohttp session when the cog is unloaded
        asyncio.create_task(self.session.close())

    async def get_bf_stats(self, player: str, platform: str, game: str):
        """
        Fetches the player stats from the GameTools API.

        Args:
            player (str): The player's name.
            platform (str): The platform (pc, psn, xbox).
            game (str): The Battlefield game (bfv, bf1, bf2042).

        Returns:
            dict: The JSON data from the API, or None on failure.
        """
        params = {
            "player": player,
            "platform": platform,
            "game": game,
            "lang": "en"  # Use English language for stats
        }

        try:
            async with self.session.get(API_BASE_URL, params=params) as response:
                if response.status != 200:
                    # The API might return non-200 for player not found or API errors
                    data = await response.json()
                    # Check for specific error message structure from the API
                    if data.get('error') == 'Player not found':
                        return "Player Not Found"
                    return f"API Error (Status {response.status}): {data.get('error', 'Unknown Error')}"

                return await response.json()

        except aiohttp.ClientError as e:
            return f"Network Error: Could not connect to the stats API. ({e})"
        except asyncio.TimeoutError:
            return "Timeout Error: The API took too long to respond."
        except Exception as e:
            return f"An unexpected error occurred: {e}"

    @commands.command(name="bfstats")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def get_stats_command(self, ctx: commands.Context, player: str, platform: str, game: str = "bfv"):
        """
        Checks a player's Battlefield stats.

        <player>: The player's name (e.g., 'your_name').
        <platform>: The platform ('pc', 'psn', or 'xbox').
        <game>: The game ('bfv', 'bf1', or 'bf2042'). Defaults to 'bfv'.

        Example: [p]bfstats JohnDoe pc bfv
        """

        # Validate platform input
        valid_platforms = ["pc", "psn", "xbox"]
        platform = platform.lower()
        if platform not in valid_platforms:
            return await ctx.send(
                f"Invalid platform specified. Please use one of: {', '.join(valid_platforms)}"
            )

        # Validate game input
        valid_games = ["bfv", "bf1", "bf2042"]
        game = game.lower()
        if game not in valid_games:
            return await ctx.send(
                f"Invalid game specified. Please use one of: {', '.join(valid_games)}"
            )

        await ctx.trigger_typing()

        data = await self.get_bf_stats(player, platform, game)

        # Check if data is an error string
        if isinstance(data, str):
            if data == "Player Not Found":
                return await ctx.send(
                    f"Player **{player}** on platform **{platform.upper()}** for **{game.upper()}** was not found."
                )
            return await ctx.send(data)

        # --- Data Formatting and Embed Generation ---

        # Utility function for safely retrieving values
        def get_value(key, default="N/A"):
            return data.get(key, default)

        # Set the title and color based on the game
        game_map = {
            "bfv": {"title": "Battlefield V Stats", "color": discord.Color.dark_green()},
            "bf1": {"title": "Battlefield 1 Stats", "color": discord.Color.gold()},
            "bf2042": {"title": "Battlefield 2042 Stats", "color": discord.Color.blue()},
        }

        info = game_map.get(game, {"title": "Battlefield Stats", "color": discord.Color.default()})

        # --- Page 1: Overview ---
        embed1 = discord.Embed(
            title=f"{info['title']} for {get_value('userName')}",
            description=f"Platform: **{platform.upper()}** | Rank: **{get_value('rank')}**",
            color=info['color']
        )

        # Add basic stats
        embed1.add_field(name="K/D Ratio", value=get_value('kdr'), inline=True)
        embed1.add_field(name="Kills", value=f"{int(get_value('kills', 0)):,}", inline=True)
        embed1.add_field(name="Deaths", value=f"{int(get_value('deaths', 0)):,}", inline=True)

        embed1.add_field(name="Win/Loss %", value=get_value('wlr'), inline=True)
        embed1.add_field(name="Headshots", value=f"{int(get_value('headshots', 0)):,}", inline=True)
        embed1.add_field(name="Skill Rating", value=get_value('skill', 'N/A'), inline=True)

        embed1.add_field(name="Time Played", value=get_value('timePlayed', 'N/A'), inline=False)

        if get_value('avatar'):
            embed1.set_thumbnail(url=get_value('avatar'))
        embed1.set_footer(text=f"Data provided by api.gametools.network | Page 1/2: Overview")

        # --- Page 2: Score and Best Class ---
        embed2 = discord.Embed(
            title=f"{info['title']} - Scores & Class",
            description=f"Summary stats for {get_value('userName')}.",
            color=info['color']
        )

        # Score Breakdown
        embed2.add_field(name="Total Score", value=f"{int(get_value('score', 0)):,}", inline=True)
        embed2.add_field(name="Score/Min (SPM)", value=get_value('spm'), inline=True)
        embed2.add_field(name="Kills/Min (KPM)", value=get_value('kpm'), inline=True)

        # Best Class
        best_class = get_value('bestClass', 'N/A')
        class_score = get_value('bestClassScore', 'N/A')
        embed2.add_field(name="Best Class", value=best_class, inline=True)
        if best_class != 'N/A' and class_score != 'N/A':
            embed2.add_field(name="Class Score", value=f"{int(class_score):,}", inline=True)

        # Separator field
        embed2.add_field(name="\u200b", value="\u200b", inline=False)

        # Best Weapon (optional, API response might vary)
        try:
            best_weapon = data['bestWeapon']['weaponName']
            weapon_kills = int(data['bestWeapon']['kills'])
            embed2.add_field(name="Best Weapon", value=best_weapon, inline=True)
            embed2.add_field(name="Weapon Kills", value=f"{weapon_kills:,}", inline=True)
        except (KeyError, TypeError):
            embed2.add_field(name="Best Weapon", value="N/A", inline=True)
            embed2.add_field(name="Weapon Kills", value="N/A", inline=True)

        if get_value('avatar'):
            embed2.set_thumbnail(url=get_value('avatar'))
        embed2.set_footer(text=f"Data provided by api.gametools.network | Page 2/2: Score & Class")

        # Use the Redbot menu utility for pagination
        pages = [embed1, embed2]
        await menu(ctx, pages, DEFAULT_CONTROLS)

    # Alias for common mistake
    @commands.command(name="bf2042stats", hidden=True)
    async def bf2042_alias(self, ctx: commands.Context, player: str, platform: str):
        """Alias for [p]bfstats <player> <platform> bf2042"""
        await ctx.invoke(self.get_stats_command, player, platform, "bf2042")


def setup(bot):
    bot.add_cog(BattlefieldStats(bot))
