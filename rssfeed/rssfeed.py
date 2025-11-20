import discord
from redbot.core import Config, commands, app_commands
from redbot.core.utils.chat_formatting import pagify, box, humanize_list
from discord.ext import tasks
import feedparser
import time
import re
import asyncio
import math
from html import unescape


class RSSFeed(commands.Cog):
    """
    A cog to monitor and post updates from multiple RSS feeds.
    Now with Image Extraction, Keyword Filtering, Role Pings, and Read Times!
    """

    IDENTIFIER = 1234567890
    FOOTER_TEXT_AUTHOR = " | RSS Feed by Malvinarum"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=self.IDENTIFIER, force_registration=True)

        # Schema:
        # feeds: {
        #   "url": {
        #     "channel_id": int,
        #     "last_entry_link": str,
        #     "role_id": int (optional),
        #     "include_keywords": list (optional),
        #     "exclude_keywords": list (optional),
        #     "embed_color": int (optional)
        #   }
        # }
        default_global = {"feeds": {}}
        self.config.register_global(**default_global)
        self._check_all_feeds_task.start()

    def cog_unload(self):
        self._check_all_feeds_task.cancel()

    # --- UTILITIES ---

    def _strip_html(self, raw_html: str) -> str:
        if not raw_html: return ""
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', raw_html)
        return unescape(cleantext).strip()

    def _estimate_reading_time(self, text: str) -> str:
        word_count = len(text.split())
        minutes = math.ceil(word_count / 200)  # Avg reading speed 200 wpm
        if minutes <= 1:
            return "1 min read"
        return f"{minutes} min read"

    def _extract_image(self, entry) -> str:
        """
        Aggressively hunts for an image URL in the feed entry.
        """
        # 1. Try 'media_content' (Common in news feeds)
        if 'media_content' in entry:
            for media in entry.media_content:
                if media.get('medium') == 'image' and 'url' in media:
                    return media['url']

        # 2. Try 'media_thumbnail'
        if 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
            return entry.media_thumbnail[0]['url']

        # 3. Try 'links' (Enclosures)
        if 'links' in entry:
            for link in entry.links:
                if link.get('rel') == 'enclosure' and link.get('type', '').startswith('image/'):
                    return link['href']

        # 4. Regex the HTML description/summary content for <img src="...">
        # Note: This is a fallback and might catch tracking pixels if not careful,
        # but usually the first image is the main one.
        content = entry.get('content', [{'value': ''}])[0]['value'] or entry.get('summary', '') or entry.get(
            'description', '')

        img_match = re.search(r'<img[^>]+src="([^">]+)"', content)
        if img_match:
            return img_match.group(1)

        return None

    def _create_rss_embed(self, entry, feed_url: str, feed_data: dict) -> discord.Embed:
        title = entry.get("title", f"New Post from {feed_url}")
        link = entry.get("link", feed_url)

        # Clean summary
        raw_summary = entry.get("summary", "Click the link for details.")
        summary = self._strip_html(raw_summary)

        # Fix "Read more" text not being a link (Artifact of stripping <a> tags)
        # Matches "Read more" at the end of string, matching dots, arrows, or whitespace
        summary = re.sub(r"(?i)Read\s+more.*$", f"[Read more]({link})", summary)

        # Truncate summary to avoid 4096 limit, leave room for "..."
        # We use 2000 to be safe and consistent
        if len(summary) > 2000:
            summary = summary[:1950] + f"... [Read more]({link})"

        # Custom Color or Default Blue
        color_val = feed_data.get("embed_color", 0x3498db)
        color = discord.Color(color_val)

        embed = discord.Embed(title=title, description=summary, url=link, color=color)

        # Image
        image_url = self._extract_image(entry)
        if image_url:
            embed.set_image(url=image_url)

        # Footer with Timestamp & Read Time
        pub_time = entry.get("published_parsed")
        if pub_time:
            time_str = time.strftime('%Y-%m-%d %H:%M UTC', pub_time)
        else:
            time_str = discord.utils.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        read_time = self._estimate_reading_time(summary)
        footer_text = f"{time_str} • ⏱️ {read_time}{self.FOOTER_TEXT_AUTHOR}"
        embed.set_footer(text=footer_text)

        return embed

    # --- CORE LOGIC ---

    async def _process_feed(self, feed_url: str, data: dict, force_post: bool = False) -> str:
        channel_id = data.get("channel_id")
        last_link = data.get("last_entry_link")

        # Filter Configs
        include_keywords = data.get("include_keywords", [])
        exclude_keywords = data.get("exclude_keywords", [])

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return "ERROR_CHANNEL"

        # Retry Logic
        feed = None
        for _ in range(3):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RedBot/RSS"}
                feed = feedparser.parse(feed_url, request_headers=headers)
                if feed.entries: break
                await asyncio.sleep(2)
            except:
                await asyncio.sleep(2)

        if not feed or not feed.entries:
            return "NO_ENTRIES"

        # SCAN LOGIC: Look through top 15 entries for a match
        target_entry = None
        skip_reason = "NO_NEW_POST"

        for entry in feed.entries[:15]:
            link = entry.get("link") or entry.get("guid")

            # 1. Filter Check
            content_to_check = (entry.get("title", "") + " " + entry.get("summary", "")).lower()

            # Excludes
            if exclude_keywords:
                if any(kw.lower() in content_to_check for kw in exclude_keywords):
                    skip_reason = "SKIPPED_FILTER_EXCLUDE"
                    continue

                    # Includes
            if include_keywords:
                if not any(kw.lower() in content_to_check for kw in include_keywords):
                    skip_reason = "SKIPPED_FILTER_INCLUDE"
                    continue

                    # 2. Duplicate Check
            # If we hit the last posted link, we stop searching (unless forcing)
            if link == last_link and not force_post:
                skip_reason = "NO_NEW_POST"
                break  # Stop loop, we reached old content

            # If we are here, we found a valid candidate!
            target_entry = entry
            # If we are not forcing, we just want the NEWEST valid one.
            # Since we iterate 0->15, the first one we find IS the newest.
            break

        if not target_entry:
            return skip_reason

        # --- POSTING ---
        embed = self._create_rss_embed(target_entry, feed_url, data)
        latest_link = target_entry.get("link") or target_entry.get("guid")

        content = None
        role_id = data.get("role_id")
        if role_id:
            role = channel.guild.get_role(role_id)
            if role:
                content = f"{role.mention} New update available!"

        try:
            await channel.send(content=content, embed=embed)
        except Exception as e:
            print(f"RSS Send Error: {e}")
            return "ERROR_SEND"

        # Update Config
        data["last_entry_link"] = latest_link
        await self.config.feeds.set_raw(feed_url, value=data)

        return "SUCCESS"

    async def _check_all_feeds_logic(self):
        await self.bot.wait_until_ready()
        all_feeds = await self.config.feeds()
        if not all_feeds: return

        for feed_url, data in all_feeds.items():
            await self._process_feed(feed_url, data, force_post=False)

    @tasks.loop(minutes=5.0)
    async def _check_all_feeds_task(self):
        await self._check_all_feeds_logic()

    @_check_all_feeds_task.before_loop
    async def before_rss_checker(self):
        await self.bot.wait_until_ready()

    # --- COMMANDS ---

    @commands.group(name="rss")
    @commands.is_owner()
    async def rss_settings(self, ctx: commands.Context):
        """Manage RSS feeds, filters, and styling."""
        pass

    @rss_settings.command(name="add")
    async def add_feed(self, ctx: commands.Context, channel: discord.TextChannel, *, url: str):
        """Add a new feed. Usage: [p]rss add #channel https://site.com/feed"""
        if not url.startswith(("http://", "https://")):
            return await ctx.send("Invalid URL.")

        async with self.config.feeds() as feeds:
            if url in feeds:
                return await ctx.send("Feed already exists.")

            feeds[url] = {
                "channel_id": channel.id,
                "last_entry_link": None,
                "include_keywords": [],
                "exclude_keywords": [],
                "role_id": None,
                "embed_color": 0x3498db
            }
        await ctx.send(f"✅ Feed added to {channel.mention}.")

    @rss_settings.command(name="remove")
    async def remove_feed(self, ctx: commands.Context, *, url: str):
        """Remove a feed."""
        async with self.config.feeds() as feeds:
            if url in feeds:
                del feeds[url]
                await ctx.send("🗑️ Feed removed.")
            else:
                await ctx.send("Feed not found.")

    @rss_settings.command(name="role")
    async def set_role(self, ctx: commands.Context, role: discord.Role, *, url: str):
        """Set a role to ping when this feed updates."""
        async with self.config.feeds() as feeds:
            if url not in feeds:
                return await ctx.send("Feed not found.")
            feeds[url]["role_id"] = role.id
        await ctx.send(f"🔔 Role {role.mention} will be pinged for updates.")

    @rss_settings.command(name="color")
    async def set_color(self, ctx: commands.Context, color_hex: str, *, url: str):
        """Set embed color (e.g., FF0000 for red)."""
        try:
            color_int = int(color_hex.replace("#", ""), 16)
        except ValueError:
            return await ctx.send("Invalid hex color.")

        async with self.config.feeds() as feeds:
            if url not in feeds: return await ctx.send("Feed not found.")
            feeds[url]["embed_color"] = color_int

        await ctx.send("🎨 Color updated.")

    @rss_settings.group(name="filter")
    async def rss_filter(self, ctx: commands.Context):
        """Manage inclusion/exclusion filters."""
        pass

    @rss_filter.command(name="include")
    async def filter_include(self, ctx: commands.Context, keyword: str, *, url: str):
        """Only post if keyword exists."""
        async with self.config.feeds() as feeds:
            if url not in feeds: return await ctx.send("Feed not found.")
            if keyword not in feeds[url].get("include_keywords", []):
                feeds[url].setdefault("include_keywords", []).append(keyword)
        await ctx.send(f"➕ Added inclusion filter: `{keyword}`")

    @rss_filter.command(name="exclude")
    async def filter_exclude(self, ctx: commands.Context, keyword: str, *, url: str):
        """Don't post if keyword exists."""
        async with self.config.feeds() as feeds:
            if url not in feeds: return await ctx.send("Feed not found.")
            if keyword not in feeds[url].get("exclude_keywords", []):
                feeds[url].setdefault("exclude_keywords", []).append(keyword)
        await ctx.send(f"⛔ Added exclusion filter: `{keyword}`")

    @rss_settings.command(name="list")
    async def list_feeds(self, ctx: commands.Context):
        """List all configured feeds with their settings."""
        feeds = await self.config.feeds()
        if not feeds:
            return await ctx.send("No feeds configured.")

        msg = ""
        for url, data in feeds.items():
            ch_id = data.get("channel_id")
            role_id = data.get("role_id")
            inc = data.get("include_keywords", [])
            exc = data.get("exclude_keywords", [])

            role_str = f"<@&{role_id}>" if role_id else "None"

            block = (
                f"**Feed:** <{url}>\n"
                f"📂 Channel: <#{ch_id}>\n"
                f"🔔 Role: {role_str}\n"
                f"✅ Includes: {humanize_list(inc) if inc else 'All'}\n"
                f"⛔ Excludes: {humanize_list(exc) if exc else 'None'}\n"
                f"────────────────\n"
            )

            if len(msg) + len(block) > 2000:
                await ctx.send(msg)
                msg = ""
            msg += block

        if msg: await ctx.send(msg)

    @rss_settings.command(name="force")
    async def force_post(self, ctx: commands.Context, *, url: str):
        """Force the latest post to appear (bypasses filters/duplicates)."""
        feeds = await self.config.feeds()
        if url not in feeds: return await ctx.send("Feed not found.")

        await ctx.send("Checking and forcing post...")
        res = await self._process_feed(url, feeds[url], force_post=True)
        await ctx.send(f"Result: {res}")

    @rss_settings.command(name="updateall")
    async def manual_update(self, ctx: commands.Context):
        """Manually check all feeds."""
        await ctx.send("Checking all feeds...")
        await self._check_all_feeds_logic()
        await ctx.send("Done.")