import discord
from redbot.core import commands


class ConvertMember(commands.MemberConverter):
    """Converts to a :class:`Member`.

    All lookups are via the local guild. If in a DM context, then the lookup
    is done by the global cache (best effort).

    The lookup strategy is as follows (in order):
    1. Lookup by ID.
    2. Lookup by mention.
    3. Lookup by name#discrim
    4. Lookup by name
    5. Lookup by nickname
    """

    async def convert(self, ctx, argument):
        try:
            return await super().convert(ctx, argument)
        except commands.BadArgument:
            # Fallback manual search
            if not ctx.guild:
                raise

            argument = argument.lower()

            # Name match
            member = discord.utils.find(
                lambda x: x.name.lower() == argument,
                ctx.guild.members
            )

            if not member:
                # Nick match
                member = discord.utils.find(
                    lambda x: x.display_name.lower() == argument,
                    ctx.guild.members,
                )

            if member is None:
                raise commands.BadArgument(f'Member "{argument}" was not found')

            return member