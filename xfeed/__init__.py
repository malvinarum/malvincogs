from .x_updates import XFeed

async def setup(bot):
    """
    Sets up the XFeed cog and adds it to the Redbot instance.
    """
    await bot.add_cog(XFeed(bot))
