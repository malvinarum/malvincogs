import discord
from redbot.core import commands, Config
from discord.ext import tasks
from .systemmonitor import SystemMonitor
from .plex_activity import PlexActivity
import psutil

class SystemMonitor(commands.Cog):
    """A Redbot cog for monitoring system and network usage with a dynamic report channel."""

    def __init__(self, bot):
        self.bot = bot
async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))
    """
    This function is called by Redbot when the cog is loaded.
    It adds the PlexActivity cog to the bot.
    """
    await bot.add_cog(PlexActivity(bot))