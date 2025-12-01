from .customchannels import CustomChannels
from .dynamicchannels import DynamicChannels
from .gamingprofile import GamingProfile
from .pcspecs import PCSpecs
from .playerstats import PlayerStats
from .publishermanager import PublisherManager
from .status import MemberStatus


async def setup(bot):
    await bot.add_cog(DynamicChannels(bot))
    await bot.add_cog(CustomChannels(bot))
    await bot.add_cog(GamingProfile(bot))
    await bot.add_cog(MemberStatus(bot))
    await bot.add_cog(PCSpecs(bot))
    await bot.add_cog(PublisherManager(bot))
    await bot.add_cog(PlayerStats(bot))