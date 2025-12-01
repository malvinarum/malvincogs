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

logger = logging.getLogger("red.drapercogs.draperbundle.utils")
_START = "#"
_header = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Redbot/CKS-Companion"}
MAX_STRING_LENGTH = 100000


def fmt_join(words: Sequence, ending: str = "or"):
    if not words:
        return ""
    elif len(words) == 1:
        return words[0]
    else:
        return f'{", ".join(map(str, words[:-1]))} {ending} {words[-1]}'


class Colour:
    def __init__(self, value):
        value = list(value)
        if len(value) != 3:
            raise ValueError("value must have a length of three")
        self._values = value

    def __str__(self):
        return _START + "".join(f"{v:02X}" for v in self)

    def __iter__(self):
        return iter(self._values)

    def __getitem__(self, index):
        return self._values[index]

    def __setitem__(self, index):
        return self._values[index]

    @staticmethod
    def from_string(string):
        colour = iter(string)
        if string[0] == _START:
            next(colour, None)
        return Colour(int("".join(v), 16) for v in zip(colour, colour))

    @staticmethod
    def hex_to_rgb(string):
        colour = iter(string)
        if string[0] == _START:
            next(colour, None)
        return tuple(int("".join(v), 16) for v in zip(colour, colour))

    @staticmethod
    def rgb_to_hex(r, g, b):
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def random():
        return Colour(random.randrange(256) for _ in range(3))

    def contrast(self):
        return Colour(255 - v for v in self)


def list_filter(_list: list, what_to_remove: Union[str, int, bool] = None):
    return [x for x in _list if x != what_to_remove]


async def has_a_profile(member: discord.Member):
    if not member:
        return False
    return bool(await ConfigHolder.GamingProfile.user(member).country())


async def get_website_data(url, session: aiohttp.ClientSession = None, headers=None):
    """
    Fetches data from a URL.
    Refactored to require a session to prevent resource leaks.
    """
    if not headers:
        headers = _header

    if session:
        async with session.get(url, headers=headers) as response:
            return await response.read()
    else:
        # Fallback for legacy calls (discouraged)
        async with aiohttp.ClientSession() as new_session:
            async with new_session.get(url, headers=headers) as response:
                return await response.read()


async def get_member(guild: discord.Guild, member):
    if isinstance(member, discord.Member):
        return member
    elif isinstance(member, int):
        return guild.get_member(member)
    elif isinstance(member, str):
        return get_member_named(guild, member)
    return member


def count_members(roles: list):
    return sum(len(role.members) for role in roles)


def get_channel_named(guild, name):
    channels = guild.channels

    def pred(c):
        try:
            return str(c.name).lower().strip() == name.lower().strip()
        except Exception:
            return False

    return discord.utils.find(pred, channels)


def safe_add(first, second):
    """Safely add two numbers (check resulting length)"""
    if len(str(first)) + len(str(second)) > MAX_STRING_LENGTH:
        raise KeyError
    return first + second


def safe_mult(first, second):
    """Safely multiply two numbers (check resulting length)"""
    if second * len(str(first)) > MAX_STRING_LENGTH:
        raise KeyError
    if first * len(str(second)) > MAX_STRING_LENGTH:
        raise KeyError
    return first * second


OPERATORS = {
    ast.Add: safe_add,
    ast.Sub: op.sub,
    ast.Mult: safe_mult,
    ast.Div: op.truediv,
    ast.USub: op.neg,
}


def eval_expr(expr):
    """Evaluate math problems safely"""
    return eval_(ast.parse(expr, mode="eval").body)


def eval_(node):
    """Do the evaluation."""
    if isinstance(node, ast.Num):  # <number>
        return node.n
    if isinstance(node, ast.BinOp):  # <left> <operator> <right>
        return OPERATORS[type(node.op)](eval_(node.left), eval_(node.right))
    if isinstance(node, ast.UnaryOp):  # <operator> <operand> e.g., -1
        return OPERATORS[type(node.op)](eval_(node.operand))
    raise TypeError(node)


async def get_supported_platforms(lists: bool = True, supported: bool = False):
    platforms = (await ConfigHolder.PublisherManager.get_raw()).get("services", {})
    if supported:
        platforms = [(value.get("identifier")) for _, value in platforms.items()]
    elif lists:
        platforms = [
            (value.get("identifier"), value.get("name"))
            for _, value in platforms.items()
        ]
    return platforms


