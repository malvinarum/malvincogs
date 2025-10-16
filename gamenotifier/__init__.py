from .freegames import FreeGames

async def setup(bot):
    """Adds the FreeGames cog to the bot."""
    await bot.add_cog(FreeGames(bot))

# This is an optional function for unloading the cog, often kept for completeness.
async def teardown(bot):
    """Removes the FreeGames cog from the bot."""
    # You would typically add any necessary cleanup here, but for simple cogs,
    # the default unload handles most things.
    pass
