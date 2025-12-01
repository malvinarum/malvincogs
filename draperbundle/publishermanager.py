import asyncio
import contextlib
import json
import logging
import time
from typing import Optional, Dict

import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import pagify, box
from discord.ext import tasks

# Relative imports
from .config_holder import ConfigHolder
from .utilities import get_member_activity, get_supported_platforms

logger = logging.getLogger("red.drapercogs.publisher_manager")

IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_BASE = "https://api.igdb.com/v4"


class PublisherManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = ConfigHolder.PublisherManager

        self._igdb_token = None
        self._token_expires_at = 0

        self._update_task = self.update_game_database_loop.start()

    async def cog_unload(self):
        self._update_task.cancel()

    # --- IGDB HELPER METHODS ---

    async def _get_igdb_token(self, client_id: str, client_secret: str) -> Optional[str]:
        """Fetches a valid App Access Token for IGDB."""
        now = time.time()
        if self._igdb_token and now < self._token_expires_at:
            return self._igdb_token

        params = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }

        try:
            async with self.bot.session.post(IGDB_AUTH_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._igdb_token = data["access_token"]
                    self._token_expires_at = now + data["expires_in"] - 60  # Buffer
                    return self._igdb_token
                else:
                    logger.error(f"Failed to get IGDB Token: {resp.status} - {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"IGDB Auth Exception: {e}")
            return None

    async def _query_igdb(self, game_name: str) -> Optional[Dict]:
        """Queries IGDB for a game and attempts to identify the platform."""
        creds = await self.config.igdb_creds()
        c_id = creds.get("client_id")
        c_secret = creds.get("client_secret")

        if not c_id or not c_secret:
            return None

        token = await self._get_igdb_token(c_id, c_secret)
        if not token:
            return None

        headers = {
            "Client-ID": c_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }

        # Query for game, asking for websites and external games (steam id, etc)
        # Category 1 = Main Game, 2 = DLC, etc.
        query = f'search "{game_name}"; fields name, websites.url, external_games.category; limit 1;'

        try:
            async with self.bot.session.post(f"{IGDB_API_BASE}/games", headers=headers, data=query) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return data[0]  # Best match
        except Exception as e:
            logger.error(f"IGDB Query Error: {e}")

        return None

    # --- COMMANDS ---

    @commands.group(enabled=True, case_insensitive=True)
    async def service(self, ctx: commands.Context):
        """Add, Remove, Show services & IGDB Config"""

    @commands.is_owner()
    @service.command(name="igdb")
    async def setup_igdb(self, ctx: commands.Context):
        """Interactive setup for IGDB API credentials."""
        await ctx.send(
            "To enable auto-discovery, we need IGDB (Twitch) API credentials.\n"
            "1. Go to <https://dev.twitch.tv/console>\n"
            "2. Register an Application (Category: Game Integration)\n"
            "3. Copy **Client ID** and **Client Secret**.\n\n"
            "Type `cancel` to exit."
        )

        try:
            await ctx.send("**Enter Client ID:**")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            if msg.content.lower() == "cancel": return await ctx.send("Cancelled.")
            c_id = msg.content.strip()

            await ctx.send("**Enter Client Secret:**")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            if msg.content.lower() == "cancel": return await ctx.send("Cancelled.")
            c_secret = msg.content.strip()

            await self.config.igdb_creds.set({"client_id": c_id, "client_secret": c_secret})
            await ctx.send("✅ Credentials saved! I will now try to fetch metadata for games.")

        except asyncio.TimeoutError:
            await ctx.send("Timed out.")

    @commands.is_owner()
    @service.command(name="add", aliases=["+"])
    async def service_add(self, ctx: commands.Context, identifier: str, *, name: str):
        """Add a service to the list of supported services"""
        new_service = dict(name=name, identifier=identifier)
        new_service = {new_service["identifier"]: new_service}

        async with self.config.services() as services:
            services.update(new_service)
        await ctx.tick()

    @commands.is_owner()
    @service.command(name="remove", aliases=["-", "delete"])
    async def service_remove(self, ctx: commands.Context, *, message: str):
        """Remove a service from the list of supported services"""
        async with self.config.services() as services:
            if message in services:
                del services[message]
                await ctx.tick()
            else:
                await ctx.send("Service not found.")

    @service.command(name="show")
    @commands.guild_only()
    async def service_show(self, ctx: commands.Context):
        """Show all services in the list of supported services"""
        platforms = await get_supported_platforms()
        embed = discord.Embed(title="Supported Platforms", color=await ctx.embed_color())

        description = ""
        for command, name in platforms:
            description += f"**{name}** (`{command}`)\n"

        embed.description = description
        await ctx.send(embed=embed)

    @commands.is_owner()
    @service.command(name="playing", enabled=True)
    async def service_playing(self, ctx: commands.Context):
        """Shows how many games need to be parsed (assigned to a service)."""
        await self._run_update_logic()

        config_data = await self.config.publisher.get_raw()
        existing_data = [key for key, value in config_data.items() if value is None]

        await ctx.send(f"{len(existing_data)} games need to be parsed.")

        if existing_data:
            text = json.dumps(existing_data, indent=2)
            for page in pagify(text):
                await ctx.send(box(page, lang="json"))

    @commands.is_owner()
    @service.group(name="parse")
    async def _parse(self, ctx: commands.Context):
        """Parses game database"""

    @_parse.command(name="auto")
    async def _parse_auto(self, ctx: commands.Context):
        """Attempts to auto-match unparsed games using IGDB."""
        creds = await self.config.igdb_creds()
        if not creds:
            return await ctx.send(f"⚠️ IGDB credentials missing. Run `{ctx.prefix}service igdb` first.")

        await ctx.send("🔍 Starting auto-discovery... This may take a while.")

        config_data = await self.config.publisher()
        unparsed = [k for k, v in config_data.items() if v is None]

        if not unparsed:
            return await ctx.send("Nothing to parse.")

        platforms = await get_supported_platforms()
        # platforms structure is [(id, name), ...]

        manual_map = {
            "visual studio": "coding",
            "chrome": "web",
            "spotify": "spotify"
        }

        matched_count = 0

        async with self.config.publisher() as publisher_data:
            for game in unparsed:
                lower_game = game.lower()

                # 1. Manual Override check
                found = False
                for k, v in manual_map.items():
                    if k in lower_game:
                        # Logic to apply mapped service if it exists could go here
                        pass

                # 2. IGDB Query
                if not found:
                    token = await self._get_igdb_token(creds['client_id'], creds['client_secret'])
                    if not token:
                        await ctx.send("Failed to authenticate with IGDB.")
                        return

                    headers = {
                        "Client-ID": creds['client_id'],
                        "Authorization": f"Bearer {token}",
                    }

                    q = f'search "{game}"; fields name, websites.category, websites.url; limit 1;'

                    try:
                        async with self.bot.session.post(f"{IGDB_API_BASE}/games", headers=headers, data=q) as resp:
                            if resp.status == 200:
                                res_json = await resp.json()
                                if res_json:
                                    g_data = res_json[0]
                                    logger.info(f"IGDB Found match for '{game}': {g_data['name']}")
                                    # Future: Implement logic to map IGDB website categories to your services

                            await asyncio.sleep(0.25)
                    except Exception:
                        pass

        await ctx.send(f"Auto-parse complete. Matched {matched_count} games.")

    @_parse.command(name="manual")
    async def _parse_manual(self, ctx: commands.Context):
        """Interactive manual parsing."""
        config_data = await self.config.publisher()
        existing_data = [key for key, value in config_data.items() if value is None]
        await self.parse_playing(ctx, existing_data)

    async def parse_playing(self, ctx, existing_data):
        """Interactive parsing session."""
        if not existing_data:
            return await ctx.send("Nothing to parse.")

        platforms = await get_supported_platforms()

        prompt_map = {str(i + 1): name for i, (ident, name) in enumerate(platforms)}
        prompt_map[str(len(platforms) + 1)] = "None"
        prompt_map[str(len(platforms) + 2)] = "Delete"
        prompt_map[str(len(platforms) + 3)] = "Stop"

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        async with self.config.publisher() as publisher_data:
            for game in existing_data:
                # Build the embed
                desc_lines = []
                for k, v in prompt_map.items():
                    desc_lines.append(f"**{k}.** {v}")

                # Try to enhance with IGDB info if available
                igdb_info = ""
                data = await self._query_igdb(game)
                if data:
                    igdb_info = f"\nIGDB Match: **{data.get('name')}** (ID: {data.get('id')})"

                embed = discord.Embed(
                    title=f"Assign Service to: {game}",
                    description=f"Type the number to assign.{igdb_info}"
                )
                embed.add_field(name="Options", value="\n".join(desc_lines))

                msg = await ctx.send(embed=embed)

                try:
                    reply = await self.bot.wait_for("message", check=check, timeout=60)
                except asyncio.TimeoutError:
                    await ctx.send("Timed out.")
                    break

                content = reply.content.lower().strip()
                choice = prompt_map.get(content)

                if content == "stop" or choice == "Stop":
                    break

                if choice == "Delete":
                    if game in publisher_data:
                        del publisher_data[game]
                    await ctx.send(f"🗑️ Deleted {game}.")
                elif choice == "None":
                    publisher_data[game] = None
                    await ctx.send(f"⏭️ Skipped {game}.")
                elif choice:
                    ident = next((i for i, n in platforms if n == choice), None)
                    if ident:
                        publisher_data[game] = ident
                        await ctx.send(f"✅ Mapped **{game}** to **{choice}**.")
                else:
                    await ctx.send("Invalid, skipping.")

                try:
                    await msg.delete()
                    await reply.delete()
                except:
                    pass

        await ctx.send("Session ended.")

    async def _run_update_logic(self):
        """Scans all users for new game activities."""
        config_data = await self.config.publisher.get_raw()
        new_games = []

        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot: continue
                activity_name = get_member_activity(member, database=True)

                if activity_name and activity_name not in config_data and activity_name not in new_games:
                    new_games.append(activity_name)

        if new_games:
            async with self.config.publisher() as publisher:
                for game in new_games:
                    publisher[game] = None
            logger.info(f"Added {len(new_games)} new games to PublisherManager.")

    @tasks.loop(minutes=15)
    async def update_game_database_loop(self):
        await self._run_update_logic()

    @update_game_database_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()