# __init__.py

from .skippy import Skippy # Import the main cog class from skippy.py

async def setup(bot):
    """
    This function is called by Redbot to load the cog.
    It adds the Skippy cog to the bot.
    """
    await bot.add_cog(Skippy(bot))