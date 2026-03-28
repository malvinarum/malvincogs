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
    """
    A Redbot cog to track and display Plex Media Server activity.
    Features: TMDB, Google Books, iTunes Search, Tech Specs, User Mapping!
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = None  # Created in cog_load
        self._plex_activity_loop_task = None
        self.color_cache = {}

    async def cog_load(self):
        log.info("PlexActivity cog loaded. Starting activity loop.")
        # Only set the User-Agent globally. Don't force Accept: application/json here!
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        if not self._plex_activity_loop_task or self._plex_activity_loop_task.done():
            self._plex_activity_loop_task = self.plex_activity_loop.start()

    async def cog_unload(self):
        log.info("PlexActivity cog unloaded. Stopping activity loop and closing session.")
        if self._plex_activity_loop_task:
            self.plex_activity_loop.cancel()
        if self.session:
            await self.session.close()

    def _format_milliseconds_to_time(self, milliseconds: int) -> str:
        total_seconds = milliseconds // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            return f"{minutes:02}:{seconds:02}"

    def _generate_progress_bar(self, current_ms: int, total_ms: int, length: int = 10) -> str:
        if total_ms == 0:
            return "░" * length
        percent = min(1.0, max(0.0, current_ms / total_ms))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    def _get_device_emoji(self, device_name: str) -> str:
        d = device_name.lower()
        if any(x in d for x in ["tv", "roku", "chromecast", "fire", "shield", "bravia", "lg", "samsung"]): return "📺"
        if any(x in d for x in ["playstation", "xbox", "ps4", "ps5", "switch"]): return "🎮"
        if "mac" in d or "osx" in d or "apple" in d: return "🍎"
        if "windows" in d or "pc" in d: return "🪟"
        if "linux" in d: return "🐧"
        if any(x in d for x in ["desktop", "laptop"]): return "💻"
        if any(x in d for x in ["web", "chrome", "firefox", "edge", "safari", "opera"]): return "🌐"
        if any(x in d for x in ["phone", "ipad", "iphone", "android", "mobile", "tablet"]): return "📱"
        return "📱"

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
                    if len(self.color_cache) > 100: self.color_cache.clear()
                    self.color_cache[image_url] = color
                    return color
        except Exception:
            return None

    # --- ITUNES HELPERS ---
    async def _fetch_itunes_metadata(self, artist, title, album=None):
        """Searches iTunes for a track and returns the high-res album art URL."""
        if not artist or not title or not self.session:
            return None

        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
        search_url = "https://itunes.apple.com/search"
        params = {"term": f"{artist} {clean_title}", "entity": "song", "limit": "50"}

        try:
            # Force JSON accept for iTunes
            async with self.session.get(search_url, params=params, headers={"Accept": "application/json"},
                                        timeout=5) as resp:
                if resp.status == 200:
                    # Use content_type=None to bypass the text/javascript check
                    data = await resp.json(content_type=None)
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
                    if art_url:
                        return art_url.replace("100x100bb", "600x600bb")
        except Exception as e:
            log.error(f"iTunes Search Error: {e}")
        return None

    async def _fetch_tmdb_poster(self, api_key: str, query: str, media_type: str = 'movie', year: str = None):
        if not api_key or not self.session: return None
        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {'api_key': api_key, 'query': query, 'page': 1}
        if year and media_type == 'movie': params['year'] = year
        try:
            async with self.session.get(search_url, params=params, headers={"Accept": "application/json"},
                                        timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['results']:
                        path = data['results'][0].get('poster_path')
                        if path: return f"https://image.tmdb.org/t/p/w500{path}"
        except Exception:
            pass
        return None

    async def _fetch_google_books_cover(self, api_key: str, title: str, author: str):
        if not api_key or not title or not self.session: return None
        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
        query = f"intitle:{clean_title}"
        if author: query += f"+inauthor:{author}"
        search_url = "https://www.googleapis.com/books/v1/volumes"
        params = {'q': query, 'key': api_key, 'maxResults': 1, 'printType': 'books'}
        try:
            async with self.session.get(search_url, params=params, headers={"Accept": "application/json"},
                                        timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    if "items" in data and len(data["items"]) > 0:
                        volume_info = data["items"][0].get("volumeInfo", {})
                        image_links = volume_info.get("imageLinks", {})
                        url = image_links.get("extraLarge") or \
                              image_links.get("large") or \
                              image_links.get("medium") or \
                              image_links.get("thumbnail") or \
                              image_links.get("smallThumbnail")
                        if url:
                            return url.replace("http://", "https://")
        except Exception:
            pass
        return None

    async def _get_plex_sessions(self, guild_id: int):
        settings = await self.config.guild_from_id(guild_id).all()
        plex_url = settings["plex_url"]
        plex_token = settings["plex_token"]
        tmdb_key = settings.get("tmdb_api_key")
        gb_key = settings.get("google_books_api_key")
        audiobook_libs = settings.get("audiobook_libraries", [])
        user_map = settings.get("user_map", {})

        if not plex_url or not plex_token or not self.session: return []
        if not plex_url.endswith("/"): plex_url += "/"
        api_url = f"{plex_url}status/sessions?X-Plex-Token={plex_token}"

        try:
            # Force XML accept for Plex to ensure it doesn't send JSON back
            async with self.session.get(api_url, headers={"Accept": "application/xml"}, timeout=10) as response:
                response.raise_for_status()
                data = await response.text()
                sessions = []
                root = ET.fromstring(data)
                # Using your reverted findall logic
                for session_elem in root.findall("./Video") + root.findall("./Photo") + root.findall("./Track"):
                    user_elem = session_elem.find("User")
                    player_elem = session_elem.find("Player")
                    media_elem = session_elem.find("Media")
                    transcode_elem = session_elem.find("TranscodeSession")

                    if user_elem is None or player_elem is None: continue

                    plex_username = user_elem.get("title", "Unknown User")
                    display_name = plex_username
                    user_thumb = user_elem.get("thumb")
                    discord_id = user_map.get(plex_username)

                    if discord_id:
                        guild = self.bot.get_guild(guild_id)
                        member = guild.get_member(discord_id) if guild else None
                        if member:
                            display_name = member.display_name
                            user_thumb = member.display_avatar.url

                    if user_thumb and not str(user_thumb).startswith("http"):
                        user_thumb = f"{plex_url.rstrip('/')}{user_thumb}?X-Plex-Token={plex_token}"

                    media_title = session_elem.get("title", "Unknown Title")
                    v_off, dur = int(session_elem.get("viewOffset", "0")), int(session_elem.get("duration", "1"))
                    library_name = session_elem.get("librarySectionTitle", "")
                    is_audiobook = library_name in audiobook_libs

                    current_time_formatted = self._format_milliseconds_to_time(v_off)
                    total_duration_formatted = self._format_milliseconds_to_time(dur)
                    finish_ts = int((datetime.now() + timedelta(milliseconds=max(0, dur - v_off))).timestamp())

                    series_title = session_elem.get("grandparentTitle")
                    artist_name = session_elem.get("grandparentTitle")
                    album_name = session_elem.get("parentTitle")
                    media_type = session_elem.get("type", "media")
                    device = player_elem.get("product", "Unknown Device")
                    state = player_elem.get("state", "playing")

                    bitrate_kbps = 0
                    if media_elem is not None:
                        bitrate_kbps = int(media_elem.get("bitrate", 0))
                        if bitrate_kbps == 0:
                            part_elem = media_elem.find("Part")
                            if part_elem is not None:
                                bitrate_kbps = int(part_elem.get("bitrate", 0))

                    if bitrate_kbps > 0:
                        bandwidth_str = f"{bitrate_kbps} kbps" if bitrate_kbps < 1000 or media_type == 'track' else f"{bitrate_kbps / 1000:.1f} Mbps"
                    else:
                        bandwidth_str = "Unknown"

                    stream_info = "Direct Play"
                    if transcode_elem is not None:
                        video_decision = transcode_elem.get("videoDecision", "unknown")
                        stream_info = "Transcoding ⚠️" if video_decision == "transcode" else "Direct Stream"

                    image_url = None
                    if tmdb_key and media_type in ['movie', 'episode']:
                        query = series_title if media_type == 'episode' else media_title
                        image_url = await self._fetch_tmdb_poster(tmdb_key, query,
                                                                  'tv' if media_type == 'episode' else 'movie',
                                                                  session_elem.get("year"))

                    if (media_type == 'track' or media_type == 'audio'):
                        if is_audiobook and gb_key:
                            image_url = await self._fetch_google_books_cover(gb_key, album_name or media_title,
                                                                             artist_name or "")
                        if not image_url:
                            image_url = await self._fetch_itunes_metadata(artist_name, media_title, album_name)

                    if not image_url:
                        thumb_path = session_elem.get("parentThumb") or session_elem.get("thumb") if media_type in [
                            'track', 'audio'] else session_elem.get("thumb")
                        if thumb_path:
                            image_url = f"{plex_url.rstrip('/')}{thumb_path}?X-Plex-Token={plex_token}"

                    sessions.append({
                        "user": display_name, "user_thumb": user_thumb, "discord_id": discord_id,
                        "type": media_type, "is_audiobook": is_audiobook, "current_time": current_time_formatted,
                        "total_duration": total_duration_formatted, "current_ms": v_off,
                        "total_ms": dur, "finish_ts": finish_ts, "device": device, "state": state,
                        "stream_info": stream_info, "bandwidth": bandwidth_str, "image_url": image_url,
                        "title": media_title, "series_title": series_title,
                        "season_num": session_elem.get("parentIndex"),
                        "episode_num": session_elem.get("index"), "artist": artist_name, "album": album_name
                    })
                return sessions
        except Exception as e:
            log.error(f"Plex Session Error: {e}")
            return []

    async def _generate_session_embeds(self, sessions: list):
        if not sessions:
            return [discord.Embed(title="Plex Media Server", description="😴 No active streams.",
                                  color=discord.Color.dark_grey(), timestamp=datetime.now())]

        embeds = []
        for session in sessions[:10]:
            media_type = session.get("type")
            image_url = session.get("image_url")
            color = discord.Color.teal()
            if media_type == 'movie':
                color = discord.Color.orange()
            elif media_type == 'episode':
                color = discord.Color.blue()
            elif session.get("is_audiobook"):
                color = discord.Color.gold()

            if image_url and HAS_PIL:
                dyn = await self._get_dominant_color(image_url)
                if dyn: color = dyn

            embed = discord.Embed(color=color)
            verb = "listening to an Audiobook 📖" if session.get(
                "is_audiobook") else "listening to..." if media_type in ["track", "audio"] else "watching..."
            embed.set_author(name=f"{session['user']} {verb}",
                             icon_url=session.get("user_thumb") or "https://i.imgur.com/1F0B7gP.png")

            if media_type == "episode":
                embed.title = session.get("series_title")
                embed.description = f"**{session.get('title')}**\n`S{int(session.get('season_num', 0)):02}E{int(session.get('episode_num', 0)):02}`"
            elif media_type in ["track", "audio"]:
                embed.title = session.get("title")
                embed.description = f"👤 **{session.get('artist', 'Unknown')}**" + (
                    f"\n💿 *{session['album']}*" if session.get('album') else "")
            else:
                embed.title = session.get("title")
                embed.description = f"*{media_type.capitalize()}*"

            bar = self._generate_progress_bar(session["current_ms"], session["total_ms"])
            embed.add_field(name=f"{'⏸️' if session['state'] == 'paused' else '▶️'} Progress",
                            value=f"`{bar}`\n`{session['current_time']} / {session['total_duration']}`\nEnds: <t:{session['finish_ts']}:R>",
                            inline=False)

            user_str = f"👤 **User:** <@{session['discord_id']}>\n" if session.get('discord_id') else ""
            embed.add_field(name="Tech Specs",
                            value=f"{user_str}{self._get_device_emoji(session['device'])} **Device:** `{session['device']}`\n⚙️ **Stream:** `{session['stream_info']}`\n📶 **Bitrate:** `{session['bandwidth']}`",
                            inline=False)

            if image_url and str(image_url).startswith("http") and not any(
                    x in str(image_url) for x in ["127.0.0.1", "192.168", "localhost"]):
                embed.set_thumbnail(url=image_url)
            embeds.append(embed)

        embeds[-1].timestamp = datetime.now()
        embeds[-1].set_footer(text="Plex Activity • Live Update")
        return embeds

    @tasks.loop(seconds=60)
    async def plex_activity_loop(self):
        for guild_id in await self.config.all_guilds():
            settings = await self.config.guild_from_id(guild_id).all()
            channel = self.bot.get_channel(settings["activity_channel"])
            if not channel: continue
            data = await self._get_plex_sessions(guild_id)
            embeds = await self._generate_session_embeds(data)
            try:
                if settings["activity_message_id"]:
                    try:
                        msg = await channel.fetch_message(settings["activity_message_id"])
                        await msg.edit(embeds=embeds)
                    except:
                        msg = await channel.send(embeds=embeds)
                        await self.config.guild_from_id(guild_id).activity_message_id.set(msg.id)
                else:
                    msg = await channel.send(embeds=embeds)
                    await self.config.guild_from_id(guild_id).activity_message_id.set(msg.id)
            except:
                pass

    @plex_activity_loop.before_loop
    async def before_plex_activity_loop(self):
        await self.bot.wait_until_ready()

    @commands.group(name="plex")
    @checks.mod_or_permissions(manage_guild=True)
    async def plex(self, ctx):
        """Manage Plex Activity settings."""
        pass

    @plex.command(name="setup")
    async def plex_setup(self, ctx):
        def check(m): return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Plex URL:")
        url = (await self.bot.wait_for("message", check=check)).content.strip()
        await ctx.send("Plex Token:")
        token = (await self.bot.wait_for("message", check=check)).content.strip()
        await self.config.guild(ctx.guild).plex_url.set(url)
        await self.config.guild(ctx.guild).plex_token.set(token)
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

    @plex.command(name="debugitunes")
    async def plex_debugitunes(self, ctx, *, query: str):
        """Debug iTunes API search results."""
        if not self.session: return await ctx.send("Error: API Session not initialized.")
        await ctx.send(f"🔍 Searching iTunes for: `{query}`...")
        try:
            params = {"term": query, "entity": "song", "limit": "5"}
            async with self.session.get("https://itunes.apple.com/search", params=params,
                                        headers={"Accept": "application/json"}, timeout=10) as resp:
                data = await resp.json(content_type=None)
                results = data.get("results", [])
                if not results: return await ctx.send("❓ No results found.")
                msg = f"✅ Found {len(results)} results.\n\n"
                for idx, item in enumerate(results, 1):
                    msg += f"{idx}. **{item.get('trackName')}** by **{item.get('artistName')}**\n💿 {item.get('collectionName')}\nArt: <{item.get('artworkUrl100')}>\n\n"
                await ctx.send(msg)
                top_art = results[0].get('artworkUrl100', '').replace("100x100bb", "600x600bb")
                if top_art:
                    e = discord.Embed(title="Art Test", description=f"Source: {top_art}");
                    e.set_image(url=top_art)
                    await ctx.send(embed=e)
        except Exception as e:
            await ctx.send(box(f"Exception: {str(e)}", lang="py"))


async def setup(bot): await bot.add_cog(PlexActivity(bot))