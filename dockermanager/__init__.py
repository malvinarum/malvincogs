class DockerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 3. RE-REGISTER THE VIEW ON LOAD
        # This tells the bot: "If you see a button with ID 'docker_mission_control:force_refresh',
        # use this class to handle it."
        self.bot.add_view(DockerControlView())

    @commands.command()
    async def dockerpanel(self, ctx):
        """Sends the persistent panel for the first time"""
        embed = discord.Embed(title="Docker Mission Control", description="Loading...", color=0x2b2d31)
        # We send the view normally here
        await ctx.send(embed=embed, view=DockerControlView())

async def setup(bot):
    await bot.add_cog(DockerCog(bot))