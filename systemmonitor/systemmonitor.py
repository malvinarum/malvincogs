import discord
import psutil
import asyncio
from datetime import datetime, timedelta
from redbot.core import commands, app_commands, Config
import humanize  # Import the humanize library
from redbot.core.bot import Red  # Ensure this import is present and correct
import logging
from typing import Dict, Any, Tuple

log = logging.getLogger("red.systemmonitor")


class SystemMonitor(commands.Cog):
    """
    Monitors and displays the current usage of the Ubuntu server, with auto-updating messages.
    """

    # Default configuration for each guild
    DEFAULT_GUILD_SETTINGS = {
        "channel_id": None,
        "message_id": None,
        "enabled": False,
        "show_full_disk": True,  # Aggregate disk usage (legacy)
        "split_disk_stats": False,  # NEW: Show separate fields for root and nfs mount
        "last_net_io_sent": 0,
        "last_net_io_recv": 0,
        "last_net_time": 0.0  # Unix timestamp
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
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

    async def _get_system_stats(self, settings: Dict[str, Any], guild_id: int = None) -> Tuple:
        """
        Gathers system usage statistics.
        Returns: (cpu_usage, used_memory_gb, total_memory_gb,
                  total_disk_stats,  # Dictionary containing aggregated and split disk usage
                  download_speed_mbps, upload_speed_mbps,
                  uptime_string, last_updated_human_format)
        """
        cpu_usage = psutil.cpu_percent(interval=0.5)

        memory = psutil.virtual_memory()
        total_memory_gb = memory.total / (1024 ** 3)
        used_memory_gb = memory.used / (1024 ** 3)

        # --- DISK USAGE AGGREGATION ---

        # Initialize storage variables
        total_disk_stats = {
            'used_disk_gb': 0.0,
            'total_disk_gb': 0.0,
            'root_used_gb': 0.0,
            'root_total_gb': 0.0,
            'nfs_used_gb': 0.0,
            'nfs_total_gb': 0.0,
            'total_total_disk_bytes': 0  # Used for sanity check
        }

        show_full_disk = settings.get("show_full_disk", True)

        # Define partitions we want to track separately for the split view
        TARGET_MOUNTS = {
            '/': 'root',
            '/mnt/storage': 'nfs'
        }

        if show_full_disk or settings.get("split_disk_stats", False):
            # Use all=True to ensure network mounts (like NFS) are included
            for part in psutil.disk_partitions(all=True):

                # Filter 1: Exclude common virtual/non-storage mounts
                if part.fstype in ('', 'cdrom', 'tmpfs', 'devtmpfs', 'overlay', 'aufs',
                                   'squashfs') or part.mountpoint.startswith('/snap'):
                    continue

                try:
                    usage = psutil.disk_usage(part.mountpoint)

                    # 2. Check if this partition is one of our two targets (Root or NFS)
                    is_target = part.mountpoint in TARGET_MOUNTS

                    # 3. Apply the filtering logic only if we are aggregating the sum (show_full_disk)
                    # When aggregating, we only want the Root and the NFS mount, avoiding everything else.
                    if show_full_disk and not is_target:
                        continue

                        # 4. If it passes filters, aggregate to the TOTAL SUM
                    total_disk_stats['total_total_disk_bytes'] += usage.total
                    total_disk_stats['used_disk_gb'] += usage.used / (1024 ** 3)
                    total_disk_stats['total_disk_gb'] += usage.total / (1024 ** 3)

                    # 5. Track split stats if it's a target partition
                    if is_target:
                        mount_type = TARGET_MOUNTS[part.mountpoint]
                        if mount_type == 'root':
                            total_disk_stats['root_used_gb'] = usage.used / (1024 ** 3)
                            total_disk_stats['root_total_gb'] = usage.total / (1024 ** 3)
                        elif mount_type == 'nfs':
                            total_disk_stats['nfs_used_gb'] = usage.used / (1024 ** 3)
                            total_disk_stats['nfs_total_gb'] = usage.total / (1024 ** 3)

                except Exception as e:
                    log.debug(f"Could not read disk usage for {part.mountpoint}: {e}")
        # --- END DISK USAGE AGGREGATION ---

        # Bandwidth Usage - calculated over the interval between updates (no changes here)
        download_speed_mbps = 0.0
        upload_speed_mbps = 0.0

        current_net_io = psutil.net_io_counters()
        current_time = datetime.now()
        current_timestamp = current_time.timestamp()

        # Retrieve last stored network stats from config for this guild
        if guild_id:
            guild_settings = await self.config.guild(discord.Object(id=guild_id)).all()
            last_net_io_sent = guild_settings["last_net_io_sent"]
            last_net_io_recv = guild_settings["last_net_io_recv"]
            last_net_time = guild_settings["last_net_time"]

            if last_net_time != 0.0 and current_timestamp > last_net_time:
                time_diff_seconds = current_timestamp - last_net_time
                if time_diff_seconds > 0:
                    bytes_recv_diff = current_net_io.bytes_recv - last_net_io_recv
                    bytes_sent_diff = current_net_io.bytes_sent - last_net_io_sent

                    download_speed_mbps = (bytes_recv_diff * 8) / (1024 ** 2 * time_diff_seconds)
                    upload_speed_mbps = (bytes_sent_diff * 8) / (1024 ** 2 * time_diff_seconds)

            # Update config with current network stats for the next iteration
            try:
                await self.config.guild(discord.Object(id=guild_id)).last_net_io_sent.set(current_net_io.bytes_sent)
                await self.config.guild(discord.Object(id=guild_id)).last_net_io_recv.set(current_net_io.bytes_recv)
                await self.config.guild(discord.Object(id=guild_id)).last_net_time.set(current_timestamp)
                log.debug(f"Updated network stats in config for guild {guild_id}.")
            except Exception as e:
                log.error(f"Failed to save network stats to config for guild {guild_id}: {e}", exc_info=True)
        else:
            # Manual command bandwidth snapshot
            initial_net_io = psutil.net_io_counters()
            await asyncio.sleep(0.5)
            final_net_io = psutil.net_io_counters()

            download_bytes_diff = final_net_io.bytes_recv - initial_net_io.bytes_recv
            upload_bytes_diff = final_net_io.bytes_sent - initial_net_io.bytes_sent

            download_speed_mbps = (download_bytes_diff * 8) / (1024 ** 2 * 0.5)
            upload_speed_mbps = (upload_bytes_diff * 8) / (1024 ** 2 * 0.5)

        # Uptime
        boot_time_timestamp = psutil.boot_time()
        uptime_seconds = datetime.now().timestamp() - boot_time_timestamp
        uptime_string = humanize.naturaldelta(timedelta(seconds=int(uptime_seconds)))

        # Last Updated Time
        last_updated = datetime.now()
        last_updated_human_format = last_updated.strftime("%Y-%m-%d %H:%M:%S %Z")

        return (cpu_usage, used_memory_gb, total_memory_gb,
                total_disk_stats,
                download_speed_mbps, upload_speed_mbps,
                uptime_string, last_updated_human_format)

    def _get_bar_chart(self, percentage: float, length: int = 10):
        """
        Generates a text-based bar chart using Unicode block characters.
        """
        percentage = max(0, min(100, percentage))
        filled_blocks = int(length * (percentage / 100))
        empty_blocks = length - filled_blocks
        return "█" * filled_blocks + "░" * empty_blocks

    async def _build_embed(self, stats: Tuple, settings: Dict[str, Any]):
        """
        Builds the Discord embed from system statistics.
        """
        (cpu_usage, used_memory_gb, total_memory_gb,
         total_disk_stats,
         download_speed_mbps, upload_speed_mbps,
         uptime_string, last_updated_human_format) = stats

        embed = discord.Embed(title=":pushpin: System Usage", color=discord.Color.blue())

        # CPU
        cpu_bar = self._get_bar_chart(cpu_usage)
        embed.add_field(name=":desktop: CPU", value=f"{cpu_usage:.1f}%\n`{cpu_bar}`", inline=False)

        # Memory
        memory_percentage = (used_memory_gb / total_memory_gb) * 100 if total_memory_gb > 0 else 0
        memory_bar = self._get_bar_chart(memory_percentage)
        embed.add_field(name=":bar_chart: Memory",
                        value=f"{used_memory_gb:.2f} of {total_memory_gb:.2f} GB\n`{memory_bar}`", inline=False)

        # --- Storage Display Logic ---
        used_disk_gb = total_disk_stats['used_disk_gb']
        total_disk_gb = total_disk_stats['total_disk_gb']

        if total_disk_gb > 0:
            if settings.get("split_disk_stats", False):
                # 1. Show Local NVMe (Root)
                root_used = total_disk_stats['root_used_gb']
                root_total = total_disk_stats['root_total_gb']
                root_percent = (root_used / root_total) * 100 if root_total > 0 else 0
                root_bar = self._get_bar_chart(root_percent)

                embed.add_field(name=":zap: Local NVMe (Root)",
                                value=f"{root_used:.2f} of {root_total:.2f} GB\n`{root_bar}`", inline=False)

                # 2. Show Remote HDD (NFS)
                nfs_used = total_disk_stats['nfs_used_gb']
                nfs_total = total_disk_stats['nfs_total_gb']
                nfs_percent = (nfs_used / nfs_total) * 100 if nfs_total > 0 else 0
                nfs_bar = self._get_bar_chart(nfs_percent)

                embed.add_field(name=":floppy_disk: Remote HDD (NFS)",
                                value=f"{nfs_used:.2f} of {nfs_total:.2f} GB\n`{nfs_bar}`", inline=False)

                # 3. Show Combined Total (as a simple field, no bar needed)
                embed.add_field(name=":file_folder: Combined Total",
                                value=f"{used_disk_gb:.2f} of {total_disk_gb:.2f} GB", inline=False)

            else:
                # Legacy / Default: Show Combined Total only
                disk_percentage = (used_disk_gb / total_disk_gb) * 100 if total_disk_gb > 0 else 0
                disk_bar = self._get_bar_chart(disk_percentage)
                embed.add_field(name=":file_folder: Storage (Total)",
                                value=f"{used_disk_gb:.2f} of {total_disk_gb:.2f} GB\n`{disk_bar}`", inline=False)
        # --- End Storage Display Logic ---

        # Bandwidth (Download)
        total_bandwidth_mbps = 1024  # Assuming 1024 Mbps as total for display
        download_percentage = (download_speed_mbps / total_bandwidth_mbps) * 100 if total_bandwidth_mbps > 0 else 0
        download_bar = self._get_bar_chart(download_percentage)
        embed.add_field(name=":arrow_double_down: Bandwidth (DL)",
                        value=f"{download_speed_mbps:.2f} of 1024 Mbps\n`{download_bar}`", inline=False)

        # Bandwidth (Upload)
        upload_percentage = (upload_speed_mbps / total_bandwidth_mbps) * 100 if total_bandwidth_mbps > 0 else 0
        upload_bar = self._get_bar_chart(upload_percentage)
        embed.add_field(name=":arrow_double_up: Bandwidth (UL)",
                        value=f"{upload_speed_mbps:.2f} of 1024 Mbps\n`{upload_bar}`",
                        inline=False)

        embed.add_field(name=":stopwatch: Uptime", value=uptime_string, inline=False)
        embed.set_footer(text=f"Last Updated: {last_updated_human_format}")
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
                    if not guild:
                        log.warning(f"Guild {guild_id} not found. Disabling SystemMonitor for it.")
                        try:
                            await self.config.guild(discord.Object(id=guild_id)).enabled.set(False)
                        except Exception as e:
                            log.error(f"Failed to save config for guild {guild_id} after guild not found: {e}",
                                      exc_info=True)
                        continue

                    channel_id = settings["channel_id"]
                    message_id = settings["message_id"]

                    if not channel_id:
                        log.warning(f"No channel set for guild {guild.name} ({guild_id}). Skipping update.")
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        log.warning(
                            f"Channel {channel_id} not found for guild {guild.name} ({guild_id}). Disabling SystemMonitor for it.")
                        try:
                            await self.config.guild(guild).enabled.set(False)
                        except Exception as e:
                            log.error(f"Failed to save config for guild {guild_id} after channel not found: {e}",
                                      exc_info=True)
                        continue

                    if guild_id not in self._update_locks:
                        self._update_locks[guild_id] = asyncio.Lock()

                    async with self._update_locks[guild_id]:
                        try:
                            stats = await self._get_system_stats(settings, guild_id)
                            embed = await self._build_embed(stats, settings)

                            message = None
                            if message_id:
                                try:
                                    message = await channel.fetch_message(message_id)
                                except discord.NotFound:
                                    log.warning(
                                        f"Message {message_id} not found in channel {channel.name} ({channel_id}) for guild {guild.name}. Sending new message.")
                                    message = None
                                    try:
                                        await self.config.guild(guild).message_id.set(None)
                                    except Exception as e:
                                        log.error(f"Failed to clear message_id in config for guild {guild_id}: {e}",
                                                  exc_info=True)
                                except discord.Forbidden:
                                    log.error(
                                        f"Bot lacks permissions to fetch message in {channel.name} ({channel_id}) for guild {guild.name}. Disabling.")
                                    try:
                                        await self.config.guild(guild).enabled.set(False)
                                    except Exception as e:
                                        log.error(
                                            f"Failed to save config for guild {guild_id} after fetch permissions error: {e}",
                                            exc_info=True)
                                    continue
                                except Exception as e:
                                    log.error(
                                        f"Error fetching message in {channel.name} ({channel_id}) for guild {guild.name}: {e}. Disabling.")
                                    try:
                                        await self.config.guild(guild).enabled.set(False)
                                    except Exception as e:
                                        log.error(
                                            f"Failed to save config for guild {guild_id} after general fetch error: {e}",
                                            exc_info=True)
                                    continue

                            if message:
                                await message.edit(embed=embed)
                                log.debug(f"Updated message {message_id} in {channel.name} for guild {guild.name}.")
                            else:
                                new_message = await channel.send(embed=embed)
                                try:
                                    await self.config.guild(guild).message_id.set(new_message.id)
                                    log.info(
                                        f"Sent new SystemMonitor message {new_message.id} in {channel.name} for guild {guild.name}.")
                                except Exception as e:
                                    log.error(f"Failed to save new message_id to config for guild {guild_id}: {e}",
                                              exc_info=True)

                        except discord.Forbidden:
                            log.error(
                                f"Bot lacks permissions to send/edit messages in {channel.name} ({channel_id}) for guild {guild.name}. Disabling SystemMonitor for it.")
                            try:
                                await self.config.guild(guild).enabled.set(False)
                            except Exception as e:
                                log.error(
                                    f"Failed to save config for guild {guild_id} after send/edit permissions error: {e}",
                                    exc_info=True)
                        except Exception as e:
                            log.error(f"Error during SystemMonitor update for guild {guild.name} ({guild_id}): {e}",
                                      exc_info=True)

            except asyncio.CancelledError:
                log.info("SystemMonitor update loop cancelled.")
                break
            except Exception as e:
                log.critical(f"Unhandled error in SystemMonitor update loop: {e}", exc_info=True)

            await asyncio.sleep(60)

    @commands.group(name="systemmonitor", aliases=["sysmon"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def systemmonitor(self, ctx: commands.Context):
        """
        Manage the System Monitor auto-updating message.
        """
        pass

    @systemmonitor.command(name="setchannel")
    @app_commands.describe(
        channel="The channel where the system usage message should be posted."
    )
    async def sysmon_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the channel for the auto-updating system usage message.

        The bot will post and update the system usage message in this channel.
        """
        try:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await self.config.guild(ctx.guild).message_id.set(None)
            await ctx.send(f"System usage message will now be posted and updated in {channel.mention}.")
            log.info(f"SystemMonitor channel set to {channel.id} for guild {ctx.guild.name}.")
        except Exception as e:
            log.error(f"Failed to set channel in config for guild {ctx.guild.id}: {e}", exc_info=True)
            await ctx.send(f"An error occurred while setting the channel: {e}")

    @systemmonitor.command(name="toggle")
    async def sysmon_toggle(self, ctx: commands.Context):
        """
        Toggles the auto-updating system usage message for this guild.

        If enabled, the bot will periodically update the message in the set channel.
        """
        current_status = await self.config.guild(ctx.guild).enabled()
        new_status = not current_status
        try:
            await self.config.guild(ctx.guild).enabled.set(new_status)

            if new_status:
                current_net_io = psutil.net_io_counters()
                await self.config.guild(ctx.guild).last_net_io_sent.set(current_net_io.bytes_sent)
                await self.config.guild(ctx.guild).last_net_io_recv.set(current_net_io.bytes_recv)
                await self.config.guild(ctx.guild).last_net_time.set(datetime.now().timestamp())

                await ctx.send("System usage auto-updates have been **enabled**.")
                log.info(f"SystemMonitor enabled for guild {ctx.guild.name}.")
            else:
                await ctx.send("System usage auto-updates have been **disabled**.")
                log.info(f"SystemMonitor disabled for guild {ctx.guild.name}.")
        except Exception as e:
            log.error(f"Failed to toggle enabled status in config for guild {ctx.guild.id}: {e}", exc_info=True)
            await ctx.send(f"An error occurred while toggling auto-updates: {e}")

    @systemmonitor.command(name="togglesplitdisk")
    async def sysmon_toggle_split_disk(self, ctx: commands.Context):
        """
        Toggles whether to display disk usage as a single aggregated total,
        or split into Local NVMe (Root) and Remote HDD (NFS) fields.
        """
        current_setting = await self.config.guild(ctx.guild).split_disk_stats()
        new_setting = not current_setting
        try:
            await self.config.guild(ctx.guild).split_disk_stats.set(new_setting)
            status_text = "split view (NVMe, NFS, Total)" if new_setting else "single aggregated total"
            await ctx.send(f"System Monitor storage display set to **{status_text}**.")
            log.info(f"SystemMonitor split disk display toggled to {new_setting} for guild {ctx.guild.name}.")
        except Exception as e:
            log.error(f"Failed to toggle split disk setting in config for guild {ctx.guild.id}: {e}", exc_info=True)
            await ctx.send(f"An error occurred while toggling split disk display: {e}")

    @systemmonitor.command(name="togglefulldisk", hidden=True)
    async def sysmon_toggle_full_disk(self, ctx: commands.Context):
        """
        Toggles whether to show full disk stats or just the root partition. (Deprecated, use togglesplitdisk)
        """
        current_setting = await self.config.guild(ctx.guild).show_full_disk()
        new_setting = not current_setting
        try:
            await self.config.guild(ctx.guild).show_full_disk.set(new_setting)
            status_text = "full disk usage (all partitions)" if new_setting else "root partition usage only"
            await ctx.send(f"System Monitor will now show **{status_text}**.")
            log.info(f"SystemMonitor full disk display toggled to {new_setting} for guild {ctx.guild.name}.")
        except Exception as e:
            log.error(f"Failed to toggle full disk setting in config for guild {ctx.guild.id}: {e}", exc_info=True)
            await ctx.send(f"An error occurred while toggling full disk display: {e}")

    @systemmonitor.command(name="showsettings")
    async def sysmon_show_settings(self, ctx: commands.Context):
        """
        Shows the current System Monitor settings for this guild.
        """
        settings = await self.config.guild(ctx.guild).all()
        channel_id = settings["channel_id"]
        message_id = settings["message_id"]
        enabled = settings["enabled"]
        show_full_disk = settings["show_full_disk"]
        split_disk_stats = settings["split_disk_stats"]

        channel_mention = f"<#{channel_id}>" if channel_id else "Not set"
        message_link = f"https://discord.com/channels/{ctx.guild.id}/{channel_id}/{message_id}" if channel_id and message_id else "Not set"

        embed = discord.Embed(title="System Monitor Settings", color=discord.Color.purple())
        embed.add_field(name="Auto-Updates Enabled", value=str(enabled), inline=False)
        embed.add_field(name="Update Channel", value=channel_mention, inline=False)
        embed.add_field(name="Message ID", value=str(message_id) if message_id else "Not set", inline=False)
        embed.add_field(name="Message Link", value=message_link, inline=False)
        embed.add_field(name="Show Full Disk (Legacy)", value=str(show_full_disk), inline=False)
        embed.add_field(name="Split Disk Stats", value=str(split_disk_stats), inline=False)

        await ctx.send(embed=embed)

    @systemmonitor.command(name="manualupdate")
    async def sysmon_manual_update(self, ctx: commands.Context):
        """
        Manually triggers an update of the system usage message.
        """
        guild_id = ctx.guild.id
        settings = await self.config.guild(ctx.guild).all()
        channel_id = settings["channel_id"]
        message_id = settings["message_id"]

        if not channel_id:
            return await ctx.send("Please set a channel first using `[p]systemmonitor setchannel <channel>`.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send(
                f"The configured channel (<#{channel_id}>) was not found. Please set a valid channel.")

        if guild_id not in self._update_locks:
            self._update_locks[guild_id] = asyncio.Lock()

        async with self._update_locks[guild_id]:
            try:
                stats = await self._get_system_stats(settings, None)
                embed = await self._build_embed(stats, settings)

                message = None
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                    except discord.NotFound:
                        await ctx.send("The existing message was not found. Sending a new one.")
                        message = None
                        try:
                            await self.config.guild(ctx.guild).message_id.set(None)
                        except Exception as e:
                            log.error(
                                f"Failed to clear message_id in config for guild {guild_id} during manual update: {e}",
                                exc_info=True)
                    except discord.Forbidden:
                        return await ctx.send("I don't have permissions to fetch the message in that channel.")
                    except Exception as e:
                        log.error(f"Error fetching message during manual update: {e}")
                        return await ctx.send(f"An error occurred while fetching the message: {e}")

                if message:
                    await message.edit(embed=embed)
                    await ctx.send("System usage message updated manually.")
                    log.debug(f"Manually updated message {message_id} in {channel.name} for guild {ctx.guild.name}.")
                else:
                    new_message = await channel.send(embed=embed)
                    try:
                        await self.config.guild(ctx.guild).message_id.set(new_message.id)
                        await ctx.send("New system usage message sent and configured for updates.")
                        log.info(
                            f"Manual update sent new message {new_message.id} in {channel.name} for guild {ctx.guild.name}.")
                    except Exception as e:
                        log.error(
                            f"Failed to save new message_id to config for guild {guild_id} during manual update: {e}",
                            exc_info=True)

            except discord.Forbidden:
                await ctx.send("I don't have permissions to send messages in that channel.")
            except Exception as e:
                log.error(f"Error during manual SystemMonitor update for guild {ctx.guild.name}: {e}", exc_info=True)
                await ctx.send(f"An unexpected error occurred during manual update: {e}")