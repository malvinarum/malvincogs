# voicegemini/__init__.py
# Assuming the main class file is 'voicegemini.py' within the same directory.
from .voicegemini import VoiceGemini

async def setup(bot):
    """
    This function is called by Redbot when the cog is loaded.
    It adds the VoiceGemini cog to the bot.
    """
    await bot.add_cog(VoiceGemini(bot))
