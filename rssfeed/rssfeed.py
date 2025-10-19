import discord
import aiohttp
import feedparser
import dateutil.parser
from redbot.core import commands, Config, checks
from redbot.core.tasks import loop
from redbot.core.utils.chat_formatting import pagify


# NOTE: This code assumes 'feedparser' and 'python-dateutil' are installed in the Red environment.
# They are listed as requirements in the info.json file.

class RSSFeed(commands.Cog):
    """
    Automatically fetches and posts news from RSS/Atom feeds to designated channels.
    """

    # Unique identifier for configuration data persistence
    # This number should be unique to your cog.
    IDENTIFIER = 5891784930123012

    def __init__(self, bot):
        self.bot = bot
        # Config setup: We store a list of feeds globally.
        # Each feed is a dictionary:
        # {"url": str, "channel_id": int, "last_published": float (timestamp)}
        self.config = Config.get_conf(self, identifier=self.IDENTIFIER, force_registration=True)
        self.config.register_global(feeds=[])

        # Start the background task loop
        self.feed_checker.start()

    def cog_unload(self):
        # Always stop the background task when the cog is unloaded
        self.feed_checker.cancel()

    @loop(minutes=15.0)
    async def feed_checker(self):
        """
        Periodically checks all configured RSS feeds for new entries.
        """
        # Ensure the bot is fully ready before trying to send messages
        await self.bot.wait_until_ready()

        # Fetch the list of feeds from configuration
        async with self.config.feeds() as feeds:

            # Use a single aiohttp session for all requests in this loop run
            async with aiohttp.ClientSession() as session:

                # Iterate over the feeds using enumerate to allow in-place modification
                for i, feed_data in enumerate(feeds):
                    url = feed_data["url"]
                    channel_id = feed_data["channel_id"]

                    # Resolve channel
                    channel = self.bot.get_channel(channel_id)
                    if channel is None:
                        print(f"RSSFeed: Channel ID {channel_id} for feed {url} not found or inaccessible. Skipping.")
                        continue

                    # Fetch feed content asynchronously
                    try:
                        async with session.get(url, timeout=10) as response:
                            if response.status != 200:
                                print(f"RSSFeed: Failed to fetch {url}. Status: {response.status}")
                                continue

                            content = await response.text()

                    except aiohttp.ClientError as e:
                        print(f"RSSFeed: Network error fetching {url}: {e}")
                        continue
                    except Exception as e:
                        print(f"RSSFeed: Unknown error during fetch for {url}: {e}")
                        continue

                    # Parse the feed content
                    try:
                        parsed_feed = feedparser.parse(content)
                        if parsed_feed.bozo:
                            # bozo means parsing errors (usually non-fatal, but can indicate issues)
                            if isinstance(parsed_feed.bozo_exception, feedparser.NonXMLContentType):
                                # If it's a content type error, it's serious
                                print(f"RSSFeed: Non-XML content type for {url}. Skipping.")
                                continue

                    except Exception as e:
                        print(f"RSSFeed: Error parsing feed {url}: {e}")
                        continue

                    # Check for new entries and reverse the list so the oldest new entry is posted first
                    new_entries = []

                    # Sort entries by published date (ascending) to post in correct order
                    # Check if 'published_parsed' exists and can be parsed, otherwise skip the entry
                    entries_with_dates = []
                    for entry in parsed_feed.entries:
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            # Convert time tuple to datetime object
                            dt = dateutil.parser.parse(entry.published)
                            entries_with_dates.append((dt.timestamp(), entry))

                    # Sort from oldest to newest
                    entries_with_dates.sort(key=lambda x: x[0])

                    for timestamp, entry in entries_with_dates:
                        # Check if the entry is newer than the last published one
                        if timestamp > feed_data.get("last_published", 0):
                            new_entries.append(entry)

                    # Post new entries
                    if new_entries:
                        print(
                            f"RSSFeed: Found {len(new_entries)} new entries for {parsed_feed.feed.title if hasattr(parsed_feed.feed, 'title') else url}")

                        last_timestamp = feed_data.get("last_published", 0)

                        for entry in new_entries:

                            # Create a clean embed
                            embed = discord.Embed(
                                title=entry.get('title', 'No Title Available'),
                                url=entry.get('link', url),
                                color=discord.Color.red()
                            )

                            # Add feed title to the author field
                            feed_title = parsed_feed.feed.get('title', 'Unknown Feed')
                            embed.set_author(name=feed_title)

                            # Add summary, cleaning up HTML if present (feedparser usually gives a clean version)
                            summary = entry.get('summary', entry.get('description', ''))
                            if summary:
                                # Truncate summary to fit Discord embed limit (2048 chars)
                                trimmed_summary = summary.replace('\n', ' ').strip()
                                # Use pagify to ensure we don't exceed limit, taking the first chunk
                                embed.description = next(pagify(trimmed_summary, page_length=1024),
                                                         "Click link for details.")

                            # Get the published timestamp for tracking
                            entry_timestamp = dateutil.parser.parse(entry.published).timestamp()
                            embed.timestamp = discord.Object(id=0, ts=entry_timestamp).created_at

                            try:
                                await channel.send(embed=embed)
                                # Update the timestamp *after* a successful send
                                if entry_timestamp > last_timestamp:
                                    last_timestamp = entry_timestamp

                            except discord.Forbidden:
                                print(f"RSSFeed: Missing permissions to send messages in channel {channel_id}.")
                                break
                            except Exception as e:
                                print(f"RSSFeed: Error sending message for entry {entry.get('title', 'N/A')}: {e}")
                                break

                        # After processing all new entries for this feed, update the last_published marker in the config
                        if last_timestamp > feed_data.get("last_published", 0):
                            feeds[i]["last_published"] = last_timestamp
                            print(f"RSSFeed: Updated last_published for {url} to {last_timestamp}")

    @feed_checker.before_loop
    async def before_feed_checker(self):
        """Wait until bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        print("RSSFeed: Background feed checking loop started.")

    # --- Commands ---

    @commands.group()
    @checks.admin_or_permissions(manage_channels=True)
    async def rss(self, ctx: commands.Context):
        """Manage automatic RSS/Atom feed publishing."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @rss.command(name="add")
    async def rss_add(self, ctx: commands.Context, feed_url: str, channel: discord.TextChannel = None):
        """
        Add a new RSS/Atom feed to track.

        <feed_url> is the link to the RSS/Atom feed.
        [channel] is the channel to post updates to (defaults to the current channel).
        """

        target_channel = channel or ctx.channel

        # 1. Try to fetch and parse the feed once to validate the URL
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(feed_url, timeout=10) as response:
                    if response.status != 200:
                        return await ctx.send(
                            f"❌ Failed to fetch feed data from URL. HTTP status code: `{response.status}`.")

                    content = await response.text()

            except aiohttp.ClientError as e:
                return await ctx.send(f"❌ Network error while validating feed URL: `{e}`")
            except Exception as e:
                return await ctx.send(f"❌ An unexpected error occurred while fetching the feed: `{e}`")

        # 2. Parse the content to check if it's a valid feed
        parsed_feed = feedparser.parse(content)

        if not parsed_feed.feed:
            return await ctx.send("❌ That doesn't look like a valid RSS or Atom feed URL.")

        # 3. Add to config
        async with self.config.feeds() as feeds:
            # Check for duplicates
            if any(f["url"] == feed_url for f in feeds):
                return await ctx.send("⚠️ This feed URL is already being tracked.")

            # Use the publication date of the most recent entry as the initial "last_published"
            # so we don't spam the channel with old articles on first run.
            initial_timestamp = 0.0
            if parsed_feed.entries:
                try:
                    # Get the most recent entry and its published date
                    latest_entry = max(
                        (e for e in parsed_feed.entries if hasattr(e, 'published')),
                        key=lambda e: dateutil.parser.parse(e.published).timestamp()
                    )
                    initial_timestamp = dateutil.parser.parse(latest_entry.published).timestamp()
                except Exception:
                    # Fallback if date parsing fails for existing entries
                    initial_timestamp = 0.0

            feeds.append({
                "url": feed_url,
                "channel_id": target_channel.id,
                "last_published": initial_timestamp
            })

        feed_title = parsed_feed.feed.get('title', 'Unknown Feed')
        await ctx.send(
            f"✅ **{feed_title}** has been added and will post to {target_channel.mention}. "
            f"Initial check timestamp set to {initial_timestamp}."
        )

    @rss.command(name="remove")
    async def rss_remove(self, ctx: commands.Context, feed_url: str):
        """
        Remove a tracked RSS/Atom feed by its URL.

        <feed_url> is the link you want to stop tracking.
        """
        removed = False
        async with self.config.feeds() as feeds:
            initial_count = len(feeds)
            feeds[:] = [f for f in feeds if f["url"] != feed_url]
            if len(feeds) < initial_count:
                removed = True

        if removed:
            await ctx.send(f"✅ Feed removed successfully: `{feed_url}`")
        else:
            await ctx.send("❌ That feed URL was not found in the tracked list.")

    @rss.command(name="list")
    async def rss_list(self, ctx: commands.Context):
        """List all currently tracked RSS/Atom feeds."""
        feeds = await self.config.feeds()

        if not feeds:
            return await ctx.send("No RSS feeds are currently being tracked.")

        output = "### Tracked RSS Feeds:\n\n"

        for i, feed in enumerate(feeds, 1):
            channel = self.bot.get_channel(feed["channel_id"])
            channel_name = channel.mention if channel else f"Unkown Channel ID ({feed['channel_id']})"

            last_pub_dt = 'Never'
            if feed.get("last_published", 0) > 0:
                # Convert timestamp back to a readable date
                last_pub_dt = discord.Object(id=0, ts=feed["last_published"]).created_at.strftime(
                    '%Y-%m-%d %H:%M:%S UTC')

            output += (
                f"**{i}.** To: {channel_name}\n"
                f"URL: `{feed['url']}`\n"
                f"Last Posted: `{last_pub_dt}`\n\n"
            )

        # Use pagify for long lists
        for page in pagify(output, delims=['\n\n'], page_length=1900):
            await ctx.send(page)


def setup(bot):
    bot.add_cog(RSSFeed(bot))
