from .streamsentry import StreamSentry

async def setup(bot):
    await bot.add_cog(StreamSentry(bot))