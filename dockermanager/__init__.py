from .dockermanager import DockerManager

async def setup(bot):
    await bot.add_cog(DockerManager(bot))