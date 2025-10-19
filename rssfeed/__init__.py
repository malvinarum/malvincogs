from .rssfeed import RSSFeed

async def setup(bot):
    """
    This function is called by Redbot when the cog is loaded.
    It adds the RSSFeed cog to the bot.
    """
    await bot.add_cog(RSSFeed(bot))
