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
from .constants import CONTINENT_DATA
from .country import WorldData

# IGDB Constants
IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"
IGDB_API_BASE = "https://api.igdb.com/v4"

logger = logging.getLogger("red.drapercogs.draperbundle.utils")
_START = "#"
_header = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Redbot/CKS-Companion"}
MAX_STRING_LENGTH = 100000


# ... (fmt_join, Colour, list_filter, has_a_profile, get_website_data, get_member, count_members, get_channel_named, safe_add, safe_mult, OPERATORS, eval_expr, eval_, get_supported_platforms, account_adder, update_profile, get_user_named -> Keep all these identical)

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
        if len(value) != 3: raise ValueError("value must have a length of three")
        self._values = value

    def __str__(self):
        return _START + "".join(f"{v:02X}" for v in self)

    def __iter__(self):
        return iter(self._values)

    def __getitem__(self, index):
        return self._values[index]

    def __setitem__(self, index):
        self._values[index]

    @staticmethod
    def from_string(string):
        colour = iter(string)
        if string[0] == _START: next(colour, None)
        return Colour(int("".join(v), 16) for v in zip(colour, colour))

    @staticmethod
    def hex_to_rgb(string):
        colour = iter(string)
        if string[0] == _START: next(colour, None)
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
    if not member: return False
    return bool(await ConfigHolder.GamingProfile.user(member).country())


async def get_website_data(url, session: aiohttp.ClientSession = None, headers=None):
    if not headers: headers = _header
    if session:
        async with session.get(url, headers=headers) as response:
            return await response.read()
    else:
        async with aiohttp.ClientSession() as new_session:
            async with new_session.get(url, headers=headers) as response: return await response.read()


async def get_member(guild: discord.Guild, member):
    if isinstance(member, discord.Member):
        return member
    elif isinstance(member, int):
        return guild.get_member(member)
    elif isinstance(member, str):
        return get_member_named(guild, member)
    return member


def count_members(roles: list): return sum(len(role.members) for role in roles)


def get_channel_named(guild, name):
    def pred(c):
        try:
            return str(c.name).lower().strip() == name.lower().strip()
        except Exception:
            return False

    return discord.utils.find(pred, guild.channels)


def safe_add(first, second):
    if len(str(first)) + len(str(second)) > MAX_STRING_LENGTH: raise KeyError
    return first + second


def safe_mult(first, second):
    if second * len(str(first)) > MAX_STRING_LENGTH: raise KeyError
    if first * len(str(second)) > MAX_STRING_LENGTH: raise KeyError
    return first * second


OPERATORS = {ast.Add: safe_add, ast.Sub: op.sub, ast.Mult: safe_mult, ast.Div: op.truediv, ast.USub: op.neg}


def eval_expr(expr): return eval_(ast.parse(expr, mode="eval").body)


def eval_(node):
    if isinstance(node, ast.Num): return node.n
    if isinstance(node, ast.BinOp): return OPERATORS[type(node.op)](eval_(node.left), eval_(node.right))
    if isinstance(node, ast.UnaryOp): return OPERATORS[type(node.op)](eval_(node.operand))
    raise TypeError(node)


async def get_supported_platforms(lists: bool = True, supported: bool = False):
    platforms = (await ConfigHolder.PublisherManager.get_raw()).get("services", {})
    if supported:
        platforms = [(value.get("identifier")) for _, value in platforms.items()]
    elif lists:
        platforms = [(value.get("identifier"), value.get("name")) for _, value in platforms.items()]
    return platforms


async def account_adder(bot, author: discord.User):
    platforms = await get_supported_platforms()
    platform_prompt = [name for _, name in platforms]
    platform_prompt_dict = {str(counter): name for counter, name in enumerate(platform_prompt, start=1)}
    return await smart_prompt(bot, author, platform_prompt_dict, platforms)


async def update_profile(bot, user_data: dict, author: discord.User):
    msg = await author.send("What country are you from (Enter the number next to the country)?")
    country_data = WorldData.get("country", {})
    validcountries = sorted([value.get("name") for _, value in country_data.items()])
    desc = ""
    valid_county_list = []
    for index, value in enumerate(validcountries, start=1):
        desc += f"{index}. {value}\n"
        valid_county_list.append(str(index))
    pages = [box(page, lang="md") for page in list(pagify(desc, shorten_by=20))]
    Context = namedtuple("Context", "author me bot send channel")
    new_ctx = Context(author, bot.user, bot, author.send, msg.channel)
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
    if not country: return user_data
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
        if zone: user_data["zone"] = continent_data[int(zone) - 1]
    else:
        user_data["zone"] = region
    user_data["language"] = None
    if country_timezones and len(country_timezones) > 1:
        country_timezones_dict = {str(i): key for i, key in enumerate(country_timezones, start=1)}
        valid_timezone_list = sorted(country_timezones_dict.keys())
        await author.send("There are multiple timezones for your country, please pick the one that matches yours:")
        embed = discord.Embed(title="Pick a number that matches your timezone")
        desc = ""
        for i, val in country_timezones_dict.items(): desc += f"{i}. {val}\n"
        embed.description = box(desc, lang="md")
        await author.send(embed=embed)
        timezone_choice = None
        pred_check = MessagePredicate.contained_in(valid_timezone_list, ctx=new_ctx)
        try:
            await bot.wait_for("message", timeout=30.0, check=pred_check)
            timezone_choice = valid_timezone_list[pred_check.result] if pred_check.result is not None else None
        except asyncio.TimeoutError:
            pass
        if timezone_choice: user_data["timezone"] = country_timezones[int(timezone_choice) - 1]
    elif country_timezones and len(country_timezones) == 1:
        user_data["timezone"] = country_timezones[0]
    return user_data


