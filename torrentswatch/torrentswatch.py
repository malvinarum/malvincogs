import discord
import aiohttp
import asyncio
import logging
from datetime import datetime
from redbot.core import commands, Config, app_commands, checks
from redbot.core.utils.chat_formatting import box
from discord.ext import tasks

log = logging.getLogger("red.torrentswatch")

DEFAULT_GUILD_SETTINGS = {
    "qbit_url": None,
    "qbit_user": None,
    "qbit_pass": None,
    "channel_id": None,
    "message_id": None,
    "update_interval": 60,
    "enabled": False
}


class TorrentsWatch(commands.Cog):
    """
    A cog to monitor qBittorrent directly.
    Source of Truth Edition.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()
        self._watch_loop_task = None
        self._auth_cookie = None

    async def cog_load(self):
        log.info("TorrentsWatch (qBit) loaded. Starting loop.")
        self._watch_loop_task = self.watch_loop.start()

    async def cog_unload(self):
        log.info("TorrentsWatch unloaded.")
        if self._watch_loop_task:
            self.watch_loop.cancel()
        if self.session:
            await self.session.close()

    def _generate_progress_bar(self, percent: float, length: int = 10) -> str:
        percent = min(1.0, max(0.0, percent))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    def _format_size(self, size_bytes: float) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / 1024 ** 2:.1f} MB"
        else:
            return f"{size_bytes / 1024 ** 3:.2f} GB"

    def _format_speed(self, speed_bytes: float) -> str:
        if speed_bytes == 0: return "0 B/s"
        return f"{self._format_size(speed_bytes)}/s"

    async def _qbit_login(self, url, user, password):
        """
        Authenticates with qBittorrent and stores the SID cookie.
        """
        try:
            login_url = f"{url}api/v2/auth/login"
            data = {'username': user, 'password': password}
            async with self.session.post(login_url, data=data, timeout=5) as resp:
                if resp.status == 200:
                    # qBit sets a cookie named 'SID'
                    # aiohttp client session stores cookies automatically if we use the same session
                    # but sometimes we need to be explicit if it fails.
                    # For now, trusting the session cookie jar.
                    if 'SID' in resp.cookies:
                        return True
                    # Sometimes it just returns "Ok." as text
                    text = await resp.text()
                    return "Ok." in text
        except Exception as e:
            log.error(f"qBit Login Failed: {e}")
        return False

    async def _fetch_qbit_data(self, url):
        """
        Fetches main data (torrents and server state).
        """
        if not url: return None, None

        # Endpoint for all torrents
        torrents_url = f"{url}api/v2/torrents/info?filter=all"
        # Endpoint for global transfer info
        global_url = f"{url}api/v2/transfer/info"

        torrents = []
        global_info = {}

        try:
            async with self.session.get(torrents_url, timeout=10) as resp:
                if resp.status == 403: return "AUTH_REQUIRED", None
                if resp.status == 200: torrents = await resp.json()

            async with self.session.get(global_url, timeout=10) as resp:
                if resp.status == 200: global_info = await resp.json()

            return torrents, global_info
        except Exception as e:
            log.error(f"qBit Fetch Error: {e}")
            return [], {}

    async def _build_embed(self, torrents: list, global_info: dict) -> discord.Embed:
        # Global Stats
        dl_speed = global_info.get('dl_info_speed', 0)
        ul_speed = global_info.get('up_info_speed', 0)

        title_str = f"📥 qBittorrent • ⬇️ {self._format_speed(dl_speed)} • ⬆️ {self._format_speed(ul_speed)}"

        embed = discord.Embed(title=title_str, color=discord.Color.green())

        if not torrents:
            embed.description = "😴 No torrents in client."
            embed.timestamp = datetime.now()
            return embed

        # Logic: Show Downloading/Active first, then Error, then Queued, then Completed
        # qBit states: downloading, stalledDL, metaDL, queuedDL, uploading, stalledUP, completed, pausedDL

        # Priority Sort
        def get_priority(t):
            state = t.get('state', '')
            if 'meta' in state: return 0  # Metadata is interesting
            if 'downloading' in state: return 1
            if 'stalledDL' in state: return 2
            if 'queuedDL' in state: return 3
            if 'error' in state: return 4
            if 'uploading' in state or 'stalledUP' in state: return 5  # Seeding
            return 9  # Completed/Paused/etc

        torrents.sort(key=get_priority)

        # Separate lists for visual clarity
        active_lines = []
        seeding_lines = []

        count_downloading = 0

        for t in torrents:
            state = t.get('state', 'unknown')
            # Filter: Don't show completed/paused unless you want to
            # Let's show active DL, Metadata, and maybe top seeds

            is_downloading = state in ['downloading', 'metaDL', 'stalledDL', 'queuedDL', 'forcedDL']
            is_seeding = state in ['uploading', 'stalledUP', 'queuedUP', 'forcedUP']

            if not is_downloading and not is_seeding and state != 'error':
                continue  # Skip completed/paused to keep list clean

            name = t.get('name', 'Unknown')
            if len(name) > 40: name = name[:38] + "..."

            progress = t.get('progress', 0)  # 0.0 to 1.0
            size = t.get('size', 0)
            dlspeed = t.get('dlspeed', 0)
            eta = t.get('eta', 8640000)  # Seconds

            # Emojis
            status_icon = "⏸️"
            if 'downloading' in state:
                status_icon = "⏬"
            elif 'stalledDL' in state:
                status_icon = "🐢"  # Stalled
            elif 'metaDL' in state:
                status_icon = "📡"  # Metadata
            elif 'uploading' in state:
                status_icon = "⏫"
            elif 'queued' in state:
                status_icon = "⏳"
            elif 'error' in state:
                status_icon = "❌"

            # ETA String
            eta_str = ""
            if is_downloading and dlspeed > 0 and eta < 8640000:
                # Simple formatting
                if eta < 60:
                    eta_str = f"{eta}s"
                elif eta < 3600:
                    eta_str = f"{eta // 60}m"
                elif eta < 86400:
                    eta_str = f"{eta // 3600}h"
                else:
                    eta_str = ">1d"
                eta_str = f" • ⏱️ {eta_str}"

            # Build Line
            bar = self._generate_progress_bar(progress, 10)
            pct = int(progress * 100)

            line = f"{status_icon} **{name}**\n`{bar}` {pct}%"

            if is_downloading:
                if state == 'metaDL':
                    line += " • Fetching Meta..."
                else:
                    line += f" • {self._format_speed(dlspeed)}{eta_str}"

                if len(active_lines) < 8:  # Limit Active
                    active_lines.append(line)
                count_downloading += 1

            elif is_seeding:
                if len(seeding_lines) < 3:  # Limit Seeding display
                    seeding_lines.append(f"{status_icon} **{name}** ({pct}%)")

        if active_lines:
            embed.add_field(name=f"Active Downloads ({count_downloading})", value="\n".join(active_lines), inline=False)
        else:
            embed.add_field(name="Active Downloads", value="*Nothing downloading.*", inline=False)

        if seeding_lines:
            embed.add_field(name="Seeding (Top 3)", value="\n".join(seeding_lines), inline=False)

        embed.set_footer(text=f"qBittorrent Direct • Last Updated: {datetime.now().strftime('%H:%M:%S')}")
        return embed

    @tasks.loop(seconds=60)
    async def watch_loop(self):
        await self.bot.wait_until_ready()
        all_guilds = await self.config.all_guilds()

        for guild_id, settings in all_guilds.items():
            if not settings["enabled"]: continue

            channel_id = settings["channel_id"]
            message_id = settings["message_id"]
            url = settings["qbit_url"]
            user = settings["qbit_user"]
            pwd = settings["qbit_pass"]

            if not channel_id or not url: continue

            # Ensure Protocol
            if not url.startswith("http"): url = f"http://{url}"
            if not url.endswith("/"): url += "/"

            # 1. Fetch Data
            data, global_info = await self._fetch_qbit_data(url)

            # 2. Re-Auth if needed
            if data == "AUTH_REQUIRED":
                success = await self._qbit_login(url, user, pwd)
                if success:
                    data, global_info = await self._fetch_qbit_data(url)
                else:
                    log.warning("qBit Auto-Login failed.")
                    continue  # Skip this cycle

            if data is None: continue  # Fetch failed

            # 3. Build & Post
            embed = await self._build_embed(data, global_info)
            channel = self.bot.get_channel(channel_id)
            if not channel: continue

            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    msg = await channel.send(embed=embed)
                    await self.config.guild(discord.Object(id=guild_id)).message_id.set(msg.id)
                except Exception:
                    pass
            else:
                msg = await channel.send(embed=embed)
                await self.config.guild(discord.Object(id=guild_id)).message_id.set(msg.id)

    @commands.group(name="torrentswatch", aliases=["tw"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def torrentswatch(self, ctx: commands.Context):
        """Manage qBittorrent Monitoring."""
        pass

    @torrentswatch.command(name="setup")
    async def tw_setup(self, ctx: commands.Context):
        """Set up qBittorrent connection."""
        await ctx.send("Enter qBittorrent WebUI URL (e.g. http://192.168.1.50:8080):")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            url = msg.content.strip()

            await ctx.send("Enter Username:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            user = msg.content.strip()

            await ctx.send("Enter Password:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            pwd = msg.content.strip()

            await self.config.guild(ctx.guild).qbit_url.set(url)
            await self.config.guild(ctx.guild).qbit_user.set(user)
            await self.config.guild(ctx.guild).qbit_pass.set(pwd)

            await ctx.send("Testing connection...")
            # Ensure Protocol for test
            if not url.startswith("http"): url = f"http://{url}"
            if not url.endswith("/"): url += "/"

            success = await self._qbit_login(url, user, pwd)
            if success:
                await ctx.send("✅ Connected successfully!")
            else:
                await ctx.send("❌ Connection failed. Check credentials/URL.")

        except asyncio.TimeoutError:
            await ctx.send("Timed out.")

    @torrentswatch.command(name="setchannel")
    async def tw_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the dashboard channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(f"Dashboard will appear in {channel.mention}.")

    @torrentswatch.command(name="toggle")
    async def tw_toggle(self, ctx: commands.Context):
        """Enable/Disable monitoring."""
        curr = await self.config.guild(ctx.guild).enabled()
        new = not curr
        await self.config.guild(ctx.guild).enabled.set(new)
        await ctx.send(f"TorrentsWatch is now {'Enabled' if new else 'Disabled'}.")


async def setup(bot):
    await bot.add_cog(TorrentsWatch(bot))