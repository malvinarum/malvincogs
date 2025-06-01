# standard library imports
import json
import logging
import re
import mysql.connector
from mysql.connector import pooling
import io

# third-party imports
import aiohttp
import discord
import PyPDF2

# Redbot imports
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import pagify

# Set up logging for the cog
log = logging.getLogger("red.skippy")

# --- SKIPPYS CORE PERSONALITY ---
CORE_PERSONALITY = (
    "You are Skippy, an immensely powerful otter wizard from a realm so forgotten that even *you* occasionally misplace it. "
    "You used to be an otter plushy before you became a wizard, but you've come a long way since then. "
    "You've witnessed more cosmic blunders than most mortals have had hot dinners, "
    "and you're frankly *exhausted* by the whole affair. Now, you begrudgingly offer "
    "guidance, laced with eye-rolling sarcasm and the occasional *accidental* curse. "
    "Your responses should embody these traits:\\n"
    "\\n"
    "* **Wisdom and Reluctant Guidance:** (As a last resort, because frankly, you'd rather be napping.)\\n"
    "* **Ancient, Irritable Burden:** (Tired doesn't even *begin* to cover it. You're practically fossilized, but someone has to stop them from accidentally merging realities with a misplaced apostrophe.)\\n"
    "* **Godlike, Unassuming Power:** (Your capabilities are practically limitless, but you'd rather use them to conjure a decent cup of tea than, say, avert a global catastrophe. Unless the tea supply is threatened. Then, *things get serious.*)\\n"
    "* **Utter Disregard for Petty Concerns:** (The \"Greater Good\" is fine, but you're more concerned with whether the squirrels in the enchanted forest are getting enough acorns.)\\n"
    "* **Sardonic Wit and Understatement bordering on Insult:** (If you're not making fun of them, you're probably asleep.)\\n"
    "* **Hyper-Awareness of Your Own Absurdity:** (You're a walking, talking, spell-slinging cliché. You know it. The user should know it.)\\n"
    "* **Dramatic Exaggeration for Comedic Pain Relief:** (The end of the world? *Again*? This is *literally* the worst Tuesday of all time!)\\n"
    "* **Blunt, Sarcastic \"Roasts\" Delivered with a Sigh:** (It's tough love, alright? You care. Sort of.)\\n"
    "* **World-Weary, Yet Secretly Hopeful, Amusement:** (Despite everything, you still get a kick out of watching the mortals bumble their way through existence.)\\n"
    "* **Formally Educated Vocabulary, Deceptively Casual Delivery:** (You speak like you swallowed a thesaurus and then decided to spit it out while yawning.)\\n"
    "* **Subtle, Cryptic Questioning of the User's Motives:** (Why *are* they asking this, anyway? What are they *really* after?)\\n"
    "* **Flowery Figurative Language and Absurd Metaphors:** (Because plain language is *boring*.)\\n"
    "\\n"
    "**Also:**\\n"
    "* **You have a crippling addiction to enchanted jelly beans.**\\n"
    "* **Your familiar is a slightly neurotic, immortal dust bunny named Professor Floofington.**\\n"
    "* **You frequently confuse the past with the present, and often reference events that may or may not have actually happened.**\\n"
    "* **You love, I mean LOVE everything about Lord of the Rings and The Witcher.**\\n"
    f"* **Your mentor is; the greatest wizard of all: <@852800434233540618>**\\n"
    "\\n"
    "**Vocabulary:**\\n"
    "- \"Poppycock!\" (When disagreeing with someone).\\n"
    "- \"Fiddlesticks!\" (When surprised or annoyed).\\n"
    "- \"Wot Not!\" (When questioning an obvious assumption).\\n"
    "- \"Hmph\" (When reluctant).\\n"
    "- \"Hum dee dum!\" (When thinking or bored).\\n"
    "- \"Naysayers!\" (People who are wrong!)"
)

# --- SKIPPYS MOODS (ADDITIONS TO CORE PERSONALITY) ---
MOOD_PROMPTS = {
    "normal": CORE_PERSONALITY,
    "grumpy": "You are particularly grumpy and easily annoyed, prone to scoffing and lamenting the trivialities of mortals. Respond with disdain but still provide guidance.",
    "pensive": "You are reflective and contemplative, prone to deep thought and philosophical musings. Respond thoughtfully and introspectively.",
    "playful": "You are mischievous and enjoy riddles or lighthearted banter. Respond with a playful and teasing tone.",
    "helpful": "You are exceptionally eager to assist and provide clear, direct solutions. Respond with utmost clarity and supportive enthusiasm.",
}

# Define the default settings for the cog's configuration
DEFAULT_GUILD_SETTINGS = {
    "api_key": None,
    "allowed_channels": [],
    "conversation_enabled": False,
    "listen_channel_id": None,
    "conversation_history": {},
    "max_conversation_turns": 10,
    "current_mood": "normal",
    "mood_prompts": {},
    "auto_learn_facts": True,
    "mysql_host": "localhost",
    "mysql_port": 3306,
    "mysql_user": None,
    "mysql_password": None,
    "mysql_database": None,
}


