import discord
import asyncio
import psutil
from redbot.core import commands

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage."""

    def __init__(self, bot):
        self.bot = bot
        self.message = None
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
        memory_usage = psutil.virtual_memory().used // (1024 * 1024)
        disk_usage = psutil.disk_usage("/").percent
        net_io = psutil.net_io_counters()
        network_sent = net_io.bytes_sent // (1024 * 1024)  # Convert to MB
        network_received = net_io.bytes_recv // (1024 * 1024)  # Convert to MB

        embed = discord.Embed(title="System Usage", color=discord.Color.blue())
        embed.add_field(name="CPU", value=f"{cpu_usage}%", inline=True)
        embed.add_field(name="Memory", value=f"{memory_usage} MB", inline=True)
        embed.add_field(name="Disk", value=f"{disk_usage}%", inline=True)
        embed.add_field(name="Network Sent", value=f"{network_sent} MB", inline=True)
        embed.add_field(name="Network Received", value=f"{network_received} MB", inline=True)

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

def setup(bot):
    bot.add_cog(SystemMonitor(bot))
