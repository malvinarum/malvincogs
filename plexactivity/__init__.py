def __init__(self, bot):
    self.bot = bot
    self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
    self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
    self.session = aiohttp.ClientSession()
    self._plex_activity_loop_task = None
    self.color_cache = {}

    # Spotify Cache
    self.spotify_token = None
    self.spotify_token_expires = 0