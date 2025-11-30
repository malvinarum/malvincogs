import discord
import aiohttp
import asyncio
import logging
import psutil
from datetime import datetime, timedelta
from redbot.core import commands, Config, app_commands, checks
from redbot.core.utils.chat_formatting import box
from discord.ext import tasks
import base64

log = logging.getLogger("red.palworldwatch")

DEFAULT_GUILD_SETTINGS = {
    "api_url": None,  # e.g. http://127.0.0.1:8212
    "api_password": None,  # AdminPassword from PalWorldSettings.ini
    "channel_id": None,
    "message_id": None,
    "server_name": "Palworld Server",
    "max_players": 32,
    "enabled": False
}


class PalworldWatch(commands.Cog):
    """
    Mission Control for Palworld.
    Monitors via REST API and OS Process inspection.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=555666777, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()
        self._watch_loop_task = None

    async def cog_load(self):
        log.info("PalworldWatch loaded. Starting loop.")
        self._watch_loop_task = self.watch_loop.start()

    async def cog_unload(self):
        log.info("PalworldWatch unloaded.")
        if self._watch_loop_task:
            self.watch_loop.cancel()
        if self.session:
            await self.session.close()

    # --- HELPERS ---
    def _generate_progress_bar(self, current: int, total: int, length: int = 10) -> str:
        if total == 0: return "░" * length
        percent = min(1.0, max(0.0, current / total))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    def _get_process_stats(self):
        """
        Hunts for the PalServer-Linux process to get real CPU/RAM usage.
        """
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                # Name might vary: 'PalServer-Linux-Test', 'PalServer-Linux', etc.
                if 'PalServer' in proc.info['name']:
                    # cpu_percent needs a second call to be accurate usually, but we take what we get
                    # dividing by cpu_count is optional depending on how you want to display it
                    mem_gb = proc.info['memory_info'].rss / (1024 ** 3)
                    return {
                        "cpu": proc.info['cpu_percent'],
                        "ram_gb": mem_gb,
                        "status": "Running"
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    async def _fetch_api_metrics(self, url, password):
        """
        Fetches metrics/info from Palworld REST API.
        """
        if not url or not password: return None

        if not url.endswith("/"): url += "/"

        # Basic Auth construction
        auth_str = f"admin:{password}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        headers = {"Authorization": f"Basic {auth_b64}", "Accept": "application/json"}

        # We need /v1/api/metrics and /v1/api/players
        metrics = {}
        players = []
        info = {}

        try:
            # 1. Metrics (FPS, FrameTime)
            async with self.session.get(f"{url}v1/api/metrics", headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    metrics = await resp.json()

            # 2. Players
            async with self.session.get(f"{url}v1/api/players", headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    players = data.get("players", [])

            # 3. Server Info (Version/Name)
            async with self.session.get(f"{url}v1/api/info", headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    info = await resp.json()

            return {"metrics": metrics, "players": players, "info": info}

        except Exception as e:
            # log.error(f"Palworld API Fail: {e}")
            # Commented out to prevent log spam if server is restarting
            return None

    # --- BUILD EMBED ---
    async def _build_embed(self, api_data, proc_data, settings) -> discord.Embed:
        server_name = settings["server_name"]
        max_players = settings["max_players"]

        if not api_data and not proc_data:
            # Total blackout
            return discord.Embed(
                title=f"🦖 {server_name}",
                description="🔴 **Offline**\nServer is unreachable via API or Process list.",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )

        # Status Logic
        status_color = discord.Color.green()
        status_text = "🟢 Online"

        # API Data
        metrics = api_data.get("metrics", {}) if api_data else {}
        players = api_data.get("players", []) if api_data else []
        info = api_data.get("info", {}) if api_data else {}

        server_fps = metrics.get("serverfps", 0)
        frame_time = metrics.get("frametime", 0)  # In ms usually
        version = info.get("version", "Unknown")

        # Degraded check
        if server_fps < 30 and server_fps > 0:
            status_color = discord.Color.orange()
            status_text = "🟡 Degraded (Low FPS)"

        if not api_data and proc_data:
            # Process exists but API down (Starting up or Crashed)
            status_color = discord.Color.yellow()
            status_text = "🟡 Starting / API Unreachable"

        embed = discord.Embed(title=f"🦖 {server_name}", color=status_color)
        embed.description = f"**Status:** {status_text} • **v{version}**"

        # --- PERFORMANCE BLOCK ---
        perf_str = "Waiting for data..."
        if api_data:
            perf_str = f"**FPS:** `{server_fps:.1f}`"
            # Target 60
            if server_fps >= 55:
                perf_str += " ✨"
            elif server_fps < 20:
                perf_str += " 💀"

            # Process Data Merge
            if proc_data:
                perf_str += f"\n**RAM:** `{proc_data['ram_gb']:.1f} GB`"
                perf_str += f"\n**CPU:** `{proc_data['cpu']:.1f}%`"

        embed.add_field(name="📊 Performance", value=perf_str, inline=True)

        # --- POPULATION BLOCK ---
        curr_players = len(players)
        bar = self._generate_progress_bar(curr_players, max_players, length=10)

        pop_str = f"`{bar}` **{curr_players} / {max_players}**"

        if players:
            player_list = []
            for p in players:
                # Palworld API usually gives: name, playerId, userId, ip, ping, location_x/y
                name = p.get("name", "Unknown")
                # Filter weird empty names
                if not name: name = "Unknown Survivor"

                # Ping (if available)
                ping = p.get("ping", 0)
                # Level (sometimes available in newer versions or mods, standard api might lack it)
                level = p.get("level", None)

                detail = f" • **{name}**"
                if level: detail += f" (Lvl {level})"
                # if ping > 0: detail += f" `{ping}ms`" # Ping often broken in API, enable if you trust it

                player_list.append(detail)

            # Truncate if too many
            if len(player_list) > 15:
                pop_str += "\n" + "\n".join(player_list[:15]) + f"\n...and {len(player_list) - 15} more."
            else:
                pop_str += "\n" + "\n".join(player_list)
        else:
            pop_str += "\n*The archipelago is quiet.*"

        embed.add_field(name="👥 Population", value=pop_str, inline=False)

        embed.timestamp = datetime.now()
        embed.set_footer(text="PalworldWatch • Live Telemetry")
        return embed

    @tasks.loop(seconds=60)
    async def watch_loop(self):
        await self.bot.wait_until_ready()
        all_guilds = await self.config.all_guilds()

        for guild_id, settings in all_guilds.items():
            if not settings["enabled"]: continue

            channel_id = settings["channel_id"]
            if not channel_id: continue

            channel = self.bot.get_channel(channel_id)
            if not channel: continue

            # 1. Fetch Data
            # Use run_in_executor for psutil to avoid blocking
            proc_data = await self.bot.loop.run_in_executor(None, self._get_process_stats)
            api_data = await self._fetch_api_metrics(settings["api_url"], settings["api_password"])

            # 2. Build Embed
            embed = await self._build_embed(api_data, proc_data, settings)

            # 3. Send/Edit
            message_id = settings["message_id"]
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

    @commands.group(name="palwatch", aliases=["pw"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def palwatch(self, ctx: commands.Context):
        """Manage PalworldWatch."""
        pass

    @palwatch.command(name="setup")
    async def pw_setup(self, ctx: commands.Context):
        """Configure API connection."""
        await ctx.send("Enter REST API URL (e.g. http://127.0.0.1:8212):")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            url = msg.content.strip()

            await ctx.send("Enter Admin Password:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            pwd = msg.content.strip()

            await self.config.guild(ctx.guild).api_url.set(url)
            await self.config.guild(ctx.guild).api_password.set(pwd)
            await ctx.send("Saved! (Ensure REST API is enabled in PalWorldSettings.ini)")
        except asyncio.TimeoutError:
            await ctx.send("Timed out.")

    @palwatch.command(name="setchannel")
    async def pw_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set output channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(f"Dashboard will spawn in {channel.mention}.")

    @palwatch.command(name="toggle")
    async def pw_toggle(self, ctx: commands.Context):
        """Enable/Disable."""
        curr = await self.config.guild(ctx.guild).enabled()
        new = not curr
        await self.config.guild(ctx.guild).enabled.set(new)
        await ctx.send(f"PalworldWatch {'Enabled' if new else 'Disabled'}.")

    @palwatch.command(name="setname")
    async def pw_setname(self, ctx: commands.Context, *, name: str):
        """Set the display name for the server."""
        await self.config.guild(ctx.guild).server_name.set(name)
        await ctx.send(f"Server name set to: **{name}**")


async def setup(bot):
    await bot.add_cog(PalworldWatch(bot))