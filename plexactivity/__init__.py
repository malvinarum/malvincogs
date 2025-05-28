# plex_activity/__init__.py
from .plex_activity import PlexActivity

async def setup(bot):
    """
    This function is called by Redbot when the cog is loaded.
    It adds the PlexActivity cog to the bot.
    """
    await bot.add_cog(PlexActivity(bot))