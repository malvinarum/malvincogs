import asyncio
import aiohttp
import logging
import io
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
    "tmdb_api_key": None
}


class PlexActivity(commands.Cog):
    """
    A Redbot cog to track and display Plex Media Server activity.
    Now with Device-Specific Emojis and Bandwidth/Bitrate info! 📺 🎮 💻
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()
        self._plex_activity_loop_task = None
        self.color_cache = {}

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

    # --- DEVICE EMOJI LOGIC ---
    def _get_device_emoji(self, device_name: str) -> str:
        d = device_name.lower()

        if any(x in d for x in ["tv", "roku", "chromecast", "fire", "shield", "bravia", "lg", "samsung"]):
            return "📺"

        if any(x in d for x in ["playstation", "xbox", "ps4", "ps5", "switch"]):
            return "🎮"

        if "mac" in d or "osx" in d or "apple" in d:
            return "🍎"
        if "windows" in d or "pc" in d:
            return "🪟"
        if "linux" in d:
            return "🐧"

        if any(x in d for x in ["desktop", "laptop"]):
            return "💻"

        if any(x in d for x in ["web", "chrome", "firefox", "edge", "safari", "opera"]):
            return "🌐"

        if any(x in d for x in ["phone", "ipad", "iphone", "android", "mobile", "tablet"]):
            return "📱"

        return "📱"

    async def _get_dominant_color(self, image_url: str):
        if not HAS_PIL or not image_url:
            return None
        if image_url in self.color_cache:
            return self.color_cache[image_url]

        try:
            async with self.session.get(image_url) as resp:
                if resp.status == 200:
                    data = await resp.read()

                    def get_color(img_data):
                        img = Image.open(io.BytesIO(img_data))
                        img = img.convert("RGB")
                        img = img.resize((1, 1))
                        return img.getpixel((0, 0))

                    rgb = await self.bot.loop.run_in_executor(None, get_color, data)
                    color = discord.Color.from_rgb(*rgb)
                    if len(self.color_cache) > 100:
                        self.color_cache.clear()
                    self.color_cache[image_url] = color
                    return color
        except Exception:
            return None

    async def _fetch_tmdb_poster(self, api_key: str, query: str, media_type: str = 'movie', year: str = None):
        if not api_key:
            return None
        search_url = f"https://api.themoviedb.org/3/search/{media_type}"
        params = {'api_key': api_key, 'query': query, 'page': 1}
        if year and media_type == 'movie':
            params['year'] = year

        try:
            async with self.session.get(search_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data['results']:
                        poster_path = data['results'][0].get('poster_path')
                        if poster_path:
                            return f"https://image.tmdb.org/t/p/w500{poster_path}"
        except Exception:
            pass
        return None

    async def _get_plex_sessions(self, guild_id: int):
        settings = await self.config.guild_from_id(guild_id).all()
        plex_url = settings["plex_url"]
        plex_token = settings["plex_token"]
        tmdb_key = settings.get("tmdb_api_key")

        if not plex_url or not plex_token:
            return []

        if not plex_url.endswith("/"):
            plex_url += "/"

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
                        media_elem = session_elem.find("Media")  # Important for Bitrate
                        transcode_elem = session_elem.find("TranscodeSession")

                        if user_elem is None or player_elem is None:
                            continue

                        username = user_elem.get("title", "Unknown User")
                        user_thumb = user_elem.get("thumb")
                        if user_thumb and not user_thumb.startswith("http"):
                            user_thumb = f"{user_thumb}?X-Plex-Token={plex_token}"

                        media_title = session_elem.get("title", "Unknown Title")
                        view_offset_ms = int(session_elem.get("viewOffset", "0"))
                        duration_ms = int(session_elem.get("duration", "1"))
                        year = session_elem.get("year")

                        current_time_formatted = self._format_milliseconds_to_time(view_offset_ms)
                        total_duration_formatted = self._format_milliseconds_to_time(duration_ms)

                        remaining_ms = max(0, duration_ms - view_offset_ms)
                        finish_time = datetime.now() + timedelta(milliseconds=remaining_ms)
                        finish_ts = int(finish_time.timestamp())

                        series_title = session_elem.get("grandparentTitle")
                        media_type = session_elem.get("type", "media")
                        device = player_elem.get("product", "Unknown Device")
                        state = player_elem.get("state", "playing")

                        # --- BANDWIDTH / BITRATE ---
                        bitrate_kbps = 0
                        if media_elem is not None:
                            # Plex reports bitrate in kbps
                            bitrate_kbps = int(media_elem.get("bitrate", 0))

                        bandwidth_str = "Unknown"
                        if bitrate_kbps > 0:
                            bandwidth_mbps = bitrate_kbps / 1000
                            bandwidth_str = f"{bandwidth_mbps:.1f} Mbps"

                        stream_info = "Direct Play"
                        if transcode_elem is not None:
                            video_decision = transcode_elem.get("videoDecision", "unknown")
                            if video_decision == "transcode":
                                stream_info = "Transcoding ⚠️"
                            else:
                                stream_info = "Direct Stream"

                        image_url = None
                        if tmdb_key:
                            search_query = series_title if media_type == 'episode' else media_title
                            search_type = 'tv' if media_type == 'episode' else 'movie'
                            image_url = await self._fetch_tmdb_poster(tmdb_key, search_query, search_type, year)

                        if not image_url:
                            thumb_path = session_elem.get("art") or session_elem.get("thumb")
                            if thumb_path:
                                base_plex_url = plex_url.rstrip('/')
                                image_url = f"{base_plex_url}{thumb_path}?X-Plex-Token={plex_token}"

                        session_data = {
                            "user": username,
                            "user_thumb": user_thumb,
                            "type": media_type,
                            "current_time": current_time_formatted,
                            "total_duration": total_duration_formatted,
                            "current_ms": view_offset_ms,
                            "total_ms": duration_ms,
                            "finish_ts": finish_ts,
                            "device": device,
                            "state": state,
                            "stream_info": stream_info,
                            "bandwidth": bandwidth_str,
                            "image_url": image_url
                        }

                        if media_type == "episode":
                            session_data["series_title"] = series_title
                            session_data["episode_title"] = media_title
                            session_data["season_num"] = int(session_elem.get("parentIndex", "0"))
                            session_data["episode_num"] = int(session_elem.get("index", "0"))
                        else:
                            session_data["title"] = media_title

                        sessions.append(session_data)
                except ET.ParseError:
                    return []

                return sessions
        except Exception:
            return []

    async def _generate_session_embeds(self, sessions: list):
        if not sessions:
            embed = discord.Embed(
                title="Plex Media Server",
                description="😴 No active streams. Server is idle.",
                color=discord.Color.dark_grey(),
                timestamp=datetime.now()
            )
            return [embed]

        embeds = []
        for session in sessions[:10]:
            user = session.get("user", "Unknown")
            media_type = session.get("type")
            current = session.get("current_time")
            total = session.get("total_duration")
            device = session.get("device")
            image_url = session.get("image_url")
            state = session.get("state")
            stream_info = session.get("stream_info")
            bandwidth = session.get("bandwidth")
            finish_ts = session.get("finish_ts")

            state_icon = "▶️"
            if state == "paused":
                state_icon = "⏸️"
            elif state == "buffering":
                state_icon = "⏳"

            device_emoji = self._get_device_emoji(device)

            color = discord.Color.orange() if media_type == 'movie' else discord.Color.blue()
            if image_url and HAS_PIL:
                dynamic_color = await self._get_dominant_color(image_url)
                if dynamic_color:
                    color = dynamic_color

            embed = discord.Embed(color=color)
            user_icon = session.get("user_thumb") or "https://i.imgur.com/1F0B7gP.png"
            embed.set_author(name=f"{user} is watching...", icon_url=user_icon)

            if media_type == "episode":
                embed.title = session.get("series_title")
                embed.description = f"**{session.get('episode_title')}**\n`S{session.get('season_num'):02}E{session.get('episode_num'):02}`"
            else:
                embed.title = session.get("title")
                embed.description = f"*{media_type.capitalize()}*"

            bar = self._generate_progress_bar(session.get("current_ms"), session.get("total_ms"))

            embed.add_field(
                name=f"{state_icon} Progress",
                value=f"`{bar}`\n`{current} / {total}`\nEnds: <t:{finish_ts}:R>",
                inline=False
            )

            # Added Bandwidth to Tech Specs
            embed.add_field(
                name="Tech Specs",
                value=f"{device_emoji} **Device:** `{device}`\n⚙️ **Stream:** `{stream_info}`\n📶 **Bitrate:** `{bandwidth}`",
                inline=False
            )

            if image_url:
                embed.set_thumbnail(url=image_url)

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

            if not channel_id:
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                continue

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
        await ctx.send("Enter Plex URL (e.g. http://192.168.1.100:32400):")
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
        """Set the TMDB API Key for fetching posters."""
        await self.config.guild(ctx.guild).tmdb_api_key.set(api_key)
        await ctx.send("✅ TMDB API Key set!")

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

    @plex.command(name="status")
    async def plex_status(self, ctx: commands.Context):
        """Check settings."""
        data = await self.config.guild(ctx.guild).all()
        await ctx.send(box(
            f"URL: {data['plex_url']}\n"
            f"Token: {'Set' if data['plex_token'] else 'Missing'}\n"
            f"TMDB Key: {'Set' if data['tmdb_api_key'] else 'Missing'}\n"
            f"Channel: {data['activity_channel']}"
        ))


# Setup function
async def setup(bot):
    await bot.add_cog(PlexActivity(bot))