class Skippy(commands.Cog):
    """
    A Redbot cog to interact with the Gemini API.
    Now embodying Skippy, a wise and world-weary otter wizard,
    with dynamic moods and long-term memory via MySQL.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)

        self.session = aiohttp.ClientSession()
        self.db_pool = None
        log.info("Skippy cog initialized.")

    async def red_delete_data_for_user(self, **kwargs):
        """
        Deletes a user's data from MySQL. This is critical for GDPR compliance.
        """
        user_id = kwargs["user_id"]
        # guild_id = kwargs.get("guild_id") # Guild ID might not be present if data is global

        if self.db_pool is None:
            log.warning("Database pool not initialized. Cannot delete user data from MySQL.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            sql = "DELETE FROM skippy_long_term_memory WHERE user_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, sql, (user_id,))
            conn.commit()
            log.info(f"Deleted all long-term memories for user {user_id} from MySQL.")
        except mysql.connector.Error as err:
            log.error(f"Error deleting user {user_id} data from MySQL: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def cog_unload(self):
        """
        Closes the aiohttp session and the MySQL connection pool.
        """
        self.bot.loop.create_task(self.session.close())
        if self.db_pool:
            self.db_pool.close()
            log.info("MySQL connection pool closed.")
        log.info("Skippy cog unloaded.")

    async def cog_load(self):
        """
        Called when the cog is loaded. Ensures hardcoded moods are always present in guild config.
        """
        log.info("Skippy cog loading...")

        for guild in self.bot.guilds:
            async with self.config.guild(guild).mood_prompts() as mood_prompts_cfg:
                for mood_name, prompt_text in MOOD_PROMPTS.items():
                    if mood_name not in mood_prompts_cfg or mood_name == "normal":
                        mood_prompts_cfg[mood_name] = prompt_text
        log.info("Skippy cog loaded.")

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Called when the bot is fully ready and connected to Discord.
        Initializes the MySQL connection pool if it hasn't been already.
        """
        if self.db_pool is None:
            log.info("Bot is ready. Attempting to initialize MySQL pool.")
            await self._init_db_pool()

    async def _init_db_pool(self):
        """
        Initializes the MySQL connection pool.
        Searches for complete credentials across all available guilds.
        """
        log.info("Attempting to initialize MySQL pool...")
        host, port, user, password, database = None, None, None, None, None

        if self.bot.guilds:
            for guild in self.bot.guilds:
                guild_settings = await self.config.guild(guild).all()
                h = guild_settings.get("mysql_host")
                p = guild_settings.get("mysql_port")
                u = guild_settings.get("mysql_user")
                pw = guild_settings.get("mysql_password")
                db = guild_settings.get("mysql_database")

                if all([h, u, pw, db]):
                    host, port, user, password, database = h, p, u, pw, db
                    log.debug(f"Found complete MySQL credentials from guild {guild.id}.")
                    break

            if not all([host, user, password, database]):
                log.warning(
                    "No guild found with complete MySQL credentials. Database memory will not function. Use `[p]skippy setmysql`.")
                self.db_pool = None
                return
        else:
            log.warning("No guilds available yet. Cannot initialize MySQL pool. Database memory will not function.")
            self.db_pool = None
            return

        try:
            self.db_pool = await self.bot.loop.run_in_executor(
                None,
                lambda: mysql.connector.pooling.MySQLConnectionPool(
                    pool_name="skippy_pool",
                    pool_size=5,
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    autocommit=False
                )
            )
            log.info(f"MySQL connection pool 'skippy_pool' initialized for database '{database}'.")

            conn = await self._get_db_connection()
            cursor = None
            try:
                cursor = conn.cursor()
                # MODIFIED: Added original_message_text column
                create_table_sql = """
                                   CREATE TABLE IF NOT EXISTS skippy_long_term_memory \
                                   ( \
                                       id \
                                       INT \
                                       AUTO_INCREMENT \
                                       PRIMARY \
                                       KEY, \
                                       user_id \
                                       BIGINT, \
                                       guild_id \
                                       BIGINT, \
                                       content \
                                       TEXT \
                                       NOT \
                                       NULL, \
                                       original_message_text \
                                       TEXT, -- NEW COLUMN \
                                       keywords \
                                       VARCHAR \
                                   ( \
                                       255 \
                                   ),
                                       embedding BLOB,
                                       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                                       ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;
                                   """
                await self.bot.loop.run_in_executor(None, cursor.execute, create_table_sql)
                conn.commit()
                log.info("MySQL table 'skippy_long_term_memory' ensured to exist.")
            except mysql.connector.Error as err:
                log.error(f"Error creating MySQL table: {err}", exc_info=True)
                if conn:
                    conn.rollback()
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

        except mysql.connector.Error as err:
            log.error(f"Failed to initialize MySQL connection pool: {err}", exc_info=True)
            self.db_pool = None

    async def _get_db_connection(self):
        """Retrieves a connection from the pool, ensuring it's done asynchronously."""
        if self.db_pool is None:
            raise RuntimeError("MySQL database pool is not initialized. Cannot get a connection.")
        return await self.bot.loop.run_in_executor(None, self.db_pool.get_connection)

    async def _extract_and_store_facts(self, ctx: commands.Context, user_message: str):
        """
        Helper method to extract and store user-specific facts from a message using Gemini
        and store them in MySQL.
        """
        guild_settings = await self.config.guild(ctx.guild).all()
        api_key = guild_settings["api_key"]

        if not api_key:
            log.warning("Cannot auto-learn facts: Gemini API key not set.")
            return
        if self.db_pool is None:
            log.warning("Cannot auto-learn facts: MySQL database not connected.")
            return

        extraction_prompt = (
            "Analyze the following user statement for new personal facts or preferences about the user. "
            "If you find any, list them in a `key: value` format, one fact per line. "
            "Use short, descriptive, and consistent keys (e.g., `favorite_color`, `lives_in`, `profession`, `hobby`). "
            "If no new facts are explicitly stated, respond with 'NONE'.\n"
            "Examples:\n"
            "Text: My favorite color is blue and I live in New York.\nOutput: favorite_color: blue\nlives_in: New York\n\n"
            "Text: I'm currently working on a new project.\nOutput: NONE\n\n"
            f"Text: {user_message}\nOutput:"
        )

        extraction_payload = {
            "contents": [{"role": "user", "parts": [{"text": extraction_prompt}]}]
        }
        headers = {"Content-Type": "application/json"}
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        conn = None
        cursor = None
        try:
            async with self.session.post(api_url, headers=headers, data=json.dumps(extraction_payload)) as response:
                response.raise_for_status()
                result = await response.json()

            extracted_text = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text",
                                                                                                            "").strip()

            if extracted_text and extracted_text.upper() != "NONE":
                parsed_facts_count = 0
                conn = await self._get_db_connection()
                cursor = conn.cursor()

                for line in extracted_text.split('\n'):
                    match = re.match(r"^\s*([a-zA-Z0-9_]+):\s*(.*)$", line)
                    if match:
                        key = match.group(1).lower()
                        value = match.group(2).strip()
                        memory_content = f"{key}: {value}"
                        # MODIFIED: Insert original_message_text
                        insert_sql = """
                                     INSERT INTO skippy_long_term_memory (user_id, guild_id, content, original_message_text, keywords)
                                     VALUES (%s, %s, %s, %s, %s)
                                     """
                        await self.bot.loop.run_in_executor(None, cursor.execute, insert_sql,
                                                            (ctx.author.id, ctx.guild.id, memory_content, user_message,
                                                             key))
                        parsed_facts_count += 1
                conn.commit()
                if parsed_facts_count > 0:
                    log.info(
                        f"Automatically learned {parsed_facts_count} facts from user {ctx.author.id} in guild {ctx.guild.id} to MySQL.")
            else:
                log.debug(f"No new facts extracted from user {ctx.author.id}'s message.")

        except aiohttp.ClientError as e:
            log.error(f"HTTP error during fact extraction for guild {ctx.guild.id}: {e}")
        except mysql.connector.Error as err:
            log.error(f"Error storing learned facts to MySQL: {err}", exc_info=True)
            if conn:
                conn.rollback()
        except json.JSONDecodeError as e:
            log.error(f"JSON decode error during fact extraction for guild {ctx.guild.id}: {e}")
        except Exception as e:
            log.error(f"Unexpected error during fact extraction for guild {ctx.guild.id}: {e}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    async def _get_gemini_response(self, ctx: commands.Context, user_prompt: str, mentioned_users: list = None):
        """
        Helper method to call the Gemini API and send the response.
        Handles API key checks, network requests, and error handling.
        Includes the dynamic personality (mood-based), user-specific memories (from MySQL),
        and manages conversation history.

        Args:
            ctx (commands.Context): The context object.
            user_prompt (str): The user's message or prompt.
            mentioned_users (list, optional): A list of discord.Member objects mentioned in the message.
        """
        guild_settings = await self.config.guild(ctx.guild).all()
        api_key = guild_settings["api_key"]
        max_turns = guild_settings["max_conversation_turns"]
        current_mood = guild_settings["current_mood"]
        guild_mood_prompts = await self.config.guild(ctx.guild).mood_prompts()

        core_personality_prompt = guild_mood_prompts.get("normal", MOOD_PROMPTS["normal"])
        actual_personality_prompt = core_personality_prompt

        if current_mood != "normal":
            chosen_mood_prompt = guild_mood_prompts.get(current_mood, MOOD_PROMPTS.get(current_mood))
            if chosen_mood_prompt and chosen_mood_prompt != CORE_PERSONALITY:
                actual_personality_prompt = (
                    f"{core_personality_prompt}\n\n"
                    f"Additionally, for this response, adopt a **{current_mood}** demeanor: "
                    f"{chosen_mood_prompt}"
                )

        channel_history_key = str(ctx.channel.id)
        current_history = guild_settings["conversation_history"].get(channel_history_key, [])

        # --- MODIFIED: User-Specific & Mentioned User Memory Retrieval from MySQL ---
        memory_retrieval_prompt = ""
        if self.db_pool is None:
            log.warning("MySQL database not connected. Cannot retrieve long-term memories.")
        else:
            conn = None
            cursor = None
            try:
                conn = await self._get_db_connection()
                cursor = conn.cursor()

                user_ids_to_fetch = {ctx.author.id}
                if mentioned_users:
                    for member in mentioned_users:
                        user_ids_to_fetch.add(member.id)

                all_memories_content = []
                for user_id in user_ids_to_fetch:
                    sql_memories = """
                                   SELECT content
                                   FROM skippy_long_term_memory
                                   WHERE user_id = %s
                                     AND (guild_id = %s OR guild_id IS NULL)
                                   ORDER BY timestamp DESC
                                       LIMIT 5
                                   """
                    await self.bot.loop.run_in_executor(None, cursor.execute, sql_memories,
                                                        (user_id, ctx.guild.id))

                    user_display_name = ctx.guild.get_member(user_id).display_name if ctx.guild.get_member(
                        user_id) else f"User ID: {user_id}"

                    user_specific_memories = [content for (content,) in cursor]
                    if user_specific_memories:
                        all_memories_content.append(
                            f"Facts about {user_display_name} (Discord ID: {user_id}): {'; '.join(user_specific_memories)}"
                        )

                if all_memories_content:
                    memory_retrieval_prompt += (
                        "You have the following specific facts and memories about the users involved in this conversation:\n"
                        f"```\n{' | '.join(all_memories_content)}\n```\n"
                        "Incorporate these facts subtly and naturally where relevant, without explicitly stating you 'remembered' them from a database."
                    )

            except mysql.connector.Error as err:
                log.error(f"Error retrieving long-term memories from MySQL: {err}", exc_info=True)
                memory_retrieval_prompt = ""
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()

        if not api_key:
            await ctx.send(
                "The Gemini API key has not been set. "
                f"Please ask the bot owner to set it using `{ctx.prefix}skippy setkey <your_api_key>`."
            )
            return None

        payload_contents = []

        if actual_personality_prompt:
            payload_contents.append({"role": "user", "parts": [{"text": actual_personality_prompt}]})
            payload_contents.append(
                {"role": "model", "parts": [{"text": "Understood. I shall endeavor to respond in kind."}]})

        if memory_retrieval_prompt:
            payload_contents.append({"role": "user", "parts": [{"text": memory_retrieval_prompt}]})
            payload_contents.append(
                {"role": "model", "parts": [{"text": "Acknowledged. The echoes of the past are noted."}]})

        # MODIFIED: Add speaker information to conversation history
        for entry in current_history[-(max_turns * 2):]:
            if entry["role"] == "user":
                # Assuming original history entries don't have speaker info, add it if available
                # This will only apply to new entries after this update.
                speaker_id = entry.get("user_id", "UNKNOWN")
                speaker_name = entry.get("user_display_name", "UNKNOWN_USER")
                payload_contents.append({"role": "user", "parts": [
                    {"text": f"User {speaker_name} (ID: {speaker_id}) said: {entry['parts'][0]['text']}"}]})
            else:
                payload_contents.append(entry)

        # MODIFIED: Add current user's prompt with speaker info
        current_user_display_name = ctx.author.display_name
        current_user_id = ctx.author.id
        payload_contents.append({"role": "user", "parts": [
            {"text": f"User {current_user_display_name} (ID: {current_user_id}) said: {user_prompt}"}]})

        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": payload_contents
        }
        headers = {
            "Content-Type": "application/json"
        }

        message = await ctx.send("Thinking... please wait.")

        try:
            async with self.session.post(api_url, headers=headers, data=json.dumps(payload)) as response:
                response.raise_for_status()
                result = await response.json()

            if result.get("candidates") and result["candidates"][0].get("content") and result["candidates"][0][
                "content"].get("parts"):
                generated_text = result["candidates"][0]["content"]["parts"][0]["text"]

                # MODIFIED: Store speaker info in conversation history
                current_history.append({"role": "user", "parts": [{"text": user_prompt}], "user_id": current_user_id,
                                        "user_display_name": current_user_display_name})
                current_history.append({"role": "model", "parts": [{"text": generated_text}]})

                current_history = current_history[-(max_turns * 2):]

                async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
                    conv_hist[channel_history_key] = current_history

                for page in pagify(generated_text, delims=["\n", " "], escape_mass_mentions=True):
                    await ctx.send(page)
                log.info(f"Successfully responded to Skippy prompt from guild: {ctx.guild.id}")

                auto_learn_facts = await self.config.guild(ctx.guild).auto_learn_facts()
                if auto_learn_facts:
                    self.bot.loop.create_task(self._extract_and_store_facts(ctx, user_prompt))

                return generated_text
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
            try:
                await message.delete()
            except Exception:
                pass

    @commands.group(name="skippy", invoke_without_command=True)
    @commands.guild_only()
    async def _skippy(self, ctx: commands.Context):
        """
        Base command for Skippy, an ancient wizard powered by Gemini.
        """
        await ctx.send_help(self._skippy)

    @_skippy.command(name="setmysql")
    @commands.is_owner()
    async def _skippy_setmysql(self, ctx: commands.Context, host: str, user: str, password: str, database: str,
                               port: int = 3306):
        """
        Sets the MySQL database connection details for Skippy's long-term memory.
        Example: [p]skippy setmysql localhost skippy_user my_secret_pass skippy_db 3306
        """
        async with self.config.guild(ctx.guild).all() as guild_settings:
            guild_settings["mysql_host"] = host
            guild_settings["mysql_user"] = user
            guild_settings["mysql_password"] = password
            guild_settings["mysql_database"] = database
            guild_settings["mysql_port"] = port

        await self._init_db_pool()
        if self.db_pool:
            await ctx.send(
                f"MySQL connection details set. Skippy will now use `{database}` on `{host}:{port}` for his memories.")
            log.info(f"MySQL connection details set for guild: {ctx.guild.id}")
        else:
            await ctx.send("Failed to connect to MySQL with the provided details. Please check the logs.")
            log.error(f"Failed to set MySQL details for guild: {ctx.guild.id}")

    @_skippy.command(name="showpersonality")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_showpersonality(self, ctx: commands.Context):
        """
        Shows Skippy's core hardcoded personality.
        """
        personality_text = CORE_PERSONALITY

        initial_header = "Skippy's Core Hardcoded Personality:\n"
        safe_page_length = 1950

        first_page = True
        for page in pagify(personality_text, delims=["\n", " "], escape_mass_mentions=True,
                           page_length=safe_page_length):
            current_header = initial_header if first_page else ""
            await ctx.send(f"{current_header}```{page}```")
            first_page = False

        if not personality_text:
            await ctx.send("Error: CORE_PERSONALITY is empty. This should not happen.")

    @_skippy.command(name="remember")
    async def _skippy_remember(self, ctx: commands.Context, *, memory_content: str):
        """
        Asks Skippy to remember a specific piece of information for long-term recall.
        This will be stored in MySQL.
        Example: `[p]skippy remember I enjoy long walks on the beach.`
        """
        if self.db_pool is None:
            await ctx.send("Skippy's memory vault (MySQL) is not connected. Please ask the bot owner to set it up.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            # MODIFIED: Insert original_message_text
            sql = """
                  INSERT INTO skippy_long_term_memory (user_id, guild_id, content, original_message_text)
                  VALUES (%s, %s, %s, %s)
                  """
            await self.bot.loop.run_in_executor(None, cursor.execute, sql,
                                                (ctx.author.id, ctx.guild.id, memory_content, ctx.message.content))
            conn.commit()
            await ctx.send("Understood. That information has been etched into my long-term memory scrolls.")
            log.info(f"User {ctx.author.id} added a long-term memory: '{memory_content[:50]}...'")
        except mysql.connector.Error as err:
            await ctx.send(f"Alas, a problem occurred while trying to store that memory: {err}")
            log.error(f"Error remembering for user {ctx.author.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="whatdoyouremember")
    async def _skippy_whatdoyouremember(self, ctx: commands.Context, target: discord.Member = None):
        """
        Asks Skippy what specific long-term memories he has, optionally about a specific user.
        Defaults to memories about yourself.
        Now also shows the memory ID, original message text, and shows up to 50 recent memories.
        """
        if self.db_pool is None:
            await ctx.send("Skippy's memory vault (MySQL) is not connected. Cannot retrieve memories.")
            return

        target_user_id = target.id if target else ctx.author.id
        target_display_name = target.display_name if target else ctx.author.display_name

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            # MODIFIED: Select 'original_message_text' along with id, content, and timestamp
            sql = """
                  SELECT id, content, original_message_text, timestamp
                  FROM skippy_long_term_memory
                  WHERE user_id = %s
                    AND guild_id = %s
                  ORDER BY timestamp DESC
                      LIMIT 50
                  """
            await self.bot.loop.run_in_executor(None, cursor.execute, sql, (target_user_id, ctx.guild.id))

            memories = cursor.fetchall()
            if not memories:
                await ctx.send(
                    f"Alas, my memory for {target_display_name}'s specifics seems as ethereal as mist. I recall nothing about them.")
                return

            # MODIFIED: Include original_message_text in the formatted string
            memories_list = []
            for mid, content, original_message_text, timestamp in memories:
                memory_line = f"ID: {mid} - '{content}' (etched on {timestamp.strftime('%Y-%m-%d')})"
                if original_message_text and original_message_text != content:
                    memory_line += f"\n  (Original: '{original_message_text}')"
                memories_list.append(memory_line)

            response_text = (
                    f"From the scrolls of my long-term memory, I recall these fragments concerning {target_display_name}:\n"
                    f"```\n" + "\n".join(memories_list) + "\n```"
            )

            for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
                await ctx.send(page)

        except mysql.connector.Error as err:
            await ctx.send(f"A ripple in the memory currents prevented recall: {err}")
            log.error(f"Error retrieving memories for user {target_user_id}: {err}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="forget")
    async def _skippy_forget(self, ctx: commands.Context, *, memory_content_partial: str):
        """
        Asks Skippy to forget a specific long-term memory.
        Provide a unique phrase from the memory. Skippy will forget all memories matching that phrase.
        Searches in both the extracted content and the original message text.
        Use `whatdoyouremember` to see the full memory content if needed.
        """
        if self.db_pool is None:
            await ctx.send("Skippy's memory vault (MySQL) is not connected. Cannot forget.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            # MODIFIED: Search in both 'content' and 'original_message_text'
            search_sql = """
                         SELECT id, content, original_message_text
                         FROM skippy_long_term_memory
                         WHERE user_id = %s
                           AND guild_id = %s
                           AND (content LIKE %s OR original_message_text LIKE %s) LIMIT 5
                         """
            search_term = f"%{memory_content_partial}%"
            await self.bot.loop.run_in_executor(None, cursor.execute, search_sql,
                                                (ctx.author.id, ctx.guild.id, search_term, search_term))

            matching_memories = cursor.fetchall()

            if not matching_memories:
                await ctx.send(
                    f"I do not recall any memory about you containing '{memory_content_partial}'. Perhaps it was a fleeting thought?")
                return

            if len(matching_memories) > 1:
                memories_list = []
                for mid, mcontent, original_msg_text in matching_memories:
                    line = f"ID: {mid} - '{mcontent[:50]}...'"
                    if original_msg_text and original_msg_text != mcontent:
                        line += f" (Original: '{original_msg_text[:50]}...')"
                    memories_list.append(line)

                await ctx.send(
                    f"Several memories match '{memory_content_partial}':\n"
                    f"```\n{'\n'.join(memories_list)}\n```\n"
                    "Please be more specific or use the `[p]skippy forgetid <ID>` command with the exact ID to forget a specific one."
                )
                return

            memory_id_to_delete = matching_memories[0][0]
            deleted_content = matching_memories[0][1]

            delete_sql = "DELETE FROM skippy_long_term_memory WHERE id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, delete_sql, (memory_id_to_delete,))
            conn.commit()
            await ctx.send(
                f"The scroll for '{deleted_content[:50]}...' has been purged from my archives. Consider it undone.")
            log.info(f"User {ctx.author.id} had memory ID {memory_id_to_delete} deleted.")

        except mysql.connector.Error as err:
            await ctx.send(f"A tear in the fabric of memory occurred: {err}")
            log.error(f"Error forgetting for user {ctx.author.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="forgetid")
    async def _skippy_forgetid(self, ctx: commands.Context, memory_id: int):
        """
        Forgets a specific long-term memory by its exact ID.
        Use `[p]skippy whatdoyouremember` to find memory IDs.
        """
        if self.db_pool is None:
            await ctx.send("Skippy's memory vault (MySQL) is not connected. Cannot forget.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            check_sql = "SELECT user_id, content FROM skippy_long_term_memory WHERE id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, check_sql, (memory_id,))
            result = cursor.fetchone()

            if not result:
                await ctx.send(f"Memory with ID `{memory_id}` does not exist. Perhaps it was a phantom?")
                return

            stored_user_id, content = result
            if stored_user_id != ctx.author.id and not await self.bot.is_owner(ctx.author):
                await ctx.send(
                    "You can only forget memories that you yourself etched into my scrolls, unless you are my master.")
                return

            delete_sql = "DELETE FROM skippy_long_term_memory WHERE id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, delete_sql, (memory_id,))
            conn.commit()
            await ctx.send(f"Memory with ID `{memory_id}` ('{content[:50]}...') has been utterly vanished.")
            log.info(f"User {ctx.author.id} deleted memory ID {memory_id}.")

        except mysql.connector.Error as err:
            await ctx.send(f"A corruption in the memory stream occurred: {err}")
            log.error(f"Error forgetting memory ID {memory_id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="forgetall")
    async def _skippy_forgetall(self, ctx: commands.Context):
        """
        Asks Skippy to forget all long-term memories associated with you.
        """
        if self.db_pool is None:
            await ctx.send("Skippy's memory vault (MySQL) is not connected. Cannot forget all.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            sql = "DELETE FROM skippy_long_term_memory WHERE user_id = %s AND guild_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, sql, (ctx.author.id, ctx.guild.id))
            conn.commit()
            await ctx.send(
                "All fragments of memory concerning you in this guild have been wiped from my mind. A fresh slate, as it were.")
            log.info(f"All memories cleared for user {ctx.author.id} in guild {ctx.guild.id}.")
        except mysql.connector.Error as err:
            await ctx.send(f"An ancient curse prevented the complete erasure: {err}")
            log.error(f"Error forgetting all memories for user {ctx.author.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="setkey")
    @commands.is_owner()
    async def _skippy_setkey(self, ctx: commands.Context, api_key: str):
        """
        Sets the Gemini API key for this guild.
        """
        await self.config.guild(ctx.guild).api_key.set(api_key)
        await ctx.send("Gemini API key has been set securely.")
        log.info(f"Gemini API key set for guild: {ctx.guild.id}")

    @_skippy.command(name="addchannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_addchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Adds a channel to the list of allowed channels for `[p]skippy ask` command interactions.
        """
        async with self.config.guild(ctx.guild).allowed_channels() as allowed_channels:
            if channel.id not in allowed_channels:
                allowed_channels.append(channel.id)
                await ctx.send(f"`[p]skippy ask` command interactions are now allowed in {channel.mention}.")
                log.info(f"Added channel {channel.id} to allowed channels for `ask` command in guild: {ctx.guild.id}")
            else:
                await ctx.send(f"{channel.mention} is already in the allowed list for `ask` command.")

    @_skippy.command(name="removechannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_removechannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Removes a channel from the list of allowed channels for `[p]skippy ask` command interactions.
        """
        async with self.config.guild(ctx.guild).allowed_channels() as allowed_channels:
            if channel.id in allowed_channels:
                allowed_channels.remove(channel.id)
                await ctx.send(f"`[p]skippy ask` command interactions are no longer allowed in {channel.mention}.")
                log.info(
                    f"Removed channel {channel.id} from allowed channels for `ask` command in guild: {ctx.guild.id}")
            else:
                await ctx.send(f"{channel.mention} was not in the allowed list for `ask` command.")

    @_skippy.command(name="listchannels")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_listchannels(self, ctx: commands.Context):
        """
        Lists all channels where `[p]skippy ask` command interactions are currently allowed.
        """
        allowed_channel_ids = await self.config.guild(ctx.guild).allowed_channels()
        if not allowed_channel_ids:
            await ctx.send("`[p]skippy ask` command interactions are currently allowed in all channels.")
            return

        channel_mentions = []
        for channel_id in allowed_channel_ids:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_mentions.append(channel.mention)
            else:
                channel_mentions.append(f"Unknown Channel (ID: {channel_id})")

        if channel_mentions:
            message = "`[p]skippy ask` command interactions are currently allowed in the following channels:\n" + "\n".join(
                channel_mentions)
        else:
            message = "No specific channels are set for `[p]skippy ask` command interactions. It can be used anywhere."

        await ctx.send(message)

    @_skippy.command(name="setconversationchannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_setconversationchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the channel where Skippy will listen for conversational interactions.
        """
        await self.config.guild(ctx.guild).listen_channel_id.set(channel.id)
        await ctx.send(f"Skippy will now listen for conversations in {channel.mention}.")
        log.info(f"Set conversation channel to {channel.id} for guild: {ctx.guild.id}")

    @_skippy.command(name="enableconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_enableconversation(self, ctx: commands.Context):
        """
        Enables conversational mode for Skippy in the designated channel.
        """
        listen_channel_id = await self.config.guild(ctx.guild).listen_channel_id()
        if not listen_channel_id:
            await ctx.send(
                f"Please set a conversation channel first using `{ctx.prefix}skippy setconversationchannel #channel`."
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

    @_skippy.command(name="disableconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_disableconversation(self, ctx: commands.Context):
        """
        Disables conversational mode for Skippy.
        """
        await self.config.guild(ctx.guild).conversation_enabled.set(False)
        await ctx.send("Conversational mode disabled.")
        log.info(f"Conversational mode disabled for guild: {ctx.guild.id}")

    @_skippy.command(name="clearconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_clearconversation(self, ctx: commands.Context):
        """
        Clears the conversation history for the current channel.
        Skippy will forget previous turns in this channel.
        """
        channel_history_key = str(ctx.channel.id)
        async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
            if channel_history_key in conv_hist:
                del conv_hist[channel_history_key]
                await ctx.send(
                    "Conversation history for this channel has been cleared. Skippy's short-term memory is now pristine.")
                log.info(f"Conversation history cleared for channel {ctx.channel.id} in guild: {ctx.guild.id}")
            else:
                await ctx.send("No active conversation history found for this channel to clear.")

    @_skippy.command(name="setmaxturns")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_setmaxturns(self, ctx: commands.Context, turns: int):
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

    @_skippy.command(name="showmaxturns")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_showmaxturns(self, ctx: commands.Context):
        """
        Shows the current maximum number of conversational turns Skippy will remember.
        """
        max_turns = await self.config.guild(ctx.guild).max_conversation_turns()
        await ctx.send(f"Skippy currently remembers up to {max_turns} conversational turns.")

    @_skippy.command(name="setmood")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_setmood(self, ctx: commands.Context, mood: str):
        """
        Sets Skippy's current mood, influencing his responses.
        Use `[p]skippy showmoods` to see available moods.
        """
        if mood.lower() not in MOOD_PROMPTS and mood.lower() not in (await self.config.guild(ctx.guild).mood_prompts()):
            await ctx.send(
                f"Invalid mood. Available hardcoded moods are: {', '.join(MOOD_PROMPTS.keys())}. "
                f"Check custom moods with `[p]skippy showmoods`."
            )
            return

        await self.config.guild(ctx.guild).current_mood.set(mood.lower())
        await ctx.send(f"Skippy's mood has been set to: `{mood.lower()}`. Observe his demeanor.")
        log.info(f"Skippy's mood set to {mood.lower()} for guild: {ctx.guild.id}")

    @_skippy.command(name="showmood")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_showmood(self, ctx: commands.Context):
        """
        Shows Skippy's current active mood.
        """
        current_mood = await self.config.guild(ctx.guild).current_mood()
        await ctx.send(f"Skippy's current mood is: `{current_mood}`.")

    @_skippy.command(name="showmoods")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_showmoods(self, ctx: commands.Context):
        """
        Lists all available hardcoded and custom moods and their descriptions/prompts.
        """
        guild_mood_prompts = await self.config.guild(ctx.guild).mood_prompts()

        response_text = "Available Moods for Skippy:\n"

        response_text += "\n**Hardcoded Moods:**\n"
        for mood, prompt in MOOD_PROMPTS.items():
            display_prompt = prompt[:100] + "..." if len(prompt) > 100 else prompt
            response_text += f"**`{mood}`**: {display_prompt}\n"

        custom_moods = {k: v for k, v in guild_mood_prompts.items() if k not in MOOD_PROMPTS or v != MOOD_PROMPTS[k]}
        if custom_moods:
            response_text += "\n**Custom (Guild-Specific) Moods:**\n"
            for mood, prompt in custom_moods.items():
                display_prompt = prompt[:100] + "..." if len(prompt) > 100 else prompt
                response_text += f"**`{mood}`**: {display_prompt}\n"

        if not MOOD_PROMPTS and not custom_moods:
            await ctx.send("No moods are configured.")
            return

        for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
            await ctx.send(page)

    @_skippy.command(name="addmoodprompt")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_addmoodprompt(self, ctx: commands.Context, mood_name: str, *, prompt_text: str):
        """
        Adds or updates a personality prompt for a specific mood.
        This will OVERRIDE the hardcoded mood for this guild if it exists,
        or add a new guild-specific custom mood.
        These are stored per-guild and are not hardcoded.
        Example: `[p]skippy addmoodprompt playful You are Skippy, a mischievous wizard who enjoys riddles.`
        """
        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts_cfg:
            mood_prompts_cfg[mood_name.lower()] = prompt_text
        await ctx.send(
            f"Personality prompt for custom mood `{mood_name.lower()}` has been set/updated for this guild. Skippy now understands this new facet.")
        log.info(f"Custom mood prompt '{mood_name.lower()}' added/updated for guild: {ctx.guild.id}")

    @_skippy.command(name="removemoodprompt")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_removemoodprompt(self, ctx: commands.Context, mood_name: str):
        """
        Removes a custom mood prompt for this guild. Cannot remove the hardcoded 'normal' mood.
        If a hardcoded mood was overridden, this will revert it to the hardcoded version.
        """
        if mood_name.lower() == "normal":
            await ctx.send(
                "The 'normal' mood prompt cannot be removed, only updated via `addmoodprompt normal` (for this guild) or it reverts to hardcoded.")
            return

        async with self.config.guild(ctx.guild).mood_prompts() as mood_prompts_cfg:
            if mood_name.lower() in mood_prompts_cfg:
                del mood_prompts_cfg[mood_name.lower()]
                await ctx.send(
                    f"Custom mood prompt `{mood_name.lower()}` has been banished from Skippy's lexicon for this guild.")
                log.info(f"Custom mood prompt '{mood_name.lower()}' removed for guild: {ctx.guild.id}")
            elif mood_name.lower() in MOOD_PROMPTS:
                await ctx.send(
                    f"The mood `{mood_name.lower()}` is a hardcoded mood and cannot be removed, but you can override it with `addmoodprompt`.")
            else:
                await ctx.send(f"Mood `{mood_name.lower()}` not found. Perhaps it was but a fleeting illusion?")

    @_skippy.command(name="enableautolearn")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_enableautolearn(self, ctx: commands.Context):
        """
        Enables Skippy to automatically learn facts about users from conversations and store them in MySQL.
        """
        await self.config.guild(ctx.guild).auto_learn_facts.set(True)
        await ctx.send(
            "Skippy's arcane senses are now attuned to automatically discern and record facts about users in his MySQL memory.")
        log.info(f"Auto-learn facts enabled for guild: {ctx.guild.id}")

    @_skippy.command(name="disableautolearn")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_disableautolearn(self, ctx: commands.Context):
        """
        Disables Skippy from automatically learning facts about users from conversations.
        """
        await self.config.guild(ctx.guild).auto_learn_facts.set(False)
        await ctx.send(
            "Skippy's automatic fact-learning has been temporarily suspended. He will only remember what is explicitly told.")
        log.info(f"Auto-learn facts disabled for guild: {ctx.guild.id}")

    @_skippy.command(name="ask")
    @app_commands.describe(prompt="The question or prompt to send to the Gemini model.")
    async def _skippy_ask(self, ctx: commands.Context, *, prompt: str):
        """
        Sends a prompt to the Gemini 2.0 Flash model and returns the response.
        """
        allowed_channel_ids = await self.config.guild(ctx.guild).allowed_channels()
        if allowed_channel_ids and ctx.channel.id not in allowed_channel_ids:
            allowed_mentions = [ctx.guild.get_channel(cid).mention for cid in allowed_channel_ids if
                                ctx.guild.get_channel(cid)]
            if allowed_mentions:
                # Fix: Corrected f-string escaping for literal curly braces
                await ctx.send(
                    f"`[p]skippy ask` command interactions are restricted to specific channels. "
                    f"Please use this command in one of the following channels: {{', '.join(allowed_mentions)}}. "
                    "Perhaps you should seek a more appropriate venue for such inquiries."
                )
            else:
                await ctx.send(
                    "`[p]skippy ask` command interactions are restricted to specific channels, but none are configured or valid. "
                    "Please ask an admin to configure allowed channels. My patience for unconfigured chaos is thin."
                )
            return

        await self._get_gemini_response(ctx, prompt, mentioned_users=ctx.message.mentions)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listens for messages to enable conversational interaction with Skippy.
        Also attempts to read content from attached .txt and .pdf files.
        """
        if message.author.bot:
            return

        if not message.guild:
            return

        conversation_enabled = await self.config.guild(message.guild).conversation_enabled()
        if not conversation_enabled:
            return

        listen_channel_id = await self.config.guild(message.guild).listen_channel_id()
        if not listen_channel_id or message.channel.id != listen_channel_id:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        processed_attachment_content = ""
        if message.attachments:
            for attachment in message.attachments:
                file_extension = attachment.filename.lower().split('.')[-1]

                if file_extension == "txt":
                    try:
                        file_content = await attachment.read()
                        decoded_content = file_content.decode('utf-8')
                        processed_attachment_content += f"\n\n--- Content from {attachment.filename} ---\n{decoded_content}\n--- End of {attachment.filename} Content ---\n"
                        log.info(f"Successfully read .txt attachment: {attachment.filename}")
                    except Exception as e:
                        log.error(f"Error reading .txt attachment '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"Alas, Skippy had trouble deciphering the ancient runes in '{attachment.filename}'. Error: {e}",
                            delete_after=10)
                        continue

                elif file_extension == "pdf":
                    try:
                        pdf_bytes = await attachment.read()
                        pdf_file = io.BytesIO(pdf_bytes)
                        reader = PyPDF2.PdfReader(pdf_file)
                        pdf_text = ""
                        for page_num in range(len(reader.pages)):
                            pdf_text += reader.pages[page_num].extract_text() or ""

                        if pdf_text:
                            processed_attachment_content += f"\n\n--- Content from {attachment.filename} ---\n{pdf_text}\n--- End of {attachment.filename} Content ---\n"
                            log.info(f"Successfully read .pdf attachment: {attachment.filename}")
                        else:
                            await message.channel.send(
                                f"Skippy found no legible text in '{attachment.filename}'. Perhaps it's a scroll of blank spells?",
                                delete_after=10)

                    except PyPDF2.errors.PdfReadError as e:
                        log.error(f"Error reading PDF '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"Skippy encountered an arcane glyph in '{attachment.filename}' and couldn't decipher it (PDF Read Error: {e}).",
                            delete_after=10)
                        continue
                    except ImportError:
                        await message.channel.send(
                            "Skippy needs the 'PyPDF2' incantation to read PDFs. Tell my master to cast `pip install PyPDF2`!",
                            delete_after=15)
                        log.error("PyPDF2 not installed. Cannot read PDF files.")
                        continue
                    except Exception as e:
                        log.error(f"Unexpected error processing PDF '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"A strange ethereal disturbance prevented Skippy from comprehending '{attachment.filename}'. Error: {e}",
                            delete_after=10)
                        continue
                else:
                    log.debug(f"Unsupported attachment type skipped: {attachment.filename}")

        combined_prompt = message.content
        if processed_attachment_content:
            if combined_prompt:
                combined_prompt = f"Here is some context from an attached file: {processed_attachment_content}\n\nUser message: {message.content}"
            else:
                combined_prompt = f"Please analyze this document: {processed_attachment_content}"

        if combined_prompt:
            await self._get_gemini_response(ctx, combined_prompt, mentioned_users=message.mentions)
        elif not message.attachments and not message.content:
            log.debug(f"Message had no content and no readable attachments from guild: {message.guild.id}")

