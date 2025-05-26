import discord
from discord.ext import commands, tasks
import psutil
from datetime import datetime

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage, now aggregating multiple disks."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None
        # For network speed calculations.
        self.previous_net_io = psutil.net_io_counters()
        self.previous_time = datetime.now()
        # Start the periodic monitoring task.
        self.monitor_loop.start()

    @tasks.loop(seconds=60)
    async def monitor_loop(self):
        """Background task that updates system stats every 60 seconds."""
        try:
            await self.monitor()
        except Exception as e:
            print(f"Error in monitor_loop: {e}")

    async def monitor(self):
        now = datetime.now()
        # Calculate elapsed time for accurate network speed measurement.
        sample_period = (now - self.previous_time).total_seconds()

        # CPU usage (this call blocks 1 second).
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

        # Convert bytes to megabits (1 byte = 8 bits; 1 Megabit = 1024*1024 bits)
        upload_speed_mbps = (delta_bytes_sent * 8) / (1024 * 1024 * sample_period)
        download_speed_mbps = (delta_bytes_recv * 8) / (1024 * 1024 * sample_period)

        # Update counters for the next iteration.
        self.previous_net_io = current_net_io
        self.previous_time = now

        last_updated = now.astimezone().strftime("%Y-%m-%d %H:%M %Z")

        # Build the embed.
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
        embed.add_field(
            name="Upload Speed", value=f"{upload_speed_mbps:.2f} of 1024 Mbps", inline=True
        )
        embed.add_field(
            name="Download Speed", value=f"{download_speed_mbps:.2f} of 1024 Mbps", inline=False
        )
        embed.set_footer(text=f"Last Updated: {last_updated}")

        # Update the persistent message or send a new one if necessary.
        try:
            if self.message:
                await self.message.edit(embed=embed)
            else:
                channel = self.bot.get_channel(1369497173678358651)  # Replace with your actual channel ID
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