async def account_adder(bot, author: discord.User):
    platforms = await get_supported_platforms()
    platform_prompt = [name for _, name in platforms]
    # Map index to platform name for the prompt
    platform_prompt_dict = {
        str(counter): name for counter, name in enumerate(platform_prompt, start=1)
    }
    return await smart_prompt(bot, author, platform_prompt_dict, platforms)


async def update_profile(bot, user_data: dict, author: discord.User):
    msg = await author.send(
        "What country are you from (Enter the number next to the country)?"
    )
    country_data = WorldData.get("country", {})
    validcountries = sorted([value.get("name") for _, value in country_data.items()])
    desc = ""
    valid_county_list = []

    # Only show a subset to avoid spamming DMs too hard, or rely on pagify
    for index, value in enumerate(validcountries, start=1):
        desc += f"{index}. {value}\n"
        valid_county_list.append(str(index))

    pages = [box(page, lang="md") for page in list(pagify(desc, shorten_by=20))]

    # Using namedtuple for mock context
    Context = namedtuple("Context", "author me bot send channel")
    new_ctx = Context(author, bot.user, bot, author.send, msg.channel)

    # We shouldn't use create_task for menu if we need to block for result,
    # but the original code did it to allow input while menu is up.
    menu_task = asyncio.create_task(menu(new_ctx, pages, DEFAULT_CONTROLS, timeout=180))

    country = None
    pred_check = MessagePredicate.contained_in(valid_county_list, ctx=new_ctx)

    try:
        await bot.wait_for("message", timeout=60.0, check=pred_check)
        country = valid_county_list[pred_check.result] if pred_check.result is not None else None
    except asyncio.TimeoutError:
        country = None

    with contextlib.suppress(Exception):
        menu_task.cancel()

    if not country:
        return user_data

    user_data["country"] = validcountries[int(country) - 1]
    cached_country = user_data["country"].lower().strip()

    if cached_country:
        country_info = country_data.get(cached_country, {})
        region = country_info.get("region")
        country_timezones = country_info.get("timezones")
        user_data["subzone"] = country_info.get("subregion")
    else:
        region = None
        country_timezones = None

    continent_data = sorted(CONTINENT_DATA.values())

    if not region:
        await author.send("Which zone are you from?")
        embed = discord.Embed(title="Pick a number that matches your zone")
        desc = ""
        valid_continent_list = []
        for index, value in enumerate(continent_data, start=1):
            desc += f"{index}. {value.title()}\n"
            valid_continent_list.append(str(index))
        embed.description = box(desc, lang="md")
        await author.send(embed=embed)

        zone = None
        pred_check = MessagePredicate.contained_in(valid_continent_list, ctx=new_ctx)
        try:
            await bot.wait_for("message", timeout=30.0, check=pred_check)
            zone = valid_continent_list[pred_check.result] if pred_check.result is not None else None
        except asyncio.TimeoutError:
            pass

        if zone:
            user_data["zone"] = continent_data[int(zone) - 1]
    else:
        user_data["zone"] = region

    user_data["language"] = None

    if country_timezones and len(country_timezones) > 1:
        # User selection for multiple timezones
        country_timezones_dict = {
            str(i): key for i, key in enumerate(country_timezones, start=1)
        }
        valid_timezone_list = sorted(country_timezones_dict.keys())

        await author.send(
            "There are multiple timezones for your country, please pick the one that matches yours:"
        )
        embed = discord.Embed(title="Pick a number that matches your timezone")
        desc = ""
        for i, val in country_timezones_dict.items():
            desc += f"{i}. {val}\n"

        embed.description = box(desc, lang="md")
        await author.send(embed=embed)

        timezone_choice = None
        pred_check = MessagePredicate.contained_in(valid_timezone_list, ctx=new_ctx)
        try:
            await bot.wait_for("message", timeout=30.0, check=pred_check)
            timezone_choice = valid_timezone_list[pred_check.result] if pred_check.result is not None else None
        except asyncio.TimeoutError:
            pass

        if timezone_choice:
            user_data["timezone"] = country_timezones[int(timezone_choice) - 1]

    elif country_timezones and len(country_timezones) == 1:
        user_data["timezone"] = country_timezones[0]

    return user_data


