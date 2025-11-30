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
    "image_url_online": None,  # Image when server is UP
    "image_url_offline": None,  # Image when server is DOWN
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
        self.pal_process = None  # Persistent process handle for accurate CPU stats

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
        Hunts for the PalServer process to get real CPU/RAM usage.
        Uses a blocking interval in a thread for accurate instantaneous CPU stats.
        """
        try:
            target_proc = None

            # 1. Try to reuse existing process handle if valid
            if self.pal_process:
                if self.pal_process.is_running():
                    target_proc = self.pal_process
                else:
                    self.pal_process = None

            # 2. Hunt for it if we don't have it
            if not target_proc:
                target_names = ['PalServer-Linux', 'PalServer-Win64']

                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        name = proc.info['name']
                        cmdline = proc.info['cmdline'] or []

                        # Check for the binary name explicitly
                        if any(t in name for t in target_names) or 'PalServer-Linux-Shipp' in name:
                            target_proc = proc
                            break

                            # Fallback: Check for the script if binary isn't found yet
                        if 'PalServer.sh' in name or any('PalServer.sh' in arg for arg in cmdline):
                            try:
                                children = proc.children()
                                for child in children:
                                    if any(t in child.name() for t in
                                           target_names) or 'PalServer-Linux' in child.name():
                                        target_proc = child
                                        break
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass

                            if not target_proc:
                                target_proc = proc

                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                if target_proc:
                    self.pal_process = target_proc

            # 3. Get Stats
            if target_proc:
                # Memory
                with target_proc.oneshot():
                    mem = target_proc.memory_info().rss / (1024 ** 3)

                # CPU: We use a small interval to get an instant reading.
                # Since this runs in an executor, 0.1s blocking is fine and gives >0 results.
                # We divide by cpu_count() to normalize to 0-100% system usage instead of per-core usage.
                raw_cpu = target_proc.cpu_percent(interval=0.1)
                cpu = raw_cpu / psutil.cpu_count()

                return {"cpu": cpu, "ram_gb": mem, "status": "Running"}

        except Exception as e:
            log.error(f"Process check error: {e}")

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

            # 3. Server Info (Version/Name/MaxPlayers?)
            async with self.session.get(f"{url}v1/api/info", headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    info = await resp.json()

            # 4. Settings (Try to get MaxPlayers)
            try:
                async with self.session.get(f"{url}v1/api/settings", headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        settings_data = await resp.json()
                        if "PublicServerPlayerCount" in settings_data:
                            info["maxplayers"] = settings_data["PublicServerPlayerCount"]
                        elif "ServerPlayerMaxNum" in settings_data:
                            info["maxplayers"] = settings_data["ServerPlayerMaxNum"]
            except Exception:
                pass

            return {"metrics": metrics, "players": players, "info": info}

        except Exception as e:
            # log.error(f"Palworld API Fail: {e}")
            return None

    # --- BUILD EMBED ---
    async def _build_embed(self, api_data, proc_data, settings) -> discord.Embed:
        server_name = settings["server_name"]

        # Determine Max Players: API > Config
        max_players = settings["max_players"]
        if api_data and "info" in api_data:
            api_max = api_data["info"].get("maxplayers") or api_data["info"].get("MaxPlayers")
            if api_max:
                try:
                    max_players = int(api_max)
                except ValueError:
                    pass

        is_online = bool(api_data or proc_data)

        # --- IMAGE LOGIC ---
        image_url = settings.get("image_url_online") if is_online else settings.get("image_url_offline")
        # Fallback if one is missing but the other exists
        if not image_url:
            image_url = settings.get("image_url_online") or settings.get("image_url_offline")

        if not is_online:
            embed = discord.Embed(
                title=f"🦖 {server_name}",
                description="🔴 **Offline**\nServer is unreachable via API or Process list.",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            # Use Thumbnail for offline state too if image exists
            if image_url: embed.set_thumbnail(url=image_url)
            return embed

        status_color = discord.Color.green()
        status_text = "🟢 Online"

        metrics = api_data.get("metrics", {}) if api_data else {}
        players = api_data.get("players", []) if api_data else []
        info = api_data.get("info", {}) if api_data else {}

        server_fps = metrics.get("serverfps", 0)
        frame_time = metrics.get("frametime", 0)
        version = info.get("version", "Unknown")

        if server_fps < 30 and server_fps > 0:
            status_color = discord.Color.orange()
            status_text = "🟡 Degraded (Low FPS)"

        if not api_data and proc_data:
            status_color = discord.Color.yellow()
            status_text = "🟡 Starting / API Unreachable"

        embed = discord.Embed(title=f"🦖 {server_name}", color=status_color)
        embed.description = f"**Status:** {status_text} • **{version}**"

        # CHANGED: Use set_thumbnail instead of set_image
        if image_url: embed.set_thumbnail(url=image_url)

        # --- PERFORMANCE BLOCK ---
        perf_str = "Waiting for data..."
        if api_data:
            perf_str = f"**FPS:** `{server_fps:.1f}`"
            if server_fps >= 55:
                perf_str += " ✨"
            elif server_fps < 20:
                perf_str += " 💀"

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
                name = p.get("name", "Unknown")
                if not name: name = "Unknown Survivor"
                level = p.get("level", None)
                detail = f" • **{name}**"
                if level: detail += f" (Lvl {level})"
                player_list.append(detail)

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

    # --- NEW COMMAND: Set Max Players ---
    @palwatch.command(name="setmax")
    async def pw_setmax(self, ctx: commands.Context, max_players: int):
        """Set the maximum player count manually."""
        if max_players < 1:
            return await ctx.send("Max players must be at least 1.")

        await self.config.guild(ctx.guild).max_players.set(max_players)
        await ctx.send(f"Max players set to: **{max_players}**")

    # --- NEW COMMANDS: Set Images ---
    @palwatch.command(name="setimage")
    async def pw_setimage(self, ctx: commands.Context, state: str, image_url: str):
        """
        Set the embed image for online/offline states.
        Usage: [p]pw setimage online <url> OR [p]pw setimage offline <url>
        """
        state = state.lower()
        if state not in ["online", "offline"]:
            return await ctx.send("State must be 'online' or 'offline'.")

        if not image_url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with http/https.")

        if state == "online":
            await self.config.guild(ctx.guild).image_url_online.set(image_url)
        else:
            await self.config.guild(ctx.guild).image_url_offline.set(image_url)

        await ctx.send(f"Server {state} image set!")

    @palwatch.command(name="clearimage")
    async def pw_clearimage(self, ctx: commands.Context):
        """Remove both server images."""
        await self.config.guild(ctx.guild).image_url_online.set(None)
        await self.config.guild(ctx.guild).image_url_offline.set(None)
        await ctx.send("Server images removed.")


async def setup(bot):
    await bot.add_cog(PalworldWatch(bot))