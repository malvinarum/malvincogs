from .systemmonitor import SystemMonitor

def setup(bot):
    bot.add_cog(SystemMonitor(bot))
