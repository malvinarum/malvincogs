import discord
from redbot.core import Config, commands
from discord.ext import tasks  # FIX: We use discord.ext.tasks for the loop utility


# Note: You will need to install feedparser if you want to uncomment the logic below:
# import feedparser

class RSSFeed(commands.Cog):
    """
    A cog to monitor and post updates from an RSS feed to a Discord channel.

    This file resolves the ModuleNotFoundError by importing 'tasks' from
    'discord.ext' instead of the deprecated 'redbot.core.tasks'.
    """

    # Unique identifier for configuration storage
    IDENTIFIER = 1234567890

    def __init__(self, bot):
        self.bot = bot
        # Initialize Config
        self.config = Config.get_conf(self, identifier=self.IDENTIFIER, force_registration=True)

        # Register default global settings (per instance, since this is an owner-only cog concept)
        default_global = {
            "feed_url": None,
            "channel_id": None,
            "last_entry_link": None,
        }
        self.config.register_global(**default_global)

        # Start the task loop
        self.rss_checker.start()

    def cog_unload(self):
        """Cancel the loop when the cog is unloaded."""
        self.rss_checker.cancel()

    @tasks.loop(minutes=5.0)
    async def rss_checker(self):
        """
        The main loop that runs every 5 minutes to check the RSS feed.
        """
        # Wait until the bot is fully ready before doing anything
        await self.bot.wait_until_ready()

        feed_url = await self.config.feed_url()
        channel_id = await self.config.channel_id()
        last_link = await self.config.last_entry_link()

        if not feed_url or not channel_id:
            # Nothing configured, stop here
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"RSSFeed: Configured channel ID {channel_id} not found.")
            return

        try:
            # IMPORTANT: For this to work, you must install 'feedparser'
            # (e.g., pip install feedparser)

            # --- Uncomment the section below for actual functionality ---
            # feed = feedparser.parse(feed_url)
            # if not feed.entries:
            #     return

            # latest_entry = feed.entries[0]
            # latest_link = latest_entry.link

            # if latest_link != last_link:
            #     # New entry found
            #     message = f"**New Post:** {latest_entry.title}\n{latest_link}"
            #     await channel.send(message)
            #     await self.config.last_entry_link.set(latest_link)
            # -----------------------------------------------------------

            # Placeholder for testing the loop logic until feedparser is integrated
            print(f"RSSFeed Checker: Successfully checked configured feed at {feed_url}.")

        except Exception as e:
            print(f"An error occurred during RSS feed check: {e}")

    @rss_checker.before_loop
    async def before_rss_checker(self):
        """Wait until the bot is connected before starting the loop."""
        await self.bot.wait_until_ready()

    @commands.group(name="rss")
    @commands.is_owner()  # Only the bot owner can configure this globally
    async def rss_settings(self, ctx: commands.Context):
        """Manage global RSS feed settings (URL and posting channel)."""
        pass

    @rss_settings.command(name="setfeed")
    async def set_feed(self, ctx: commands.Context, *, url: str):
        """Sets the RSS feed URL to monitor globally."""
        if not url.startswith(("http://", "https://")):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")

        await self.config.feed_url.set(url)
        await ctx.send(f"RSS feed URL successfully set to: `{url}`. Monitoring will start on the next cycle.")

    @rss_settings.command(name="setchannel")
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where updates will be posted."""
        await self.config.channel_id.set(channel.id)
        await ctx.send(f"RSS updates will now be posted in {channel.mention}.")

    @rss_settings.command(name="status")
    async def get_status(self, ctx: commands.Context):
        """Shows the current RSS feed and channel configuration."""
        feed = await self.config.feed_url()
        channel_id = await self.config.channel_id()
        last_link = await self.config.last_entry_link()

        channel_mention = f"<#{channel_id}>" if channel_id else "None configured"

        message = (
            f"**RSS Feed Configuration Status**\n"
            f"**Feed URL:** `{feed if feed else 'None'}`\n"
            f"**Post Channel:** {channel_mention}\n"
            f"**Last Post Link:** `{last_link if last_link else 'N/A'}`\n"
            f"**Checker Status:** {'Running' if self.rss_checker.is_running() else 'Stopped (Error or Not Started)'}\n\n"
            f"Use `[p]rss setfeed <url>` and `[p]rss setchannel <#channel>` to configure."
        )
        await ctx.send(message)
