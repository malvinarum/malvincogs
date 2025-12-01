import logging
import asyncio
from copy import copy
from operator import itemgetter

import discord
import regex

from redbot.core import commands
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu

# FIX: Changed import from 'draperbundle' to '.' (relative) or 'drapercogs'
from .config_holder import ConfigHolder
from .constants import REPLACE_BRACKER
from .utilities import (
    get_all_user_rigs,
    get_date_string,
    get_date_time,
    get_member_activity,
)

log = logging.getLogger("red.drapercogs.pc_specs")


class PCSpecs(commands.Cog):
    """
    Manage and display PC Specifications for users.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = ConfigHolder.PCSpecs

    @commands.group()
    async def specs(self, ctx: commands.Context):
        """Rig management"""
        pass

    @specs.group(name="show", invoke_without_command=True)
    async def _specs_show(self, ctx, *, show_all: str = None):
        """Shows your rig info or all users."""
        if ctx.invoked_subcommand is not None:
            return

        if show_all and show_all.lower() == "all":
            data = await get_all_user_rigs(
                ctx.guild, pm=isinstance(ctx.channel, discord.DMChannel)
            )
            if not data:
                return await ctx.send("No one here has a rig profile with me.")

            embed_list = []
            description = ""

            for rig_data, _, mention, _ in sorted(data, key=itemgetter(3, 1)):
                if rig_data and mention:
                    line = f"{mention}\n"
                    if len(description) + len(line) > 1000:
                        embed = discord.Embed(title="Users with a Rig Profile", description=description,
                                              color=await ctx.embed_color())
                        embed_list.append(embed)
                        description = ""
                    description += line

            if description:
                embed = discord.Embed(title="Users with a Rig Profile", description=description,
                                      color=await ctx.embed_color())
                embed_list.append(embed)

            await menu(ctx, embed_list, DEFAULT_CONTROLS, timeout=60)

        elif not show_all:
            embed = await self._get_member_rig(ctx, ctx.author)
            if embed:
                await ctx.send(embed=embed)
        else:
            await ctx.send_help()

    @_specs_show.command(name="member")
    async def _show_member(self, ctx, members: commands.Greedy[discord.Member]):
        """Show Rig Data for multiple Members."""
        if not members:
            return await ctx.send("Please specify at least one member.")

        members = list(set(members))
        embed_list = []
        for member in members:
            embed = await self._get_member_rig(ctx, member)
            if embed:
                embed_list.append(embed)

        if embed_list:
            await menu(ctx, embed_list, DEFAULT_CONTROLS)
        else:
            await ctx.send("No rig data found for the specified members.")

    @specs.command(name="add")
    async def _specs_add(self, ctx: commands.Context):
        """Interactive wizard to add your rig specs."""
        try:
            # Initialize default struct if missing
            member = ctx.author
            current_data = await self.config.user(member).rig()

            # Run the interactive updater
            updated_data = await self.update_rig(current_data, ctx.author)

            if updated_data:
                await self.config.user(member).rig.set(updated_data)
                await ctx.author.send("✅ **Success!** I've updated your rig data.")
            else:
                await ctx.author.send("❌ Setup cancelled.")

        except discord.Forbidden:
            await ctx.send(f"I can't DM you, {ctx.author.mention}. Please enable DMs to set up your specs.")

    @specs.command(name="remove")
    async def _specs_remove(self, ctx: commands.Context, *, component: str):
        """Remove a specific component from your rig profile."""
        member = ctx.author
        component_clean = component.strip().lower()

        async with self.config.user(member).rig() as rig_data:
            found = False
            for key in list(rig_data.keys()):
                if key.lower() == component_clean:
                    rig_data[key] = None
                    await ctx.send(f"🗑️ Removed **{key}** from your rig.")
                    found = True
                    break

            if not found:
                await ctx.send(
                    f"❌ Component `{component}` not found in your rig. Valid components: CPU, GPU, RAM, etc.")

    async def update_rig(self, rig_data: dict, author: discord.User):
        """Interactive DM session to update rig stats."""

        questions = [
            ("CPU", "What CPU do you have?"),
            ("GPU", "What/How many GPUs do you have? (Separate with |)"),
            ("RAM", "How much RAM do you have?"),
            ("Motherboard", "What motherboard do you have?"),
            ("Storage", "What is your storage setup?"),
            ("Monitor", "What monitor(s) do you have?"),
            ("Mouse", "What mouse do you use?"),
            ("Keyboard", "What keyboard do you use?"),
            ("Headset", "What headset/audio do you use?"),
            ("Case", "What case do you have?")
        ]

        def check(m):
            return m.author == author and isinstance(m.channel, discord.DMChannel)

        await author.send(
            "**PC Specs Setup**\n"
            "Type `skip` to keep current/empty value.\n"
            "Type `cancel` to stop completely.\n"
            "Type `clear` to remove a value."
        )

        new_data = rig_data.copy()

        for key, question in questions:
            current_val = new_data.get(key)
            prompt = f"**{question}**"
            if current_val:
                prompt += f"\n(Current: {current_val})"

            await author.send(prompt)

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=60)
            except asyncio.TimeoutError:
                await author.send("⏳ **Timed out.** Setup cancelled.")
                return None

            content = msg.content.strip()

            if content.lower() == "cancel":
                await author.send("🚫 Setup cancelled.")
                return None
            elif content.lower() == "skip":
                continue
            elif content.lower() == "clear":
                new_data[key] = None
            else:
                new_data[key] = content

        return new_data

    async def _get_member_rig(self, ctx: commands.Context, member: discord.Member):
        rig_data = await self.config.user(member).rig()

        # Check if rig has any data
        if not any(rig_data.values()):
            if ctx.author == member:
                await ctx.send(f"You don't have a rig profile yet! Use `{ctx.prefix}specs add` to create one.")
            return None

        embed = discord.Embed(title=f"{member.display_name}'s Rig", color=member.color)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)

        for component, value in rig_data.items():
            if value:
                # Format cleaner values
                clean_val = value.replace("|", "\n").replace(",", "\n")
                clean_val = regex.sub(REPLACE_BRACKER, "", clean_val).strip()

                embed.add_field(name=component, value=clean_val, inline=True)

        return embed