import asyncio
import logging
from datetime import datetime, timezone

import discord
from redbot.core import Config, commands, app_commands, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list, box
from discord.ext import tasks
import aiohttp

log = logging.getLogger("red.mycogs.xfeed")

X_API_BASE = "https://api.twitter.com/2"
MAX_EMBED_DESCRIPTION_LENGTH = 2000


class XFeed(commands.Cog):
    """
    XFeed: Advanced X.com (Twitter) tracking.
    Optimized for strict API limits (Free Tier Friendly-ish).
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=147385960, force_registration=True)
        self.config.register_guild(accounts={})
        self.session = aiohttp.ClientSession()
        self.update_check.start()

    def cog_unload(self):
        if self.session:
            asyncio.create_task(self.session.close())
        self.update_check.cancel()

    async def _get_auth_headers(self):
        tokens = await self.bot.get_shared_api_tokens("x")
        bearer_token = tokens.get("bearer_token")
        if not bearer_token:
            return None
        return {"Authorization": f"Bearer {bearer_token}"}

    async def _fetch_user_id(self, username: str, headers: dict):
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
        # Note: On Free Tier, some fields/expansions might be restricted.
        # We ask for the basics.
        params = {
            "tweet.fields": "created_at,author_id,text",
            # Removed metrics/attachments for safety if needed, but trying to keep them
            "max_results": 5,
            "exclude": "replies,retweets",
        }
        # Try adding expansions if the tier permits, otherwise fallback
        # "expansions": "author_id,attachments.media_keys",
        # "media.fields": "url,type,preview_image_url",

        if last_id:
            params['since_id'] = last_id

        try:
            url = f"{X_API_BASE}/users/{user_id}/tweets"
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    log.warning("X API Rate Limit Hit! Backing off.")
                    return "RATE_LIMIT"
                elif resp.status in (401, 403):
                    log.error(f"X API Forbidden ({resp.status}). Check tier limits.")
                    return None
                else:
                    log.warning(f"X API request failed. Status: {resp.status}")
                    return None
        except Exception as e:
            log.error(f"Error fetching posts: {e}", exc_info=True)
            return None

    def _check_filters(self, text: str, include: list, exclude: list) -> bool:
        text_lower = text.lower()
        if exclude:
            if any(kw.lower() in text_lower for kw in exclude):
                return False
        if include:
            if not any(kw.lower() in text_lower for kw in include):
                return False
        return True

    async def _post_updates(self, guild: discord.Guild, username: str, account_data: dict, headers: dict):
        user_id = account_data.get("user_id")
        channel_id = account_data.get("channel_id")
        last_id = account_data.get("last_id")

        include = account_data.get("include_keywords", [])
        exclude = account_data.get("exclude_keywords", [])
        role_id = account_data.get("role_id")
        embed_color = account_data.get("embed_color", 0x000000)

        channel = guild.get_channel(channel_id)
        if not user_id or not channel:
            return False

        posts_response = await self._fetch_latest_posts(user_id, last_id, headers)

        if posts_response == "RATE_LIMIT":
            return False

        if posts_response and 'data' in posts_response:
            new_posts = posts_response['data']
            newest_id = last_id

            # Process oldest to newest
            for post in reversed(new_posts):
                text = post['text']

                if not self._check_filters(text, include, exclude):
                    newest_id = post['id']
                    continue

                try:
                    # Simple Embed for stability
                    tweet_id = post['id']
                    post_url = f"https://x.com/{username}/status/{tweet_id}"

                    if len(text) > MAX_EMBED_DESCRIPTION_LENGTH:
                        text = text[:MAX_EMBED_DESCRIPTION_LENGTH] + f" **...** [[Read more]]({post_url})"

                    embed = discord.Embed(
                        description=text,
                        url=post_url,
                        color=embed_color,
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.set_author(name=f"@{username}", url=f"https://x.com/{username}",
                                     icon_url="https://upload.wikimedia.org/wikipedia/commons/5/5a/X_icon_2.svg")
                    embed.set_footer(text="XFeed by Malvinarum")

                    content = None
                    if role_id:
                        role = guild.get_role(role_id)
                        if role: content = f"{role.mention} New tweet!"

                    await channel.send(content=content, embed=embed)
                    newest_id = post['id']
                except Exception as e:
                    log.error(f"Error sending tweet: {e}")

            if newest_id != last_id:
                account_data['last_id'] = newest_id
                await self.config.guild(guild).accounts.set_raw(username, value=account_data)
                return True

        return False

    # SLOW DOWN: Check every 6 hours to save the 100 monthly calls
    @tasks.loop(hours=6)
    async def update_check(self):
        await self.bot.wait_until_ready()
        headers = await self._get_auth_headers()
        if not headers: return

        all_guild_data = await self.config.all_guilds()
        for guild_id, guild_data in all_guild_data.items():
            guild = self.bot.get_guild(guild_id)
            if not guild: continue

            accounts = guild_data.get("accounts", {}).copy()
            for username, account_data in accounts.items():
                await self._post_updates(guild, username, account_data, headers)
                # Sleep between accounts to avoid burst limits
                await asyncio.sleep(5)

    @update_check.before_loop
    async def before_update_check(self):
        await self.bot.wait_until_ready()

    # --- COMMANDS ---

    @commands.group(name="xfeed", aliases=["xupdates"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def x_updates(self, ctx: commands.Context):
        """Manage X.com account tracking."""
        pass

    @x_updates.command(name="follow")
    async def follow(self, ctx: commands.Context, username: str, channel: discord.TextChannel):
        """Track an account."""
        username = username.strip('@').lower()
        headers = await self._get_auth_headers()

        if not headers:
            return await ctx.send("⚠️ API Key missing.")

        # Warning about the limits
        await ctx.send(f"🔍 Resolving `@{username}`... (Note: Free Tier is strictly limited)")
        user_id = await self._fetch_user_id(username, headers)
        if not user_id:
            return await ctx.send("❌ User not found or API Error.")

        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username in accounts:
                return await ctx.send("Already tracking.")

            accounts[username] = {
                "user_id": user_id,
                "channel_id": channel.id,
                "last_id": None,
                "role_id": None,
                "include_keywords": [],
                "exclude_keywords": [],
                "embed_color": 0x000000
            }
        await ctx.send(f"✅ Now tracking `@{username}`. Checks run every 6 hours.")

    @x_updates.command(name="unfollow")
    async def unfollow(self, ctx: commands.Context, username: str):
        """Stop tracking."""
        username = username.strip('@').lower()
        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username in accounts:
                del accounts[username]
                await ctx.send(f"🗑️ Stopped tracking `@{username}`.")
            else:
                await ctx.send("Not found.")

    @x_updates.command(name="role")
    async def set_role(self, ctx: commands.Context, username: str, role: discord.Role):
        """Set notification role."""
        username = username.strip('@').lower()
        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username not in accounts: return await ctx.send("Account not found.")
            accounts[username]["role_id"] = role.id
        await ctx.send(f"🔔 Role updated for `@{username}`.")

    @x_updates.group(name="filter")
    async def x_filter(self, ctx: commands.Context):
        """Manage filters."""
        pass

    @x_filter.command(name="include")
    async def inc_filter(self, ctx: commands.Context, username: str, keyword: str):
        """Add include filter."""
        username = username.strip('@').lower()
        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username not in accounts: return await ctx.send("Account not found.")
            if keyword not in accounts[username].get("include_keywords", []):
                accounts[username].setdefault("include_keywords", []).append(keyword)
        await ctx.send(f"➕ Include added: `{keyword}`")

    @x_filter.command(name="exclude")
    async def exc_filter(self, ctx: commands.Context, username: str, keyword: str):
        """Add exclude filter."""
        username = username.strip('@').lower()
        async with self.config.guild(ctx.guild).accounts() as accounts:
            if username not in accounts: return await ctx.send("Account not found.")
            if keyword not in accounts[username].get("exclude_keywords", []):
                accounts[username].setdefault("exclude_keywords", []).append(keyword)
        await ctx.send(f"⛔ Exclude added: `{keyword}`")

    @x_updates.command(name="list")
    async def list_accounts(self, ctx: commands.Context):
        """List accounts."""
        accounts = await self.config.guild(ctx.guild).accounts()
        if not accounts: return await ctx.send("No accounts tracked.")

        msg = ""
        for user, data in accounts.items():
            ch = f"<#{data['channel_id']}>"
            inc = humanize_list(data.get('include_keywords', [])) or "All"

            msg += f"**@{user}** in {ch}\n✅ In: {inc}\n\n"

        await ctx.send(box(msg, lang="yaml"))

    @x_updates.command(name="check")
    async def manual_check(self, ctx: commands.Context, username: str):
        """Force check (Use sparingly!)."""
        username = username.strip('@').lower()
        headers = await self._get_auth_headers()
        if not headers: return await ctx.send("No API Key.")

        data = await self.config.guild(ctx.guild).accounts.get_raw(username, default=None)
        if not data: return await ctx.send("Not tracking.")

        await ctx.send("Checking...")
        res = await self._post_updates(ctx.guild, username, data, headers)
        await ctx.send(f"Done. New posts: {res}")


async def setup(bot):
    await bot.add_cog(XFeed(bot))