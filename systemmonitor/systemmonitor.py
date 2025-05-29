import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord.ext import tasks
import psutil
from datetime import datetime, timedelta
import logging

# Initialize logger
log = logging.getLogger("red.SystemMonitor")


# Admin/Owner check decorator
def is_owner_or_admin():
    async def predicate(ctx: commands.Context):
        if await ctx.bot.is_owner(ctx.author):
            return True
        if ctx.guild is not None and ctx.author.guild_permissions.administrator:
            return True
        raise commands.CheckFailure("Only the bot owner or administrators can use this command.")

    return commands.check(predicate)


class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage with a dynamic, persistent report message."""

    # Define constants for conversions
    BYTES_TO_GB = 1024 ** 3
    BYTES_TO_MBPS_FACTOR = 8 / (1024 * 1024)  # For converting bytes/sec to Mbps

    def __init__(self, bot: Red):
        self.bot = bot
        self.message: discord.Message | None = None  # The persistent message for system stats.

        # For accurate network speed measurements.
        self.previous_net_io = psutil.net_io_counters()
        self.previous_time = datetime.now()

        # Setup Redbot configuration
        self.config = Config.get_conf(self, identifier=1234567891,
                                      force_registration=True)  # Changed identifier slightly
        default_global = {
            "monitor_channel_id": None,
            "monitor_message_id": None,
        }
        self.config.register_global(**default_global)

        self.monitor_channel_id: int | None = None
        self.monitor_message_id: int | None = None

        # Start the background monitoring task
        self.monitor_loop.start()

    def cog_unload(self):
        """Cog cleanup, cancel the monitoring task."""
        self.monitor_loop.cancel()
        log.info("SystemMonitor cog unloaded, monitor loop cancelled.")

    async def initialize_config_and_message(self):
        """Loads configuration and attempts to fetch the existing monitor message."""
        self.monitor_channel_id = await self.config.monitor_channel_id()
        self.monitor_message_id = await self.config.monitor_message_id()

        if self.monitor_channel_id and self.monitor_message_id:
            channel = self.bot.get_channel(self.monitor_channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                try:
                    self.message = await channel.fetch_message(self.monitor_message_id)
                    log.info(
                        f"Successfully fetched existing monitor message ID: {self.message.id} in channel ID: {channel.id}")
                except discord.NotFound:
                    log.info(
                        f"Previous monitor message (ID: {self.monitor_message_id}) not found in channel (ID: {self.monitor_channel_id}). Will create a new one.")
                    await self.config.monitor_message_id.set(None)
                    self.monitor_message_id = None
                    self.message = None
                except discord.Forbidden:
                    log.error(
                        f"Missing permissions to fetch message (ID: {self.monitor_message_id}) in channel (ID: {self.monitor_channel_id}).")
                    # Optionally clear IDs, or keep them hoping perms are restored.
                    # For now, clearing to allow new message creation if possible.
                    await self.config.monitor_message_id.set(None)
                    self.monitor_message_id = None
                    self.message = None
                except Exception as e:
                    log.error(
                        f"Error fetching initial message (ID: {self.monitor_message_id}) in channel (ID: {self.monitor_channel_id}): {e}",
                        exc_info=True)
                    self.message = None
            else:
                log.warning(
                    f"Monitor channel (ID: {self.monitor_channel_id}) not found or not a text channel during init. Clearing message ID.")
                await self.config.monitor_channel_id.set(None)  # Clear invalid channel
                await self.config.monitor_message_id.set(None)
                self.monitor_channel_id = None
                self.monitor_message_id = None
                self.message = None
        elif self.monitor_channel_id and not self.monitor_message_id:
            log.info(
                f"Monitor channel (ID: {self.monitor_channel_id}) is set, but no message ID. A new message will be posted.")

    @tasks.loop(seconds=60)
    async def monitor_loop(self):
        """Periodically calls the monitor function."""
        try:
            await self.monitor()
        except Exception as e:
            log.error(f"Unhandled error in monitor_loop: {e}", exc_info=True)

    @monitor_loop.before_loop
    async def before_monitor_loop(self):
        """Ensures the bot is ready and config is loaded before the loop starts."""
        await self.bot.wait_until_ready()
        log.info("Bot is ready. Initializing SystemMonitor configuration and message...")
        await self.initialize_config_and_message()
        log.info("SystemMonitor initialization complete. Starting monitor loop.")

    async def monitor(self):
        """Gathers system stats and updates the Discord message."""
        if not self.monitor_channel_id:
            # log.debug("Monitor channel not set. Skipping system monitoring.") # Use debug for frequent messages
            return

        channel = self.bot.get_channel(self.monitor_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            log.warning(
                f"Configured monitor channel (ID: {self.monitor_channel_id}) not found or is not a text channel. Clearing configuration.")
            await self.config.monitor_channel_id.set(None)
            await self.config.monitor_message_id.set(None)
            self.monitor_channel_id = None
            self.monitor_message_id = None
            self.message = None
            return

        # --- Gather System Stats ---
        now = datetime.now()
        sample_period = max((now - self.previous_time).total_seconds(),
                            0.1)  # Avoid division by zero if time hasn't passed

        cpu_usage = psutil.cpu_percent(interval=0.5)  # Reduced interval for less blocking

        memory = psutil.virtual_memory()
        used_memory_gb = memory.used / self.BYTES_TO_GB
        total_memory_gb = memory.total / self.BYTES_TO_GB

        total_used_disk = 0
        total_total_disk = 0
        try:
            partitions = psutil.disk_partitions(all=False)
            for partition in partitions:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    total_used_disk += usage.used
                    total_total_disk += usage.total
                except Exception as e:
                    log.debug(f"Skipping disk usage for {partition.mountpoint}: {e}")  # Debug for non-critical errors
        except Exception as e:
            log.warning(f"Could not retrieve disk partitions: {e}")

        used_disk_gb = total_used_disk / self.BYTES_TO_GB
        total_disk_gb = total_total_disk / self.BYTES_TO_GB if total_total_disk > 0 else 0

        current_net_io = psutil.net_io_counters()
        delta_bytes_sent = current_net_io.bytes_sent - self.previous_net_io.bytes_sent
        delta_bytes_recv = current_net_io.bytes_recv - self.previous_net_io.bytes_recv

        upload_speed_mbps = (delta_bytes_sent * self.BYTES_TO_MBPS_FACTOR) / sample_period
        download_speed_mbps = (delta_bytes_recv * self.BYTES_TO_MBPS_FACTOR) / sample_period

        self.previous_net_io = current_net_io
        self.previous_time = now

        boot_time_timestamp = psutil.boot_time()
        boot_time = datetime.fromtimestamp(boot_time_timestamp)
        uptime_delta = now - boot_time
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, _ = divmod(remainder, 60)  # Seconds not needed for brief uptime string
        uptime_string = f"{days}d {hours}h {minutes}m"

        # Use Discord's dynamic timestamp for "Last Updated"
        # :F shows "Wednesday, June 9, 2021 9:02 AM" (example)
        # :f shows "June 9, 2021 9:02 AM" (example)
        # :R shows relative time "2 hours ago" (example) - not ideal for fixed update time
        unix_timestamp = int(now.timestamp())
        last_updated_discord_format = f"<t:{unix_timestamp}:F>"

        embed = discord.Embed(title=":pushpin: System Usage", color=discord.Color.blue())
        embed.add_field(name=":desktop: CPU", value=f"{cpu_usage:.1f}%", inline=False)
        embed.add_field(name=":bar_chart: Memory", value=f"{used_memory_gb:.2f} of {total_memory_gb:.2f} GB",
                        inline=False)
        if total_total_disk > 0:  # Only show disk if we could read it
            embed.add_field(name=":file_folder: Storage", value=f"{used_disk_gb:.2f} of {total_disk_gb:.2f} GB",
                            inline=False)
        embed.add_field(name=":arrow_double_down: Bandwidth (DL)", value=f"{download_speed_mbps:.2f} of 1024 Mbps",
                        inline=False)
        embed.add_field(name=":arrow_double_up: Bandwidth (UL)", value=f"{upload_speed_mbps:.2f} of 1024 Mbps",
                        inline=False)
        embed.add_field(name=":stopwatch: Uptime", value=uptime_string, inline=False)
        embed.set_footer(text=f"Last Updated: {last_updated_discord_format}")

        # --- Update Discord Message ---
        try:
            if self.message:
                # Ensure message is still in the configured channel (could be manually moved or config changed)
                if self.message.channel.id != self.monitor_channel_id:
                    log.warning(
                        f"Monitor message (ID: {self.message.id}) is in channel {self.message.channel.id}, but configured channel is {self.monitor_channel_id}. Resetting message.")
                    self.message = None
                    await self.config.monitor_message_id.set(None)
                    self.monitor_message_id = None
                else:
                    await self.message.edit(embed=embed)
                    # log.debug(f"Updated system message ID: {self.message.id}") # Too verbose for every 60s
                    return  # Successfully edited

            # If self.message is None (either initially, or after a failed edit/mismatch)
            if not self.message:
                log.info(f"Sending new system monitor message to channel ID: {channel.id}")
                new_message = await channel.send(embed=embed)
                self.message = new_message
                self.monitor_message_id = new_message.id
                await self.config.monitor_message_id.set(new_message.id)
                log.info(f"New system message posted with ID: {self.message.id}")

        except discord.NotFound:
            log.warning(
                f"Failed to edit message (ID: {self.monitor_message_id if self.monitor_message_id else 'Unknown'}) - not found. Will try to send a new one.")
            self.message = None
            self.monitor_message_id = None
            await self.config.monitor_message_id.set(None)
            # Attempt to send a new one in the next cycle, or immediately:
            try:
                log.info(
                    f"Sending new system monitor message to channel ID: {channel.id} after previous was not found.")
                new_message = await channel.send(embed=embed)
                self.message = new_message
                self.monitor_message_id = new_message.id
                await self.config.monitor_message_id.set(new_message.id)
                log.info(f"New system message posted with ID: {self.message.id} after previous not found.")
            except Exception as e_send:
                log.error(f"Failed to send new message after previous not found: {e_send}", exc_info=True)

        except discord.Forbidden:
            log.error(f"Failed to send/edit message in channel (ID: {channel.id}) due to missing permissions.")
            # Potentially set self.message = None to prevent repeated failed attempts if it was an edit
            if self.message:  # If it was an edit attempt
                self.message = None  # Stop trying to edit this specific message
                # Keep monitor_message_id in config, maybe perms will be restored for fetching
        except Exception as e:
            log.error(f"Error updating the system message: {e}", exc_info=True)

    @commands.command()
    @is_owner_or_admin()
    async def systemmonitorset(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Set the channel where system monitor reports will be posted.
        A new message will be posted in the specified channel.

        Example:
          [p]systemmonitorset #system-monitor
        """
        await self.config.monitor_channel_id.set(channel.id)
        await self.config.monitor_message_id.set(None)  # Clear old message ID as channel changes

        self.monitor_channel_id = channel.id
        self.monitor_message_id = None  # Reset instance variable
        self.message = None  # Reset the existing message object

        await ctx.send(f"System monitor channel updated to {channel.mention}. A new message will be posted there.")
        log.info(f"System monitor channel set to {channel.id} by {ctx.author} ({ctx.author.id}). Message ID cleared.")
        # Trigger an immediate update to post the new message
        await self.monitor()

    @commands.command(aliases=["sysmon"])
    @is_owner_or_admin()
    async def system(self, ctx: commands.Context):
        """
        Manually trigger a system report and post it to the current channel.
        This also updates the persistent monitor message if configured.
        """
        # Call monitor to update the persistent message and gather current stats
        await self.monitor()

        if self.message and self.message.embeds:
            # Send a copy of the latest embed to the command channel
            await ctx.send(embed=self.message.embeds[0])
        elif self.monitor_channel_id:
            await ctx.send(
                "System monitor is configured, but the message could not be updated or retrieved. Check logs.")
        else:
            await ctx.send("System monitor channel is not configured. Use `[p]systemmonitorset`.")


async def setup(bot: Red):
    cog = SystemMonitor(bot)
    await bot.add_cog(cog)
    log.info("SystemMonitor cog loaded.")
