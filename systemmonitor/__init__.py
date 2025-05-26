import discord
from redbot.core import commands, Config
from discord.ext import tasks
import psutil

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage with a dynamic report channel."""

    def __init__(self, bot):
        self.bot = bot
from .systemmonitor import SystemMonitor
async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))
