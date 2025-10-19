import discord
from redbot.core import Config, commands
from discord.ext import tasks
import feedparser
import time
import re


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

    def _strip_html(self, raw_html: str) -> str:
        """Simple helper to strip common HTML tags using regex."""
        # Regex to match and remove any HTML tag
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', raw_html)
        return cleantext.strip()

    def _create_rss_embed(self, entry, feed_url: str) -> discord.Embed:
        """Helper to create a consistent embed style for new RSS entries."""

        # Ensure title and link are present
        # Use link or feed_url as a fallback for the URL property in the embed
        title = entry.get("title", f"New Post from {feed_url}")
        link = entry.get("link", feed_url)

        # Get summary and strip HTML tags before using it in the embed description
        raw_summary = entry.get("summary", "Click the link for details.")
        summary = self._strip_html(raw_summary)

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

    async def _process_feed(self, feed_url: str, data: dict, force_post: bool = False) -> bool:
        """
        Fetches, processes, and posts the latest entry for a single feed.

        Args:
            feed_url: The URL of the RSS feed.
            data: The configuration data for this feed (channel_id, last_entry_link).
            force_post: If True, posts the latest entry even if the link matches the last_entry_link.

        Returns:
            True if a post was sent, False otherwise.
        """
        channel_id = data.get("channel_id")
        last_link = data.get("last_entry_link")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"RSSFeed: Configured channel ID {channel_id} for feed {feed_url} not found.")
            return False

        try:
            # Add User-Agent header to prevent blocking by some servers (like rss.app)
            request_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
            }
            feed = feedparser.parse(feed_url, request_headers=request_headers)

            if not feed.entries:
                print(f"RSSFeed: Feed {feed_url} returned no entries.")
                return False

            latest_entry = feed.entries[0]

            # --- START FIX: Robust link extraction ---
            latest_link = latest_entry.get("link")

            # Fallback 1: Try 'guid' if 'link' is missing or empty
            if not latest_link:
                latest_link = latest_entry.get("guid")

            # Fallback 2: Check if link is still invalid
            if not latest_link or not latest_link.startswith(("http://", "https://")):
                print(
                    f"RSSFeed: Could not extract a valid link (link or guid) for the latest entry in feed {feed_url}. Skipping post.")
                return False

            # --- END FIX: Robust link extraction ---

            is_new = latest_link != last_link

            # Post if it's new OR if we were explicitly told to force a post
            if is_new or force_post:
                # 1. Create the embed
                embed = self._create_rss_embed(latest_entry, feed_url)

                # 2. Send the embed
                await channel.send(embed=embed)

                # 3. Update the last_entry_link in the configuration for this specific feed
                # We update the last link if a post happened (either new or forced)
                data["last_entry_link"] = latest_link
                await self.config.feeds.set_raw(feed_url, value=data)

                return True
            else:
                # Log that the check occurred, but no new posts were found
                print(f"RSSFeed Checker: Checked feed {feed_url}. No new posts.")
                return False

        except Exception as e:
            print(f"An error occurred during RSS feed check for {feed_url}: {e}")
            return False

    @tasks.loop(minutes=5.0)
    async def rss_checker(self):
        """
        The main loop that runs every 5 minutes to check all configured RSS feeds.
        It calls _process_feed with force_post=False (only posts new entries).
        """
        await self.bot.wait_until_ready()

        all_feeds = await self.config.feeds()

        if not all_feeds:
            return

        for feed_url, data in all_feeds.items():
            # Process the feed, but only post if a new link is found
            await self._process_feed(feed_url, data, force_post=False)

    @rss_checker.before_loop
    async def before_rss_checker(self):
        """Wait until the bot is connected before starting the loop."""
        await self.bot.wait_until_ready()

    @commands.group(name="rss")
    @commands.is_owner()  # Only the bot owner can configure this globally
    async def rss_settings(self, ctx: commands.Context):
        """Manage multiple RSS feed configurations (URL, posting channel, and removal)."""
        pass

    # --- NEW COMMANDS ---

    @rss_settings.command(name="updateall")
    async def update_all_feeds(self, ctx: commands.Context):
        """Forces an immediate check and update for all configured RSS feeds."""
        await ctx.send("Starting manual update check for all configured feeds...")
        try:
            # Immediately run the loop's core function
            await self.rss_checker()
            await ctx.send("Manual update check complete. Any new posts have been sent.")
        except Exception as e:
            await ctx.send(f"An error occurred during the manual update: {e}")

    @rss_settings.command(name="postlatest")
    async def post_latest_entry(self, ctx: commands.Context, *, url: str):
        """Forces a post of the absolute latest entry for the given feed, even if it was already posted."""
        feeds = await self.config.feeds()
        if url not in feeds:
            return await ctx.send(f"Error: Feed URL `{url}` is not currently monitored. Please use `[p]rss add` first.")

        # Get a fresh copy of the data from the config
        data = feeds[url]

        await ctx.send(f"Forcing post of the latest entry for `{url}`...")

        try:
            # Process the feed, forcing a post (force_post=True)
            posted = await self._process_feed(url, data, force_post=True)

            if posted:
                await ctx.send(f"Successfully posted the latest entry from `{url}` to the configured channel.")
            else:
                await ctx.send(f"No valid entry was found to post for `{url}`.")
        except Exception as e:
            await ctx.send(f"Failed to post latest entry for `{url}`: {e}")

    @rss_settings.command(name="deleteall")
    async def delete_all_feeds(self, ctx: commands.Context):
        """Deletes ALL configured RSS feeds after confirmation."""
        await ctx.send(
            "⚠️ **WARNING** ⚠️\n"
            "This command will delete **ALL** configured RSS feeds globally.\n"
            "To confirm, type `yes` within 30 seconds."
        )

        def check(m):
            # Check if the message is from the same author, in the same channel, and contains 'yes'
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'yes'

        try:
            # Wait for the user to confirm for 30 seconds
            confirmation = await self.bot.wait_for('message', check=check, timeout=30.0)

            # Confirmation received
            if confirmation:
                # Clear all feeds by setting the 'feeds' dictionary to an empty dictionary
                await self.config.feeds.set({})
                await ctx.send("✅ **Success!** All monitored RSS feeds have been deleted.")

        except TimeoutError:
            await ctx.send("❌ Confirmation timed out. No feeds were deleted.")
        except Exception as e:
            await ctx.send(f"An error occurred during deletion: {e}")

    # --- END NEW COMMANDS ---

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

        # Create a simulated entry dictionary for the helper function
        simulated_entry = {
            "title": test_title,
            "link": test_link,
            "summary": (
                "This is a simulated test post to verify that the bot is correctly configured. "
                "This text simulates the summary field retrieved from the RSS feed. No actual data will be posted."
            ),
            # Use current time struct for the test entry
            "published_parsed": ctx.message.created_at.timetuple()
        }

        # Use the unified embed creation helper
        embed = self._create_rss_embed(simulated_entry, url)
        # Manually override the color for the test command
        embed.color = await ctx.embed_color()

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
