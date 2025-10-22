import asyncio
import logging
from datetime import datetime, timezone

import discord
from redbot.core import Config, commands, app_commands, checks
from redbot.core.bot import Red
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from discord.ext import tasks
import aiohttp

# Set up logging for the cog
log = logging.getLogger("red.mycogs.xfeed")

# Base URL for the X API v2
X_API_BASE = "https://api.twitter.com/2"

# Discord's embed description limit is 2048 characters. We use 2000 to safely
# accommodate the "Read more" link and ellipsis if truncation occurs.
MAX_EMBED_DESCRIPTION_LENGTH = 2000


class XFeed(commands.Cog):
    """
    XFeed: Automatically checks for and posts new updates from X.com accounts
    to designated Discord channels using the X API v2.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Config structure:
        # Guild: {
        #   "accounts": {
        #       "account_name": {
        #           "user_id": 12345,
        #           "channel_id": 67890,
        #           "last_id": "1800..."
        #       }
        #   }
        # }
        self.config = Config.get_conf(self, identifier=147385960, force_registration=True)
        self.config.register_guild(accounts={})
        self.session = aiohttp.ClientSession()

        # The update check now runs every 30 minutes, balancing responsiveness
        # with the strict Free Tier limits (100 posts/month).
        self.update_check.start()

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        if self.session:
            asyncio.create_task(self.session.close())
        self.update_check.cancel()

    async def _get_auth_headers(self):
        """Retrieves the X.com Bearer Token from Red's shared API storage."""
        tokens = await self.bot.get_shared_api_tokens("x")
        bearer_token = tokens.get("bearer_token")
        if not bearer_token:
            return None
        return {"Authorization": f"Bearer {bearer_token}"}

    async def _fetch_user_id(self, username: str, headers: dict):
        """Converts a username into a user ID using the X API."""
        try:
            url = f"{X_API_BASE}/users/by/username/{username}"
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['data']['id']
                else:
                    log.warning(f"Failed to fetch user ID for {username}. Status: {resp.status}")
                    return None
        except Exception as e:
            log.error(f"Error fetching user ID for {username}: {e}", exc_info=True)
            return None

    async def _fetch_latest_posts(self, user_id: str, last_id: str, headers: dict):
        """Fetches new posts for a given user ID, newer than last_id."""
        params = {
            "tweet.fields": "created_at,author_id,attachments,public_metrics",
            "max_results": 5,
            # Added media expansion and media fields to get image/video URLs
            "expansions": "author_id,attachments.media_keys",
            "media.fields": "url,type,preview_image_url",
            # This 'exclude' parameter ensures we only get original posts and quoted posts.
            "exclude": "replies,retweets",
        }
        if last_id:
            params['since_id'] = last_id

        try:
            url = f"{X_API_BASE}/users/{user_id}/tweets"
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status in (401, 403):
                    log.error("X API authentication failed or permission denied. Check your Bearer Token.")
                    return None
                else:
                    log.warning(f"X API request failed for user {user_id}. Status: {resp.status}")
                    return None
        except Exception as e:
            log.error(f"Error fetching posts for user {user_id}: {e}", exc_info=True)
            return None

    async def _post_updates(self, guild: discord.Guild, username: str, account_data: dict, headers: dict):
        """Handles the fetching, posting, and config update for a single account."""
        user_id = account_data.get("user_id")
        channel_id = account_data.get("channel_id")
        last_id = account_data.get("last_id")

        channel = guild.get_channel(channel_id)
        if not user_id or not channel:
            log.warning(f"Skipping {username} in {guild.name}: Missing user ID or channel.")
            return False  # Indicate failure

        # Fetch new posts
        posts_response = await self._fetch_latest_posts(user_id, last_id, headers)

        if posts_response and 'data' in posts_response:
            new_posts = posts_response['data']
            newest_id = last_id

            # Find author info from includes (simplification)
            author_name = username
            author_username = username
            media_list = posts_response.get('includes', {}).get('media', [])  # Get media list

            if 'includes' in posts_response and 'users' in posts_response['includes']:
                user_info = next((u for u in posts_response['includes']['users'] if str(u['id']) == str(user_id)), None)
                if user_info:
                    author_name = user_info.get('name', username)
                    author_username = user_info.get('username', username)

            # Posts are returned newest first, so we process them in reverse order
            # to ensure the last_id update is correct and they post chronologically.
            for post in reversed(new_posts):
                try:
                    embed = self._create_embed(post, author_username, author_name, media_list)
                    await channel.send(embed=embed)
                    newest_id = post['id']
                except discord.Forbidden:
                    log.error(f"Missing permissions to post to channel {channel.name} in {guild.name}.")
                    return False  # Stop processing if we can't send
                except Exception as e:
                    log.error(f"Error sending post for {username}: {e}", exc_info=True)

            # Update the last_id only if new posts were successfully processed
            if newest_id != last_id:
                account_data['last_id'] = newest_id
                await self.config.guild(guild).accounts.set_raw(username, value=account_data)
                log.info(f"Updated last ID for @{username} to {newest_id}.")
                return True  # Indicate success with new post
            return False  # Indicate no new posts
        elif posts_response and 'meta' in posts_response and posts_response['meta'].get('result_count', 0) == 0:
            log.debug(f"No new posts found for @{username}.")
            return False  # Indicate no new posts
        return False  # Indicate API error

    def _create_embed(self, post_data: dict, author_username: str, author_name: str, media_list: list = None):
        """
        Creates a rich Discord embed from the X post data, now including media,
        and ensures the description does not exceed Discord's 2048 character limit,
        adding a "Read more" link if truncation occurs.
        """
        tweet_id = post_data['id']
        text = post_data['text']
        created_at_str = post_data['created_at']
        post_url = f"https://x.com/{author_username}/status/{tweet_id}"

        # --- TRUNCATION LOGIC (with Read more link) ---
        if len(text) > MAX_EMBED_DESCRIPTION_LENGTH:
            # Truncate the text
            text = text[:MAX_EMBED_DESCRIPTION_LENGTH]
            # Add ellipsis and the "Read more" link in Discord markdown format
            # Example: ... [Read more](https://x.com/user/status/123)
            read_more_link = f" **...** [[Read more]]({post_url})"
            text += read_more_link
        # --- END TRUNCATION LOGIC ---

        # Format timestamp
        timestamp = datetime.strptime(created_at_str, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)

        # Build the embed
        embed = discord.Embed(
            # The main link is on the title of the embed
            title=f"New Post from @{author_username}",
            description=text,
            url=post_url,  # Sets the main clickable area for the embed
            timestamp=timestamp,
            color=0x000000  # X's primary color
        )
        embed.set_author(name=f"{author_name} (@{author_username})",
                         url=f"https://x.com/{author_username}",
                         icon_url="https://i.imgur.com/k2H999N.png")  # Placeholder X logo/icon

        # --- MEDIA HANDLING ---
        if media_list and 'attachments' in post_data and 'media_keys' in post_data['attachments']:
            media_keys = post_data['attachments']['media_keys']

            # Find the first image/video in the included media list
            for key in media_keys:
                media_item = next((m for m in media_list if m.get('media_key') == key), None)

                if media_item:
                    media_url = None
                    media_type = media_item.get('type')

                    # For photos, use 'url'. For videos/gifs, use 'preview_image_url'.
                    if media_type == 'photo' and media_item.get('url'):
                        media_url = media_item['url']
                    elif media_type in ('video', 'animated_gif') and media_item.get('preview_image_url'):
                        # Discord won't autoplay video in an embed image field, so use the preview.
                        media_url = media_item['preview_image_url']

                    if media_url:
                        embed.set_image(url=media_url)
                        # We only display the first media item in the embed image slot
                        break

        # Footer: Only show the bot credit.
        embed.set_footer(text="XFeed by Malvinarum")

        return embed

    @tasks.loop(minutes=30)  # Now running every 30 minutes
    async def update_check(self):
        """The main background loop to check for new X posts."""
        await self.bot.wait_until_ready()
        headers = await self._get_auth_headers()
        if not headers:
            log.warning("X API Bearer Token not set. Skipping update check.")
            return

        all_guild_data = await self.config.all_guilds()

        for guild_id, guild_data in all_guild_data.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            accounts = guild_data.get("accounts", {}).copy()

            for username, account_data in accounts.items():
                log.debug(f"Checking updates for @{username} in {guild.name} (Scheduled Run)...")
                await self._post_updates(guild, username, account_data, headers)

    # --- Commands ---

    @commands.group(name="xfeed", aliases=["xupdates"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def x_updates(self, ctx: commands.Context):
        """Manage X.com account tracking and settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @x_updates.command(name="check", aliases=["manual"])
    async def manual_update_check(self, ctx: commands.Context, username: str):
        """
        Manually checks for and posts updates from a tracked X.com account.

        Usage: [p]xfeed check <username>
        Example: [p]xfeed check TheOfficialX
        """
        username = username.strip('@').lower()

        headers = await self._get_auth_headers()
        if not headers:
            return await ctx.send("The X API Bearer Token is not set. Cannot perform manual check.")

        account_data = await self.config.guild(ctx.guild).accounts.get_raw(username, default=None)

        if not account_data:
            return await ctx.send(
                f"The account `@{username}` is not currently being tracked. Use `{ctx.prefix}xfeed follow` to add it.")

        await ctx.defer()  # Acknowledge the command quickly

        log.info(f"Checking updates for @{username} in {ctx.guild.name} (Manual Run)...")

        # Use the new helper method to process updates
        success = await self._post_updates(ctx.guild, username, account_data, headers)

        if success:
            await ctx.send(f"**Manual check complete:** New updates from `@{username}` were found and posted.")
        else:
            await ctx.send(f"**Manual check complete:** No new updates found for `@{username}`.")

    @x_updates.command(name="setapi")
    @checks.is_owner()
    async def set_api(self, ctx: commands.Context):
        """
        Sets the X.com Bearer Token using Red's secure API storage.

        You must set the API key in Red's main config using:
        `[p]set api x bearer_token <your_token>`
        """
        msg = (
            "To set the X API Bearer Token securely, use the following command:\n\n"
            "`{prefix}set api x bearer_token <your_token>`\n\n"
            "This token must be generated from your X.com Developer account with **Read** access."
        ).format(prefix=ctx.prefix)
        await ctx.send(msg)

    @x_updates.command(name="follow")
    async def follow_x_account(self, ctx: commands.Context, username: str, channel: discord.TextChannel):
        """
        Starts tracking an X.com account and posts updates to a channel.

        Usage: [p]xfeed follow <username> <#channel>
        Example: [p]xfeed follow TheOfficialX #x-news
        """
        username = username.strip('@').lower()

        headers = await self._get_auth_headers()
        if not headers:
            return await ctx.send(f"The X API Bearer Token is not set. Please use `{ctx.prefix}xfeed setapi` first.")

        # --- API Warning Block ---
        current_accounts = await self.config.guild(ctx.guild).accounts()
        # Note: The bot is currently checking every 30 mins (1,440 requests/month).
        # Given the Free Tier is limited to 100 Posts per month, this check frequency
        # is necessary to catch updates but the limit is a hard cap based on total posts retrieved.
        await ctx.send(
            "⚠️ **Warning on API Limits (Free Tier):**\n"
            "The bot is now checking every **30 minutes**.\n"
            "The X Free Tier is limited to retrieving only **100 Posts per month** in total. Once that limit is hit, the bot will stop posting updates until the next month, regardless of how many accounts you follow."
        )
        # --- End API Warning Block ---

        # 1. Get the X user ID
        await ctx.send(f"Attempting to resolve username '@{username}' to a User ID...")
        user_id = await self._fetch_user_id(username, headers)

        if not user_id:
            return await ctx.send(
                f"Could not find an X user with the username `@{username}` or the API call failed."
            )

        # 2. Store config
        new_account_data = {
            "user_id": user_id,
            "channel_id": channel.id,
            "last_id": None  # Will be set on the first successful check
        }

        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username in accounts:
                return await ctx.send(f"The account `@{username}` is already being tracked in this server.")

            accounts[username] = new_account_data

        await ctx.send(
            f"Successfully started tracking `@{username}`. Updates will be posted to {channel.mention}.\n"
            "The first post will appear after the next scheduled check (up to **30 minutes**)."
        )

    @x_updates.command(name="unfollow")
    async def unfollow_x_account(self, ctx: commands.Context, username: str):
        """
        Stops tracking an X.com account.

        Usage: [p]xfeed unfollow <username>
        """
        username = username.strip('@').lower()

        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username in accounts:
                del accounts[username]
                await ctx.send(f"Successfully stopped tracking `@{username}`.")
            else:
                await ctx.send(f"The account `@{username}` is not currently being tracked.")

    @x_updates.command(name="list")
    async def list_x_accounts(self, ctx: commands.Context):
        """Lists all X.com accounts currently being tracked in this server."""
        accounts = await self.config.guild(ctx.guild).accounts()

        if not accounts:
            return await ctx.send(
                f"No X.com accounts are currently being tracked. Use `{ctx.prefix}xfeed follow <username> <#channel>` to add one.")

        msg = ["**Currently Tracked X Accounts:**\n"]
        for username, data in accounts.items():
            channel = ctx.guild.get_channel(data['channel_id'])
            channel_name = channel.mention if channel else "#channel-deleted"

            last_id = data.get('last_id', 'None (first run pending)')

            msg.append(f"• **@{username}** → Posts to: {channel_name} (Last Post ID: {last_id})")

        # Send as a single message, or use Red's menu utility for long lists
        await ctx.send('\n'.join(msg))

    @update_check.before_loop
    async def before_update_check(self):
        """Wait until the bot is ready before starting the task."""
        await self.bot.wait_until_ready()
