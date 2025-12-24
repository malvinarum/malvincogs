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
    "spotify_client_id": None,
    "spotify_client_secret": None,
    "audiobook_libraries": [],  # List of library names that contain audiobooks
    "user_map": {}
}


class PlexActivity(commands.Cog):
    """
    A Redbot cog to track and display Plex Media Server activity.
    Features: TMDB, Google Books, Spotify, Dynamic Colors, Tech Specs, User Mapping!
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()
        self._plex_activity_loop_task = None
        self.color_cache = {}

        # Spotify Cache
        self.spotify_token = None
        self.spotify_token_expires = 0

    async def cog_load(self):
        log.info("PlexActivity cog loaded. Starting activity loop.")
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
        if not HAS_PIL or not image_url: return None
        if image_url in self.color_cache: return self.color_cache[image_url]
        try:
            async with self.session.get(image_url) as resp:
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

    # --- SPOTIFY HELPERS ---
    async def _get_spotify_token(self, client_id, client_secret):
        """Retrieves a valid Bearer token for Spotify."""
        if self.spotify_token and time.time() < self.spotify_token_expires:
            return self.spotify_token

        url = "https://accounts.spotify.com/api/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
        try:
            async with self.session.post(url, data=data) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    self.spotify_token = js.get("access_token")
                    # Set expiry (usually 3600s) minus a buffer
                    self.spotify_token_expires = time.time() + js.get("expires_in", 3600) - 60
                    return self.spotify_token
                else:
                    log.error(f"Spotify Auth Failed: {resp.status} - {await resp.text()}")
        except Exception as e:
            log.error(f"Spotify Token Error: {e}")
        return None

    async def _fetch_spotify_metadata(self, client_id, client_secret, artist, title):
        """Searches Spotify for a track and returns the album art URL."""
        if not client_id or not client_secret or not artist or not title:
            return None

        token = await self._get_spotify_token(client_id, client_secret)
        if not token:
            return None

        # Clean strings for better search accuracy
        clean_artist = re.sub(r"[^a-zA-Z0-9 ]", "", artist)
        clean_title = re.sub(r"\s*\(.*?\)", "", title)  # Remove (feat. X) etc

        search_url = "https://api.spotify.com/v1/search"
        params = {
            "q": f"artist:{clean_artist} track:{clean_title}",
            "type": "track",
            "limit": "1"
        }
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with self.session.get(search_url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tracks = data.get("tracks", {}).get("items", [])
                    if tracks:
                        track = tracks[0]
                        album = track.get("album", {})
                        images = album.get("images", [])
                        if images:
                            # Spotify usually returns [640x640, 300x300, 64x64]. Grab the first (largest).
                            return images[0].get("url")
        except Exception as e:
            log.error(f"Spotify Search Error: {e}")
        return None
    # -----------------------

    async def _fetch_tmdb_poster(self, api_key: str, query: str, media_type: str = 'movie', year: str = None):
        if not api_key: return None
        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {'api_key': api_key, 'query': query, 'page': 1}
        if year and media_type == 'movie': params['year'] = year
        try:
            async with self.session.get(search_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['results']:
                        path = data['results'][0].get('poster_path')
                        if path: return f"https://image.tmdb.org/t/p/w500{path}"
        except Exception:
            pass
        return None

    async def _fetch_google_books_cover(self, api_key: str, title: str, author: str):
        """
        Searches Google Books API for a cover with smart query cleaning.
        """
        if not api_key or not title: return None

        # --- CLEAN TITLE LOGIC ---
        # Remove (Unabridged), (Audiobook), [Dramatized], etc.
        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()

        query = f"intitle:{clean_title}"
        if author:
            query += f"+inauthor:{author}"

        search_url = "https://www.googleapis.com/books/v1/volumes"
        params = {'q': query, 'key': api_key, 'maxResults': 1, 'printType': 'books'}

        try:
            async with self.session.get(search_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if "items" in data and len(data["items"]) > 0:
                        volume_info = data["items"][0].get("volumeInfo", {})
                        image_links = volume_info.get("imageLinks", {})

                        # Google API keys: extraLarge, large, medium, small, thumbnail, smallThumbnail
                        url = image_links.get("extraLarge") or \
                              image_links.get("large") or \
                              image_links.get("medium") or \
                              image_links.get("thumbnail") or \
                              image_links.get("smallThumbnail")

                        if url:
                            # Force HTTPS
                            if url.startswith("http://"):
                                url = url.replace("http://", "https://")
                            log.info(f"Google Books found cover for '{clean_title}': {url}")
                            return url
                    else:
                        log.info(f"Google Books found NO results for '{clean_title}' by '{author}'")
                else:
                    log.error(f"Google Books API Error: {response.status}")
        except Exception as e:
            log.error(f"Google Books Fetch Exception: {e}")
        return None

    async def _get_plex_sessions(self, guild_id: int):
        settings = await self.config.guild_from_id(guild_id).all()
        plex_url = settings["plex_url"]
        plex_token = settings["plex_token"]
        tmdb_key = settings.get("tmdb_api_key")
        gb_key = settings.get("google_books_api_key")
        spotify_id = settings.get("spotify_client_id")
        spotify_secret = settings.get("spotify_client_secret")
        audiobook_libs = settings.get("audiobook_libraries", [])
        user_map = settings.get("user_map", {})

        if not plex_url or not plex_token: return []
        if not plex_url.endswith("/"): plex_url += "/"
        api_url = f"{plex_url}status/sessions?X-Plex-Token={plex_token}"

        try:
            async with self.session.get(api_url, timeout=10) as response:
                response.raise_for_status()
                data = await response.text()
                sessions = []
                try:
                    root = ET.fromstring(data)
                    for session_elem in root.findall("./Video") + root.findall("./Photo") + root.findall("./Track"):
                        user_elem = session_elem.find("User")
                        player_elem = session_elem.find("Player")
                        media_elem = session_elem.find("Media")
                        transcode_elem = session_elem.find("TranscodeSession")

                        if user_elem is None or player_elem is None: continue

                        plex_username = user_elem.get("title", "Unknown User")
                        display_name = plex_username
                        user_thumb = user_elem.get("thumb")
                        discord_id = None

                        d_id = user_map.get(plex_username)
                        if d_id:
                            guild = self.bot.get_guild(guild_id)
                            if guild:
                                member = guild.get_member(d_id)
                                if member:
                                    display_name = member.display_name
                                    user_thumb = member.display_avatar.url
                                    discord_id = member.id

                        if user_thumb and not user_thumb.startswith("http"):
                            user_thumb = f"{user_thumb}?X-Plex-Token={plex_token}"

                        media_title = session_elem.get("title", "Unknown Title")
                        view_offset_ms = int(session_elem.get("viewOffset", "0"))
                        duration_ms = int(session_elem.get("duration", "1"))
                        year = session_elem.get("year")
                        
                        # Identify Library to distinguish Music vs Audiobooks
                        library_name = session_elem.get("librarySectionTitle", "")
                        is_audiobook = library_name in audiobook_libs

                        current_time_formatted = self._format_milliseconds_to_time(view_offset_ms)
                        total_duration_formatted = self._format_milliseconds_to_time(duration_ms)
                        remaining_ms = max(0, duration_ms - view_offset_ms)
                        finish_ts = int((datetime.now() + timedelta(milliseconds=remaining_ms)).timestamp())

                        series_title = session_elem.get("grandparentTitle")
                        artist_name = session_elem.get("grandparentTitle")
                        album_name = session_elem.get("parentTitle")

                        media_type = session_elem.get("type", "media")
                        device = player_elem.get("product", "Unknown Device")
                        state = player_elem.get("state", "playing")

                        bitrate_kbps = int(media_elem.get("bitrate", 0)) if media_elem is not None else 0
                        bandwidth_str = f"{bitrate_kbps / 1000:.1f} Mbps" if bitrate_kbps > 0 else "Unknown"

                        stream_info = "Direct Play"
                        if transcode_elem is not None:
                            video_decision = transcode_elem.get("videoDecision", "unknown")
                            stream_info = "Transcoding ⚠️" if video_decision == "transcode" else "Direct Stream"

                        image_url = None

                        # 1. Video -> TMDB
                        if tmdb_key and media_type in ['movie', 'episode']:
                            search_query = series_title if media_type == 'episode' else media_title
                            search_type = 'tv' if media_type == 'episode' else 'movie'
                            image_url = await self._fetch_tmdb_poster(tmdb_key, search_query, search_type, year)

                        # 2. AUDIOBOOK STRATEGY
                        if (media_type == 'track' or media_type == 'audio') and is_audiobook:
                            # Prefer Google Books for Audiobooks
                            if gb_key:
                                book_title = album_name or media_title
                                author = artist_name or ""
                                image_url = await self._fetch_google_books_cover(gb_key, book_title, author)
                            # Fallback to Spotify if GB fails
                            if not image_url and spotify_id and spotify_secret:
                                image_url = await self._fetch_spotify_metadata(spotify_id, spotify_secret, artist_name, media_title)

                        # 3. MUSIC STRATEGY
                        elif (media_type == 'track' or media_type == 'audio') and not is_audiobook:
                             # Prefer Spotify for Music
                            if spotify_id and spotify_secret:
                                image_url = await self._fetch_spotify_metadata(spotify_id, spotify_secret, artist_name, media_title)
                             # No Google Books fallback for music (usually returns weird results)

                        # 4. Fallback -> Plex Internal
                        if not image_url:
                            thumb_path = None
                            if media_type == 'track' or media_type == 'audio':
                                thumb_path = session_elem.get("parentThumb") or session_elem.get("thumb")
                            else:
                                thumb_path = session_elem.get("thumb") or session_elem.get("art")

                            if thumb_path:
                                base_plex_url = plex_url.rstrip('/')
                                image_url = f"{base_plex_url}{thumb_path}?X-Plex-Token={plex_token}"

                        session_data = {
                            "user": display_name,
                            "user_thumb": user_thumb,
                            "discord_id": discord_id,
                            "type": media_type,
                            "is_audiobook": is_audiobook, # Pass flag to embed generator
                            "current_time": current_time_formatted,
                            "total_duration": total_duration_formatted,
                            "current_ms": view_offset_ms,
                            "total_ms": duration_ms,
                            "finish_ts": finish_ts,
                            "device": device,
                            "state": state,
                            "stream_info": stream_info,
                            "bandwidth": bandwidth_str,
                            "image_url": image_url,
                            "title": media_title,
                            "series_title": series_title,
                            "season_num": session_elem.get("parentIndex"),
                            "episode_num": session_elem.get("index"),
                            "artist": artist_name,
                            "album": album_name
                        }
                        sessions.append(session_data)
                except ET.ParseError:
                    return []
                return sessions
        except Exception:
            return []

    async def _generate_session_embeds(self, sessions: list):
        if not sessions:
            return [discord.Embed(title="Plex Media Server", description="😴 No active streams.",
                                  color=discord.Color.dark_grey(), timestamp=datetime.now())]

        embeds = []
        for session in sessions[:10]:
            user = session.get("user", "Unknown")
            discord_id = session.get("discord_id")
            media_type = session.get("type")
            is_audiobook = session.get("is_audiobook", False)
            device = session.get("device")
            image_url = session.get("image_url")
            state = session.get("state")

            state_icon = "▶️"
            if state == "paused":
                state_icon = "⏸️"
            elif state == "buffering":
                state_icon = "⏳"

            device_emoji = self._get_device_emoji(device)

            if media_type == 'movie':
                color = discord.Color.orange()
            elif media_type == 'episode':
                color = discord.Color.blue()
            elif media_type == 'track':
                if is_audiobook:
                    color = discord.Color.gold()
                else:
                    color = discord.Color.teal()
            else:
                color = discord.Color.purple()

            if image_url and HAS_PIL:
                dynamic_color = await self._get_dominant_color(image_url)
                if dynamic_color: color = dynamic_color

            embed = discord.Embed(color=color)
            user_icon = session.get("user_thumb") or "https://i.imgur.com/1F0B7gP.png"

            verb = "is watching..."
            if is_audiobook:
                verb = "is listening to an Audiobook 📖"
            elif media_type == "track" or media_type == "audio":
                verb = "is listening to..."

            embed.set_author(name=f"{user} {verb}", icon_url=user_icon)

            if media_type == "episode":
                s_num = int(session.get("season_num")) if session.get("season_num") else 0
                e_num = int(session.get("episode_num")) if session.get("episode_num") else 0
                embed.title = session.get("series_title")
                embed.description = f"**{session.get('title')}**\n`S{s_num:02}E{e_num:02}`"
            
            elif media_type == "track" or media_type == "audio":
                if is_audiobook:
                    # Audiobook Format
                    book_title = session.get("album") or "Unknown Book"
                    chapter_title = session.get("title")
                    author = session.get("artist") or "Unknown Author"
                    embed.title = book_title
                    embed.description = f"**{chapter_title}**\n✍️ *{author}*"
                else:
                    # Music Format
                    track_title = session.get("title")
                    artist = session.get("artist") or "Unknown Artist"
                    album = session.get("album")
                    embed.title = track_title
                    if album:
                         embed.description = f"👤 **{artist}**\n💿 *{album}*"
                    else:
                         embed.description = f"👤 **{artist}**"

            else:
                embed.title = session.get("title")
                embed.description = f"*{media_type.capitalize()}*"

            bar = self._generate_progress_bar(session.get("current_ms"), session.get("total_ms"))
            embed.add_field(name=f"{state_icon} Progress",
                            value=f"`{bar}`\n`{session.get('current_time')} / {session.get('total_duration')}`\nEnds: <t:{session.get('finish_ts')}:R>",
                            inline=False)

            user_field_str = f"👤 **User:** <@{discord_id}>\n" if discord_id else ""
            embed.add_field(name="Tech Specs",
                            value=f"{user_field_str}{device_emoji} **Device:** `{device}`\n⚙️ **Stream:** `{session.get('stream_info')}`\n📶 **Bitrate:** `{session.get('bandwidth')}`",
                            inline=False)
            if image_url: embed.set_thumbnail(url=image_url)
            embeds.append(embed)

        embeds[-1].timestamp = datetime.now()
        embeds[-1].set_footer(text="Plex Activity • Live Update")
        return embeds

    @tasks.loop(seconds=60)
    async def plex_activity_loop(self):
        for guild_id in await self.config.all_guilds():
            guild_settings = await self.config.guild_from_id(guild_id).all()
            channel_id = guild_settings["activity_channel"]
            message_id = guild_settings["activity_message_id"]
            update_interval = guild_settings.get("update_interval", 60)

            if self.plex_activity_loop.seconds != update_interval:
                self.plex_activity_loop.change_interval(seconds=update_interval)

            if not channel_id: continue
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            channel = guild.get_channel(channel_id)
            if not channel: continue

            sessions = await self._get_plex_sessions(guild_id)
            embeds = await self._generate_session_embeds(sessions)

            try:
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embeds=embeds)
                    except discord.NotFound:
                        message = await channel.send(embeds=embeds)
                        await self.config.guild(guild).activity_message_id.set(message.id)
                    except discord.Forbidden:
                        log.warning(f"Forbidden edit in {channel.name}")
                else:
                    message = await channel.send(embeds=embeds)
                    await self.config.guild(guild).activity_message_id.set(message.id)
            except Exception as e:
                log.error(f"Error updating message: {e}")

    @plex_activity_loop.before_loop
    async def before_plex_activity_loop(self):
        await self.bot.wait_until_ready()

    @commands.group(name="plex", invoke_without_command=True)
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def plex(self, ctx: commands.Context):
        """Manage Plex Media Server activity tracking."""
        await ctx.send_help(self.plex)

    @plex.command(name="setup")
    async def plex_setup(self, ctx: commands.Context):
        """Interactive setup for Plex URL and Token."""
        await ctx.send("Enter Plex URL:")
        try:
            msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=60)
            url = msg.content.strip()
            await ctx.send("Enter Plex Token:")
            msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=60)
            token = msg.content.strip()
            await self.config.guild(ctx.guild).plex_url.set(url)
            await self.config.guild(ctx.guild).plex_token.set(token)
            await ctx.send("Configured!")
        except asyncio.TimeoutError:
            await ctx.send("Timed out.")

    @plex.command(name="tmdb")
    async def plex_tmdb(self, ctx: commands.Context, api_key: str):
        """Set the TMDB API Key."""
        await self.config.guild(ctx.guild).tmdb_api_key.set(api_key)
        await ctx.send("✅ TMDB API Key set!")

    @plex.command(name="googlebooks")
    async def plex_googlebooks(self, ctx: commands.Context, api_key: str):
        """Set the Google Books API Key."""
        await self.config.guild(ctx.guild).google_books_api_key.set(api_key)
        await ctx.send("✅ Google Books API Key set!")

    @plex.command(name="spotify")
    async def plex_spotify(self, ctx: commands.Context, client_id: str, client_secret: str):
        """
        Set the Spotify Client ID and Secret.
        Get these from the Spotify Developer Dashboard.
        """
        await self.config.guild(ctx.guild).spotify_client_id.set(client_id)
        await self.config.guild(ctx.guild).spotify_client_secret.set(client_secret)
        await ctx.send("✅ Spotify credentials set! Music metadata should now be much better.")

    @plex.command(name="setchannel")
    async def plex_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel for Plex updates."""
        await self.config.guild(ctx.guild).activity_channel.set(channel.id)
        await self.config.guild(ctx.guild).activity_message_id.set(None)
        await ctx.send(f"Updates will post to {channel.mention}.")
        sessions = await self._get_plex_sessions(ctx.guild.id)
        embeds = await self._generate_session_embeds(sessions)
        msg = await channel.send(embeds=embeds)
        await self.config.guild(ctx.guild).activity_message_id.set(msg.id)

    @plex.command(name="map")
    async def plex_map(self, ctx: commands.Context, plex_user: str, discord_user: discord.Member):
        """
        Map a Plex username to a Discord user.
        Example: [p]plex map malvinarum @Malvin
        """
        async with self.config.guild(ctx.guild).user_map() as user_map:
            user_map[plex_user] = discord_user.id

        await ctx.send(f"✅ Mapped Plex user `{plex_user}` to {discord_user.mention}.")

    @plex.command(name="unmap")
    async def plex_unmap(self, ctx: commands.Context, plex_user: str):
        """Remove a mapping."""
        async with self.config.guild(ctx.guild).user_map() as user_map:
            if plex_user in user_map:
                del user_map[plex_user]
                await ctx.send(f"🗑️ Unmapped `{plex_user}`.")
            else:
                await ctx.send("User not found in map.")

    @plex.command(name="listmaps")
    async def plex_listmaps(self, ctx: commands.Context):
        """List all user mappings."""
        user_map = await self.config.guild(ctx.guild).user_map()
        if not user_map: return await ctx.send("No mappings.")

        msg = "**Plex User ➡️ Discord User**\n"
        for p_user, d_id in user_map.items():
            d_user = ctx.guild.get_member(d_id)
            name = d_user.mention if d_user else f"Unknown ID: {d_id}"
            msg += f"`{p_user}` ➡️ {name}\n"

        await ctx.send(msg)
    
    # --- AUDIOBOOK LIBRARY MANAGEMENT ---
    @plex.group(name="audiobooks")
    async def plex_audiobooks(self, ctx: commands.Context):
        """Manage Audiobook Libraries."""
        pass

    @plex_audiobooks.command(name="add")
    async def plex_audiobooks_add(self, ctx: commands.Context, *, library_name: str):
        """
        Add a Plex library to the Audiobook list.
        Exact name required (case-sensitive usually).
        """
        async with self.config.guild(ctx.guild).audiobook_libraries() as libs:
            if library_name not in libs:
                libs.append(library_name)
                await ctx.send(f"✅ Added `{library_name}` to Audiobook libraries.")
            else:
                await ctx.send(f"⚠️ `{library_name}` is already in the list.")

    @plex_audiobooks.command(name="remove")
    async def plex_audiobooks_remove(self, ctx: commands.Context, *, library_name: str):
        """Remove a library from the Audiobook list."""
        async with self.config.guild(ctx.guild).audiobook_libraries() as libs:
            if library_name in libs:
                libs.remove(library_name)
                await ctx.send(f"🗑️ Removed `{library_name}` from Audiobook libraries.")
            else:
                await ctx.send(f"⚠️ `{library_name}` not found in list.")

    @plex_audiobooks.command(name="list")
    async def plex_audiobooks_list(self, ctx: commands.Context):
        """List configured Audiobook libraries."""
        libs = await self.config.guild(ctx.guild).audiobook_libraries()
        if not libs:
            await ctx.send("No Audiobook libraries configured.")
        else:
            await ctx.send(f"**Audiobook Libraries:**\n" + "\n".join([f"- {x}" for x in libs]))
    # ------------------------------------

    @plex.command(name="status")
    async def plex_status(self, ctx: commands.Context):
        """Check settings."""
        data = await self.config.guild(ctx.guild).all()
        await ctx.send(box(
            f"URL: {data['plex_url']}\n"
            f"Token: {'Set' if data['plex_token'] else 'Missing'}\n"
            f"TMDB Key: {'Set' if data['tmdb_api_key'] else 'Missing'}\n"
            f"Google Books Key: {'Set' if data.get('google_books_api_key') else 'Missing'}\n"
            f"Spotify Creds: {'Set' if data.get('spotify_client_id') else 'Missing'}\n"
            f"Channel: {data['activity_channel']}\n"
            f"Audiobook Libs: {data.get('audiobook_libraries')}\n"
            f"Mapped Users: {len(data.get('user_map', {}))}"
        ))


# Setup function
async def setup(bot):
    await bot.add_cog(PlexActivity(bot))
