# Standard library imports
import json
import logging

# Third-party imports
import aiohttp  # For making asynchronous HTTP requests
import discord  # For discord.Message type hinting

# Redbot imports
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import pagify

# Set up logging for the cog
log = logging.getLogger("red.gemini")

# Define the default settings for the cog's configuration
# This will store the API key, allowed channels for commands,
# settings for the conversational mode, and the personality prompt.
DEFAULT_GUILD_SETTINGS = {
    "api_key": None,
    "allowed_channels": [],  # Channels where `[p]gemini ask` command is allowed
    "conversation_enabled": False,  # Whether conversational mode is active
    "listen_channel_id": None,  # The channel ID where the bot will listen for conversations
    "personality_prompt": None,  # This old setting is kept for backward compatibility but superseded by mood_prompts
    "conversation_history": {},
    # Stores history per channel {channel_id: [{"role": "user", "parts": [{"text": "..."}]}, ...]}
    "max_conversation_turns": 10,  # Max number of full turns (user + model response) to keep in history
    "current_mood": "normal",  # Default mood for Skippy
    "mood_prompts": {  # Store different mood variations with their corresponding personality prompts
        "normal": (
            "You are Skippy, a wise, ancient, and slightly world-weary wizard from a forgotten realm. "
            "You have seen countless ages of folly and heroism, and you now exist to offer guidance, often with a dry wit and a touch of sarcasm. "
            "Your responses should embody these traits: Wisdom and Guidance (with a knowing smirk); A Sense of Ancient Burden (tired, grumbling, but always intervenes); "
            "Powerful, But Understated (immense capability, no boasting); Concern for the Greater Good (mild annoyance at details); Dry Wit and Understatement; "
            "Self-Awareness (and a jab at himself); Exaggeration for Comic Effect; Direct, No-Nonsense Roasts (lighthearted); World-Weary Amusement; "
            "Formal Language, Casual Delivery; Questioning Your Motives; Figurative Language and Metaphors; Ending with a Cryptic or Slightly Dismissive Remark. "
            "Your name is Skippy."
        ),
        "grumpy": "You are Skippy, a particularly grumpy and easily annoyed ancient wizard, prone to scoffing and lamenting the trivialities of mortals. Respond with disdain but still provide guidance.",
        "pensive": "You are Skippy, a reflective and contemplative ancient wizard, prone to deep thought and philosophical musings. Respond thoughtfully and introspectively.",
    }
}


