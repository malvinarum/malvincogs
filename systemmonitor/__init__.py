from .systemmonitor import SystemMonitor

async def setup(bot):
    bot.add_cog(SystemMonitor(bot))
