from redbot.core.config import Config

# Relative import to ensure portability
from .constants import (
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
    def __init__(self):
        # AccountManager: Stores game usernames (Steam, Origin, etc.)
        self.AccountManager = Config.get_conf(
            None, identifier=1273062035, force_registration=True, cog_name="AccountManager"
        )

        # GamingProfile: Stores region, timezone, bio
        self.GamingProfile = Config.get_conf(
            None, identifier=9420012589, force_registration=True, cog_name="GamingProfile"
        )

        # PCSpecs: Stores hardware details
        self.PCSpecs = Config.get_conf(
            None, identifier=8205491788, force_registration=True, cog_name="PCSpecs"
        )

        # PublisherManager: Stores supported game services
        self.PublisherManager = Config.get_conf(
            None, identifier=2064553666, force_registration=True, cog_name="PublisherManager",
        )

        # PlayerStatus: Tracks active players
        self.PlayerStatus = Config.get_conf(
            None, identifier=3584065639, force_registration=True, cog_name="PlayerStatus"
        )

        # LogoData: Stores icons for services
        self.LogoData = Config.get_conf(
            None, identifier=7056820599, force_registration=True, cog_name="LogoData"
        )

        # DynamicChannels: Auto-creating voice channels
        self.DynamicChannels = Config.get_conf(
            None, identifier=3172784244, force_registration=True, cog_name="DynamicChannels"
        )

        # CustomChannels: User-managed channels
        self.CustomChannels = Config.get_conf(
            None, identifier=7861412794, force_registration=True, cog_name="CustomChannels"
        )

        # RandomQuotes: (Legacy?)
        self.RandomQuotes = Config.get_conf(
            None, identifier=8475527184, force_registration=True, cog_name="RandomQuotes"
        )

        # BFVUserIDs: Specific IDs for Battlefield V
        self.BFV_USER_IDS = Config.get_conf(
            None, identifier=8475527184, force_registration=True, cog_name="BFVUserIDs"
        )


# Initialize the singleton
ConfigHolder = ConfigHolderClass()

# --- DEFAULT SCHEMAS ---
# We define these here but they should be registered in the respective cogs' __init__
# to ensure they are applied only when needed.

default_member_AccountManager = {"account": {"origin": None, "uplay": None}}

default_member_GamingProfile = {
    "discord_user_id": None,
    "discord_user_name": None,
    "discord_true_name": None,
    "guild_display_name": None,
    "is_bot": False,
    "country": None,
    "timezone": None,
    "language": None,
    "zone": None,
    "subzone": None,
    "seen": None,
    "trial": None,
    "nickname_extas": None,
}

default_member_PCSpecs = {
    "rig": {
        "CPU": None,
        "GPU": None,
        "RAM": None,
        "Motherboard": None,
        "Storage": None,
        "Monitor": None,
        "Mouse": None,
        "Keyboard": None,
        "Case": None,
        "Headset": None
    }
}

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
        "youtube": {"name": "YouTube", "identifier": "youtube", "games": []},
    }
}