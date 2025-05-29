import discord
import psutil
import asyncio
from datetime import datetime, timedelta
from redbot.core import commands, app_commands, Config
# from redbot.core.utils.chat_formatting import humanize_timedelta # No longer needed/used for uptime
from redbot.core.bot import Red
import humanize  # Import the humanize library
import logging

log = logging.getLogger("red.systemmonitor")


class SystemMonitor(commands.Cog):
    """
    Monitors and displays the current usage of the Ubuntu server, with auto-updating messages.
    """

    # Default configuration for each guild
    # This will store channel_id, message_id, enabled status, and disk display preference
    # It also stores the last network I/O counters for bandwidth calculation over time.
    DEFAULT_GUILD_SETTINGS = {
        "channel_id": None,
        "message_id": None,
        "enabled": False,
        "show_full_disk": False,
        "last_net_io_sent": 0,
        "last_net_io_recv": 0,
        "last_net_time": 0.0  # Unix timestamp
    }

    def __init__(self, bot: Red):
        self.bot = bot
        # Initialize the Config object for guild-specific settings
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(**self.DEFAULT_GUILD_SETTINGS)

        # Initialize the background task to None
        self._update_task = None
        # Store a lock to prevent multiple simultaneous updates for the same guild
        self._update_locks = {}

    async def cog_load(self):
        """
        Starts the background task when the cog is loaded.
        """
        log.info("SystemMonitor cog loaded. Starting update loop.")
        self._update_task = self.bot.loop.create_task(self._update_loop())

    async def cog_unload(self):
        """
        Cancels the background task when the cog is unloaded.
        """
        log.info("SystemMonitor cog unloaded. Cancelling update loop.")
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass  # Task was cancelled as expected

    async def _get_system_stats(self, show_full_disk: bool = False, guild_id: int = None):
        """
        Gathers system usage statistics.
        Returns a tuple: (cpu_usage, used_memory_gb, total_memory_gb,
                          used_disk_gb, total_disk_gb, download_speed_mbps,
                          upload_speed_mbps, uptime_string, last_updated_human_format)
        """
        # CPU Usage
        cpu_usage = psutil.cpu_percent(interval=0.5)  # Measure over 0.5 seconds

        # Memory Usage
        memory = psutil.virtual_memory()
        total_memory_gb = memory.total / (1024 ** 3)
        used_memory_gb = memory.used / (1024 ** 3)

        # Disk Usage
        total_total_disk = 0  # Used to check if any disk could be read
        used_disk_gb = 0
        total_disk_gb = 0

        if show_full_disk:
            for part in psutil.disk_partitions(all=False):
                if 'cdrom' in part.opts or part.fstype == '':
                    continue  # Skip optical drives and empty fstypes
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    total_total_disk += usage.total
                    used_disk_gb += usage.used / (1024 ** 3)
                    total_disk_gb += usage.total / (1024 ** 3)
                except Exception as e:
                    log.debug(f"Could not read disk usage for {part.mountpoint}: {e}")
        else:
            try:
                disk = psutil.disk_usage('/')
                total_total_disk = disk.total
                used_disk_gb = disk.used / (1024 ** 3)
                total_disk_gb = disk.total / (1024 ** 3)
            except Exception as e:
                log.debug(f"Could not read disk usage for root partition: {e}")
                total_total_disk = 0  # Indicate no disk info if root fails

        # Bandwidth Usage - calculated over the interval between updates
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
            await self.config.guild(discord.Object(id=guild_id)).set({
                "last_net_io_sent": current_net_io.bytes_sent,
                "last_net_io_recv": current_net_io.bytes_recv,
                "last_net_time": current_timestamp
            })
        else:
            # If no guild_id (e.g., manual command), calculate over a short snapshot
            # This is less accurate for "speed" but provides a value.
            # For a manual command, we don't store global state for bandwidth.
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
        # Format the datetime object directly into a human-readable string
        last_updated_human_format = last_updated.strftime("%Y-%m-%d %H:%M:%S %Z")  # Example: 2023-10-27 15:30:00 UTC

        return (cpu_usage, used_memory_gb, total_memory_gb,
                used_disk_gb, total_disk_gb, total_total_disk,  # Pass total_total_disk for the check
                download_speed_mbps, upload_speed_mbps,
                uptime_string, last_updated_human_format)

    def _get_bar_chart(self, percentage: float, length: int = 10):
        """
        Generates a text-based bar chart using Unicode block characters.
        """
        filled_blocks = int(length * (percentage / 100))
        empty_blocks = length - filled_blocks
        return "█" * filled_blocks + "░" * empty_blocks

    async def _build_embed(self, stats: tuple, show_full_disk: bool):
        """
        Builds the Discord embed from system statistics.
        """
        (cpu_usage, used_memory_gb, total_memory_gb,
         used_disk_gb, total_disk_gb, total_total_disk,
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

        # Storage
        if total_total_disk > 0:
            disk_name = "Storage (Total)" if show_full_disk else "Storage (Root)"
            disk_percentage = (used_disk_gb / total_disk_gb) * 100 if total_disk_gb > 0 else 0
            disk_bar = self._get_bar_chart(disk_percentage)
            embed.add_field(name=f":file_folder: {disk_name}",
                            value=f"{used_disk_gb:.2f} of {total_disk_gb:.2f} GB\n`{disk_bar}`", inline=False)

        embed.add_field(name=":arrow_double_down: Bandwidth (DL)", value=f"{download_speed_mbps:.2f} Mbps",
                        inline=False)
        embed.add_field(name=":arrow_double_up: Bandwidth (UL)", value=f"{upload_speed_mbps:.2f} Mbps", inline=False)
        embed.add_field(name=":stopwatch: Uptime", value=uptime_string, inline=False)
        embed.set_footer(text=f"Last Updated: {last_updated_human_format}")  # Use the human-formatted string
        return embed

    async def _update_loop(self):
        """
        The main background task that periodically updates system usage messages.
        """
        await self.bot.wait_until_ready()
        while True:
            try:
                # Get all guild settings
                all_guild_settings = await self.config.all_guilds()

                for guild_id, settings in all_guild_settings.items():
                    if not settings["enabled"]:
                        continue  # Skip if auto-update is not enabled for this guild

                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        log.warning(f"Guild {guild_id} not found. Disabling SystemMonitor for it.")
                        await self.config.guild(discord.Object(id=guild_id)).enabled.set(False)
                        continue

                    channel_id = settings["channel_id"]
                    message_id = settings["message_id"]
                    show_full_disk = settings["show_full_disk"]

                    if not channel_id:
                        log.warning(f"No channel set for guild {guild.name} ({guild_id}). Skipping update.")
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        log.warning(
                            f"Channel {channel_id} not found for guild {guild.name} ({guild_id}). Disabling SystemMonitor for it.")
                        await self.config.guild(guild).enabled.set(False)
                        continue

                    # Acquire a lock for this guild to prevent race conditions if manual update runs
                    if guild_id not in self._update_locks:
                        self._update_locks[guild_id] = asyncio.Lock()

                    async with self._update_locks[guild_id]:
                        try:
                            stats = await self._get_system_stats(show_full_disk, guild_id)
                            embed = await self._build_embed(stats, show_full_disk)

                            message = None
                            if message_id:
                                try:
                                    message = await channel.fetch_message(message_id)
                                except discord.NotFound:
                                    log.warning(
                                        f"Message {message_id} not found in channel {channel.name} ({channel_id}) for guild {guild.name}. Sending new message.")
                                    message = None  # Message was deleted, need to send a new one
                                except discord.Forbidden:
                                    log.error(
                                        f"Bot lacks permissions to fetch message in {channel.name} ({channel_id}) for guild {guild.name}. Disabling.")
                                    await self.config.guild(guild).enabled.set(False)
                                    continue
                                except Exception as e:
                                    log.error(
                                        f"Error fetching message in {channel.name} ({channel_id}) for guild {guild.name}: {e}. Disabling.")
                                    await self.config.guild(guild).enabled.set(False)
                                    continue

                            if message:
                                await message.edit(embed=embed)
                            else:
                                # Send a new message and store its ID
                                new_message = await channel.send(embed=embed)
                                await self.config.guild(guild).message_id.set(new_message.id)
                                log.info(
                                    f"Sent new SystemMonitor message {new_message.id} in {channel.name} for guild {guild.name}.")

                        except discord.Forbidden:
                            log.error(
                                f"Bot lacks permissions to send/edit messages in {channel.name} ({channel_id}) for guild {guild.name}. Disabling SystemMonitor for it.")
                            await self.config.guild(guild).enabled.set(False)
                        except Exception as e:
                            log.error(f"Error during SystemMonitor update for guild {guild.name} ({guild_id}): {e}",
                                      exc_info=True)
                            # Do not disable on general errors, just log and continue

            except asyncio.CancelledError:
                log.info("SystemMonitor update loop cancelled.")
                break  # Exit the loop if cancelled
            except Exception as e:
                log.critical(f"Unhandled error in SystemMonitor update loop: {e}", exc_info=True)

            await asyncio.sleep(60)  # Wait for 60 seconds before the next update

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
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        # Reset message_id when channel changes to ensure a new message is sent
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(f"System usage message will now be posted and updated in {channel.mention}.")
        log.info(f"SystemMonitor channel set to {channel.id} for guild {ctx.guild.name}.")

    @systemmonitor.command(name="toggle")
    async def sysmon_toggle(self, ctx: commands.Context):
        """
        Toggles the auto-updating system usage message for this guild.

        If enabled, the bot will periodically update the message in the set channel.
        """
        current_status = await self.config.guild(ctx.guild).enabled()
        new_status = not current_status
        await self.config.guild(ctx.guild).enabled.set(new_status)

        if new_status:
            # When enabling, reset network stats to get accurate bandwidth from now on
            current_net_io = psutil.net_io_counters()
            await self.config.guild(ctx.guild).set({
                "last_net_io_sent": current_net_io.bytes_sent,
                "last_net_io_recv": current_net_io.bytes_recv,
                "last_net_time": datetime.now().timestamp()
            })
            await ctx.send("System usage auto-updates have been **enabled**.")
            log.info(f"SystemMonitor enabled for guild {ctx.guild.name}.")
        else:
            await ctx.send("System usage auto-updates have been **disabled**.")
            log.info(f"SystemMonitor disabled for guild {ctx.guild.name}.")

    @systemmonitor.command(name="togglefulldisk")
    async def sysmon_toggle_full_disk(self, ctx: commands.Context):
        """
        Toggles whether to show full disk stats or just the root partition.
        """
        current_setting = await self.config.guild(ctx.guild).show_full_disk()
        new_setting = not current_setting
        await self.config.guild(ctx.guild).show_full_disk.set(new_setting)
        status_text = "full disk usage (all partitions)" if new_setting else "root partition usage only"
        await ctx.send(f"System Monitor will now show **{status_text}**.")
        log.info(f"SystemMonitor full disk display toggled to {new_setting} for guild {ctx.guild.name}.")

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

        channel_mention = f"<#{channel_id}>" if channel_id else "Not set"
        message_link = f"https://discord.com/channels/{ctx.guild.id}/{channel_id}/{message_id}" if channel_id and message_id else "Not set"

        embed = discord.Embed(title="System Monitor Settings", color=discord.Color.purple())
        embed.add_field(name="Auto-Updates Enabled", value=str(enabled), inline=False)
        embed.add_field(name="Update Channel", value=channel_mention, inline=False)
        embed.add_field(name="Message ID", value=str(message_id) if message_id else "Not set", inline=False)
        embed.add_field(name="Message Link", value=message_link, inline=False)
        embed.add_field(name="Show Full Disk", value=str(show_full_disk), inline=False)

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
        show_full_disk = settings["show_full_disk"]
        enabled = settings["enabled"]  # Check if enabled, though manual update can run regardless

        if not channel_id:
            return await ctx.send("Please set a channel first using `[p]systemmonitor setchannel <channel>`.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send(
                f"The configured channel (<#{channel_id}>) was not found. Please set a valid channel.")

        # Acquire a lock for this guild to prevent race conditions with the background task
        if guild_id not in self._update_locks:
            self._update_locks[guild_id] = asyncio.Lock()

        async with self._update_locks[guild_id]:
            try:
                # For manual update, we use a snapshot for bandwidth as it's not part of the continuous loop
                stats = await self._get_system_stats(show_full_disk,
                                                     None)  # Pass None for guild_id to use snapshot bandwidth
                embed = await self._build_embed(stats, show_full_disk)

                message = None
                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                    except discord.NotFound:
                        await ctx.send("The existing message was not found. Sending a new one.")
                        message = None
                    except discord.Forbidden:
                        return await ctx.send("I don't have permissions to fetch the message in that channel.")
                    except Exception as e:
                        log.error(f"Error fetching message during manual update: {e}")
                        return await ctx.send(f"An error occurred while fetching the message: {e}")

                if message:
                    await message.edit(embed=embed)
                    await ctx.send("System usage message updated manually.")
                else:
                    new_message = await channel.send(embed=embed)
                    await self.config.guild(ctx.guild).message_id.set(new_message.id)
                    await ctx.send("New system usage message sent and configured for updates.")
                    log.info(
                        f"Manual update sent new message {new_message.id} in {channel.name} for guild {ctx.guild.name}.")

            except discord.Forbidden:
                await ctx.send("I don't have permissions to send messages in that channel.")
            except Exception as e:
                log.error(f"Error during manual SystemMonitor update for guild {ctx.guild.name}: {e}", exc_info=True)
                await ctx.send(f"An unexpected error occurred during manual update: {e}")

