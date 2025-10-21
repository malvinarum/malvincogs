from .xfeed import XFeed

async def setup(bot):
    """Entry point for RedBot to load the XFeed cog."""
    # This imports the XFeed class from the xfeed.py file
    await bot.add_cog(XFeed(bot))
