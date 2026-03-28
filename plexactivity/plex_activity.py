import asyncio
import aiohttp
import logging
import io
import urllib.parse
import re
import time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

import discord
from redbot.core import commands, Config, app_commands, checks
from redbot.core.utils.chat_formatting import humanize_list, box, pagify
from discord.ext import tasks

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

log = logging.getLogger("red.plex_activity")

DEFAULT_GUILD_SETTINGS = {
    "plex_url": None,
    "plex_token": None,
    "activity_channel": None,
    "activity_message_id": None,
    "update_interval": 30,  # Faster updates for debugging
    "tmdb_api_key": None,
    "google_books_api_key": None,
    "audiobook_libraries": [],
    "user_map": {}
}


class PlexActivity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = None
        self._plex_activity_loop_task = None
        self.color_cache = {}

    async def cog_load(self):
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PlexRPC-Bot/2.3",
            "Accept": "application/xml, text/xml"
        })
        if not self._plex_activity_loop_task or self._plex_activity_loop_task.done():
            self._plex_activity_loop_task = self.plex_activity_loop.start()

    async def cog_unload(self):
        if self._plex_activity_loop_task:
            self.plex_activity_loop.cancel()
        if self.session:
            await self.session.close()

    def _format_ms(self, ms: int) -> str:
        s = ms // 1000
        h, m, s = s // 3600, (s % 3600) // 60, s % 60
        return f"{h:02}:{m:02}:{s:02}" if h > 0 else f"{m:02}:{s:02}"

    def _generate_progress_bar(self, current_ms: int, total_ms: int, length: int = 10) -> str:
        if total_ms == 0: return "░" * length
        percent = min(1.0, max(0.0, current_ms / total_ms))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    async def _get_dominant_color(self, url: str):
        if not HAS_PIL or not url or not self.session: return None
        if url in self.color_cache: return self.color_cache[url]
        try:
            async with self.session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.read()

                    def get_c(d):
                        return Image.open(io.BytesIO(d)).convert("RGB").resize((1, 1)).getpixel((0, 0))

                    rgb = await self.bot.loop.run_in_executor(None, get_c, data)
                    c = discord.Color.from_rgb(*rgb)
                    self.color_cache[url] = c
                    return c
        except:
            return None

    async def _fetch_itunes_art(self, artist, title, album=None):
        if not artist or not title or not self.session: return None
        c_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
        params = {"term": f"{artist} {c_title}", "entity": "song", "limit": "20"}
        try:
            async with self.session.get("https://itunes.apple.com/search", params=params, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    res = data.get("results", [])
                    if not res: return None
                    best = res[0]
                    if album:
                        target = album.lower()
                        for t in res:
                            if t.get("collectionName") and target in t["collectionName"].lower():
                                best = t
                                break
                    url = best.get("artworkUrl100")
                    return url.replace("100x100bb", "600x600bb") if url else None
        except:
            return None

    async def _get_plex_sessions(self, guild_id: int):
        s = await self.config.guild_from_id(guild_id).all()
        if not s["plex_url"] or not s["plex_token"] or not self.session: return []

        url = f"{s['plex_url'].rstrip('/')}/status/sessions?X-Plex-Token={s['plex_token']}"
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    log.error(f"Plex API returned {resp.status}")
                    return []

                raw_xml = await resp.text()
                root = ET.fromstring(raw_xml)
                sessions = []

                # Broad iteration: check EVERY element in the XML for playback data
                for item in root.iter():
                    if item.tag not in ["Video", "Track", "Photo"]:
                        continue

                    try:
                        u_elem = item.find("User")
                        p_elem = item.find("Player")

                        # If a session lacks a User or Player, it's likely not a real active stream
                        if u_elem is None or p_elem is None: continue

                        p_user = u_elem.get("title")
                        d_id = s["user_map"].get(p_user)
                        disp_name = p_user
                        u_thumb = u_elem.get("thumb")

                        if d_id:
                            guild = self.bot.get_guild(guild_id)
                            member = guild.get_member(d_id) if guild else None
                            if member:
                                disp_name, u_thumb = member.display_name, member.display_avatar.url

                        m_type = item.get("type")
                        title = item.get("title")
                        artist = item.get("grandparentTitle") or item.get("originalTitle")
                        album = item.get("parentTitle")

                        image_url = None
                        if m_type == 'track':
                            image_url = await self._fetch_itunes_art(artist, title, album)

                        if not image_url:
                            t_path = item.get("parentThumb") or item.get("thumb") or item.get("art")
                            if t_path: image_url = f"{s['plex_url'].rstrip('/')}{t_path}?X-Plex-Token={s['plex_token']}"

                        v_off, dur = int(item.get("viewOffset", 0)), int(item.get("duration", 1))

                        sessions.append({
                            "user": disp_name, "user_thumb": u_thumb, "discord_id": d_id,
                            "type": m_type, "title": title, "artist": artist, "album": album,
                            "current_ms": v_off, "total_ms": dur, "state": p_elem.get("state"),
                            "device": p_elem.get("product"), "image_url": image_url,
                            "finish_ts": int((datetime.now() + timedelta(milliseconds=max(0, dur - v_off))).timestamp())
                        })
                        log.info(f"✅ Found active session for {p_user}: {title}")
                    except Exception as e:
                        log.error(f"Error parsing session item: {e}")

                if not sessions:
                    log.debug(
                        "Plex MediaContainer found, but no active 'Video', 'Track', or 'Photo' tags with Player data.")

                return sessions
        except Exception as e:
            log.error(f"Plex Session Fetch Error: {e}")
            return []

    async def _generate_embeds(self, sessions: list):
        if not sessions:
            return [discord.Embed(title="Plex Media Server", description="😴 No active streams.",
                                  color=discord.Color.dark_grey(), timestamp=datetime.now())]

        embeds = []
        for s in sessions[:10]:
            color = discord.Color.teal()
            if s['type'] == 'episode':
                color = discord.Color.blue()
            elif s['type'] == 'movie':
                color = discord.Color.orange()

            if s['image_url'] and HAS_PIL:
                dyn = await self._get_dominant_color(s['image_url'])
                if dyn: color = dyn

            embed = discord.Embed(color=color)
            verb = "listening to" if s['type'] == 'track' else "watching"
            embed.set_author(name=f"{s['user']} is {verb}...",
                             icon_url=s.get("user_thumb") or "https://i.imgur.com/1F0B7gP.png")
            embed.title = s['title']

            if s['artist']:
                desc = f"👤 **{s['artist']}**"
                if s['album']: desc += f"\n💿 *{s['album']}*"
                embed.description = desc

            bar = self._generate_progress_bar(s["current_ms"], s["total_ms"])
            state_icon = '⏸️' if s['state'] == 'paused' else '▶️'
            embed.add_field(name=f"{state_icon} Progress", value=f"`{bar}`\nEnds: <t:{s['finish_ts']}:R>", inline=False)

            u_str = f"👤 **User:** <@{s['discord_id']}>\n" if s.get('discord_id') else ""
            embed.add_field(name="Tech Specs", value=f"{u_str}📱 **Device:** `{s['device']}`", inline=False)

            if s['image_url'] and not any(x in str(s['image_url']) for x in ["127.0.0.1", "192.168", "localhost"]):
                embed.set_thumbnail(url=s['image_url'])
            embeds.append(embed)
        return embeds

    @tasks.loop(seconds=60)
    async def plex_activity_loop(self):
        for g_id in await self.config.all_guilds():
            conf = await self.config.guild_from_id(g_id).all()
            chan = self.bot.get_channel(conf["activity_channel"])
            if not chan: continue

            data = await self._get_plex_sessions(g_id)
            embeds = await self._generate_embeds(data)

            try:
                if conf["activity_message_id"]:
                    try:
                        m = await chan.fetch_message(conf["activity_message_id"])
                        await m.edit(embeds=embeds)
                    except:
                        m = await chan.send(embeds=embeds)
                        await self.config.guild_from_id(g_id).activity_message_id.set(m.id)
                else:
                    m = await chan.send(embeds=embeds)
                    await self.config.guild_from_id(g_id).activity_message_id.set(m.id)
            except:
                pass

    @plex_activity_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.group(name="plex")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex(self, ctx):
        """Manage Plex Activity settings."""
        pass

    @plex.command(name="setup")
    async def plex_setup(self, ctx):
        def check(m): return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Plex URL (including port, e.g., http://127.0.0.1:32400):")
        u = (await self.bot.wait_for("message", check=check)).content.strip()
        await ctx.send("Plex Token:")
        t = (await self.bot.wait_for("message", check=check)).content.strip()
        await self.config.guild(ctx.guild).plex_url.set(u)
        await self.config.guild(ctx.guild).plex_token.set(t)
        await ctx.send("✅ Plex connection updated!")

    @plex.command(name="setchannel")
    async def plex_setchannel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).activity_channel.set(channel.id)
        await self.config.guild(ctx.guild).activity_message_id.set(None)
        await ctx.send(f"✅ Updates will post to {channel.mention}.")

    @plex.command(name="map")
    async def plex_map(self, ctx, plex_user: str, discord_user: discord.Member):
        async with self.config.guild(ctx.guild).user_map() as m: m[plex_user] = discord_user.id
        await ctx.send(f"✅ Mapped `{plex_user}` to {discord_user.mention}.")


async def setup(bot): await bot.add_cog(PlexActivity(bot))