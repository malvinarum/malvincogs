from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from datetime import timedelta
from operator import itemgetter
from typing import Union

import discord
from discord.ext import tasks
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.antispam import AntiSpam
from redbot.core.utils.chat_formatting import box

# Relative import
from .config_holder import ConfigHolder

log = logging.getLogger("red.drapercogs.dynamic_channels")


class DynamicChannels(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = ConfigHolder.DynamicChannels
        self.antispam: dict[int, dict[int, AntiSpam]] = {}
        self.config_cache = defaultdict(dict)
        self.cleanup_task = self.clean_up_dynamic_channels.start()

    async def cog_unload(self):
        self.cleanup_task.cancel()

    @commands.admin_or_permissions()
    @commands.guild_only()
    @commands.bot_has_permissions(manage_channels=True, manage_roles=True)
    @commands.group(name="dynamicset")
    async def _dynamic_set(self, ctx: commands.Context):
        """Configure dynamic voice channels."""

    @_dynamic_set.command(name="blacklistadd")
    async def _dynamic_blacklist_add(self, ctx: commands.Context, *users: discord.Member):
        """Disallow a user from using the custom channels."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            current_list = set(blacklist.get("blacklist", []))
            current_list.update([u.id for u in users])
            blacklist["blacklist"] = list(current_list)

        self.config_cache[ctx.guild.id]["blacklist"] = await self.config.guild(ctx.guild).blacklist()
        await ctx.tick()

    @_dynamic_set.command(name="blacklistremove")
    async def _dynamic_blacklist_remove(self, ctx: commands.Context, *users: discord.Member):
        """Remove users from the blacklist."""
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            blacklisted = blacklist.get("blacklist", [])
            to_remove = [m.id for m in users]
            blacklist["blacklist"] = [u for u in blacklisted if u not in to_remove]

        self.config_cache[ctx.guild.id]["blacklist"] = await self.config.guild(ctx.guild).blacklist()
        await ctx.tick()

    @_dynamic_set.command(name="add")
    async def _dynamic_add(self, ctx: commands.Context, category_id: str, size: Union[int, None] = 0, *,
                           room_name: str):
        """Whitelist a category to have multiple types of Dynamic voice channels."""
        valid_categories = {
            str(category.id): category.name
            for category in ctx.guild.categories
            if category
        }

        if valid_categories and category_id not in valid_categories:
            await ctx.send(f"ERROR: {category_id} is not a valid category ID.")
            await ctx.send(box(json.dumps(valid_categories, indent=2), lang="json"))
            return
        elif not valid_categories:
            await ctx.send("ERROR: No valid categories found.")
            return

        category = ctx.guild.get_channel(int(category_id))
        if not category:
            return await ctx.send("Category not found.")

        # Create first channel
        await ctx.guild.create_voice_channel(
            user_limit=size,
            name=room_name.format(number=1),
            reason=f"Initializing dynamic category {category.name}",
            category=category,
            bitrate=int(ctx.guild.bitrate_limit),
        )

        async with self.config.guild(ctx.guild).dynamic_channels() as whitelist:
            whitelist.update({category_id: [(room_name, size)]})
            await ctx.send(f"Added {category_id}. Rooms: {room_name}, Size: {size}")

        self.config_cache[ctx.guild.id]["dynamic_channels"] = await self.config.guild(ctx.guild).dynamic_channels()

    @_dynamic_set.command(name="append")
    async def _dynamic_append(self, ctx, category_id: str, size: Union[int, None] = 0, *, room_name: str):
        """Add another dynamic rule to an existing category."""
        whitelist = await self.config.guild(ctx.guild).dynamic_channels()

        if category_id not in whitelist:
            return await ctx.send(f"Category {category_id} is not whitelisted. Use `add` first.")

        category = ctx.guild.get_channel(int(category_id))
        if not category: return

        await ctx.guild.create_voice_channel(
            user_limit=size,
            name=room_name.format(number=1),
            reason=f"Appending dynamic rule to {category.name}",
            category=category,
            bitrate=ctx.guild.bitrate_limit,
        )

        async with self.config.guild(ctx.guild).dynamic_channels() as w:
            w[category_id].append((room_name, size))
            await ctx.send(f"Appended rule to {category_id}.")

        self.config_cache[ctx.guild.id]["dynamic_channels"] = await self.config.guild(ctx.guild).dynamic_channels()

    @_dynamic_set.command(name="remove")
    async def _dynamic_remove(self, ctx: commands.Context, category_id: str):
        """Remove the special category from whitelist."""
        async with self.config.guild(ctx.guild).dynamic_channels() as whitelist:
            if category_id in whitelist:
                del whitelist[category_id]
                await ctx.send(f"Removed {category_id}.")
            else:
                await ctx.send("Not whitelisted.")
        self.config_cache[ctx.guild.id]["dynamic_channels"] = await self.config.guild(ctx.guild).dynamic_channels()

    # Listeners largely the same, just checking context variables
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        guild = member.guild
        if member.bot or not guild.me.guild_permissions.manage_channels: return

        # Load Caches
        if "dynamic_channels" not in self.config_cache[guild.id]:
            self.config_cache[guild.id]["dynamic_channels"] = await self.config.guild(guild).dynamic_channels()
        whitelist = self.config_cache[guild.id]["dynamic_channels"]
        if not whitelist: return

        if guild.id not in self.antispam: self.antispam[guild.id] = {}
        if member.id not in self.antispam[guild.id]:
            self.antispam[guild.id][member.id] = AntiSpam([(timedelta(seconds=60), 2)])

        # JOIN EVENT
        if after.channel and after.channel.category and str(after.channel.category.id) in whitelist:
            cat_id = str(after.channel.category.id)
            category = after.channel.category
            configs = whitelist[cat_id]

            # Check if this JOIN requires a NEW ROOM
            # Iterate through configs for this category (could be multiple types)
            for room_name, room_size in configs:
                base_name = room_name.split(" -")[0] + " -"

                # Count empty channels of this type
                empty_channels = [c for c in category.voice_channels
                                  if base_name in c.name and len(c.members) == 0]

                # If NO empty channels of this type exist, create one
                if not empty_channels and not self.antispam[guild.id][member.id].spammy:
                    self.antispam[guild.id][member.id].stamp()

                    # Count total channels of this type to number the new one
                    total_channels = [c for c in category.voice_channels if base_name in c.name]
                    new_num = len(total_channels) + 1

                    try:
                        new_chan = await guild.create_voice_channel(
                            name=room_name.format(number=new_num),
                            user_limit=room_size,
                            category=category,
                            reason="Dynamic expansion",
                            bitrate=int(guild.bitrate_limit)
                        )
                        # Register in DB so we know to clean it later
                        async with self.config.guild(guild).user_created_voice_channels() as db:
                            db[str(new_chan.id)] = new_chan.name
                    except Exception as e:
                        log.error(f"Dynamic create fail: {e}")

        # LEAVE EVENT / CLEANUP
        # We check the category the user LEFT (before.channel)
        if before.channel and before.channel.category and str(before.channel.category.id) in whitelist:
            cat_id = str(before.channel.category.id)
            category = before.channel.category
            configs = whitelist[cat_id]

            for room_name, _ in configs:
                base_name = room_name.split(" -")[0] + " -"
                # Find all empty channels of this type
                empty_channels = [c for c in category.voice_channels
                                  if base_name in c.name and len(c.members) == 0]

                # Sort by position to keep the "first" one, delete excess
                empty_channels.sort(key=lambda c: c.position)

                if len(empty_channels) > 1:
                    # Keep the first one, delete the rest
                    for channel in empty_channels[1:]:
                        try:
                            await channel.delete(reason="Dynamic contraction")
                            async with self.config.guild(guild).user_created_voice_channels() as db:
                                if str(channel.id) in db: del db[str(channel.id)]
                        except:
                            pass

    @tasks.loop(seconds=60)
    async def clean_up_dynamic_channels(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            if not guild.me.guild_permissions.manage_channels: continue

            # This loop ensures we don't have stray empty channels if events miss
            # Logic similar to event-based cleanup but broader
            # We just verify if tracked dynamic channels are empty and > 1 exists per type
            # For simplicity, we rely on the event mostly, but here we clean up broken refs in DB

            tracked = await self.config.guild(guild).user_created_voice_channels()
            to_remove = []
            for cid in tracked:
                chan = guild.get_channel(int(cid))
                if not chan:
                    to_remove.append(cid)

            if to_remove:
                async with self.config.guild(guild).user_created_voice_channels() as db:
                    for cid in to_remove: del db[cid]