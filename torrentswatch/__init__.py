from .torrentswatch import TorrentsWatch

async def setup(bot):
    await bot.add_cog(TorrentsWatch(bot))