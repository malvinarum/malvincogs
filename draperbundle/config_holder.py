from redbot.core.config import Config

from draperbundle.constants import (
    anthem_icon,
    apex_icon,
    bfv_icon,
    csgo_icon,
    division_2_icon,
    lol_icon,
    minecraft_icon,
    osrs_icon,
)


class ConfigHolderClass:
    AccountManager = Config.get_conf(
        None, identifier=1273062035, force_registration=True, cog_name="AccountManager"
    )
    GamingProfile = Config.get_conf(
        None, identifier=9420012589, force_registration=True, cog_name="GamingProfile"
    )
    PCSpecs = Config.get_conf(
        None, identifier=8205491788, force_registration=True, cog_name="PCSpecs"
    )
    PublisherManager = Config.get_conf(
        None,
        identifier=2064553666,
        force_registration=True,
        cog_name="PublisherManager",
    )
    PlayerStatus = Config.get_conf(
        None, identifier=3584065639, force_registration=True, cog_name="PlayerStatus"
    )
    LogoData = Config.get_conf(
        None, identifier=7056820599, force_registration=True, cog_name="LogoData"
    )
    DynamicChannels = Config.get_conf(
        None, identifier=3172784244, force_registration=True, cog_name="DynamicChannels"
    )
    CustomChannels = Config.get_conf(
        None, identifier=7861412794, force_registration=True, cog_name="CustomChannels"
    )
    RandomQuotes = Config.get_conf(
        None, identifier=8475527184, force_registration=True, cog_name="RandomQuotes"
    )
    BFV_USER_IDS = Config.get_conf(
        None, identifier=8475527184, force_registration=True, cog_name="BFVUserIDs"
    )


ConfigHolder = ConfigHolderClass()

default_member_AccountManager = dict(account=dict(origin=None, uplay=None))
default_member_GamingProfile = dict(
    discord_user_id=None,
    discord_user_name=None,
    discord_true_name=None,
    guild_display_name=None,
    is_bot=False,
    country=None,
    timezone=None,
    language=None,
    zone=None,
    subzone=None,
    seen=None,
    trial=None,
    nickname_extas=None,
)
default_member_PCSpecs = dict(
    rig=dict(
        CPU=None,
        GPU=None,
        RAM=None,
        Motherboard=None,
        Storage=None,
        Monitor=None,
        Mouse=None,
        Keyboard=None,
        Case=None,
        Headset=None
    )
)
default_custom_PublisherManager = {
    "services": {
        "battlenet": {
            "name": "Battle.net",
            "identifier": "battlenet",
            "games": ["Call of Duty: Modern Warfare"],
        },
        "epic": {"name": "Epic Games", "identifier": "epic", "games": []},
        "gog": {"name": "GOG.com", "identifier": "gog", "games": []},
        "mixer": {"name": "Mixer", "identifier": "mixer", "games": []},
        "psn": {"name": "PlayStation Network", "identifier": "psn", "games": []},
        "reddit": {"name": "Reddit", "identifier": "reddit", "games": []},
        "riot": {
            "name": "Riot Games",
            "identifier": "riot",
            "games": ["League of Legends"],
        },
        "spotify": {"name": "Spotify", "identifier": "spotify", "games": []},
        "steam": {"name": "Steam", "identifier": "steam", "games": []},
        "twitch": {"name": "Twitch", "identifier": "twitch", "games": []},
        "twitter": {"name": "Twitter", "identifier": "twitter", "games": []},
        "uplay": {
            "name": "Uplay",
            "identifier": "uplay",
            "games": ["Tom Clancy's The Division 2"],
        },
        "xbox": {"name": "Xbox Live", "identifier": "xbox", "games": []},
        "youtube": {"name": "YouTube", "identifier": "