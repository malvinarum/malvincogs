# Standard library imports
import json
import logging

# Third-party imports
import aiohttp # For making asynchronous HTTP requests
import discord # For discord.Message type hinting

# Redbot imports
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import pagify

# Set up logging for the cog
log = logging.getLogger("red.gemini")

# Define the default settings for the cog's configuration
# This will store the API key, allowed channels for commands,
# and settings for the conversational mode.
DEFAULT_GUILD_SETTINGS = {
    "api_key": None,
    "allowed_channels": [], # Channels where `[p]gemini ask` command is allowed
    "conversation_enabled": False, # New: Whether conversational mode is active
    "listen_channel_id": None, # New: The channel ID where the bot will listen for conversations
}

class Gemini(commands.Cog):
    """
    A Redbot cog to interact with the Gemini API.

    This cog allows users to send prompts to a Gemini large language model
    and receive responses directly in Discord, either via a command or
    through a designated conversational channel.
    """

    def __init__(self, bot):
        """
        Initializes the Gemini cog.

        Args:
            bot: The Redbot instance.
        """
        self.bot = bot
        # Initialize the Config object to store guild-specific settings
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )
        # Register the default settings
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        # Initialize an aiohttp session for making HTTP requests
        self.session = aiohttp.ClientSession()
        log.info("Gemini cog initialized.")

    async def red_delete_data_for_user(self, **kwargs):
        """
        This is a mandatory Redbot method.
        It's called when a user's data needs to be deleted due to GDPR or similar requests.
        Since this cog doesn't store user-specific data, we can pass.
        """
        return

    def cog_unload(self):
        """
        Called when the cog is unloaded.
        Closes the aiohttp session to prevent resource leaks.
        """
        self.bot.loop.create_task(self.session.close())
        log.info("Gemini cog unloaded and aiohttp session closed.")

    async def _get_gemini_response(self, ctx: commands.Context, prompt: str):
        """
        Helper method to call the Gemini API and send the response.
        Handles API key checks, network requests, and error handling.
        """
        api_key = await self.config.guild(ctx.guild).api_key()

        if not api_key:
            await ctx.send(
                "The Gemini API key has not been set. "
                f"Please ask the bot owner to set it using `{ctx.prefix}gemini setkey <your_api_key>`."
            )
            return None # Indicate failure

        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ]
        }
        headers = {
            "Content-Type": "application/json"
        }

        message = await ctx.send("Thinking... please wait.") # Provide a loading indicator

        try:
            async with self.session.post(api_url, headers=headers, data=json.dumps(payload)) as response:
                response.raise_for_status()
                result = await response.json()

            if result.get("candidates") and result["candidates"][0].get("content") and result["candidates"][0]["content"].get("parts"):
                generated_text = result["candidates"][0]["content"]["parts"][0]["text"]
                for page in pagify(generated_text, delims=["\n", " "], escape_mass_mentions=True):
                    await ctx.send(page)
                log.info(f"Successfully responded to Gemini prompt from guild: {ctx.guild.id}")
                return generated_text # Return the generated text
            else:
                await ctx.send("Could not get a valid response from the Gemini API. The model might not have generated any content.")
                log.warning(f"Unexpected Gemini API response structure: {result} for guild: {ctx.guild.id}")
                return None

        except aiohttp.ClientError as e:
            await ctx.send(f"An HTTP error occurred while trying to reach the Gemini API: {e}")
            log.error(f"HTTP error during Gemini API call for guild {ctx.guild.id}: {e}")
            return None
        except json.JSONDecodeError as e:
            await ctx.send(f"Failed to parse the API response: {e}")
            log.error(f"JSON decode error during Gemini API call for guild {ctx.guild.id}: {e}")
            return None
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
            log.error(f"Unexpected error during Gemini API call for guild {ctx.guild.id}: {e}", exc_info=True)
            return None
        finally:
            try:
                await message.delete()
            except Exception:
                pass

    @commands.group(name="gemini", invoke_without_command=True)
    @commands.guild_only() # Ensure commands are only used in guilds
    async def _gemini(self, ctx: commands.Context):
        """
        Base command for Gemini API interactions.

        Use `[p]gemini setkey <your_api_key>` to set the API key.
        Use `[p]gemini ask <your_prompt>` to interact with the model.
        Use `[p]gemini addchannel #channel` to allow `ask` interactions in a specific channel.
        Use `[p]gemini removechannel #channel` to disallow `ask` interactions in a specific channel.
        Use `[p]gemini listchannels` to see allowed `ask` channels.
        Use `[p]gemini setconversationchannel #channel` to set a channel for conversational AI.
        Use `[p]gemini enableconversation` to enable conversational mode.
        Use `[p]gemini disableconversation` to disable conversational mode.
        """
        await ctx.send_help(self._gemini)

    @_gemini.command(name="setkey")
    @commands.is_owner() # Only the bot owner can set the API key
    async def _gemini_setkey(self, ctx: commands.Context, api_key: str):
        """
        Sets the Gemini API key for this guild.

        This key is stored securely in Redbot's configuration.
        You can obtain a Gemini API key from Google AI Studio.
        """
        await self.config.guild(ctx.guild).api_key.set(api_key)
        await ctx.send("Gemini API key has been set securely.")
        log.info(f"Gemini API key set for guild: {ctx.guild.id}")

    @_gemini.command(name="addchannel")
    @commands.admin_or_permissions(manage_channels=True) # Only admins or those with manage_channels can use this
    async def _gemini_addchannel(self, ctx: commands.Context, channel: discord.TextChannel): # Changed to discord.TextChannel
        """
        Adds a channel to the list of allowed channels for `[p]gemini ask` command interactions.

        If no channels are added, `[p]gemini ask` can be used anywhere.
        If channels are added, `[p]gemini ask` can ONLY be used in these channels.
        This does NOT affect the conversational mode channel.
        """
        async with self.config.guild(ctx.guild).allowed_channels() as allowed_channels:
            if channel.id not in allowed_channels:
                allowed_channels.append(channel.id)
                await ctx.send(f"`[p]gemini ask` command interactions are now allowed in {channel.mention}.")
                log.info(f"Added channel {channel.id} to allowed channels for `ask` command in guild: {ctx.guild.id}")
            else:
                await ctx.send(f"{channel.mention} is already in the allowed list for `ask` command.")

    @_gemini.command(name="removechannel")
    @commands.admin_or_permissions(manage_channels=True) # Only admins or those with manage_channels can use this
    async def _gemini_removechannel(self, ctx: commands.Context, channel: discord.TextChannel): # Changed to discord.TextChannel
        """
        Removes a channel from the list of allowed channels for `[p]gemini ask` command interactions.
        """
        async with self.config.guild(ctx.guild).allowed_channels() as allowed_channels:
            if channel.id in allowed_channels:
                allowed_channels.remove(channel.id)
                await ctx.send(f"`[p]gemini ask` command interactions are no longer allowed in {channel.mention}.")
                log.info(f"Removed channel {channel.id} from allowed channels for `ask` command in guild: {ctx.guild.id}")
            else:
                await ctx.send(f"{channel.mention} was not in the allowed list for `ask` command.")

    @_gemini.command(name="listchannels")
    @commands.admin_or_permissions(manage_channels=True) # Only admins or those with manage_channels can use this
    async def _gemini_listchannels(self, ctx: commands.Context):
        """
        Lists all channels where `[p]gemini ask` command interactions are currently allowed.
        """
        allowed_channel_ids = await self.config.guild(ctx.guild).allowed_channels()
        if not allowed_channel_ids:
            await ctx.send("`[p]gemini ask` command interactions are currently allowed in all channels.")
            return

        channel_mentions = []
        for channel_id in allowed_channel_ids:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_mentions.append(channel.mention)
            else:
                channel_mentions.append(f"Unknown Channel (ID: {channel_id})")

        if channel_mentions:
            message = "`[p]gemini ask` command interactions are currently allowed in the following channels:\n" + "\n".join(channel_mentions)
        else:
            message = "No specific channels are set for `[p]gemini ask` command interactions. It can be used anywhere."

        await ctx.send(message)

    @_gemini.command(name="setconversationchannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def _gemini_setconversationchannel(self, ctx: commands.Context, channel: discord.TextChannel): # Changed to discord.TextChannel
        """
        Sets the channel where Gemini will listen for conversational interactions.
        """
        await self.config.guild(ctx.guild).listen_channel_id.set(channel.id)
        await ctx.send(f"Gemini will now listen for conversations in {channel.mention}.")
        log.info(f"Set conversation channel to {channel.id} for guild: {ctx.guild.id}")

    @_gemini.command(name="enableconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _gemini_enableconversation(self, ctx: commands.Context):
        """
        Enables conversational mode for Gemini in the designated channel.
        """
        listen_channel_id = await self.config.guild(ctx.guild).listen_channel_id()
        if not listen_channel_id:
            await ctx.send(
                f"Please set a conversation channel first using `{ctx.prefix}gemini setconversationchannel #channel`."
            )
            return

        await self.config.guild(ctx.guild).conversation_enabled.set(True)
        channel = ctx.guild.get_channel(listen_channel_id)
        if channel:
            await ctx.send(f"Conversational mode enabled. Gemini will respond to messages in {channel.mention}.")
        else:
            await ctx.send("Conversational mode enabled, but the designated channel is invalid. Please set a valid channel.")
        log.info(f"Conversational mode enabled for guild: {ctx.guild.id}")

    @_gemini.command(name="disableconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _gemini_disableconversation(self, ctx: commands.Context):
        """
        Disables conversational mode for Gemini.
        """
        await self.config.guild(ctx.guild).conversation_enabled.set(False)
        await ctx.send("Conversational mode disabled.")
        log.info(f"Conversational mode disabled for guild: {ctx.guild.id}")

    @_gemini.command(name="ask")
    @app_commands.describe(prompt="The question or prompt to send to the Gemini model.")
    async def _gemini_ask(self, ctx: commands.Context, *, prompt: str):
        """
        Sends a prompt to the Gemini 2.0 Flash model and returns the response.

        This command respects the `allowed_channels` setting.
        Example:
        `[p]gemini ask What is the capital of France?`
        """
        # Check if the current channel is allowed for the command
        allowed_channel_ids = await self.config.guild(ctx.guild).allowed_channels()
        if allowed_channel_ids and ctx.channel.id not in allowed_channel_ids:
            allowed_mentions = [ctx.guild.get_channel(cid).mention for cid in allowed_channel_ids if ctx.guild.get_channel(cid)]
            if allowed_mentions:
                await ctx.send(
                    f"`[p]gemini ask` command interactions are restricted to specific channels. "
                    f"Please use this command in one of the following channels: {', '.join(allowed_mentions)}."
                )
            else:
                await ctx.send(
                    "`[p]gemini ask` command interactions are restricted to specific channels, but none are configured or valid. "
                    "Please ask an admin to configure allowed channels."
                )
            return

        await self._get_gemini_response(ctx, prompt)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listens for messages to enable conversational interaction with Gemini.
        """
        # Ignore messages from bots to prevent infinite loops
        if message.author.bot:
            return

        # Only process messages in guilds
        if not message.guild:
            return

        # Check if conversational mode is enabled for this guild
        conversation_enabled = await self.config.guild(message.guild).conversation_enabled()
        if not conversation_enabled:
            return

        # Check if the message is in the designated conversation channel
        listen_channel_id = await self.config.guild(message.guild).listen_channel_id()
        if not listen_channel_id or message.channel.id != listen_channel_id:
            return

        # Check if the message is a command to avoid processing commands as prompts
        # This is crucial to prevent the bot from responding to its own commands
        ctx = await self.bot.get_context(message)
        if ctx.valid: # If ctx.valid is True, it means the message is a command
            return

        # If all checks pass, send the message content to Gemini
        # We create a dummy context for the helper function
        # The message object itself can act as a rudimentary context for sending replies
        # For full context features, you might need to build a more complete ctx object
        # but for simple replies, message.channel.send is sufficient.
        # However, _get_gemini_response expects a commands.Context object.
        # So, we'll use the ctx object we just created, knowing it's not a command.
        await self._get_gemini_response(ctx, message.content)
