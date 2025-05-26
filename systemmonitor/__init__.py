from .systemmonitor import SystemMonitor
async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))