def get_user_named(bot, name):
    result = None
    members = list(bot.get_all_members())
    if len(name) > 5 and name[-5] == "#":
        potential_discriminator = name[-4:]
        result = discord.utils.get(members, name=name[:-5], discriminator=potential_discriminator)
        if result is not None: return result

    def pred(m):
        try:
            return str(m.name).lower() == name.lower()
        except Exception:
            return False

    return discord.utils.find(pred, members)


async def get_activity_list(ctx, data, game_name, activity, thumbnail_url=None):
    logger.info(f"DEBUG: get_activity_list called. Thumbnail: {thumbnail_url}")
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
    if hasattr(ctx, "embed_colour"):
        embed_colour = await ctx.embed_colour()
    else:
        embed_colour = discord.Color.blue()

    for key, value in sorted(data.items()):
        player_data = sorted(value, key=op.itemgetter(2, 1))
        usernames = ""
        discord_names = ""

        for mention, display_name, black_hole, account in player_data:
            account = account or "Unknown"
            if (len(f"{usernames}{account}\n") > 1000 or len(f"{discord_names}{display_name}\n") > 1000):
                embed = discord.Embed(
                    title=("Who's {activity}{name}?").format(name=key, activity=activity_name),
                    colour=embed_colour,
                )
                embed.add_field(name="Discord Member", value=discord_names, inline=True)
                if username: embed.add_field(name="Username", value=usernames, inline=True)

                if thumbnail_url:
                    logger.info(f"DEBUG: Setting thumbnail in mid-loop embed to {thumbnail_url}")
                    embed.set_thumbnail(url=thumbnail_url)

                embed_list.append(embed)
                usernames = ""
                discord_names = ""
            usernames += f"{account}\n"
            discord_names += f"{display_name}\n"

        if usernames:
            embed = discord.Embed(
                title="Who's {activity} {name}?".format(name=key, activity=activity_name),
                colour=embed_colour,
            )
            embed.add_field(name="Discord Member", value=discord_names, inline=True)
            if username: embed.add_field(name="Username", value=usernames, inline=True)

            if thumbnail_url:
                logger.info(f"DEBUG: Setting thumbnail in final embed to {thumbnail_url}")
                embed.set_thumbnail(url=thumbnail_url)

            embed_list.append(embed)

    return embed_list


def get_meta_data(date: datetime):
    _, wk, dy = date.isocalendar()
    day = day_name[dy - 1]
    return day, wk, dy


def is_yesterday(a_date: date): return date.today() + timedelta(days=-1) == a_date


def is_tomorrow(a_date: date): return date.today() + timedelta(days=1) == a_date


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
    if url: username = f"[{username}]({url})"
    return username


def get_member_activity(member: discord.Member, database=False):
    activities = getattr(member, "activities", None)
    if not activities: return None
    activities_type = [activity.type for activity in activities]
    if not activities_type: return None
    stream = discord.ActivityType.streaming in activities_type
    game = discord.ActivityType.playing in activities_type
    music = discord.ActivityType.listening in activities_type
    looking_for = None
    name_property = "name"
    context = ""
    if stream:
        looking_for = discord.ActivityType.streaming
        name_property = "details"
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
    if interested_in := [activity for activity in member.activities if activity.type == looking_for]:
        act = interested_in[0]
        activity_name = getattr(act, name_property, getattr(act, "name", "Unknown"))
        return activity_name if database else context.format(name=activity_name)
    return None


async def get_all_user_profiles(guild, pm=False, withprofile=True, inactivity=False, timespan=None):
    data = await ConfigHolder.GamingProfile.all_users()
    data_list = []
    time_allowed = 0
    if inactivity and isinstance(timespan, int):
        time_now_sec = utcnow().timestamp()
        time_allowed = time_now_sec - (604800 * timespan)
    for discord_id, value in data.items():
        is_bot = value.get("is_bot")
        member = guild.get_member(int(discord_id))
        if not member: continue
        has_profile = await has_a_profile(member)
        innactive = False
        if inactivity and isinstance(timespan, int):
            last_seen = None
            if member.status != discord.Status.offline:
                last_seen = utcnow()
            else:
                seen_val = value.get("seen")
                if seen_val: last_seen = get_date_time(seen_val)
            if last_seen:
                last_seen_ts = last_seen.timestamp()
                if last_seen_ts < time_allowed: innactive = True
            elif not last_seen:
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
            if innactive: data_list.append((username_true, mention, role_value))
        elif withprofile and has_profile and username_true and not is_bot:
            data_list.append((username_true, mention, role_value))
        elif not withprofile and not has_profile and username_true and not is_bot:
            data_list.append((username_true, mention, role_value))
    return data_list


