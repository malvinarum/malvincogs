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
    "update_interval": 60,
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
        # Browser-like headers are mandatory for iTunes API now
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        })
        if not self._plex_activity_loop_task or self._plex_activity_loop_task.done():
            self._plex_activity_loop_task = self.plex_activity_loop.start()

    async def cog_unload(self):
        if self._plex_activity_loop_task:
            self.plex_activity_loop.cancel()
        if self.session:
            await self.session.close()

    def _format_milliseconds_to_time(self, milliseconds: int) -> str:
        total_seconds = milliseconds // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}" if hours > 0 else f"{minutes:02}:{seconds:02}"

    def _generate_progress_bar(self, current_ms: int, total_ms: int, length: int = 10) -> str:
        if total_ms == 0: return "░" * length
        percent = min(1.0, max(0.0, current_ms / total_ms))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    def _get_device_emoji(self, device_name: str) -> str:
        d = device_name.lower()
        if any(x in d for x in ["tv", "roku", "chromecast", "fire", "shield", "bravia", "lg", "samsung"]): return "📺"
        if any(x in d for x in ["playstation", "xbox", "ps4", "ps5", "switch"]): return "🎮"
        if any(x in d for x in ["phone", "ipad", "iphone", "android", "mobile", "tablet"]): return "📱"
        return "💻"

    async def _get_dominant_color(self, image_url: str):
        if not HAS_PIL or not image_url or not self.session: return None
        if image_url in self.color_cache: return self.color_cache[image_url]
        try:
            async with self.session.get(image_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.read()

                    def get_color(img_data):
                        img = Image.open(io.BytesIO(img_data)).convert("RGB").resize((1, 1))
                        return img.getpixel((0, 0))

                    rgb = await self.bot.loop.run_in_executor(None, get_color, data)
                    color = discord.Color.from_rgb(*rgb)
                    self.color_cache[image_url] = color
                    return color
        except:
            return None

    async def _fetch_itunes_metadata(self, artist, title, album=None):
        if not artist or not title or not self.session: return None
        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
        search_url = "https://itunes.apple.com/search"
        params = {"term": f"{artist} {clean_title}", "entity": "song", "limit": "50"}
        try:
            async with self.session.get(search_url, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results: return None
                    best_match = results[0]
                    if album:
                        target_lower = album.lower()
                        for track in results:
                            col_name = track.get("collectionName")
                            if col_name and target_lower in col_name.lower():
                                best_match = track
                                break
                    art_url = best_match.get("artworkUrl100")
                    return art_url.replace("100x100bb", "600x600bb") if art_url else None
        except Exception as e:
            log.error(f"iTunes Search Error: {e}")
        return None

    # --- DEBUG COMMAND ---
    @commands.group(name="plex")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex(self, ctx):
        """Manage Plex Activity settings."""
        pass

    @plex.command(name="debugmusic")
    async def plex_debugmusic(self, ctx, *, query: str):
        """Debug iTunes API results for a specific query."""
        if not self.session:
            return await ctx.send("Error: API Session not initialized.")

        await ctx.send(f"🔍 Searching iTunes for: `{query}`...")
        search_url = "https://itunes.apple.com/search"
        params = {"term": query, "entity": "song", "limit": "5"}

        try:
            async with self.session.get(search_url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return await ctx.send(f"❌ API returned status `{resp.status}`")

                data = await resp.json()
                results = data.get("results", [])

                if not results:
                    return await ctx.send("❓ No results found on iTunes.")

                msg = f"✅ Found {len(results)} results. Top Result:\n"
                top = results[0]
                msg += f"- **Track:** {top.get('trackName')}\n"
                msg += f"- **Artist:** {top.get('artistName')}\n"
                msg += f"- **Album:** {top.get('collectionName')}\n"
                msg += f"- **Art:** {top.get('artworkUrl100')}"

                await ctx.send(msg)

                # Check if it would be upgraded
                upgraded = top.get('artworkUrl100', '').replace("100x100bb", "600x600bb")
                embed = discord.Embed(title="iTunes Art Test",
                                      description="If you see the image below, the API is working.")
                embed.set_image(url=upgraded)
                await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(box(f"Exception: {str(e)}", lang="py"))

    # --- THE REST OF THE COG LOGIC ---
    async def _fetch_tmdb_poster(self, api_key: str, query: str, media_type: str = 'movie', year: str = None):
        if not api_key or not self.session: return None
        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {'api_key': api_key, 'query': query, 'page': 1}
        if year and media_type == 'movie': params['year'] = year
        try:
            async with self.session.get(search_url, params=params, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['results'] and data['results'][0].get('poster_path'):
                        return f"https://image.tmdb.org/t/p/w500{data['results'][0]['poster_path']}"
        except:
            pass
        return None

    async def _fetch_google_books_cover(self, api_key: str, title: str, author: str):
        if not api_key or not title or not self.session: return None
        query = f"intitle:{re.sub(r'\(.*?\)|\[.*?\]', '', title).strip()}"
        if author: query += f"+inauthor:{author}"
        params = {'q': query, 'key': api_key, 'maxResults': 1, 'printType': 'books'}
        try:
            async with self.session.get("https://www.googleapis.com/books/v1/volumes", params=params,
                                        timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if "items" in data:
                        info = data["items"][0].get("volumeInfo", {})
                        links = info.get("imageLinks", {})
                        url = links.get("extraLarge") or links.get("large") or links.get("thumbnail")
                        if url: return url.replace("http://", "https://")
        except:
            pass
        return None

    async def _get_plex_sessions(self, guild_id: int):
        s = await self.config.guild_from_id(guild_id).all()
        if not s["plex_url"] or not s["plex_token"] or not self.session: return []
        url = f"{s['plex_url'].rstrip('/')}/status/sessions?X-Plex-Token={s['plex_token']}"
        try:
            async with self.session.get(url, timeout=10) as resp:
                data = await resp.text()
                sessions = []
                root = ET.fromstring(data)
                for item in root.findall("./Video") + root.findall("./Track"):
                    user_elem = item.find("User")
                    player_elem = item.find("Player")
                    if user_elem is None or player_elem is None: continue

                    plex_user = user_elem.get("title")
                    discord_id = s["user_map"].get(plex_user)
                    display_name = plex_user
                    user_thumb = user_elem.get("thumb")

                    if discord_id:
                        guild = self.bot.get_guild(guild_id)
                        member = guild.get_member(discord_id) if guild else None
                        if member:
                            display_name = member.display_name
                            user_thumb = member.display_avatar.url

                    media_type = item.get("type")
                    title = item.get("title")
                    artist = item.get("grandparentTitle")
                    album = item.get("parentTitle")
                    lib = item.get("librarySectionTitle", "")

                    image_url = None
                    if media_type in ['movie', 'episode'] and s["tmdb_api_key"]:
                        image_url = await self._fetch_tmdb_poster(s["tmdb_api_key"],
                                                                  artist if media_type == 'episode' else title,
                                                                  'tv' if media_type == 'episode' else 'movie',
                                                                  item.get("year"))
                    elif media_type == 'track':
                        if lib in s["audiobook_libraries"] and s["google_books_api_key"]:
                            image_url = await self._fetch_google_books_cover(s["google_books_api_key"], album or title,
                                                                             artist)
                        if not image_url:
                            image_url = await self._fetch_itunes_metadata(artist, title, album)

                    if not image_url:
                        t_path = item.get("parentThumb") or item.get("thumb") if media_type == 'track' else item.get(
                            "thumb")
                        if t_path: image_url = f"{s['plex_url'].rstrip('/')}{t_path}?X-Plex-Token={s['plex_token']}"

                    v_offset = int(item.get("viewOffset", 0))
                    duration = int(item.get("duration", 1))

                    sessions.append({
                        "user": display_name, "user_thumb": user_thumb, "discord_id": discord_id,
                        "type": media_type, "title": title, "artist": artist, "album": album,
                        "current_ms": v_offset, "total_ms": duration, "state": player_elem.get("state"),
                        "device": player_elem.get("product"), "image_url": image_url,
                        "finish_ts": int(
                            (datetime.now() + timedelta(milliseconds=max(0, duration - v_offset))).timestamp())
                    })
                return sessions
        except:
            return []

    async def _generate_session_embeds(self, sessions: list):
        if not sessions:
            return [discord.Embed(title="Plex Media Server", description="😴 No active streams.",
                                  color=discord.Color.dark_grey(), timestamp=datetime.now())]

        embeds = []
        for s in sessions[:10]:
            color = discord.Color.blue() if s['type'] == 'episode' else discord.Color.orange() if s[
                                                                                                      'type'] == 'movie' else discord.Color.teal()
            if s['image_url'] and HAS_PIL:
                dyn = await self._get_dominant_color(s['image_url'])
                if dyn: color = dyn

            embed = discord.Embed(color=color)
            embed.set_author(
                name=f"{s['user']} is watching..." if s['type'] != 'track' else f"{s['user']} is listening to...",
                icon_url=s.get("user_thumb") or "https://i.imgur.com/1F0B7gP.png")
            embed.title = s['title']
            if s['artist']: embed.description = f"👤 **{s['artist']}**" + (f"\n💿 *{s['album']}*" if s['album'] else "")

            bar = self._generate_progress_bar(s["current_ms"], s["total_ms"])
            embed.add_field(name=f"{'⏸️' if s['state'] == 'paused' else '▶️'} Progress",
                            value=f"`{bar}`\nEnds: <t:{s['finish_ts']}:R>", inline=False)

            user_str = f"👤 **User:** <@{s['discord_id']}>\n" if s.get('discord_id') else ""
            embed.add_field(name="Tech Specs", value=f"{user_str}📱 **Device:** `{s['device']}`", inline=False)

            # Block local IP images from being set as Discord thumbnails (they won't load anyway)
            if s['image_url'] and not any(x in s['image_url'] for x in ["127.0.0.1", "192.168", "localhost"]):
                embed.set_thumbnail(url=s['image_url'])

            embeds.append(embed)
        return embeds

    @tasks.loop(seconds=60)
    async def plex_activity_loop(self):
        for g_id in await self.config.all_guilds():
            s = await self.config.guild_from_id(g_id).all()
            chan = self.bot.get_channel(s["activity_channel"])
            if not chan: continue
            data = await self._get_plex_sessions(g_id)
            embeds = await self._generate_session_embeds(data)
            try:
                if s["activity_message_id"]:
                    try:
                        m = await chan.fetch_message(s["activity_message_id"])
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
    async def before_plex_activity_loop(self):
        await self.bot.wait_until_ready()

    @plex.command(name="setup")
    async def plex_setup(self, ctx):
        def check(m): return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Plex URL:")
        u = (await self.bot.wait_for("message", check=check)).content.strip()
        await ctx.send("Plex Token:")
        t = (await self.bot.wait_for("message", check=check)).content.strip()
        await self.config.guild(ctx.guild).plex_url.set(u);
        await self.config.guild(ctx.guild).plex_token.set(t)
        await ctx.send("Done!")

    @plex.command(name="setchannel")
    async def plex_setchannel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).activity_channel.set(channel.id)
        await self.config.guild(ctx.guild).activity_message_id.set(None)
        await ctx.send(f"Posting to {channel.mention}.")

    @plex.command(name="map")
    async def plex_map(self, ctx, plex_user: str, discord_user: discord.Member):
        async with self.config.guild(ctx.guild).user_map() as m: m[plex_user] = discord_user.id
        await ctx.send("Mapped!")


async def setup(bot): await bot.add_cog(PlexActivity(bot))