import discord
import psutil
import asyncio
from datetime import datetime, timedelta
from redbot.core import commands, app_commands, Config
import humanize
from redbot.core.bot import Red
import logging
from typing import Dict, Any, Tuple, List

log = logging.getLogger("red.systemmonitor")


class SystemMonitor(commands.Cog):
    """
    Monitors and displays the current usage of the Ubuntu server.
    Now with Thermals, Top Processes, and Non-Blocking stats!
    """

    DEFAULT_GUILD_SETTINGS = {
        "channel_id": None,
        "message_id": None,
        "enabled": False,
        "show_full_disk": True,
        "split_disk_stats": False,
        "show_processes": True,  # NEW: Toggle for process list
        "last_net_io_sent": 0,
        "last_net_io_recv": 0,
        "last_net_time": 0.0
    }

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier="SystemMonitorCog", force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD_SETTINGS)
        self._update_task = None
        self._update_locks = {}

    async def cog_load(self):
        log.info("SystemMonitor cog loaded. Starting update loop.")
        self._update_task = self.bot.loop.create_task(self._update_loop())

    async def cog_unload(self):
        log.info("SystemMonitor cog unloaded. Cancelling update loop.")
        if self._update_task:
            self._update_task.cancel()

    def _get_bar_chart(self, percentage: float, length: int = 10) -> str:
        percentage = max(0, min(100, percentage))
        filled_blocks = int(length * (percentage / 100))
        empty_blocks = length - filled_blocks
        return "█" * filled_blocks + "░" * empty_blocks

    def _collect_data_sync(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs blocking psutil calls. This is meant to be run in an executor.
        """
        # 1. CPU Stats
        cpu_usage = psutil.cpu_percent(interval=None)  # Non-blocking first call
        # We rely on the loop interval for accurate stats, or we accept instantaneous
        # For better accuracy without blocking, we'd need persistent state,
        # but for this use case, instantaneous or simple interval is fine if threaded.
        # Let's use a small interval to be safe, since we are in a thread now.
        cpu_usage = psutil.cpu_percent(interval=0.5)

        load_avg = psutil.getloadavg()  # (1, 5, 15 min)

        # 2. Thermals (Linux specific mostly)
        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                # Try to find common CPU sensor names
                for name in ['coretemp', 'k10temp', 'cpu_thermal', 'soc_thermal']:
                    if name in temps:
                        # Average the cores or take the first one
                        entries = temps[name]
                        if entries:
                            cpu_temp = entries[0].current
                        break
                # Fallback: just take the first available sensor if we can't identify CPU
                if cpu_temp is None and len(temps) > 0:
                    first_key = next(iter(temps))
                    if temps[first_key]:
                        cpu_temp = temps[first_key][0].current
        except Exception:
            pass

        # 3. Memory
        memory = psutil.virtual_memory()

        # 4. Disk (Aggregated Logic)
        total_disk_stats = {
            'used_disk_gb': 0.0, 'total_disk_gb': 0.0,
            'root_used_gb': 0.0, 'root_total_gb': 0.0,
            'nfs_used_gb': 0.0, 'nfs_total_gb': 0.0
        }

        TARGET_MOUNTS = {'/': 'root', '/mnt/storage': 'nfs'}
        VALID_FSTYPES = ('ext4', 'ext3', 'xfs', 'nfs', 'nfs4', 'fuse.sshfs', 'fuse')

        if settings.get("show_full_disk", True) or settings.get("split_disk_stats", False):
            for part in psutil.disk_partitions(all=True):
                if part.fstype not in VALID_FSTYPES or part.mountpoint not in TARGET_MOUNTS:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    m_type = TARGET_MOUNTS[part.mountpoint]

                    if m_type == 'root':
                        total_disk_stats['root_used_gb'] = usage.used / (1024 ** 3)
                        total_disk_stats['root_total_gb'] = usage.total / (1024 ** 3)
                    elif m_type == 'nfs':
                        total_disk_stats['nfs_used_gb'] = usage.used / (1024 ** 3)
                        total_disk_stats['nfs_total_gb'] = usage.total / (1024 ** 3)
                except Exception:
                    pass

        total_disk_stats['used_disk_gb'] = total_disk_stats['root_used_gb'] + total_disk_stats['nfs_used_gb']
        total_disk_stats['total_disk_gb'] = total_disk_stats['root_total_gb'] + total_disk_stats['nfs_total_gb']

        # 5. Top Processes
        top_processes = []
        if settings.get("show_processes", True):
            try:
                # Iterate processes and sort by memory
                procs = []
                for p in psutil.process_iter(['name', 'memory_percent', 'cpu_percent']):
                    try:
                        # Filter out small stuff to save time? No, just sort.
                        procs.append(p.info)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                # Sort by Memory Usage (descending)
                top_mem = sorted(procs, key=lambda p: p['memory_percent'] or 0, reverse=True)[:3]
                top_processes = top_mem
            except Exception:
                pass

        # 6. Network IO (Snapshot)
        net_io = psutil.net_io_counters()
        boot_time = psutil.boot_time()

        return {
            'cpu_usage': cpu_usage,
            'load_avg': load_avg,
            'cpu_temp': cpu_temp,
            'memory': memory,
            'disk': total_disk_stats,
            'processes': top_processes,
            'net_io': net_io,
            'boot_time': boot_time
        }

    async def _get_system_stats(self, settings: Dict[str, Any], guild_id: int = None) -> Dict[str, Any]:
        # Run the blocking collection in an executor to keep the bot responsive
        data = await self.bot.loop.run_in_executor(None, self._collect_data_sync, settings)

        # --- BANDWIDTH CALCULATION (Requires Async/DB Context) ---
        download_speed_mbps = 0.0
        upload_speed_mbps = 0.0
        current_timestamp = datetime.now().timestamp()

        if guild_id:
            guild_settings = await self.config.guild(discord.Object(id=guild_id)).all()
            last_sent = guild_settings["last_net_io_sent"]
            last_recv = guild_settings["last_net_io_recv"]
            last_time = guild_settings["last_net_time"]

            if last_time != 0.0 and current_timestamp > last_time:
                time_diff = current_timestamp - last_time
                if time_diff > 0:
                    # Calculate diff
                    dl_diff = data['net_io'].bytes_recv - last_recv
                    ul_diff = data['net_io'].bytes_sent - last_sent
                    # Convert to Mbps
                    download_speed_mbps = (dl_diff * 8) / (1024 ** 2 * time_diff)
                    upload_speed_mbps = (ul_diff * 8) / (1024 ** 2 * time_diff)

            # Save new state
            await self.config.guild(discord.Object(id=guild_id)).last_net_io_sent.set(data['net_io'].bytes_sent)
            await self.config.guild(discord.Object(id=guild_id)).last_net_io_recv.set(data['net_io'].bytes_recv)
            await self.config.guild(discord.Object(id=guild_id)).last_net_time.set(current_timestamp)

        data['dl_speed'] = max(0, download_speed_mbps)
        data['ul_speed'] = max(0, upload_speed_mbps)

        return data

    async def _build_embed(self, data: Dict[str, Any], settings: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(title="🖥️ System Command Center", color=discord.Color.dark_teal())

        # --- CPU BLOCK ---
        cpu_usage = data['cpu_usage']
        cpu_temp = data['cpu_temp']
        load = data['load_avg']

        temp_str = f" • 🌡️ {cpu_temp:.1f}°C" if cpu_temp else ""
        load_str = f"Load: {load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
        cpu_bar = self._get_bar_chart(cpu_usage)

        embed.add_field(
            name=f":desktop: CPU: {cpu_usage}%{temp_str}",
            value=f"`{cpu_bar}`\n{load_str}",
            inline=False
        )

        # --- MEMORY BLOCK ---
        mem = data['memory']
        mem_total_gb = mem.total / (1024 ** 3)
        mem_used_gb = mem.used / (1024 ** 3)
        mem_bar = self._get_bar_chart(mem.percent)

        embed.add_field(
            name=f":brain: RAM: {mem.percent}%",
            value=f"`{mem_bar}`\n{mem_used_gb:.2f} / {mem_total_gb:.2f} GB",
            inline=False
        )

        # --- PROCESSES (THE HOGS) ---
        if settings.get("show_processes", True) and data['processes']:
            hog_list = []
            for p in data['processes']:
                name = p['name']
                # If name is too long, truncate
                if len(name) > 15: name = name[:14] + "…"
                hog_list.append(f"• **{name}**: {p['memory_percent']:.1f}% Mem | {p['cpu_percent']:.1f}% CPU")

            embed.add_field(
                name=":microbe: Top Resource Hogs",
                value="\n".join(hog_list),
                inline=False
            )

        # --- STORAGE BLOCK ---
        disk = data['disk']
        if settings.get("split_disk_stats", False):
            # Root
            r_used, r_total = disk['root_used_gb'], disk['root_total_gb']
            r_pct = (r_used / r_total * 100) if r_total else 0
            embed.add_field(name="NVMe (Root)", value=f"`{self._get_bar_chart(r_pct)}`\n{r_used:.1f}/{r_total:.1f} GB",
                            inline=True)

            # NFS
            n_used, n_total = disk['nfs_used_gb'], disk['nfs_total_gb']
            n_pct = (n_used / n_total * 100) if n_total else 0
            embed.add_field(name="NFS (Storage)",
                            value=f"`{self._get_bar_chart(n_pct)}`\n{n_used:.1f}/{n_total:.1f} GB", inline=True)
        else:
            # Combined
            t_used, t_total = disk['used_disk_gb'], disk['total_disk_gb']
            t_pct = (t_used / t_total * 100) if t_total else 0
            embed.add_field(name=":floppy_disk: Storage",
                            value=f"`{self._get_bar_chart(t_pct)}`\n{t_used:.1f}/{t_total:.1f} GB", inline=False)

        # --- NETWORK BLOCK ---
        dl = data['dl_speed']
        ul = data['ul_speed']
        # Let's make a visual distinction for activity
        net_emoji = "🟢" if (dl > 1.0 or ul > 1.0) else "⚪"

        embed.add_field(
            name=f"{net_emoji} Network I/O",
            value=f"⬇️ **DL:** {dl:.2f} Mbps\n⬆️ **UL:** {ul:.2f} Mbps",
            inline=True
        )

        # --- UPTIME BLOCK ---
        uptime_sec = datetime.now().timestamp() - data['boot_time']
        uptime_str = humanize.naturaldelta(timedelta(seconds=int(uptime_sec)))
        embed.add_field(name=":stopwatch: Uptime", value=uptime_str, inline=True)

        embed.timestamp = datetime.now()  # Let Discord handle the timezone
        embed.set_footer(text="System Monitor • Live Update")
        return embed

    async def _update_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                all_guild_settings = await self.config.all_guilds()
                for guild_id, settings in all_guild_settings.items():
                    if not settings["enabled"]:
                        continue

                    guild = self.bot.get_guild(guild_id)
                    channel_id = settings["channel_id"]
                    message_id = settings["message_id"]

                    if not guild or not channel_id:
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue

                    if guild_id not in self._update_locks:
                        self._update_locks[guild_id] = asyncio.Lock()

                    async with self._update_locks[guild_id]:
                        # Collect Data (This handles the heavy lifting in a thread)
                        stats_data = await self._get_system_stats(settings, guild_id)
                        embed = await self._build_embed(stats_data, settings)

                        if message_id:
                            try:
                                msg = await channel.fetch_message(message_id)
                                await msg.edit(embed=embed)
                            except discord.NotFound:
                                msg = await channel.send(embed=embed)
                                await self.config.guild(guild).message_id.set(msg.id)
                            except Exception:
                                pass
                        else:
                            msg = await channel.send(embed=embed)
                            await self.config.guild(guild).message_id.set(msg.id)

            except Exception as e:
                log.error(f"Error in SystemMonitor loop: {e}")

            await asyncio.sleep(60)

    @commands.group(name="systemmonitor", aliases=["sysmon"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def systemmonitor(self, ctx: commands.Context):
        """Manage System Monitor."""
        pass

    @systemmonitor.command(name="setchannel")
    async def sysmon_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the update channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(f"System Monitor set to {channel.mention}.")

    @systemmonitor.command(name="toggle")
    async def sysmon_toggle(self, ctx: commands.Context):
        """Enable/Disable updates."""
        curr = await self.config.guild(ctx.guild).enabled()
        new = not curr
        await self.config.guild(ctx.guild).enabled.set(new)
        await ctx.send(f"System Monitor {'Enabled' if new else 'Disabled'}.")

    @systemmonitor.command(name="toggleprocs")
    async def sysmon_toggle_procs(self, ctx: commands.Context):
        """Toggle the Top Processes display."""
        curr = await self.config.guild(ctx.guild).show_processes()
        new = not curr
        await self.config.guild(ctx.guild).show_processes.set(new)
        await ctx.send(f"Top Processes display {'Enabled' if new else 'Disabled'}.")

    @systemmonitor.command(name="togglesplitdisk")
    async def sysmon_splitdisk(self, ctx: commands.Context):
        """Toggle split/combined disk view."""
        curr = await self.config.guild(ctx.guild).split_disk_stats()
        new = not curr
        await self.config.guild(ctx.guild).split_disk_stats.set(new)
        await ctx.send(f"Split Disk View {'Enabled' if new else 'Disabled'}.")

    @systemmonitor.command(name="manualupdate")
    async def sysmon_manual(self, ctx: commands.Context):
        """Force an update now."""
        settings = await self.config.guild(ctx.guild).all()
        data = await self._get_system_stats(settings, ctx.guild.id)
        embed = await self._build_embed(data, settings)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))