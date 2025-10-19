import discord
from redbot.core import Config, commands
from discord.ext import tasks  # FIX: We use discord.ext.tasks for the loop utility
import feedparser  # This is required for the functionality below!
import time  # Used for timestamp in checker loop


class RSSFeed(commands.Cog):
    """
    A cog to monitor and post updates from multiple RSS feeds to different Discord channels.
    """

    # Unique identifier for configuration storage
    IDENTIFIER = 1234567890

    # Static footer part required for all embeds
    FOOTER_TEXT_AUTHOR = " | RSS Feed by Malvinarum"

    def __init__(self, bot):
        self.bot = bot
        # Initialize Config
        self.config = Config.get_conf(self, identifier=self.IDENTIFIER, force_registration=True)

        # Register default global settings. We now use a dictionary to store multiple feeds.
        default_global = {
            # Key: feed_url (str), Value: {"channel_id": int, "last_entry_link": str | None}
            "feeds": {}
        }
        self.config.register_global(**default_global)

        # Start the task loop
        self.rss_checker.start()

    def cog_unload(self):
        """Cancel the loop when the cog is unloaded."""
        self.rss_checker.cancel()

    def _create_rss_embed(self, entry, feed_url: str) -> discord.Embed:
        """Helper to create a consistent embed style for new RSS entries."""

        # Ensure title and link are present
        title = entry.get("title", f"New Post from {feed_url}")
        link = entry.get("link", feed_url)
        summary = entry.get("summary", "Click the link for details.")

        embed = discord.Embed(
            title=title,
            description=summary,
            url=link,
            color=discord.Color.blue()  # A consistent color for RSS updates
        )

        # Set the publication date if available, otherwise use current time
        pub_time = entry.get("published_parsed")
        if pub_time:
            # Format the time from struct_time object
            time_str = time.strftime('%Y-%m-%d %H:%M:%S UTC', pub_time)
        else:
            # Use current time if publish time is not available
            time_str = discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

        # Apply the required footer text
        footer_text = f"Published: {time_str}{self.FOOTER_TEXT_AUTHOR}"
        embed.set_footer(text=footer_text)

        return embed

    @tasks.loop(minutes=5.0)
    async def rss_checker(self):
        """
        The main loop that runs every 5 minutes to check all configured RSS feeds.
        """
        await self.bot.wait_until_ready()

        all_feeds = await self.config.feeds()

        if not all_feeds:
            return

        for feed_url, data in all_feeds.items():
            channel_id = data.get("channel_id")
            last_link = data.get("last_entry_link")

            if not channel_id:
                print(f"RSSFeed: Feed {feed_url} is missing a channel ID.")
                continue

            channel = self.bot.get_channel(channel_id)
            if not channel:
                print(f"RSSFeed: Configured channel ID {channel_id} for feed {feed_url} not found.")
                continue

            try:
                feed = feedparser.parse(feed_url)
                if not feed.entries:
                    continue

                latest_entry = feed.entries[0]
                latest_link = latest_entry.link

                if latest_link != last_link:
                    # New entry found

                    # 1. Create the embed using the helper function
                    embed = self._create_rss_embed(latest_entry, feed_url)

                    # 2. Send the embed
                    await channel.send(embed=embed)

                    # 3. Update the last_entry_link in the configuration for this specific feed
                    data["last_entry_link"] = latest_link
                    await self.config.feeds.set_raw(feed_url, value=data)

                else:
                    # Log that the check occurred, but no new posts were found
                    print(f"RSSFeed Checker: Checked feed {feed_url}. No new posts.")

            except Exception as e:
                print(f"An error occurred during RSS feed check for {feed_url}: {e}")

    @rss_checker.before_loop
    async def before_rss_checker(self):
        """Wait until the bot is connected before starting the loop."""
        await self.bot.wait_until_ready()

    @commands.group(name="rss")
    @commands.is_owner()  # Only the bot owner can configure this globally
    async def rss_settings(self, ctx: commands.Context):
        """Manage multiple RSS feed configurations (URL, posting channel, and removal)."""
        pass

    @rss_settings.command(name="add")
    async def add_feed(self, ctx: commands.Context, channel: discord.TextChannel, *, url: str):
        """
        Adds a new RSS feed to monitor or updates the channel for an existing feed.
        Usage: [p]rss add #channel <url>
        """
        if not url.startswith(("http://", "https://")):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")

        # Get all current feeds
        async with self.config.feeds() as feeds:
            is_new = url not in feeds

            # Update or create the feed entry
            feeds[url] = {
                "channel_id": channel.id,
                "last_entry_link": feeds.get(url, {}).get("last_entry_link"),  # Preserve last link if updating
            }

        if is_new:
            await ctx.send(f"New RSS feed added! Updates from `{url}` will be posted to {channel.mention}.")
        else:
            await ctx.send(f"RSS feed `{url}` updated! Updates will now be posted to {channel.mention}.")

    @rss_settings.command(name="remove")
    async def remove_feed(self, ctx: commands.Context, *, url: str):
        """Removes a monitored RSS feed by its URL."""
        if not url.startswith(("http://", "https://")):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")

        async with self.config.feeds() as feeds:
            if url in feeds:
                del feeds[url]
                await ctx.send(f"Successfully removed RSS feed: `{url}`.")
            else:
                await ctx.send(f"Error: Feed URL `{url}` not found in configuration.")

    @rss_settings.command(name="test")
    async def test_feed(self, ctx: commands.Context, *, url: str):
        """Simulates a new post for a configured RSS feed to test the posting functionality."""
        feeds = await self.config.feeds()

        if url not in feeds:
            return await ctx.send(f"Error: Feed URL `{url}` is not currently monitored. Please use `[p]rss add` first.")

        data = feeds[url]
        channel_id = data.get("channel_id")

        if not channel_id:
            return await ctx.send(f"Error: Feed URL `{url}` has no associated channel.")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return await ctx.send(f"Error: Could not find the configured channel ID `{channel_id}`.")

        # --- Simulate a new post with current timestamp ---
        test_title = f"RSS Test Post - {ctx.author.name}"
        test_link = f"https://test.link/rss-test-{ctx.message.created_at.timestamp()}"

        embed = discord.Embed(
            title=test_title,
            description=(
                "This is a simulated test post to verify that the bot is correctly configured "
                f"to post updates for the feed `{url}` to this channel."
            ),
            url=test_link,
            color=await ctx.embed_color()
        )
        # Apply the required footer text for the test command
        footer_text = f"Simulated update time: {ctx.message.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC{self.FOOTER_TEXT_AUTHOR}"
        embed.set_footer(text=footer_text)

        try:
            await channel.send(embed=embed)
            await ctx.send(
                f"Test post successfully sent to {channel.mention}. Please check that channel for the test embed.")
        except Exception as e:
            await ctx.send(f"Failed to send test post to {channel.mention}: {e}")

    @rss_settings.command(name="status")
    async def get_status(self, ctx: commands.Context):
        """Shows the current list of all monitored RSS feeds and their configurations."""
        feeds = await self.config.feeds()

        if not feeds:
            message = "No RSS feeds are currently configured.\nUse `[p]rss add #channel <url>` to start monitoring a feed."
            return await ctx.send(message)

        status_lines = []
        for url, data in feeds.items():
            channel_id = data.get("channel_id")
            last_link = data.get("last_entry_link")

            channel_mention = f"<#{channel_id}>" if channel_id else "No Channel Set"
            last_link_short = (last_link[:50] + "...") if last_link and len(last_link) > 50 else (
                last_link if last_link else "N/A")

            status_lines.append(
                f"**URL:** `{url}`\n"
                f"  **-> Channel:** {channel_mention}\n"
                f"  **-> Last Link:** `{last_link_short}`"
            )

        message = (
                f"**RSS Feed Configuration Status ({len(feeds)} Feeds)**\n\n"
                + "\n---\n".join(status_lines)
                + f"\n\n**Checker Status:** {'Running' if self.rss_checker.is_running() else 'Stopped (Error or Not Started)'}"
        )
        await ctx.send(message)
