import discord
import asyncio
import psutil
from datetime import datetime
from redbot.core import commands

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None
        self.previous_net_io = psutil.net_io_counters()
        self.bot.loop.create_task(self.monitor_loop())

    async def monitor_loop(self):
        """Continuously updates system stats every minute."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.monitor()
            await asyncio.sleep(60)  # Wait 60 seconds before updating

    async def monitor(self):
        """Fetches system stats and updates the message."""
        cpu_usage = psutil.cpu_percent(interval=1)
        memory_usage = psutil.virtual_memory().used // (1024 * 1024 * 1024)
        disk_usage = psutil.disk_usage("/").used / (1024 * 1024 * 1024)

        # Calculate bandwidth usage
        current_net_io = psutil.net_io_counters()
        network_sent_speed = (current_net_io.bytes_sent - self.previous_net_io.bytes_sent) / (1024 * 128)  # MB/sec
        network_received_speed = (current_net_io.bytes_recv - self.previous_net_io.bytes_recv) / (1024 * 128)  # MB/sec
        self.previous_net_io = current_net_io  # Update previous values for next iteration

        last_updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

        embed = discord.Embed(title="System Usage", color=discord.Color.blue())
        embed.add_field(name="CPU", value=f"{cpu_usage}%", inline=True)
        embed.add_field(name="Memory", value=f"{memory_usage:.2f} GB of 16 GB", inline=True)
        embed.add_field(name="Disk", value=f"{disk_usage:.2f} GB of 2.048 GB", inline=True)
        embed.add_field(name="Upload Speed", value=f"{network_sent_speed:.2f} MBPS of 1.024 MBPS", inline=True)
        embed.add_field(name="Download Speed", value=f"{network_received_speed:.2f} MBPS of 1.024 MBPS", inline=True)
        embed.set_footer(text=f"Last Updated: {last_updated}")

        if self.message:
            await self.message.edit(embed=embed)
        else:
            channel = self.bot.get_channel(1369497173678358651)  # Replace with actual channel ID
            self.message = await channel.send(embed=embed)

    @commands.command()
    async def system(self, ctx):
        """Manually trigger a system report."""
        await self.monitor()
        await ctx.send(embed=self.message.embeds[0])  # Send the same embed used in the loop

async def setup(bot):
    bot.add_cog(SystemMonitor(bot))
