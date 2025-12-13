from .systemdmanager import SystemdManager

async def setup(bot):
    await bot.add_cog(SystemdManager(bot))