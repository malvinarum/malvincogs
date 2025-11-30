from .palworldwatch import PalworldWatch

async def setup(bot):
    await bot.add_cog(PalworldWatch(bot))