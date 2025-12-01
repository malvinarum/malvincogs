from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import operator as op
import random
from calendar import day_name
from collections import namedtuple
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Union, Optional
from urllib.parse import quote_plus

import aiohttp
import dateutil.parser
import discord

# Use redbot's utcnow if available, else fallback
try:
    from discord.utils import utcnow
except ImportError:
    from datetime import datetime, timezone


    def utcnow():
        return datetime.now(timezone.utc)

from pytz import UTC
from redbot.core import commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import DEFAULT_CONTROLS, menu
from redbot.core.utils.predicates import MessagePredicate

# Relative imports
from .config_holder import ConfigHolder

# ... existing imports ...

# Add IGDB Constants
IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_BASE = "https://api.igdb.com/v4"

from .country import WorldData


async def get_igdb_cover(bot, game_name: str):
    """
    Fetches the cover art URL for a game from IGDB.
    Requires IGDB credentials to be set in PublisherManager config.
    """
    # 1. Get Credentials from ConfigHolder (PublisherManager stores them)
    creds = await ConfigHolder.PublisherManager.igdb_creds()
    c_id = creds.get("client_id")
    c_secret = creds.get("client_secret")

    if not c_id or not c_secret:
        return None

    # 2. Authenticate (We can cache this token essentially like PublisherManager did,
    # but for simplicity in a utility function, we might re-auth or need a shared token manager.
    # To keep it robust without circular imports, let's just do a quick auth or check bot.igdb_token if we attached it.)

    # Simple Auth for now (Optimization: Attach token to bot instance in PublisherManager)
    params = {
        "client_id": c_id,
        "client_secret": c_secret,
        "grant_type": "client_credentials"
    }

    token = None
    try:
        async with bot.session.post(IGDB_AUTH_URL, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                token = data["access_token"]
    except Exception:
        return None

    if not token: return None

    # 3. Query IGDB
    headers = {
        "Client-ID": c_id,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    # Query for cover
    # We match the name, asking for the cover.url field
    query = f'search "{game_name}"; fields cover.url; limit 1;'

    try:
        async with bot.session.post(f"{IGDB_API_BASE}/games", headers=headers, data=query) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and "cover" in data[0]:
                    # IGDB urls are often //images.igdb.com/...
                    url = data[0]["cover"]["url"]
                    if url.startswith("//"):
                        url = "https:" + url
                    # Resize to big cover (t_cover_big instead of t_thumb)
                    url = url.replace("t_thumb", "t_cover_big")
                    return url
    except Exception:
        pass

    return None

    # Determine what we are looking for
    if database:
        # When storing in DB, we prefer playing > streaming > listening
        # But honestly, just grabbing the first valid game is usually enough
        pass

    # Priority: Streaming > Playing > Listening
    stream = discord.ActivityType.streaming in activities_type
    game = discord.ActivityType.playing in activities_type
    music = discord.ActivityType.listening in activities_type

    looking_for = None
    name_property = "name"
    context = ""

    if stream:
        looking_for = discord.ActivityType.streaming
        name_property = "details"  # Often details is better for stream title, or name for game name
        context = "Streaming {name}"
    elif game:
        looking_for = discord.ActivityType.playing
        name_property = "name"
        context = "Playing {name}"
    elif music:
        looking_for = discord.ActivityType.listening
        name_property = "title"
        context = "Listening to {name}"
    else:
        return None

    if interested_in := [
        activity for activity in member.activities if activity.type == looking_for
    ]:
        # Grab first match
        act = interested_in[0]
        # For streaming, the 'name' is often "Twitch", we might want 'details' or 'state'
        # But 'name' is consistent with previous logic
        activity_name = getattr(act, name_property, getattr(act, "name", "Unknown"))

        return activity_name if database else context.format(name=activity_name)
    return None


async def get_all_user_profiles(
        guild, pm=False, withprofile=True, inactivity=False, timespan=None
):
    data = await ConfigHolder.GamingProfile.all_users()
    data_list = []

    time_allowed = 0
    if inactivity and isinstance(timespan, int):
        time_now_sec = utcnow().timestamp()
        time_allowed = time_now_sec - (604800 * timespan)  # 604800 = 1 week

    for discord_id, value in data.items():
        is_bot = value.get("is_bot")
        member = guild.get_member(int(discord_id))

        if not member:
            continue

        has_profile = await has_a_profile(member)

        innactive = False
        if inactivity and isinstance(timespan, int):
            last_seen = None
            if member.status != discord.Status.offline:
                last_seen = utcnow()
            else:
                seen_val = value.get("seen")
                if seen_val:
                    last_seen = get_date_time(seen_val)

            if last_seen:
                last_seen_ts = last_seen.timestamp()
                if last_seen_ts < time_allowed:
                    innactive = True
            elif not last_seen:
                # Never seen? consider inactive
                innactive = True

        if member and not pm:
            username_true = member.display_name
            mention = member.mention
            top_role = member.top_role
            role_value = top_role.position * -1
        else:
            username_true = str(member)
            mention = username_true
            role_value = 0

        if inactivity:
            if innactive:
                data_list.append((username_true, mention, role_value))
        elif withprofile and has_profile and username_true and not is_bot:
            data_list.append((username_true, mention, role_value))
        elif not withprofile and not has_profile and username_true and not is_bot:
            data_list.append((username_true, mention, role_value))

    return data_list


def get_date_string(then: datetime, now: datetime = None):
    if not now:
        now = utcnow()

    if not then.tzinfo:
        then = UTC.localize(then)
    if not now.tzinfo:
        now = UTC.localize(now)

    _, week_number_now, _ = get_meta_data(now)
    day_then, week_number_then, _ = get_meta_data(then)

    time_fmt = then.strftime("%I:%M %p")
    time_fallback = then.strftime("%b %d, %y at %I:%M %p")

    if then.date() == now.date():
        return f"Today at {time_fmt}"

    # Simple logic for yesterday/tomorrow
    if is_yesterday(then.date()):
        return f"Yesterday at {time_fmt}"
    if is_tomorrow(then.date()):
        return f"Tomorrow at {time_fmt}"

    return f"{time_fallback}"


async def get_all_user_rigs(guild, pm=False):
    data = await ConfigHolder.PCSpecs.all_users()
    data_list = []

    for discord_id, value in data.items():
        member = guild.get_member(int(discord_id))

        if member and not pm:
            username_true = member.display_name
            mention = member.mention
            top_role = member.top_role
            role_value = top_role.position * -1
        else:
            username_true = None
            mention = username_true
            role_value = 0

        # Check if they have CPU data as a proxy for having a rig
        rig_data = value.get("rig", {}).get("CPU")
        if rig_data and username_true:
            data_list.append((rig_data, username_true, mention, role_value))
    return data_list


async def smart_prompt(bot, author: discord.User, prompt_data: dict, platforms: dict):
    def check(m):
        return (
                m.author == author
                and isinstance(m.channel, discord.DMChannel)
                and len(m.content) < 64  # Increased limit slightly
        )

    data = {}
    original_len = len(prompt_data) + 1

    # Ensure exit option exists
    if "finish" not in [str(v).lower() for v in prompt_data.values()]:
        prompt_data[str(original_len)] = "Finish"

    await author.send(f"Type the number of the service to add, or type 'finish' to stop.")

    while True:
        desc = ""
        for index, value in enumerate(prompt_data.values(), start=1):
            desc += f"**{index}.** {value}\n"

        embed = discord.Embed(title="Select Service", description=desc, color=discord.Color.blue())
        await author.send(embed=embed)

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await author.send("Timed out.")
            break

        content = msg.content.strip().lower()

        # Check for direct text commands
        if content in ["stop", "finish", "exit"]:
            break

        # Check for number selection
        selected_name = None

        # Map input number to prompt_data key/value
        # prompt_data keys are usually "1", "2"...
        # But let's rely on the list index from the embed generation to be safe
        keys = list(prompt_data.keys())
        values = list(prompt_data.values())

        if content.isdigit():
            idx = int(content) - 1
            if 0 <= idx < len(values):
                selected_name = values[idx]

        if selected_name and selected_name.lower() == "finish":
            break

        if selected_name:
            # Find the internal identifier for this service
            command_id = None
            for cmd_id, name in platforms:
                if name == selected_name:
                    command_id = cmd_id
                    break

            if command_id:
                await author.send(f"Enter username for **{selected_name}** (or 'skip'):")
                try:
                    umsg = await bot.wait_for("message", check=check, timeout=60)
                    ucontent = umsg.content.strip()
                    if ucontent.lower() not in ["skip", "cancel"]:
                        data[command_id] = ucontent
                        await author.send(f"✅ Added {selected_name}: {ucontent}")
                except asyncio.TimeoutError:
                    await author.send("Timed out.")
                    break
        else:
            await author.send("Invalid selection.")

    return data


def get_member_named(guild, name):
    result = None
    members = guild.members
    if len(name) > 5 and name[-5] == "#":
        potential_discriminator = name[-4:]
        result = discord.utils.get(
            members, name=name[:-5], discriminator=potential_discriminator
        )
        if result is not None:
            return result

    def pred(m):
        return (
                str(m.nick).lower().strip() == name.lower().strip()
                or str(m.name).lower().strip() == name.lower().strip()
        )

    return discord.utils.find(pred, members)


async def get_all_by_platform(platform: str, guild: discord.Guild, pm: bool = False):
    platform = platform.lower().strip()
    data = await ConfigHolder.AccountManager.all_users()
    data_list = []

    for discord_id, value in data.items():
        member = guild.get_member(int(discord_id))

        if member and not pm:
            username_true = member.display_name
            mention = member.mention
            top_role = member.top_role
            role_value = top_role.position * -1
        else:
            username_true = None
            mention = f"<@!{discord_id}>"
            role_value = 0

        account_map = value.get("account", {})
        account = account_map.get(platform)

        steamid = None
        if platform == "steam":
            steamid = account_map.get("steamid")
        elif platform == "spotify":
            steamid = account_map.get("spotifyid")  # reusing var name

        if account and mention:
            data_list.append((account, username_true, mention, role_value, steamid))

    return data_list


def get_date_time(s: Union[int, str, datetime] = None):
    if s is None:
        return utcnow()
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s, tz=timezone.utc)
    if isinstance(s, datetime):
        return s if s.tzinfo else UTC.localize(s)

    try:
        d = dateutil.parser.parse(str(s))
        if not d.tzinfo:
            d = UTC.localize(d)
        return d
    except:
        return utcnow()


async def update_member_atomically(
        ctx: Union[commands.Context, discord.Member],
        give: list[discord.Role] = None,
        remove: list[discord.Role] = None,
        nick: str = None,
        member: discord.Member = None,
        member_update=False,
):
    """
    Safely updates a member's roles/nick without race conditions or permission errors.
    """
    if not ctx.guild:
        return None

    me = ctx.guild.me

    if member_update:
        # ctx is actually the member object in this case
        member = ctx
    else:
        member = member or ctx.author

    if member == me:
        return

    # Check bot permissions
    if not me.guild_permissions.manage_roles:
        return

    give = give or []
    remove = remove or []

    # Filter roles based on hierarchy
    roles_to_add = [r for r in give if r and r < me.top_role and r not in member.roles]
    roles_to_remove = [r for r in remove if r and r < me.top_role and r in member.roles]

    try:
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Profile Update")
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Profile Update")

        if nick and me.guild_permissions.manage_nicknames and me.top_role > member.top_role:
            if member.guild.owner != member:
                await member.edit(nick=nick)

    except discord.Forbidden:
        logger.warning(f"Failed to update roles/nick for {member.id}: Forbidden")
    except discord.HTTPException as e:
        logger.error(f"Failed to update roles/nick for {member.id}: {e}")


def get_role_named(guild, name):
    if not guild or not name:
        return None

    name = str(name).strip().lower()
    for role in guild.roles:
        if role.name.lower().strip() == name:
            return role
    return None