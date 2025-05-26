import discord
from discord.ext import commands, tasks
import psutil
from datetime import datetime
from redbot.core import Config


class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage with a dynamic report channel."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None  # The message that holds the current embed.

        # For accurate network speed measurements.
        self.previous_net_io = psutil.net_io_counters()
        self.previous_time = datetime.now()

        # Setup Redbot configuration to store the monitor channel.
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_global = {"monitor_channel": None}
        self.config.register_global(**default_global)
        self.monitor_channel_id = None

        # Retrieve stored configuration asynchronously.
        self.bot.loop.create_task(self.initialize_config())

        # Start the periodic monitoring loop.
        self.monitor_loop.start()

    async def initialize_config(self):
        """Initialize configuration values from storage."""
        self.monitor_channel_id = await self.config.monitor_channel()

    @tasks.loop(seconds=60)
    async def monitor_loop(self):
        """Update system stats every 60 seconds."""
        try:
            await self.monitor()
        except Exception as e:
            print(f"Error in monitor_loop: {e}")

    async def monitor(self):
        now = datetime.now()
        sample_period = (now - self.previous_time).total_seconds()

        # CPU usage (note: interval=1 blocks for one second).
        cpu_usage = psutil.cpu_percent(interval=1)

        # Dynamic memory usage.
        memory = psutil.virtual_memory()
        used_memory_gb = memory.used / (1024 ** 3)
        total_memory_gb = memory.total / (1024 ** 3)

        # Aggregate disk usage from all mounted partitions.
        total_used_disk = 0
        total_total_disk = 0
        partitions = psutil.disk_partitions(all=False)
        for partition in partitions:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                total_used_disk += usage.used
                total_total_disk += usage.total
            except Exception as e:
                print(f"Error getting disk usage for {partition.mountpoint}: {e}")

        used_disk_gb = total_used_disk / (1024 ** 3)
        total_disk_gb = total_total_disk / (1024 ** 3)

        # Calculate network speeds in Mbps.
        current_net_io = psutil.net_io_counters()
        delta_bytes_sent = current_net_io.bytes_sent - self.previous_net_io.bytes_sent
        delta_bytes_recv = current_net_io.bytes_recv - self.previous_net_io.bytes_recv

        upload_speed_mbps = (delta_bytes_sent * 8) / (1024 * 1024 * sample_period)
        download_speed_mbps = (delta_bytes_recv * 8) / (1024 * 1024 * sample_period)

        self.previous_net_io = current_net_io
        self.previous_time = now

        last_updated = now.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        embed = discord.Embed(title="System Usage", color=discord.Color.blue())
        embed.add_field(name="CPU", value=f"{cpu_usage:.1f}%", inline=True)
        embed.add_field(
            name="Memory",
            value=f"{used_memory_gb:.2f} of {total_memory_gb:.2f} GB",
            inline=True,
        )
        embed.add_field(
            name="Disk",
            value=f"{used_disk_gb:.2f} of {total_disk_gb:.2f} GB",
            inline=True,
        )
        embed.add_field(name="Upload Speed", value=f"{upload_speed_mbps:.2f} Mbps", inline=True)
        embed.add_field(name="Download Speed", value=f"{download_speed_mbps:.2f} Mbps", inline=True)
        embed.set_footer(text=f"Last Updated: {last_updated}")

        # Check that a monitor channel is configured.
        if self.monitor_channel_id is None:
            print("No monitor channel configured. Please use !systemmonitorset to set the channel.")
            return

        channel = self.bot.get_channel(self.monitor_channel_id)
        if channel is None:
            print("Invalid channel configured. Please use !systemmonitorset to set a valid channel.")
            return

        # If the saved message is in a different channel, clear it.
        if self.message and self.message.channel.id != self.monitor_channel_id:
            self.message = None

        try:
            if self.message:
                await self.message.edit(embed=embed)
            else:
                self.message = await channel.send(embed=embed)
        except Exception as e:
            print(f"Error updating the system message: {e}")

    @commands.command()
    async def systemmonitorset(self, ctx, channel: discord.TextChannel):
        """
        Set the channel where system monitor reports will be posted.

        Example:
          [p]systemmonitorset #system-monitor
        """
        await self.config.monitor_channel.set(channel.id)
        self.monitor_channel_id = channel.id
        # Clear the previous message so a new one is sent in the correct channel.
        self.message = None
        await ctx.send(f"System monitor channel updated to {channel.mention}.")

    @commands.command()
    async def system(self, ctx):
        """Manually trigger a system report."""
        await self.monitor()
        if self.message and self.message.embeds:
            await ctx.send(embed=self.message.embeds[0])
        else:
            await ctx.send("System information not available at the moment.")


async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))