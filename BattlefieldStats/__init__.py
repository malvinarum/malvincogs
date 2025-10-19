from .bf_stats_cog import BattlefieldStats

async def setup(bot):
    """
    Adds the BattlefieldStats cog to the bot.
    """
    await bot.add_cog(BattlefieldStats(bot))

# Optional: You can include an async def teardown(bot) function here for cleanup
# if necessary, but it's not strictly required for this simple cog.

__red_end_user_data_statement__ = "This cog does not store any end user data."
