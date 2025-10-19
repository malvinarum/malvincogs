import aiohttp
import asyncio
import discord
from redbot.core import commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu


class BattlefieldStats(commands.Cog):
    """Retrieves and displays Battlefield player statistics."""

    def __init__(self, bot):
        self.bot = bot
        # Mapping of user-friendly game names to API identifiers, methods, and paths
        self.API_GAMES = {
            "bfv": {"api_name": "BFV", "api_id": 20847, "method": "GET", "path": "bf/stats/"},
            "bf1": {"api_name": "BF1", "api_id": 10011, "method": "GET", "path": "bf/stats/"},
            "bf2042": {"api_name": "BF2042", "api_id": 33024, "method": "GET", "path": "bf/stats/"},
            "bf4": {"api_name": "BF4", "api_id": 2048, "method": "GET", "path": "bf/stats/"},
            # NEW: Battlefield 6 uses a dedicated POST endpoint and API name
            "bf6": {"api_name": "BF6", "api_id": 33025, "method": "POST", "path": "bf6/stats/"},
        }

        # Mapping of user-friendly platform names to API platform identifiers
        self.API_PLATFORMS = {
            "pc": "pc",
            "psn": "psn",
            "xbox": "xbox",
        }
        # Base URL is static, path changes based on game
        self.API_BASE_URL = "https://api.gametools.network/"

    async def fetch_stats(self, player_name: str, platform: str, game: str):
        """Fetches stats data from the GameTools API, handling GET for old games and POST for BF6."""
        game_info = self.API_GAMES.get(game.lower())
        platform_api = self.API_PLATFORMS.get(platform.lower())

        if not game_info:
            return None, f"Invalid game specified. Choose from: {', '.join(self.API_GAMES.keys())}"
        if not platform_api:
            return None, f"Invalid platform specified. Choose from: {', '.join(self.API_PLATFORMS.keys())}"

        full_url = self.API_BASE_URL + game_info["path"]

        try:
            async with aiohttp.ClientSession() as session:

                if game_info["method"] == "GET":
                    # Standard parameters for older games (BFV, BF1, BF2042, BF4)
                    params = {
                        "player": player_name,
                        "platform": platform_api,
                        "game": game_info["api_name"],
                        "skip_battlelog": "true",
                        "metadata": "true",
                        "all_platforms": "false",
                        "raw": "false",
                    }
                    async with session.get(full_url, params=params) as response:
                        return await self._process_response(response, player_name, platform_api, game_info)

                elif game_info["method"] == "POST":
                    # Specific body structure required for BF6 (using the bf6/stats/ endpoint)
                    json_payload = {
                        "game": game_info["api_name"],
                        "platform": platform_api,
                        "player": player_name,
                    }
                    async with session.post(full_url, json=json_payload) as response:
                        return await self._process_response(response, player_name, platform_api, game_info)

        except asyncio.TimeoutError:
            return None, "The request timed out. The GameTools API may be slow right now."
        except Exception as e:
            return None, f"An unexpected error occurred: {type(e).__name__}: {e}"

    async def _process_response(self, response, player_name, platform_api, game_info):
        """Helper to process the API response."""
        if response.status == 200:
            data = await response.json()

            # Check for a 'message' key indicating a non-standard 200 error (e.g., API found game but not player)
            if isinstance(data, dict) and data.get('message') == 'Player not found.':
                return None, f"Player **{player_name}** not found on **{platform_api.upper()}** for **{game_info['api_name']}**. (API message)"

            # The BF6 endpoint returns an array for player stats, so we must extract the first item
            if game_info["api_name"] == "BF6" and isinstance(data, list) and data:
                return data[0], None
            elif isinstance(data, dict):
                return data, None
            else:
                # Handle unexpected structure like empty list or strange dict
                return None, f"API returned unexpected data structure for **{game_info['api_name']}**."

        elif response.status == 404:
            # Standard 404 for player not found
            return None, f"Player **{player_name}** not found on **{platform_api.upper()}** for **{game_info['api_name']}**. (404 error)"
        else:
            text = await response.text()
            return None, f"API error: {response.status} - {text[:100]}..."

    @commands.command()
    async def bfstats(self, ctx, player: str, platform: str, game: str = "bfv"):
        """
        Displays a player's Battlefield statistics.

        Usage: [p]bfstats <player_name> <platform> [game]

        Platform options: PC, PSN, XBOX
        Game options: BFV (default), BF1, BF4, BF2042, BF6
        """
        async with ctx.typing():  # Typing indicator is now robust

            data, error = await self.fetch_stats(player, platform, game)

        if error:
            # We now append the raw input in the error message to remind the user of their exact query
            return await ctx.send(f"❌ Error for `!bfstats {player} {platform} {game}`: {error}")

        # --- Data Parsing and Formatting ---
        game_id = self.API_GAMES.get(game.lower())["api_id"]
        game_name = self.API_GAMES.get(game.lower())["api_name"]

        player_name = data.get("userName", player)
        avatar_url = data.get("avatar")

        # Determine the correct rank field based on game ID
        rank = data.get("rank")
        # BF2042 and BF6 (new games) might use a dedicated rankName field
        if game_id in [33024, 33025]:
            rank_name = data.get("rankName", "N/A")
            rank_display = f"{rank} ({rank_name})"
        elif rank is not None:
            rank_display = str(rank)
        else:
            rank_display = "N/A"

        # --- Create Pages ---

        # Page 1: Core Combat Stats (K/D, KPM, Score)
        embed1 = discord.Embed(
            title=f"📊 Battlefield Stats: {player_name} ({game_name})",
            color=discord.Color.dark_green()
        )
        embed1.set_thumbnail(url=avatar_url or "https://placehold.co/100x100/1e88e5/ffffff?text=BF")
        embed1.set_footer(text=f"Platform: {platform.upper()} | Rank: {rank_display}")

        # Core Metrics
        embed1.add_field(name="Kills", value=f"`{data.get('kills', 'N/A')}`", inline=True)
        embed1.add_field(name="Deaths", value=f"`{data.get('deaths', 'N/A')}`", inline=True)
        embed1.add_field(name="K/D Ratio", value=f"`{data.get('kdRatio', 'N/A')}`", inline=True)

        embed1.add_field(name="Kills Per Minute", value=f"`{data.get('kpm', 'N/A')}`", inline=True)
        embed1.add_field(name="Time Played", value=f"`{data.get('timePlayed', 'N/A')}`", inline=True)
        embed1.add_field(name="Total Score", value=f"`{data.get('score', 'N/A')}`", inline=True)

        # Win/Loss
        wins = data.get('wins', 0)
        losses = data.get('losses', 0)
        win_loss_ratio = data.get('wlRatio', 'N/A')

        embed1.add_field(name="Wins / Losses", value=f"`{wins} / {losses}`", inline=True)
        embed1.add_field(name="W/L Ratio", value=f"`{win_loss_ratio}`", inline=True)
        embed1.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer

        # Page 2: Game-Specific Details (Best Class, SPM, Headshots)
        embed2 = discord.Embed(
            title=f"🎯 Player Achievements: {player_name} ({game_name})",
            color=discord.Color.dark_green()
        )
        embed2.set_thumbnail(url=avatar_url or "https://placehold.co/100x100/1e88e5/ffffff?text=BF")
        embed2.set_footer(text=f"Platform: {platform.upper()} | Rank: {rank_display}")

        best_class = data.get("bestClass", "N/A")
        embed2.add_field(name="Best Class", value=f"**{best_class}**", inline=True)
        embed2.add_field(name="Score Per Minute (SPM)", value=f"`{data.get('spm', 'N/A')}`", inline=True)
        embed2.add_field(name="Headshots", value=f"`{data.get('headshots', 'N/A')}`", inline=True)

        # BF2042 and BF6 specific details
        if game_id in [33024, 33025]:
            # New games often use different keys for vehicle/gadget kills
            embed2.add_field(name="Best Weapon", value=f"`{data.get('bestWeapon', 'N/A')}`", inline=True)
            embed2.add_field(name="Revives", value=f"`{data.get('revives', 'N/A')}`", inline=True)
            embed2.add_field(name="Gadget Kills", value=f"`{data.get('gadgetKills', 'N/A')}`", inline=True)

        # General details for older games
        else:
            embed2.add_field(name="Heals", value=f"`{data.get('heals', 'N/A')}`", inline=True)
            embed2.add_field(name="Resupplies", value=f"`{data.get('resupplies', 'N/A')}`", inline=True)
            embed2.add_field(name="Vehicles Destroyed", value=f"`{data.get('vehiclesDestroyed', 'N/A')}`", inline=True)

        # --- Display Menu ---
        pages = [embed1, embed2]
        await menu(ctx, pages, DEFAULT_CONTROLS, timeout=60)


async def setup(bot):
    await bot.add_cog(BattlefieldStats(bot))
