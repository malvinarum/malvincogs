from .autodelete import AutoDelete


async def setup(bot):
    await bot.add_cog(AutoDelete(bot))