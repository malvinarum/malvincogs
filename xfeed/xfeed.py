import asyncio
import logging
from datetime import datetime, timezone

import discord
from redbot.core import Config, commands, app_commands, checks
from redbot.core.bot import Red
from redbot.core.utils.menus import DEFAULT_CONTROROLS, menu
from discord.ext import tasks  # Corrected: tasks must be imported from discord.ext
import aiohttp

# Set up logging for the cog
log = logging.getLogger("red.mycogs.xfeed")

# Base URL for the X API v2
X_API_BASE = "https://api.twitter.com/2"


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

        # Start the background task loop
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
            "expansions": "author_id",
            "exclude": "replies,retweets",  # Exclude replies and retweets to focus on original content
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

    def _create_embed(self, post_data: dict, author_username: str, author_name: str):
        """Creates a rich Discord embed from the X post data."""
        tweet_id = post_data['id']
        text = post_data['text']
        created_at_str = post_data['created_at']

        # Format timestamp
        timestamp = datetime.strptime(created_at_str, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)

        # Build the embed
        embed = discord.Embed(
            title=f"New Post from @{author_username}",
            description=text,
            url=f"https://x.com/{author_username}/status/{tweet_id}",
            timestamp=timestamp,
            color=0x000000  # X's primary color is black (0x000000) or a deep blue
        )
        embed.set_author(name=f"{author_name} (@{author_username})",
                         url=f"https://x.com/{author_username}",
                         icon_url="https://i.imgur.com/k2H999N.png")  # Placeholder X logo/icon

        # Attach image/media if available (simplified check)
        if 'attachments' in post_data and 'media_keys' in post_data['attachments']:
            # Due to the complexity of getting media URLs in V2 without 'expansions',
            # we'll use a simplified image placeholder method for this example.
            # A full implementation would require media expansion in the API call.
            pass

        # Add the requested footer (Note: Discord does not support hyperlinking footer text)
        embed.set_footer(text="Xfeed by Malvinarum")
        # Add a field for the link to satisfy the "linked to" request
        embed.add_field(name="Source", value="[Malvinarum Cogs](https://github.com/malvinarum/malvincogs/)",
                        inline=False)

        return embed

    @tasks.loop(minutes=10)
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
                user_id = account_data.get("user_id")
                channel_id = account_data.get("channel_id")
                last_id = account_data.get("last_id")

                channel = guild.get_channel(channel_id)
                if not user_id or not channel:
                    log.warning(f"Skipping {username} in {guild.name}: Missing user ID or channel.")
                    continue

                log.debug(f"Checking updates for @{username} in {guild.name}...")

                # Fetch new posts
                posts_response = await self._fetch_latest_posts(user_id, last_id, headers)

                if posts_response and 'data' in posts_response:
                    new_posts = posts_response['data']
                    newest_id = last_id

                    # Find author info from includes (simplification)
                    author_name = username
                    author_username = username
                    if 'includes' in posts_response and 'users' in posts_response['includes']:
                        user_info = next(
                            (u for u in posts_response['includes']['users'] if str(u['id']) == str(user_id)), None)
                        if user_info:
                            author_name = user_info.get('name', username)
                            author_username = user_info.get('username', username)

                    # Posts are returned newest first, so we process them in reverse order
                    # to ensure the last_id update is correct and they post chronologically.
                    for post in reversed(new_posts):
                        try:
                            embed = self._create_embed(post, author_username, author_name)
                            await channel.send(embed=embed)
                            newest_id = post['id']
                        except discord.Forbidden:
                            log.error(f"Missing permissions to post to channel {channel.name} in {guild.name}.")
                            break  # Stop processing if we can't send
                        except Exception as e:
                            log.error(f"Error sending post for {username}: {e}", exc_info=True)

                    # Update the last_id only if new posts were successfully processed
                    if newest_id != last_id:
                        account_data['last_id'] = newest_id
                        await self.config.guild(guild).accounts.set_raw(username, value=account_data)
                        log.info(f"Updated last ID for @{username} to {newest_id}.")
                elif posts_response and 'meta' in posts_response and posts_response['meta'].get('result_count', 0) == 0:
                    log.debug(f"No new posts found for @{username}.")

    # --- Commands ---

    @commands.group(name="xfeed", aliases=["xupdates"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def x_updates(self, ctx: commands.Context):
        """Manage X.com account tracking and settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

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
            "The first post will appear after the next scheduled check (up to 10 minutes)."
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