def get_user_named(bot, name):
    result = None
    members = list(bot.get_all_members())
    if len(name) > 5 and name[-5] == "#":
        potential_discriminator = name[-4:]
        result = discord.utils.get(
            members, name=name[:-5], discriminator=potential_discriminator
        )
        if result is not None:
            return result

    def pred(m):
        try:
            # Handle cases where global users might not have nicks
            return str(m.name).lower() == name.lower()
        except Exception:
            return False

    return discord.utils.find(pred, members)


async def get_activity_list(ctx, data, game_name, activity):
    username = False
    if activity == discord.ActivityType.playing:
        activity_name = "playing "
        username = True
    elif activity == discord.ActivityType.streaming:
        activity_name = "streaming "
        username = True
    elif activity == discord.ActivityType.listening:
        activity_name = "listening to"
    else:
        activity_name = "watching "

    embed_list = []
    # Assumes ctx.embed_colour is available (Redbot standard)
    if hasattr(ctx, "embed_colour"):
        embed_colour = await ctx.embed_colour()
    else:
        embed_colour = discord.Color.blue()

    for key, value in sorted(data.items()):
        # Sorting by role value then name
        player_data = sorted(value, key=op.itemgetter(2, 1))
        usernames = ""
        discord_names = ""

        for mention, display_name, black_hole, account in player_data:
            account = account or "Unknown"
            if (
                    len(f"{usernames}{account}\n") > 1000
                    or len(f"{discord_names}{display_name}\n") > 1000
            ):
                embed = discord.Embed(
                    title=("Who's {activity}{name}?").format(
                        name=key, activity=activity_name
                    ),
                    colour=embed_colour,
                )
                embed.add_field(name="Discord Member", value=discord_names, inline=True)
                if username:
                    embed.add_field(name="Username", value=usernames, inline=True)
                embed_list.append(embed)
                usernames = ""
                discord_names = ""
            usernames += f"{account}\n"
            discord_names += f"{display_name}\n"

        if usernames:
            embed = discord.Embed(
                title="Who's {activity} {name}?".format(
                    name=key, activity=activity_name
                ),
                colour=embed_colour,
            )
            embed.add_field(name="Discord Member", value=discord_names, inline=True)
            if username:
                embed.add_field(name="Username", value=usernames, inline=True)
            embed_list.append(embed)

    return embed_list


def get_meta_data(date: datetime):
    _, wk, dy = date.isocalendar()
    day = day_name[dy - 1]
    return day, wk, dy


def is_yesterday(a_date: date):
    return date.today() + timedelta(days=-1) == a_date


def is_tomorrow(a_date: date):
    return date.today() + timedelta(days=1) == a_date


def add_username_hyperlink(platform, username, _id):
    platform = platform.lower()
    url = None
    safe_user = quote_plus(str(username))
    safe_id = quote_plus(str(_id)) if _id else None

    if platform == "twitch":
        url = f'https://www.twitch.tv/{safe_user}'
    elif platform == "steam":
        if safe_id:
            url = f'https://steamcommunity.com/profiles/{safe_id}'
        else:
            url = f'https://steamcommunity.com/id/{safe_user}'
    elif platform == "instagram":
        url = f'https://www.instagram.com/{safe_user}'
    elif platform == "mixer":
        url = f'https://mixer.com/{safe_user}'
    elif platform == "reddit":
        url = f'https://www.reddit.com/user/{safe_user}'
    elif platform == "twitter":
        url = f'https://twitter.com/{safe_user}'
    elif platform == "youtube":
        url = f'https://www.youtube.com/user/{safe_user}'
    elif platform == "facebook":
        url = f'https://www.facebook.com/{safe_user}'
    elif platform == "soundcloud":
        url = f'https://www.soundcloud.com/{safe_user}'
    elif platform == "spotify":
        target = safe_id if safe_id else safe_user
        url = f'https://open.spotify.com/user/{target}'

    if url:
        username = f"[{username}]({url})"

    return username


def get_member_activity(member: discord.Member, database=False):
    activities = getattr(member, "activities", None)
    if not activities:
        return None

    # Check activity types
    activities_type = [activity.type for activity in activities]
    if not activities_type:
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

    # 2. Authenticate
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