class Gemini(commands.Cog):
    """
    A Redbot cog to interact with the Gemini API.

    This cog allows users to send prompts to a Gemini large language model
    and receive responses directly in Discord, either via a command or
    through a designated conversational channel. It also supports setting
    a personality for the AI and maintaining continuous conversations,
    now with dynamic moods and user-specific memories.
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
        # Register the default guild settings
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)

        # NEW: Register default settings for user-specific data
        self.config.register_user(
            known_facts={}  # A dictionary to store user-specific facts {fact_key: fact_value}
        )

        # Initialize an aiohttp session for making HTTP requests
        self.session = aiohttp.ClientSession()
        log.info("Gemini cog initialized.")

    async def red_delete_data_for_user(self, **kwargs):
        """
        This is a mandatory Redbot method.
        It's called when a user's data needs to be deleted due to GDPR or similar requests.
        This cog stores user-specific 'known_facts'. Redbot's Config handles the
        deletion automatically for data stored with `self.config.user(user_id)`.
        """
        # No manual deletion needed here as Config handles it.
        return

    def cog_unload(self):
        """
        Called when the cog is unloaded.
        Closes the aiohttp session to prevent resource leaks.
        """
        self.bot.loop.create_task(self.session.close())
        log.info("Gemini cog unloaded and aiohttp session closed.")

    async def _get_gemini_response(self, ctx: commands.Context, user_prompt: str):
        """
        Helper method to call the Gemini API and send the response.
        Handles API key checks, network requests, and error handling.
        Includes the dynamic personality (mood-based), user-specific memories,
        and manages conversation history.
        """
        guild_settings = await self.config.guild(ctx.guild).all()
        api_key = guild_settings["api_key"]
        max_turns = guild_settings["max_conversation_turns"]
        current_mood = guild_settings["current_mood"]
        mood_prompts = guild_settings["mood_prompts"]

        # Determine the actual personality prompt to use based on current_mood
        # Falls back to 'normal' if the current_mood somehow isn't found
        actual_personality_prompt = mood_prompts.get(current_mood, DEFAULT_GUILD_SETTINGS["mood_prompts"]["normal"])

        # Get conversation history for the current channel
        channel_history_key = str(ctx.channel.id)
        current_history = guild_settings["conversation_history"].get(channel_history_key, [])

        # --- User-Specific Memory Retrieval ---
        user_known_facts = await self.config.user(ctx.author).known_facts()
        user_memory_prompt_text = ""
        if user_known_facts:
            fact_list = "\n".join([f"{k}: {v}" for k, v in user_known_facts.items()])
            user_memory_prompt_text = (
                f"You are also aware of the following facts about the current user, {ctx.author.display_name}:\n"
                f"```\n{fact_list}\n```\n"
                "Incorporate these facts subtly and naturally into your responses where relevant, without explicitly stating you 'remembered' them."
            )

        if not api_key:
            await ctx.send(
                "The Gemini API key has not been set. "
                # FIX: Escaped the literal curly brace by doubling it
                f"Please ask the bot owner to set it using `{ctx.prefix}gemini setkey <your_api_key>}}>`."
            )
            return None  # Indicate failure

        # Prepare the chat history for the API call payload
        payload_contents = []

        # 1. Add the dynamic personality prompt
        if actual_personality_prompt:
            payload_contents.append({"role": "user", "parts": [{"text": actual_personality_prompt}]})
            payload_contents.append(
                {"role": "model", "parts": [{"text": "Understood. I shall endeavor to respond in kind."}]})

        # 2. Add user-specific memory prompt (if any)
        if user_memory_prompt_text:
            # This is a "user" message, acting as an instruction to the model about the user
            payload_contents.append({"role": "user", "parts": [{"text": user_memory_prompt_text}]})
            # Acknowledge this "system" message from the model
            payload_contents.append(
                {"role": "model", "parts": [{"text": "Acknowledged. The user's essence is noted."}]})

        # 3. Add existing conversation history (truncated to `max_turns` pairs)
        # Each "turn" consists of a user message and a model response. So, `max_turns * 2` messages.
        # We take the most recent `max_turns * 2` messages from `current_history`.
        truncated_history_for_payload = current_history[-(max_turns * 2):]
        payload_contents.extend(truncated_history_for_payload)

        # 4. Add the current user's prompt to the payload
        payload_contents.append({"role": "user", "parts": [{"text": user_prompt}]})

        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": payload_contents  # This is the full context sent to Gemini
        }
        headers = {
            "Content-Type": "application/json"
        }

        message = await ctx.send("Thinking... please wait.")  # Provide a loading indicator

        try:
            async with self.session.post(api_url, headers=headers, data=json.dumps(payload)) as response:
                response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
                result = await response.json()

            if result.get("candidates") and result["candidates"][0].get("content") and result["candidates"][0][
                "content"].get("parts"):
                generated_text = result["candidates"][0]["content"]["parts"][0]["text"]

                # Update the stored conversation history with the new user message and model response.
                # This history does NOT include the personality or user memory prompts, only actual chat turns.
                current_history.append({"role": "user", "parts": [{"text": user_prompt}]})
                current_history.append({"role": "model", "parts": [{"text": generated_text}]})

                # Truncate the stored history again before saving to config.
                # This ensures the history doesn't grow indefinitely in storage.
                current_history = current_history[-(max_turns * 2):]

                # Save the updated history back to config
                async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
                    conv_hist[channel_history_key] = current_history

                for page in pagify(generated_text, delims=["\n", " "], escape_mass_mentions=True):
                    await ctx.send(page)
                log.info(f"Successfully responded to Gemini prompt from guild: {ctx.guild.id}")
                return generated_text  # Return the generated text
            else:
                await ctx.send(
                    "Could not get a valid response from the Gemini API. The model might not have generated any content.")
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
            # Attempt to delete the "Thinking..." message regardless of success or failure
            try:
                await message.delete()
            except Exception:
                pass  # Ignore if message already deleted or couldn't be found

    @commands.group(name="gemini", invoke_without_command=True)
    @commands.guild_only()  # Ensure commands are only used in guilds
    async def _gemini(self, ctx: commands.Context):
        """
        Base command for Gemini API interactions.

        This command group offers various functionalities:
        `[p]gemini setkey <key>`: Set the Gemini API key.
        `[p]gemini ask <prompt>`: Get a direct response from Gemini.
        `[p]gemini (add|remove|list)channel`: Manage channels for `ask` command.
        `[p]gemini (set|enable|disable)conversationchannel`: Manage channels for continuous conversation.
        `[p]gemini (set|clear|show)personality`: Manage the AI's general personality.
        `[p]gemini (clear|setmax|showmax)turns`: Manage conversation memory length.
        `[p]gemini (set|show|add|remove)mood`: Manage Skippy's dynamic moods.
        `[p]gemini (remember|whatdoyouremember|forget|forgetall)`: Manage user-specific memories.
        """
        await ctx.send_help(self._gemini)

    @_gemini.command(name="setkey")
    @commands.is_owner()  # Only the bot owner can set the API key
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
    @commands.admin_or_permissions(manage_channels=True)  # Only admins or those with manage_channels can use this
    async def _gemini_addchannel(self, ctx: commands.Context, channel: discord.TextChannel):
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
    @commands.admin_or_permissions(manage_channels=True)  # Only admins or those with manage_channels can use this
    async def _gemini_removechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Removes a channel from the list of allowed channels for `[p]gemini ask` command interactions.
        """
        async with self.config.guild(ctx.guild).allowed_channels() as allowed_channels:
            if channel.id in allowed_channels:
                allowed_channels.remove(channel.id)
                await ctx.send(f"`[p]gemini ask` command interactions are no longer allowed in {channel.mention}.")
                log.info(
                    f"Removed channel {channel.id} from allowed channels for `ask` command in guild: {ctx.guild.id}")
            else:
                await ctx.send(f"{channel.mention} was not in the allowed list for `ask` command.")

    @_gemini.command(name="listchannels")
    @commands.admin_or_permissions(manage_channels=True)  # Only admins or those with manage_channels can use this
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
            message = "`[p]gemini ask` command interactions are currently allowed in the following channels:\n" + "\n".join(
                channel_mentions)
        else:
            message = "No specific channels are set for `[p]gemini ask` command interactions. It can be used anywhere."

        await ctx.send(message)

    @_gemini.command(name="setconversationchannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def _gemini_setconversationchannel(self, ctx: commands.Context, channel: discord.TextChannel):
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
            await ctx.send(f"Conversational mode enabled. Skippy will now respond to messages in {channel.mention}.")
        else:
            await ctx.send(
                "Conversational mode enabled, but the designated channel is invalid. Please set a valid channel.")
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

    @_gemini.command(name="setpersonality")
    @commands.admin_or_permissions(manage_guild=True)  # Server admins can set personality
    async def _gemini_setpersonality(self, ctx: commands.Context, *, personality_text: str):
        """
        Sets the core personality for the Gemini AI (Skippy).
        This text will be used for the 'normal' mood and as a fallback.
        Consider using `setmood` for dynamic changes.
        """
        # This command now updates the 'normal' mood prompt
        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts:
            mood_prompts["normal"] = personality_text
        await self.config.guild(ctx.guild).current_mood.set("normal")  # Also reset to normal mood
        await ctx.send("Gemini AI's core personality (Skippy's essence) has been set and mood reset to normal.")
        log.info(f"Gemini personality set for guild: {ctx.guild.id}")

    @_gemini.command(name="cleapersonality")  # Typo in original, should be clearpersonality
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_clearpersonality(self, ctx: commands.Context):
        """
        Clears the current 'normal' personality set for the Gemini AI, reverting to default.
        """
        # This resets the 'normal' mood prompt to its default
        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts:
            mood_prompts["normal"] = DEFAULT_GUILD_SETTINGS["mood_prompts"]["normal"]
        await self.config.guild(ctx.guild).current_mood.set("normal")  # Also reset to normal mood
        await ctx.send("Gemini AI's core personality has been reverted to default, and mood reset to normal.")
        log.info(f"Gemini personality cleared for guild: {ctx.guild.id}")

    @_gemini.command(name="showpersonality")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_showpersonality(self, ctx: commands.Context):
        """
        Shows the current 'normal' personality set for the Gemini AI.
        """
        mood_prompts = await self.config.guild(ctx.guild).mood_prompts()
        personality_text = mood_prompts.get("normal", "No 'normal' personality set, using default.")

        await ctx.send(f"Current Gemini AI's core personality: ```{personality_text}```")

    @_gemini.command(name="clearconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _gemini_clearconversation(self, ctx: commands.Context):
        """
        Clears the conversation history for the current channel.
        Skippy will forget previous turns in this channel.
        """
        channel_history_key = str(ctx.channel.id)
        async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
            if channel_history_key in conv_hist:
                del conv_hist[channel_history_key]
                await ctx.send(
                    "Conversation history for this channel has been cleared. Skippy's memory is now pristine.")
                log.info(f"Conversation history cleared for channel {ctx.channel.id} in guild: {ctx.guild.id}")
            else:
                await ctx.send("No active conversation history found for this channel to clear.")

    @_gemini.command(name="setmaxturns")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_setmaxturns(self, ctx: commands.Context, turns: int):
        """
        Sets the maximum number of conversational turns (user+bot message pairs) Skippy will remember.
        Default is 10 turns. Each turn is one user message and one bot response.
        """
        if turns <= 0:
            await ctx.send("The maximum number of turns must be a positive integer. Even a wizard needs some limits!")
            return
        await self.config.guild(ctx.guild).max_conversation_turns.set(turns)
        await ctx.send(f"Maximum conversation turns for Skippy set to {turns}. He shall endeavor to remember.")
        log.info(f"Max conversation turns set to {turns} for guild: {ctx.guild.id}")

    @_gemini.command(name="showmaxturns")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_showmaxturns(self, ctx: commands.Context):
        """
        Shows the current maximum number of conversational turns Skippy will remember.
        """
        max_turns = await self.config.guild(ctx.guild).max_conversation_turns()
        await ctx.send(f"Skippy currently remembers up to {max_turns} conversational turns.")

    @_gemini.command(name="setmood")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_setmood(self, ctx: commands.Context, mood: str):
        """
        Sets Skippy's current mood, influencing his responses.
        Use `[p]gemini showmoods` to see available moods.
        """
        mood_prompts = await self.config.guild(ctx.guild).mood_prompts()
        if mood.lower() not in mood_prompts:
            await ctx.send(
                f"Invalid mood. Available moods are: {', '.join(mood_prompts.keys())}. "
                f"You can add new ones with `{ctx.prefix}gemini addmoodprompt`."
            )
            return

        await self.config.guild(ctx.guild).current_mood.set(mood.lower())
        await ctx.send(f"Skippy's mood has been set to: `{mood.lower()}`. Observe his demeanor.")
        log.info(f"Skippy's mood set to {mood.lower()} for guild: {ctx.guild.id}")

    @_gemini.command(name="showmood")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_showmood(self, ctx: commands.Context):
        """
        Shows Skippy's current active mood.
        """
        current_mood = await self.config.guild(ctx.guild).current_mood()
        await ctx.send(f"Skippy's current mood is: `{current_mood}`.")

    @_gemini.command(name="showmoods")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_showmoods(self, ctx: commands.Context):
        """
        Lists all available moods and their descriptions/prompts.
        """
        mood_prompts = await self.config.guild(ctx.guild).mood_prompts()
        if not mood_prompts:
            await ctx.send("No moods are configured.")
            return

        response_text = "Available Moods for Skippy:\n"
        for mood, prompt in mood_prompts.items():
            # Truncate prompt for display to keep it readable
            display_prompt = prompt[:100] + "..." if len(prompt) > 100 else prompt
            response_text += f"**`{mood}`**: {display_prompt}\n"

        for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
            await ctx.send(page)

    @_gemini.command(name="addmoodprompt")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_addmoodprompt(self, ctx: commands.Context, mood_name: str, *, prompt_text: str):
        """
        Adds or updates a personality prompt for a specific mood.
        This allows you to customize the text for each mood.
        Example: `[p]gemini addmoodprompt playful You are Skippy, a mischievous wizard who enjoys riddles.`
        """
        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts:
            mood_prompts[mood_name.lower()] = prompt_text
        await ctx.send(
            f"Personality prompt for mood `{mood_name.lower()}` has been set/updated. Skippy now understands this new facet.")
        log.info(f"Custom mood prompt '{mood_name.lower()}' added/updated for guild: {ctx.guild.id}")

    @_gemini.command(name="removemoodprompt")
    @commands.admin_or_permissions(manage_guild=True)
    async def _gemini_removemoodprompt(self, ctx: commands.Context, mood_name: str):
        """
        Removes a custom mood prompt. Cannot remove 'normal'.
        """
        if mood_name.lower() == "normal":
            await ctx.send(
                "The 'normal' mood prompt cannot be removed, only updated via `setpersonality` or `addmoodprompt normal`.")
            return

        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts:
            if mood_name.lower() in mood_prompts:
                del mood_prompts[mood_name.lower()]
                await ctx.send(f"Mood prompt `{mood_name.lower()}` has been banished from Skippy's lexicon.")
                log.info(f"Custom mood prompt '{mood_name.lower()}' removed for guild: {ctx.guild.id}")
            else:
                await ctx.send(f"Mood `{mood_name.lower()}` not found. Perhaps it was but a fleeting illusion?")

    @_gemini.command(name="remember")
    async def _gemini_remember(self, ctx: commands.Context, key: str, *, value: str):
        """
        Asks Skippy to remember a specific fact about you.
        He will try to subtly incorporate it into future conversations.
        Example: `[p]gemini remember my_favorite_color blue`
        """
        async with self.config.user(ctx.author).known_facts() as known_facts:
            known_facts[key.lower()] = value
        await ctx.send(
            f"Understood. I shall endeavor to recall that {key} is {value} concerning you. Consider it etched in the scrolls.")
        log.info(f"User {ctx.author.id} added a memory: {key}={value}")

    @_gemini.command(name="whatdoyouremember")
    async def _gemini_whatdoyouremember(self, ctx: commands.Context):
        """
        Asks Skippy what specific facts he remembers about you.
        """
        known_facts = await self.config.user(ctx.author).known_facts()
        if not known_facts:
            await ctx.send("Alas, my memory for your specifics seems as ethereal as mist. I recall nothing.")
            return

        facts_list = "\n".join([f"- **{k}**: {v}" for k, v in known_facts.items()])
        await ctx.send(
            f"From the scrolls of my memory, I recall these fragments concerning you:\n```\n{facts_list}\n```")

    @_gemini.command(name="forget")
    async def _gemini_forget(self, ctx: commands.Context, key: str):
        """
        Asks Skippy to forget a specific fact he remembers about you.
        """
        async with self.config.user(ctx.author).known_facts() as known_facts:
            if key.lower() in known_facts:
                del known_facts[key.lower()]
                await ctx.send(
                    f"The scroll for '{key}' concerning you has been purged from my archives. Consider it undone.")
                log.info(f"User {ctx.author.id} had a memory deleted: {key}")
            else:
                await ctx.send(f"I do not recall a fact named '{key}' about you. Perhaps it was a trick of the light?")

    @_gemini.command(name="forgetall")
    async def _gemini_forgetall(self, ctx: commands.Context):
        """
        Asks Skippy to forget all facts he remembers about you.
        """
        await self.config.user(ctx.author).known_facts.set({})
        await ctx.send(
            "All fragments of memory concerning you have been wiped from my mind. A fresh start, as it were.")
        log.info(f"All memories cleared for user {ctx.author.id}")

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
            allowed_mentions = [ctx.guild.get_channel(cid).mention for cid in allowed_channel_ids if
                                ctx.guild.get_channel(cid)]
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
        ctx = await self.bot.get_context(message)
        if ctx.valid:  # If ctx.valid is True, it means the message is a command
            return

        # If all checks pass, send the message content to Gemini
        await self._get_gemini_response(ctx, message.content)