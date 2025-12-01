from __future__ import annotations

from operator import attrgetter
from typing import Union

import discord
import aiohttp
from redbot.core import commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

# Relative imports
from .config_holder import ConfigHolder
from .utilities import get_activity_list

_ = lambda s: s

IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_BASE = "https://api.igdb.com/v4"


class MemberStatus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = ConfigHolder.PlayerStatus

    async def _get_game_cover(self, game_name: str):
        """Fetches game cover from IGDB using PublisherManager credentials."""
        creds = await ConfigHolder.PublisherManager.igdb_creds()
        c_id = creds.get("client_id")
        c_secret = creds.get("client_secret")

        if not c_id or not c_secret: return None

        # Get Token (Simplified flow for specific command usage)
        params = {"client_id": c_id, "client_secret": c_secret, "grant_type": "client_credentials"}
        try:
            async with self.bot.session.post(IGDB_AUTH_URL, params=params) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                token = data["access_token"]
        except:
            return None

        headers = {"Client-ID": c_id, "Authorization": f"Bearer {token}", "Accept": "application/json"}
        query = f'search "{game_name}"; fields cover.url; limit 1;'

        try:
            async with self.bot.session.post(f"{IGDB_API_BASE}/games", headers=headers, data=query) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and "cover" in data[0]:
                        url = data[0]["cover"]["url"]
                        if url.startswith("//"): url = "https:" + url
                        return url.replace("t_thumb", "t_cover_big")
        except:
            pass
        return None

    @commands.command()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_channels=True, manage_guild=True)
    @commands.bot_has_permissions(embed_links=True)
    async def linkchannel(
            self, ctx: commands.Context, channel: discord.TextChannel, *, game: str = None
    ):
        """Link a channel to a game - Requires exact game name.
        If game is empty, clears the link.
        """
        if game:
            await self.config.channel(channel).game.set(game)
            await ctx.send(f"✅ Channel {channel.mention} linked to **{game}**.")
        else:
            await self.config.channel(channel).game.clear()
            await ctx.send(f"🔗 Link removed for {channel.mention}.")

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def splaying(self, ctx: commands.Context, *, game: str = None):
        """Shows who's playing what games."""
        game_name = _("what")
        ending = _(" any games.")
        game_list = []
        cover_url = None

        # Check if channel is linked to a specific game
        game_channel = await self.config.channel(ctx.channel).game()

        if game_channel:
            game = game_channel

        if game:
            game_name = game
            game_list = [game]
            ending = f" {game}."
            # Fetch cover if we are looking for a specific game
            cover_url = await self._get_game_cover(game)

        playing_data = await self.get_players_per_activity(
            ctx=ctx, game_name=game_list, forced_channel=game_channel
        )

        if playing_data:
            # We need to inject the cover_url into the embed generation logic
            # get_activity_list creates the embeds.
            # We might need to modify the embeds *after* they return or update utilities.
            # Updating logic locally is cleaner than changing shared utilities for one specific visual.

            embed_list = await get_activity_list(
                ctx, playing_data, game_name, discord.ActivityType.playing
            )

            # Inject thumbnail into the first page if we have one
            if cover_url and embed_list:
                for embed in embed_list:
                    embed.set_thumbnail(url=cover_url)

            await menu(
                ctx,
                pages=embed_list,
                controls=DEFAULT_CONTROLS,
                message=None,
                page=0,
                timeout=60,
            )
        else:
            await ctx.maybe_send_embed(
                _("No one is playing{ending}").format(ending=ending)
            )

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def swatching(self, ctx: commands.Context):
        """Shows who's watching what."""
        data = await self.get_players_per_activity(ctx=ctx, movie=True)
        if data:
            embed_list = await get_activity_list(
                ctx, data, None, discord.ActivityType.watching
            )
            await menu(
                ctx,
                pages=embed_list,
                controls=DEFAULT_CONTROLS,
                message=None,
                page=0,
                timeout=60,
            )
        else:
            await ctx.maybe_send_embed(_("No one is watching anything."))

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def slistening(self, ctx: commands.Context):
        """Shows who's listening what."""
        data = await self.get_players_per_activity(ctx=ctx, music=True)
        if data:
            embed_list = await get_activity_list(
                ctx, data, None, discord.ActivityType.listening
            )
            await menu(
                ctx,
                pages=embed_list,
                controls=DEFAULT_CONTROLS,
                message=None,
                page=0,
                timeout=60,
            )
        else:
            await ctx.maybe_send_embed(_("No one is listening to anything."))

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def sstreaming(self, ctx: commands.Context, *, game: str = None):
        """Shows who's streaming what games."""
        game_name = _("what")
        ending = "."
        game_list = []
        cover_url = None

        game_channel = await self.config.channel(ctx.channel).game()
        if game_channel:
            game = game_channel

        if game:
            game_name = game
            game_list = [game]
            ending = f" {game}."
            cover_url = await self._get_game_cover(game)

        streaming_data = await self.get_players_per_activity(
            ctx=ctx,
            stream=True,
            game_name=game_list,
            forced_channel=game_channel,
        )
        if streaming_data:
            embed_list = await get_activity_list(
                ctx, streaming_data, game_name, discord.ActivityType.streaming
            )

            if cover_url and embed_list:
                for embed in embed_list:
                    embed.set_thumbnail(url=cover_url)

            await menu(
                ctx,
                pages=embed_list,
                controls=DEFAULT_CONTROLS,
                message=None,
                page=0,
                timeout=60,
            )
        else:
            await ctx.maybe_send_embed(
                _("No one is streaming{ending}").format(ending=ending)
            )

    @staticmethod
    async def get_players_per_activity(
            ctx: commands.Context,
            stream: bool = False,
            music: bool = False,
            movie: bool = None,
            game_name: list[str] = None,
            forced_channel: Union[str, None] = None,
    ):
        """
        Scans guild members for specific activity types and returns a grouped dictionary.
        """
        looking_for = discord.ActivityType.playing
        name_property = "name"

        if stream:
            looking_for = discord.ActivityType.streaming
            name_property = "details"  # Usually details is the stream title
        elif music:
            looking_for = discord.ActivityType.listening
            name_property = "title"
        elif movie:
            looking_for = discord.ActivityType.watching
            name_property = "name"

        member_data = {}
        publisher_cache = await ConfigHolder.PublisherManager.publisher.get_raw()

        for member in ctx.guild.members:
            if member.bot: continue
            if not member.activities: continue

            # Filter for the activity type we want
            interested_in = [
                act for act in member.activities
                if act.type == looking_for
            ]

            if not interested_in: continue

            # Process activities
            activity_list = []
            for act in interested_in:
                # Get the name/title/details safely
                val = getattr(act, name_property, getattr(act, "name", None))
                if val:
                    activity_list.append(val)

            for game in activity_list:
                # 1. Filter by Game Name Argument
                if game_name:
                    # Case insensitive check
                    if game.lower() not in [g.lower() for g in game_name]:
                        # Try partial match if exact fail?
                        if not any(g.lower() in game.lower() for g in game_name):
                            continue

                # 2. Filter by Linked Channel
                if forced_channel and forced_channel.lower() != game.lower():
                    # If strictly forced, we skip mismatches
                    continue

                # 3. Determine Account Info (e.g. Steam username)
                # This depends on if we have mapped this game to a service in PublisherManager
                publisher = "movie" if looking_for == discord.ActivityType.watching else "spotify" if looking_for == discord.ActivityType.listening else None

                if not publisher and looking_for in [discord.ActivityType.playing, discord.ActivityType.streaming]:
                    publisher = publisher_cache.get(game)

                account_username = None
                if publisher:
                    user_accounts = (await ConfigHolder.AccountManager.user(member).get_raw()).get("account", {})
                    account_username = user_accounts.get(publisher)

                # 4. Role Hierarchy for Sorting
                # We sort visually by role hierarchy to keep admins/mods at top
                # Logic: highest hoist role position * -1 (descending sort)
                hoisted_roles = [r for r in member.roles if r.hoist]
                top_role = max(hoisted_roles, key=attrgetter("position")) if hoisted_roles else member.top_role
                role_value = top_role.position * -1

                # 5. Add to Data
                if game not in member_data:
                    member_data[game] = []

                member_data[game].append(
                    (member.mention, member.display_name, role_value, account_username)
                )

        return member_data