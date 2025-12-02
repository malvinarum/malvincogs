from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Union

import discord
from discord.ext import tasks
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.antispam import AntiSpam
from redbot.core.utils.chat_formatting import box

# Relative import
from .config_holder import ConfigHolder

logger = logging.getLogger("red.drapercogs.button_channel")

admin_permissions = discord.PermissionOverwrite(
    speak=True, connect=True, mute_members=True, deafen_members=True,
    move_members=True, priority_speaker=True, manage_channels=True,
    use_voice_activation=True, read_messages=True,
)

muted_permissions = discord.PermissionOverwrite(
    speak=False, connect=False, mute_members=False, deafen_members=False,
    move_members=False, priority_speaker=False, manage_channels=False,
    use_voice_activation=False, read_messages=False,
)

creator_permissions = discord.PermissionOverwrite(
    speak=True, connect=True, mute_members=True, priority_speaker=True,
    manage_channels=True, use_voice_activation=True, read_messages=True,
)

default_permission = discord.PermissionOverwrite(
    speak=True, connect=True, use_voice_activation=True, read_messages=True
)


class CustomChannels(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = ConfigHolder.CustomChannels
        self.antispam: dict[int, dict[int, AntiSpam]] = {}
        self.config_cache = defaultdict(dict)
        self.cleanup_task = self.clean_up_custom_channels.start()

    async def cog_unload(self):
        self.cleanup_task.cancel()

    @commands.admin_or_permissions()
    @commands.guild_only()
    @commands.group(name="buttonset")
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    async def _button_set(self, ctx: commands.Context):
        """Configure button voice channel."""

    @_button_set.command(name="blacklistadd")
    async def _button_blacklist_add(self, ctx: commands.Context, *users: discord.Member):
        """Disallow a user from using the custom channels."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            blacklisted_users = set(blacklist.get("blacklist", []))
            blacklisted_users.update([u.id for u in users])
            blacklist["blacklist"] = list(blacklisted_users)

        self.config_cache[ctx.guild.id]["blacklist"] = await self.config.guild(ctx.guild).blacklist()
        await ctx.tick()

    @_button_set.command(name="blacklistremove")
    async def _button_blacklist_remove(self, ctx: commands.Context, *users: discord.Member):
        """Remove users from the blacklist."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            blacklisted_users = blacklist.get("blacklist", [])
            to_remove = [m.id for m in users]
            blacklist["blacklist"] = [u for u in blacklisted_users if u not in to_remove]

        self.config_cache[ctx.guild.id]["blacklist"] = await self.config.guild(ctx.guild).blacklist()
        await ctx.tick()

    @_button_set.command(name="add")
    async def _button_add(self, ctx: commands.Context, category_id: str, room_id: int):
        """Whitelist a category and Channel to become a button."""
        dynamic_category_whitelist = await self.config.guild(ctx.guild).category_with_button.get_raw()

        valid_categories = {
            str(category.id): category.name
            for category in ctx.guild.categories
            if category and str(category.id) not in dynamic_category_whitelist
        }

        if valid_categories and category_id not in valid_categories:
            await ctx.send(f"ERROR: {category_id} is not a valid category ID.")
            await ctx.send(box(json.dumps(valid_categories, indent=2), lang="json"))
            return
        elif not valid_categories:
            await ctx.send(f"ERROR: No valid categories in {ctx.guild.name}")
            return

        is_valid_voice_room = ctx.guild.get_channel(room_id)
        if not isinstance(is_valid_voice_room, discord.VoiceChannel):
            await ctx.send(f"ERROR: Room {room_id} is not a valid voice channel")
            return

        async with self.config.guild(ctx.guild).category_with_button() as whitelist:
            whitelist.update({category_id: room_id})
            await ctx.send(f"Added {category_id} to the whitelist\nButton Room ID: {room_id}")

    @_button_set.command(name="remove")
    async def _button_remove(self, ctx: commands.Context, category_id: str):
        """Removes category and voice channel button from whitelist."""
        async with self.config.guild(ctx.guild).category_with_button() as whitelist:
            if category_id in whitelist:
                del whitelist[category_id]
                await ctx.send(f"Removed {category_id} from the whitelist")
            else:
                await ctx.send(f"Error: {category_id} is not a whitelisted category")

    @_button_set.group(name="role")
    async def _button_roles(self, ctx: commands.Context):
        """Whitelist roles to have special permission on user created rooms."""

    @_button_roles.command(name="manager")
    async def _button_roles_manager(self, ctx: commands.Context, *roles: discord.Role):
        """Whitelist roles to have manager permission on user created rooms."""
        roles_ids = [role.id for role in roles]
        await self.config.guild(ctx.guild).user_created_voice_channels_bypass_roles.set(roles_ids)
        await ctx.tick()

    @_button_roles.command(name="muted")
    async def _button_roles_muted(self, ctx: commands.Context, *roles: discord.Role):
        """Whitelist roles to have muted permission on user created rooms."""
        roles_ids = [role.id for role in roles]
        await self.config.guild(ctx.guild).mute_roles.set(roles_ids)
        await ctx.tick()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: Union[
        discord.VoiceChannel, discord.CategoryChannel, discord.TextChannel]):
        if not isinstance(channel, discord.VoiceChannel): return
        if not channel.guild.me.guild_permissions.manage_channels: return

        cat_id = str(channel.category.id) if channel.category else None
        whitelist = await self.config.guild(channel.guild).category_with_button()

        if cat_id and cat_id in whitelist:
            async with self.config.guild(channel.guild).custom_channels() as channel_data:
                if str(channel.id) in channel_data:
                    del channel_data[str(channel.id)]

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: Union[
        discord.VoiceChannel, discord.CategoryChannel, discord.TextChannel]):
        if not isinstance(channel, discord.VoiceChannel): return
        if not channel.guild.me.guild_permissions.manage_channels: return

        cat_id = str(channel.category.id) if channel.category else None
        whitelist = await self.config.guild(channel.guild).category_with_button()

        if cat_id and cat_id in whitelist:
            async with self.config.guild(channel.guild).custom_channels() as channel_data:
                channel_data[str(channel.id)] = channel.name

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        guild = member.guild
        if not guild.me.guild_permissions.manage_channels: return

        if guild.id not in self.antispam:
            self.antispam[guild.id] = {}

        # Cache config checks
        if "blacklist" not in self.config_cache[guild.id]:
            self.config_cache[guild.id]["blacklist"] = await self.config.guild(guild).blacklist()
        if member.id in self.config_cache[guild.id].get("blacklist", {}).get("blacklist", []):
            return

        if "category_with_button" not in self.config_cache[guild.id]:
            self.config_cache[guild.id]["category_with_button"] = await self.config.guild(guild).category_with_button()

        whitelist = self.config_cache[guild.id]["category_with_button"]

        # AntiSpam Init
        if member.id not in self.antispam[guild.id]:
            self.antispam[guild.id][member.id] = AntiSpam([(timedelta(seconds=600), 1)])

        # Initialize user_created_voice_channels cache if missing
        if "user_created_voice_channels" not in self.config_cache[guild.id]:
            self.config_cache[guild.id]["user_created_voice_channels"] = await self.config.guild(
                guild).user_created_voice_channels()

        # Channel Creation Logic
        if (after.channel and after.channel.category
                and str(after.channel.category.id) in whitelist
                and after.channel.id == whitelist[str(after.channel.category.id)]):

            if self.antispam[guild.id][member.id].spammy:
                return  # Rate limit

            self.antispam[guild.id][member.id].stamp()

            overwrites = await self._get_overrides(after.channel, member)

            try:
                created_channel = await guild.create_voice_channel(
                    name=f"Rename me - {member.display_name}",
                    category=after.channel.category,
                    overwrites=overwrites,
                    reason=f"{member.display_name} created a custom voice room",
                    bitrate=guild.bitrate_limit
                )

                await member.move_to(created_channel, reason="Moving to custom room")

                async with self.config.guild(guild).user_created_voice_channels() as user_voice:
                    user_voice[str(created_channel.id)] = created_channel.id

                # FIX: Update cache immediately so deletion works if they leave quickly
                self.config_cache[guild.id]["user_created_voice_channels"][str(created_channel.id)] = created_channel.id

                async with self.config.member(member).currentRooms() as user_rooms:
                    user_rooms[str(created_channel.id)] = created_channel.id

            except Exception as e:
                logger.error(f"Failed to create channel: {e}")

        # Cleanup Trigger
        user_created_channels = self.config_cache[guild.id]["user_created_voice_channels"]
        await self.channel_cleaner(before, guild, user_created_channels)

    async def _get_overrides(self, channel, owner):
        guild = channel.guild
        overwrites = {
            guild.default_role: default_permission,
            owner: creator_permissions
        }

        bypass_roles = await self.config.guild(guild).user_created_voice_channels_bypass_roles()
        for rid in bypass_roles:
            if role := guild.get_role(rid):
                overwrites[role] = admin_permissions

        mute_roles = await self.config.guild(guild).mute_roles()
        for rid in mute_roles:
            if role := guild.get_role(rid):
                overwrites[role] = muted_permissions
        return overwrites

    async def channel_cleaner(self, before, guild, user_created_channels):
        if before.channel and str(before.channel.id) in user_created_channels:
            channel = before.channel
            if not channel.members:
                try:
                    await channel.delete(reason="Custom channel empty")

                    async with self.config.guild(guild).user_created_voice_channels() as db:
                        if str(channel.id) in db:
                            del db[str(channel.id)]

                    # Update local cache
                    if str(channel.id) in self.config_cache[guild.id].get("user_created_voice_channels", {}):
                        del self.config_cache[guild.id]["user_created_voice_channels"][str(channel.id)]

                except discord.NotFound:
                    # Already gone, just clean cache/db
                    async with self.config.guild(guild).user_created_voice_channels() as db:
                        if str(channel.id) in db: del db[str(channel.id)]
                    if str(channel.id) in self.config_cache[guild.id].get("user_created_voice_channels", {}):
                        del self.config_cache[guild.id]["user_created_voice_channels"][str(channel.id)]

                except Exception as e:
                    logger.error(f"Error deleting channel {channel.id}: {e}")

    @tasks.loop(hours=2)
    async def clean_up_custom_channels(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            if not guild.me.guild_permissions.manage_channels: continue

            data = await self.config.guild(guild).user_created_voice_channels()
            to_delete = []

            for cid in data.keys():
                channel = guild.get_channel(int(cid))
                if not channel or not channel.members:
                    if channel:
                        try:
                            await channel.delete(reason="Cleanup task")
                        except:
                            pass
                    to_delete.append(cid)

            if to_delete:
                async with self.config.guild(guild).user_created_voice_channels() as db:
                    for cid in to_delete:
                        if cid in db: del db[cid]