def get_date_string(then: datetime, now: datetime = None):
    if not now: now = utcnow()
    if not then.tzinfo: then = UTC.localize(then)
    if not now.tzinfo: now = UTC.localize(now)
    _, week_number_now, _ = get_meta_data(now)
    day_then, week_number_then, _ = get_meta_data(then)
    time_fmt = then.strftime("%I:%M %p")
    time_fallback = then.strftime("%b %d, %y at %I:%M %p")
    if then.date() == now.date(): return f"Today at {time_fmt}"
    if is_yesterday(then.date()): return f"Yesterday at {time_fmt}"
    if is_tomorrow(then.date()): return f"Tomorrow at {time_fmt}"
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
        rig_data = value.get("rig", {}).get("CPU")
        if rig_data and username_true:
            data_list.append((rig_data, username_true, mention, role_value))
    return data_list


async def smart_prompt(bot, author: discord.User, prompt_data: dict, platforms: dict):
    def check(m):
        return m.author == author and isinstance(m.channel, discord.DMChannel) and len(m.content) < 64

    data = {}
    original_len = len(prompt_data) + 1
    if "finish" not in [str(v).lower() for v in prompt_data.values()]: prompt_data[str(original_len)] = "Finish"
    await author.send(f"Type the number of the service to add, or type 'finish' to stop.")
    while True:
        desc = ""
        for index, value in enumerate(prompt_data.values(), start=1): desc += f"**{index}.** {value}\n"
        embed = discord.Embed(title="Select Service", description=desc, color=discord.Color.blue())
        await author.send(embed=embed)
        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await author.send("Timed out.")
            break
        content = msg.content.strip().lower()
        if content in ["stop", "finish", "exit"]: break
        selected_name = None
        if content.isdigit():
            idx = int(content) - 1
            if 0 <= idx < len(list(prompt_data.values())): selected_name = list(prompt_data.values())[idx]
        if selected_name and selected_name.lower() == "finish": break
        if selected_name:
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


async def get_igdb_cover(bot, game_name: str):
    creds = await ConfigHolder.PublisherManager.igdb_creds()
    c_id = creds.get("client_id")
    c_secret = creds.get("client_secret")
    if not c_id or not c_secret: return None
    params = {"client_id": c_id, "client_secret": c_secret, "grant_type": "client_credentials"}
    token = None
    try:
        async with bot.session.post(IGDB_AUTH_URL, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                token = data["access_token"]
    except Exception:
        return None
    if not token: return None
    headers = {"Client-ID": c_id, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    query = f'search "{game_name}"; fields cover.url; limit 1;'
    try:
        async with bot.session.post(f"{IGDB_API_BASE}/games", headers=headers, data=query) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data and "cover" in data[0]:
                    url = data[0]["cover"]["url"]
                    if url.startswith("//"): url = "https:" + url
                    return url.replace("t_thumb", "t_cover_big")
    except Exception:
        pass
    return None


def get_date_time(s: Union[int, str, datetime] = None):
    if s is None: return utcnow()
    if isinstance(s, (int, float)): return datetime.fromtimestamp(s, tz=timezone.utc)
    if isinstance(s, datetime): return s if s.tzinfo else UTC.localize(s)
    try:
        d = dateutil.parser.parse(str(s))
        if not d.tzinfo: d = UTC.localize(d)
        return d
    except:
        return utcnow()


async def update_member_atomically(ctx: Union[commands.Context, discord.Member], give: list[discord.Role] = None,
                                   remove: list[discord.Role] = None, nick: str = None, member: discord.Member = None,
                                   member_update=False):
    if not ctx.guild: return None
    me = ctx.guild.me
    if member_update:
        member = ctx
    else:
        member = member or ctx.author
    if member == me: return
    if not me.guild_permissions.manage_roles: return
    give = give or []
    remove = remove or []
    roles_to_add = [r for r in give if r and r < me.top_role and r not in member.roles]
    roles_to_remove = [r for r in remove if r and r < me.top_role and r in member.roles]
    try:
        if roles_to_add: await member.add_roles(*roles_to_add, reason="Profile Update")
        if roles_to_remove: await member.remove_roles(*roles_to_remove, reason="Profile Update")
        if nick and me.guild_permissions.manage_nicknames and me.top_role > member.top_role:
            if member.guild.owner != member: await member.edit(nick=nick)
    except discord.Forbidden:
        logger.warning(f"Failed to update roles/nick for {member.id}: Forbidden")
    except discord.HTTPException as e:
        logger.error(f"Failed to update roles/nick for {member.id}: {e}")


def get_role_named(guild, name):
    if not guild or not name: return None
    name = str(name).strip().lower()
    for role in guild.roles:
        if role.name.lower().strip() == name: return role
    return None