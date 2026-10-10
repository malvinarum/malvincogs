import asyncio
import contextlib
import logging

import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, humanize_list

log = logging.getLogger("red.autodelete")

DEFAULT_GUILD = {
    "enabled": False,
    "delay": 30,
    "delete_invocation": True,
    "delete_response": True,
    "delete_on_error": False,
    "ignored_channels": [],
    "ignored_commands": [],
}


class AutoDelete(commands.Cog):
    """Tidy up after prefix commands.

    Deletes the invoking message and anything the bot sent in reply, after a
    configurable delay. Responses are tracked by wrapping ``Context.send``,
    so anything a command sends through the normal path gets cleaned up.
    """

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xA17D3133, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD)
        self._tasks: set[asyncio.Task] = set()
        self._original_send = None

    # --- lifecycle ---

    async def cog_load(self) -> None:
        self._patch_send()

    async def cog_unload(self) -> None:
        self._unpatch_send()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def _patch_send(self) -> None:
        """Wrap Context.send so replies can be tracked per invocation."""
        if self._original_send is not None:
            return

        original = commands.Context.send
        self._original_send = original

        async def tracked_send(ctx, *args, **kwargs):
            message = await original(ctx, *args, **kwargs)
            if message is not None:
                sent = getattr(ctx, "_autodelete_sent", None)
                if sent is None:
                    with contextlib.suppress(AttributeError):
                        sent = []
                        ctx._autodelete_sent = sent
                if sent is not None:
                    sent.append(message)
            return message

        commands.Context.send = tracked_send

    def _unpatch_send(self) -> None:
        if self._original_send is not None:
            commands.Context.send = self._original_send
            self._original_send = None

    # --- listeners ---

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        await self._maybe_schedule(ctx, errored=False)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        await self._maybe_schedule(ctx, errored=True)

    async def _maybe_schedule(self, ctx: commands.Context, *, errored: bool) -> None:
        if ctx.guild is None or ctx.command is None:
            return
        if await self.bot.cog_disabled_in_guild(self, ctx.guild):
            return

        conf = await self.config.guild(ctx.guild).all()
        if not conf["enabled"]:
            return
        if errored and not conf["delete_on_error"]:
            return
        if ctx.channel.id in conf["ignored_channels"]:
            return
        if ctx.command.qualified_name in conf["ignored_commands"]:
            return

        responses = list(getattr(ctx, "_autodelete_sent", []))
        targets: list[discord.Message] = []
        if conf["delete_response"]:
            targets.extend(responses)
        if conf["delete_invocation"]:
            targets.append(ctx.message)

        if not targets:
            return

        task = asyncio.create_task(self._delete_later(ctx, targets, conf["delay"]))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- deletion ---

    async def _delete_later(
        self, ctx: commands.Context, messages: list[discord.Message], delay: int
    ) -> None:
        if delay > 0:
            await asyncio.sleep(delay)

        can_manage = (
            isinstance(ctx.channel, discord.abc.GuildChannel)
            and ctx.channel.permissions_for(ctx.me).manage_messages
        )

        for message in messages:
            # The bot can always delete its own messages; others need perms.
            if message.author.id != ctx.me.id and not can_manage:
                continue
            with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await message.delete()

    # --- commands ---

    @commands.group(name="autodelete")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def autodelete(self, ctx: commands.Context) -> None:
        """Configure automatic cleanup of command messages."""

    @autodelete.command(name="toggle")
    async def autodelete_toggle(self, ctx: commands.Context, on_off: bool = None) -> None:
        """Turn auto-deletion on or off."""
        current = await self.config.guild(ctx.guild).enabled()
        new = (not current) if on_off is None else on_off
        await self.config.guild(ctx.guild).enabled.set(new)
        await ctx.send(f"Auto-delete is now **{'on' if new else 'off'}**.")

    @autodelete.command(name="delay")
    async def autodelete_delay(self, ctx: commands.Context, seconds: int) -> None:
        """Seconds to wait before deleting. 0 deletes immediately."""
        if not 0 <= seconds <= 3600:
            await ctx.send("Pick a delay between 0 and 3600 seconds.")
            return
        await self.config.guild(ctx.guild).delay.set(seconds)
        await ctx.send(f"Delay set to **{seconds}s**.")

    @autodelete.command(name="invocation")
    async def autodelete_invocation(self, ctx: commands.Context, on_off: bool) -> None:
        """Whether to delete the message that invoked the command."""
        await self.config.guild(ctx.guild).delete_invocation.set(on_off)
        await ctx.send(f"Deleting invocations: **{'yes' if on_off else 'no'}**.")

    @autodelete.command(name="response")
    async def autodelete_response(self, ctx: commands.Context, on_off: bool) -> None:
        """Whether to delete the bot's replies."""
        await self.config.guild(ctx.guild).delete_response.set(on_off)
        await ctx.send(f"Deleting responses: **{'yes' if on_off else 'no'}**.")

    @autodelete.command(name="onerror")
    async def autodelete_onerror(self, ctx: commands.Context, on_off: bool) -> None:
        """Whether to clean up after commands that errored."""
        await self.config.guild(ctx.guild).delete_on_error.set(on_off)
        await ctx.send(f"Cleaning up errors: **{'yes' if on_off else 'no'}**.")

    @autodelete.command(name="ignorechannel")
    async def autodelete_ignorechannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Toggle a channel in the ignore list."""
        async with self.config.guild(ctx.guild).ignored_channels() as ignored:
            if channel.id in ignored:
                ignored.remove(channel.id)
                await ctx.send(f"No longer ignoring {channel.mention}.")
            else:
                ignored.append(channel.id)
                await ctx.send(f"Now ignoring {channel.mention}.")

    @autodelete.command(name="ignorecommand")
    async def autodelete_ignorecommand(self, ctx: commands.Context, *, command: str) -> None:
        """Toggle a command in the ignore list, by its full name."""
        found = self.bot.get_command(command)
        if found is None:
            await ctx.send(f"No command called `{command}`.")
            return

        name = found.qualified_name
        async with self.config.guild(ctx.guild).ignored_commands() as ignored:
            if name in ignored:
                ignored.remove(name)
                await ctx.send(f"No longer ignoring `{name}`.")
            else:
                ignored.append(name)
                await ctx.send(f"Now ignoring `{name}`.")

    @autodelete.command(name="settings")
    async def autodelete_settings(self, ctx: commands.Context) -> None:
        """Show the current configuration."""
        conf = await self.config.guild(ctx.guild).all()

        channels = [
            c.mention
            for c in (ctx.guild.get_channel(cid) for cid in conf["ignored_channels"])
            if c is not None
        ]

        lines = [
            f"Enabled:            {'yes' if conf['enabled'] else 'no'}",
            f"Delay:              {conf['delay']}s",
            f"Delete invocation:  {'yes' if conf['delete_invocation'] else 'no'}",
            f"Delete response:    {'yes' if conf['delete_response'] else 'no'}",
            f"Clean up errors:    {'yes' if conf['delete_on_error'] else 'no'}",
            f"Ignored commands:   {', '.join(conf['ignored_commands']) or 'none'}",
        ]

        embed = discord.Embed(
            title="Auto-delete settings",
            description=box("\n".join(lines), lang="yaml"),
            colour=await ctx.embed_colour(),
        )
        embed.add_field(
            name="Ignored channels",
            value=humanize_list(channels) if channels else "none",
            inline=False,
        )
        await ctx.send(embed=embed)