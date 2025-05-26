import discord
from discord.ext import commands, tasks
import psutil
from datetime import datetime

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None
        # Initialize previous network I/O and timestamp for accurate speed calculation.
        self.previous_net_io = psutil.net_io_counters()
        self.previous_time = datetime.now()
        # Start the background loop.
        self.monitor_loop.start()

    @tasks.loop(seconds=60)
    async def monitor_loop(self):
        """Background task that updates the system stats every 60 seconds."""
        try:
            await self.monitor()
        except Exception as e:
            print(f"Error in monitor_loop: {e}")

    async def monitor(self):
        """Fetches system stats and updates (or creates) the embedded message."""
        now = datetime.now()
        # Calculate the elapsed time (in seconds) since the last update.
        sample_period = (now - self.previous_time).total_seconds()

        # Fetch CPU usage (the interval=1 will block for one second).
        cpu_usage = psutil.cpu_percent(interval=1)

        # Retrieve memory statistics dynamically.
        memory = psutil.virtual_memory()
        used_memory_gb = memory.used / (1024 ** 3)
        total_memory_gb = memory.total / (1024 ** 3)

        # Retrieve disk usage data.
        disk = psutil.disk_usage("/")
        used_disk_gb = disk.used / (1024 ** 3)
        total_disk_gb = disk.total / (1024 ** 3)

        # Calculate network speed in Mbps.
        current_net_io = psutil.net_io_counters()
        delta_bytes_sent = current_net_io.bytes_sent - self.previous_net_io.bytes_sent
        delta_bytes_recv = current_net_io.bytes_recv - self.previous_net_io.bytes_recv

        # Convert bytes to megabits and divide by the sample period.
        upload_speed_mbps = (delta_bytes_sent * 8) / (1024 * 1024 * sample_period)
        download_speed_mbps = (delta_bytes_recv * 8) / (1024 * 1024 * sample_period)

        # Update previous network statistics and timestamp for the next iteration.
        self.previous_net_io = current_net_io
        self.previous_time = now

        last_updated = now.astimezone().strftime("%Y-%m-%d %H:%M %Z")

        # Create the embed with dynamically retrieved values.
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

        # Update the existing message or send a new one if it's not set.
        try:
            if self.message:
                await self.message.edit(embed=embed)
            else:
                # Replace with your actual channel ID.
                channel = self.bot.get_channel(1369497173678358651)
                if channel:
                    self.message = await channel.send(embed=embed)
                else:
                    print("Channel not found; please verify the channel ID.")
        except Exception as e:
            print(f"Error updating the system message: {e}")

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
