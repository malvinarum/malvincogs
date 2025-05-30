# gemini/__init__.py
from .gemini import gemini

async def setup(bot):
    """
    This function is called by Redbot when the cog is loaded.
    It adds the Gemini cog to the bot.
    """
    await bot.add_cog(gemini(bot))