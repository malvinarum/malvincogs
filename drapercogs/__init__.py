from draperbundle.customchannels import CustomChannels
from draperbundle.dynamicchannels import DynamicChannels
from draperbundle.gamingprofile import GamingProfile
from draperbundle.pcspecs import PCSpecs
from draperbundle.playerstats import PlayerStats
from draperbundle.publishermanager import PublisherManager
from draperbundle.status import MemberStatus


async def setup(bot):
    await bot.add_cog(DynamicChannels(bot))
    await bot.add_cog(CustomChannels(bot))
    await bot.add_cog(GamingProfile(bot))
    await bot.add_cog(MemberStatus(bot))
    await bot.add_cog(PCSpecs(bot))
    await bot.add_cog(PublisherManager(bot))
    await bot.add_cog(PlayerStats(bot))
