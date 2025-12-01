import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime
from operator import itemgetter
from typing import Union

import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu, start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate
from discord.ext import tasks

# Relative imports to keep the bundle self-contained
from .config_holder import ConfigHolder
from .constants import CONTINENT_DATA
from .utilities import (
    account_adder,
    add_username_hyperlink,
    get_all_by_platform,
    get_date_string,
    get_date_time,
    get_member_activity,
    get_role_named,
    get_supported_platforms,
    has_a_profile,
    update_member_atomically,
    update_profile,
)

log = logging.getLogger("red.drapercogs.profile")


class GamingProfile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Removed self.session = aiohttp.ClientSession() - use self.bot.session instead
        self.profileConfig = ConfigHolder.GamingProfile
        self.config = ConfigHolder.AccountManager
        self._cache = {}
        self.config_cache = defaultdict(dict)

        # Register the task
        self._save_task = self.save_to_config_loop.start()

    async def cog_unload(self):
        self._save_task.cancel()
        # Force a final save before unload
        await self._save_cache_to_db()

    @tasks.loop(seconds=60)
    async def save_to_config_loop(self):
        await self._save_cache_to_db()

    @save_to_config_loop.before_loop
    async def before_save_loop(self):
        await self.bot.wait_until_ready()

    async def _save_cache_to_db(self):
        """Saves cached 'seen' data to Config to reduce DB writes."""
        if not self._cache:
            return

        users_data = self._cache.copy()
        self._cache = {}  # Clear cache immediately so new data can accumulate

        # We can't access Config.USER directly from here easily without the group,
        # so we iterate. Ideally, we should register a default for this.
        for member_id, seen_timestamp in users_data.items():
            async with self.profileConfig.user_from_id(member_id).all() as user_data:
                user_data["seen"] = seen_timestamp

    @commands.group(name="gprofile")
    @commands.bot_has_permissions(embed_links=True, add_reactions=True)
    async def _profile(self, ctx: commands.Context):
        """Managers a user profile"""

    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @_profile.command(name="setup")
    async def _profile_setup(self, ctx: commands.Context):
        """Set up the environment needed by creating the required roles."""
        countries = list(CONTINENT_DATA.values())
        roles = countries + ["No Profile", "Has Profile"]
        existing_roles = [r.name for r in ctx.guild.roles]

        created_count = 0
        for role in roles:
            if role not in existing_roles:
                await ctx.guild.create_role(
                    name=role, mentionable=False, hoist=False, reason="GamingProfile Setup"
                )
                created_count += 1
        await ctx.send(f"✅ Setup complete. Created {created_count} missing roles.")

    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @_profile.command(name="rolemanager")
    async def _profile_role_management(self, ctx: commands.Context):
        """Toggle whether to manage roles automatically."""
        async with self.profileConfig.guild(ctx.guild).all() as guild_data:
            # Handle case where key doesn't exist yet
            current_role_management = guild_data.get("role_management", False)
            guild_data["role_management"] = not current_role_management
            self.config_cache[ctx.guild.id] = not current_role_management

        if not current_role_management:
            await ctx.send(
                f"✅ Role management **Enabled**. Run `{ctx.clean_prefix}gprofile setup` to ensure roles exist."
            )
        else:
            await ctx.send("❌ Role management **Disabled**.")

    @_profile.command(name="create", aliases=["make"])
    async def _profile_create(self, ctx: commands.Context):
        """Creates and sets up or updates an existing profile"""
        author = ctx.author
        user_data = {
            "country": None,
            "identifier": author.id,
            "zone": None,
            "timezone": None,
            "language": None,
        }

        try:
            await ctx.author.send(
                "Creating your profile...\nLet's continue here to avoid spam."
            )
            exists_msg = await ctx.author.send("Do you want to setup your profile now?")
            start_adding_reactions(exists_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(exists_msg, ctx.author)
            await self.bot.wait_for("reaction_add", check=pred, timeout=60)
        except discord.Forbidden:
            return await ctx.send(f"❌ I can't DM you, {ctx.author.mention}. Please enable DMs.")
        except asyncio.TimeoutError:
            return await ctx.author.send("Timed out.")

        if pred.result:
            # Note: account_adder and update_profile are in utilities.py.
            # We assume they work, but they might need the bot.session passed if they do web requests.
            # Currently they seem to use bot.wait_for, which is fine.

            new_user_data = await update_profile(self.bot, user_data, author)
            accounts = await account_adder(self.bot, author)

            async with self.profileConfig.user(author).all() as user_data:
                user_data.update(new_user_data)

            if accounts:
                async with self.config.user(author).account() as services:
                    services.update(accounts)

            # Handle Roles
            await self._handle_roles(ctx, author, new_user_data)
            await author.send("✅ Profile creation complete!")

    async def _handle_roles(self, ctx, author, user_data):
        """Helper to handle role updates during create/update."""
        if not getattr(author, "guild", None):
            return

        is_managed = await self.profileConfig.guild(author.guild).role_management()
        if not is_managed or not author.guild.me.guild_permissions.manage_roles:
            return

        role_to_add = []
        role_to_remove = []

        doesnt_have_profile_role = get_role_named(ctx.guild, "No Profile")
        has_profile_role = get_role_named(ctx.guild, "Has Profile")
        continent_role_name = user_data.get("zone")

        current_role_names = [role.name for role in author.roles]

        if has_profile_role:
            role_to_add.append(has_profile_role)

        if continent_role_name and continent_role_name not in current_role_names:
            if role := get_role_named(author.guild, continent_role_name):
                role_to_add.append(role)

        # Cleanup old zone roles
        valid_zones = list(CONTINENT_DATA.values())
        for r_name in valid_zones:
            if r_name in current_role_names and r_name != continent_role_name:
                if old_role := get_role_named(author.guild, r_name):
                    role_to_remove.append(old_role)

        if doesnt_have_profile_role:
            role_to_remove.append(doesnt_have_profile_role)

        await update_member_atomically(
            ctx=ctx, member=author, give=role_to_add, remove=role_to_remove
        )

    @_profile.command(name="update")
    async def _profile_update(self, ctx: commands.Context):
        """Updates an existing profile"""
        author = ctx.author
        user = {"country": None, "timezone": None, "language": None, "zone": None}
        try:
            await ctx.author.send("Updating your profile...")
        except discord.Forbidden:
            return await ctx.send(f"❌ I can't DM you, {ctx.author.mention}.")

        user = await update_profile(self.bot, user, author)
        async with self.profileConfig.user(author).all() as user_data:
            user_data.update(user)

        accounts = await account_adder(self.bot, author)
        if accounts:
            async with self.config.user(author).account() as services:
                services.update(accounts)

        await self._handle_roles(ctx, author, user)
        await ctx.author.send("✅ Profile updated.")

    @_profile.command(name="show", aliases=["display", "get"])
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True, add_reactions=True)
    async def _profile_show(self, ctx: commands.Context, *member: discord.Member):
        """Shows profiles for all members who are specified"""
        members = member or [ctx.author]
        embed_list = []
        members = list(set(members))  # unique

        for m in members:
            if m is None: continue
            embed = await self.get_member_profile(ctx, m)
            if embed:
                embed_list.append(embed)

        if embed_list:
            await menu(ctx, embed_list, DEFAULT_CONTROLS, timeout=60)
        else:
            await ctx.send("No profiles found for the specified users.")

    @_profile.command(name="delete", aliases=["purge", "remove"])
    async def _profile_delete(self, ctx: commands.Context):
        """Deletes your profile permanently"""
        try:
            exists_msg = await ctx.author.send(
                "⚠️ **This cannot be undone.** Do you want to delete your profile? (y/n)"
            )
            start_adding_reactions(exists_msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(exists_msg, ctx.author)
            await self.bot.wait_for("reaction_add", check=pred, timeout=60)
        except discord.Forbidden:
            return await ctx.send(f"I can't DM you, {ctx.author.mention}")
        except asyncio.TimeoutError:
            return await ctx.author.send("Timed out.")

        if pred.result:
            await self.profileConfig.user(ctx.author).clear()
            await self.config.user(ctx.author).clear()
            await ctx.author.send(
                f"🗑️ Profile deleted. Use `{ctx.prefix}gprofile create` to start over."
            )
        else:
            await ctx.author.send("Cancelled.")

    # --- Listeners ---

    @commands.Cog.listener()
    async def on_message_without_command(self, message):
        if not message.author.bot and isinstance(message.author, discord.Member):
            self._cache[message.author.id] = int(time.time())

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        if not user.bot and isinstance(user, discord.Member):
            self._cache[user.id] = int(time.time())

    # Helper function to generate the profile Embed
    async def get_member_profile(self, ctx: commands.Context, member: discord.Member):
        data = await self.profileConfig.user(member).get_raw()

        # Check if they actually have a profile (identifier check)
        if not data.get("identifier"):
            if ctx.author == member:
                await ctx.send(f"You don't have a profile! Use `{ctx.prefix}gprofile create`.")
            else:
                await ctx.send(f"{member.display_name} doesn't have a profile.")
            return None

        # Determine Last Seen
        last_seen_ts = self._cache.get(member.id) or data.get("seen")
        last_seen_text = "Unknown"
        if last_seen_ts:
            dt = get_date_time(last_seen_ts)
            last_seen_text = get_date_string(dt)

        description_parts = []

        # Activity
        if activity := get_member_activity(member):
            description_parts.append(f"**{activity}**")

        # Basic Info
        keys_to_show = ["country", "language", "timezone"]
        for key in keys_to_show:
            if val := data.get(key):
                description_parts.append(f"**{key.title()}:** {val}")

        embed = discord.Embed(
            title=f"{member.display_name}'s Profile",
            description="\n".join(description_parts),
            color=member.color
        )

        # Services / Accounts
        accounts = await self.config.user(member).account()
        if accounts:
            # Filter out empty accounts
            valid_accounts = {k: v for k, v in accounts.items() if v and v != "None"}

            if valid_accounts:
                platforms = await get_supported_platforms(lists=False)
                lines = []
                steamid = valid_accounts.get("steamid")
                spotifyid = valid_accounts.get("spotifyid")  # Not usually public but handling logic

                for service, username in valid_accounts.items():
                    # Skip internal IDs in list
                    if service in ["steamid", "spotifyid"]: continue

                    platform_name = platforms.get(service, {}).get("name", service.title())

                    # Logic for hyperlinks
                    link_id = steamid if service == "steam" else None

                    display_text = add_username_hyperlink(platform_name, username, _id=link_id)
                    lines.append(f"**{platform_name}:** {display_text}")

                if lines:
                    embed.add_field(name="Connected Accounts", value="\n".join(lines), inline=False)

        footer_text = f"Last seen: {last_seen_text}"
        if last_seen_text.startswith("Today") and member.status != discord.Status.offline:
            footer_text = "Currently Online"

        embed.set_footer(text=footer_text)
        embed.set_thumbnail(url=member.display_avatar.url)

        return embed