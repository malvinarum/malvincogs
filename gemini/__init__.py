from .gemini import Gemini # Changed 'gemini' to 'Gemini' (capital G)

async def setup(bot):
    """
    This function is called by Redbot when the cog is loaded.
    It adds the Gemini cog to the bot.
    """
    await bot.add_cog(Gemini(bot))

