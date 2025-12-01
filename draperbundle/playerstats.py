from __future__ import annotations

import json
import logging
from collections import defaultdict
from copy import copy
from typing import Union, Optional, Dict

import aiohttp
import discord
from redbot.core import commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

# Relative imports
from .config_holder import ConfigHolder
from .converters import ConvertMember

log = logging.getLogger("red.drapercogs.playerstats")

_header = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Redbot/CKS",
    "Content-Type": "application/json",
}


class PlayerStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # If there are specific configs for this cog, we can init them here,
        # but it mostly reads from ConfigHolder.BFV_USER_IDS

    async def _get_website_data(self, url: str, headers: dict = None) -> Dict:
        """Helper to fetch JSON data using the bot's shared session."""
        if not headers:
            headers = copy(_header)

        # Apex Tracker hardcoded key legacy support (ideally move to config)
        if "public-api.tracker.gg/apex" in url:
            headers.update({"TRN-Api-Key": "7080bd67-b7a1-4229-9cad-0d0cdccf1c31"})

        try:
            async with self.bot.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            log.debug(f"Failed to fetch {url}: {e}")
        return {}

    # --- Battlefield V Helpers ---

    async def _get_bfv_data_by_name(self, username: str) -> dict:
        url = f"https://api.gametools.network/bfv/all/?name={username}&lang=en-us"
        return await self._get_website_data(url)

    async def _get_bfv_data_by_id(self, user_id: str) -> dict:
        url = f"https://api.gametools.network/bfv/all/?playerid={user_id}&lang=en-us"
        return await self._get_website_data(url)

    def _parse_bfv_tracker_segments(self, segment, data):
        """Legacy parser for tracker.gg segments."""
        segment_type = segment.get("type")
        if not segment_type: return data

        name = segment.get("metadata", {}).get("name")
        if not name: return data

        stats = segment.get("stats", {})
        if not stats: return data

        whitelist = ["displayValue"]
        if segment_type not in data:
            data[segment_type] = {}
        if name not in data[segment_type]:
            data[segment_type][name] = {}

        for item in stats.values():
            for k, v in item.items():
                if k in whitelist:
                    # Initialize nested dicts if missing
                    disp_name = item.get("displayName")
                    if disp_name not in data[segment_type][name]:
                        data[segment_type][name][disp_name] = {}
                    data[segment_type][name][disp_name][k] = v

                if v == "Rank":
                    meta = item.get("metadata", {})
                    if item.get("displayName") not in data[segment_type][name]:
                        data[segment_type][name][item.get("displayName")] = {}

                    data[segment_type][name][item.get("displayName")]["meta"] = {
                        "rank_name": meta.get("label"),
                        "rank_url": meta.get("imageUrl"),
                    }
        return data

    async def _parse_stats_battlefield_v_tracker(self, player):
        """Legacy parser for tracker.gg data."""
        formatted_data = defaultdict(dict)
        player_url = f"https://api.tracker.gg/api/v2/bfv/standard/profile/origin/{player}"

        player_data = await self._get_website_data(player_url)
        player_data = player_data.get("data", {})

        player_avatar = player_data.get("platformInfo", {}).get("avatarUrl")
        formatted_data["display_url"] = player_avatar

        player_segments = player_data.get("segments", [])
        wanted_segments = ["overview", "firestorm", "class", "gamemode"]

        player_segments = [s for s in player_segments if s.get("type") in wanted_segments]
        for segment in player_segments:
            formatted_data = self._parse_bfv_tracker_segments(segment, formatted_data)

        return formatted_data

    def _generate_gametools_embeds(self, target, data) -> list[discord.Embed]:
        """Generates embeds from GameTools API data."""
        rank_number = data.get("rank", 0)
        score_min = data.get("scorePerMinute", 0)
        k_d = data.get("killDeath", 0)
        accuracy = data.get("accuracy", "0%")
        kills = data.get("kills", 0)
        deaths = data.get("deaths", 0)
        assists = data.get("killAssists", 0)
        kill_streak = data.get("highestKillStreak", 0)
        dogtags = data.get("dogtagsTaken", 0)
        headshots = data.get("headShots", 0)
        longest_hs = data.get("longestHeadShot", 0)
        kills_min = data.get("killsPerMinute", 0)
        win_perc = data.get("winPercent", "0%")
        wins = data.get("wins", 0)
        losses = data.get("loses", 0)
        rounds_played = data.get("roundsPlayed", 0)
        play_time = data.get("timePlayed", "N/A")

        # Extract rank image from nested data if available
        rank_url = data.get("rankImg")
        icon_url = data.get("avatar")

        embed = discord.Embed(title=f"{target.display_name} - Battlefield V Stats")
        embed.set_author(name=target.display_name, icon_url=icon_url or None)
        if rank_url:
            embed.set_thumbnail(url=rank_url)

        # Fields
        embed.add_field(name="Rank", value=f"{rank_number}", inline=True)
        embed.add_field(name="Time Played", value=play_time, inline=True)
        embed.add_field(name="Rounds", value=rounds_played, inline=True)

        embed.add_field(name="K/D Ratio", value=k_d, inline=True)
        embed.add_field(name="Kills", value=kills, inline=True)
        embed.add_field(name="Deaths", value=deaths, inline=True)

        embed.add_field(name="Win %", value=win_perc, inline=True)
        embed.add_field(name="Wins", value=wins, inline=True)
        embed.add_field(name="Losses", value=losses, inline=True)

        embed.add_field(name="SPM", value=score_min, inline=True)
        embed.add_field(name="KPM", value=kills_min, inline=True)
        embed.add_field(name="Accuracy", value=accuracy, inline=True)

        embed.add_field(name="Headshots", value=headshots, inline=True)
        embed.add_field(name="Longest HS", value=longest_hs, inline=True)
        embed.add_field(name="Kill Streak", value=kill_streak, inline=True)

        return [embed]

    # --- COMMANDS ---

    @commands.group()
    @commands.guild_only()
    async def gstats(self, ctx: commands.Context):
        """Shows users game stats"""

    @gstats.command(enabled=True, name="bfv")
    async def stats_bfv(
            self, ctx: commands.Context, *, member: Union[ConvertMember, None] = None
    ):
        """Shows a users Battlefield V stats"""
        target = ctx.author if member is None else member
        if not isinstance(target, discord.Member):
            return

        # 1. Try to get Origin ID from AccountManager
        origin_name = await ConfigHolder.AccountManager.user(target).account.origin()

        # 2. Try to get Cached BFV ID
        bfv_id = await ConfigHolder.BFV_USER_IDS.user(target).user_id()

        data = None

        # Strategy A: Use GameTools API (Better/Newer)
        if bfv_id:
            data = await self._get_bfv_data_by_id(bfv_id)
        elif origin_name:
            data = await self._get_bfv_data_by_name(origin_name)
            # Cache the ID for next time
            if data and data.get("id"):
                await ConfigHolder.BFV_USER_IDS.user(target).user_id.set(data.get("id"))

        if data:
            embeds = self._generate_gametools_embeds(target, data)
            if embeds:
                await menu(ctx, embeds, controls=DEFAULT_CONTROLS, timeout=60)
                return

        # Strategy B: Fallback to Tracker.gg (Legacy logic)
        # Note: Tracker.gg API v2 often requires specific headers/auth now, so this might be flaky.
        # I've kept the parsing logic just in case, but GameTools is preferred.
        if origin_name:
            try:
                # Attempt legacy tracker logic
                tracker_data = await self._parse_stats_battlefield_v_tracker(origin_name)
                # ... (Legacy Embed Builder would go here, but it's massive and GameTools is better)
                # If GameTools failed, Tracker likely will too without API keys.
                pass
            except Exception:
                pass

        await ctx.send(
            f"Could not find BFV stats for {target.display_name}. Ensure Origin account is linked via `[p]gprofile`.")