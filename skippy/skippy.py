# standard library imports
import json
import logging
import re
import mysql.connector
from mysql.connector import pooling
import io
import numpy as np

# third-party imports
import aiohttp
import discord
import PyPDF2
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

# Redbot imports
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import pagify

# Set up logging for the cog
log = logging.getLogger("red.skippy")

# --- SKIPPYS CORE PERSONALITY ---
# This is Skippy's unchanging essence.
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
# These prompts will be layered ON TOP OF the CORE_PERSONALITY.
MOOD_PROMPTS = {
    "normal": CORE_PERSONALITY,  # 'normal' mood is just the core personality
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
    "auto_learn_relationships": True,
    # --- MySQL Configuration ---
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
    with dynamic moods, long-term memory via MySQL, and relationship recognition.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)

        self.session = aiohttp.ClientSession()
        self.db_pool = None
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        log.info("Skippy cog initialized.")

    async def red_delete_data_for_user(self, **kwargs):
        """
        Deletes a user's data from MySQL. This is critical for GDPR compliance.
        Includes memories, relationships, and stored user names.
        """
        user_id = kwargs["user_id"]
        guild_id = kwargs.get("guild_id")

        if self.db_pool is None:
            log.warning("Database pool not initialized. Cannot delete user data from MySQL.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            # Delete long-term memories
            sql_memories = "DELETE FROM skippy_long_term_memory WHERE user_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, sql_memories, (user_id,))
            log.info(f"Deleted all long-term memories for user {user_id} from MySQL.")

            # Delete relationships where this user is an initiator or target
            sql_relationships = """
                                DELETE
                                FROM skippy_relationships
                                WHERE user_id_initiator = %s
                                   OR user_id_target = %s
                                """
            await self.bot.loop.run_in_executor(None, cursor.execute, sql_relationships, (user_id, user_id))
            log.info(f"Deleted all relationships involving user {user_id} from MySQL.")

            # NEW: Delete user names
            sql_user_names = "DELETE FROM skippy_user_names WHERE user_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, sql_user_names, (user_id,))
            log.info(f"Deleted all user name records for user {user_id} from MySQL.")

            conn.commit()
            log.info(f"All data for user {user_id} deleted from MySQL.")
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
        Initializes the MySQL connection pool and ensures tables exist.
        Also adds necessary indexes for performance.
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
                # Create skippy_long_term_memory table
                create_memory_table_sql = """
                                          CREATE TABLE IF NOT EXISTS skippy_long_term_memory
                                          (
                                              id
                                              INT
                                              AUTO_INCREMENT
                                              PRIMARY
                                              KEY,
                                              user_id
                                              BIGINT,
                                              guild_id
                                              BIGINT,
                                              content
                                              TEXT
                                              NOT
                                              NULL,
                                              keywords
                                              VARCHAR
                                          (
                                              255
                                          ),
                                              embedding BLOB,
                                              timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                                              ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci;
                                          """
                await self.bot.loop.run_in_executor(None, cursor.execute, create_memory_table_sql)
                log.info("MySQL table 'skippy_long_term_memory' ensured to exist.")

                # Add 'embedding' column if it doesn't exist (for backward compatibility)
                alter_table_sql = """
                                  ALTER TABLE skippy_long_term_memory
                                      ADD COLUMN embedding BLOB AFTER keywords;
                                  """
                try:
                    await self.bot.loop.run_in_executor(None, cursor.execute, alter_table_sql)
                    log.info("Added 'embedding' column to 'skippy_long_term_memory' table.")
                except mysql.connector.Error as err:
                    if err.errno == 1060:  # Error code for "Duplicate column name"
                        log.info(
                            "Column 'embedding' already exists in 'skippy_long_term_memory' table. Skipping alter.")
                    else:
                        log.error(f"Error altering 'skippy_long_term_memory' table to add 'embedding' column: {err}",
                                  exc_info=True)

                # Add index for user_id, guild_id, and timestamp for efficient memory retrieval
                create_memory_index_sql = """
                                          CREATE INDEX IF NOT EXISTS idx_user_guild_memory
                                              ON skippy_long_term_memory (user_id, guild_id, timestamp);
                                          """
                await self.bot.loop.run_in_executor(None, cursor.execute, create_memory_index_sql)
                log.info("MySQL index 'idx_user_guild_memory' ensured to exist.")

                # Create skippy_relationships table
                create_relationships_table_sql = """
                                                 CREATE TABLE IF NOT EXISTS skippy_relationships
                                                 (
                                                     id
                                                     INT
                                                     AUTO_INCREMENT
                                                     PRIMARY
                                                     KEY,
                                                     user_id_initiator
                                                     BIGINT
                                                     NOT
                                                     NULL,
                                                     user_id_target
                                                     BIGINT
                                                     NOT
                                                     NULL,
                                                     relationship_type
                                                     VARCHAR
                                                 (
                                                     100
                                                 ) NOT NULL,
                                                     description TEXT,
                                                     guild_id BIGINT,
                                                     timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                                     UNIQUE KEY unique_relationship
                                                 (
                                                     user_id_initiator,
                                                     user_id_target,
                                                     relationship_type,
                                                     guild_id
                                                 )
                                                     ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci;
                                                 """
                await self.bot.loop.run_in_executor(None, cursor.execute, create_relationships_table_sql)
                log.info("MySQL table 'skippy_relationships' ensured to exist.")

                # Add indexes for relationships table for efficient lookups
                create_rel_guild_index_sql = """
                                             CREATE INDEX IF NOT EXISTS idx_guild_relationships
                                                 ON skippy_relationships (guild_id);
                                             """
                create_rel_initiator_index_sql = """
                                                 CREATE INDEX IF NOT EXISTS idx_initiator_relationships
                                                     ON skippy_relationships (user_id_initiator);
                                                 """
                create_rel_target_index_sql = """
                                              CREATE INDEX IF NOT EXISTS idx_target_relationships
                                                  ON skippy_relationships (user_id_target);
                                              """
                create_rel_users_index_sql = """
                                             CREATE INDEX IF NOT EXISTS idx_users_relationships
                                                 ON skippy_relationships (user_id_initiator, user_id_target);
                                             """

                await self.bot.loop.run_in_executor(None, cursor.execute, create_rel_guild_index_sql)
                await self.bot.loop.run_in_executor(None, cursor.execute, create_rel_initiator_index_sql)
                await self.bot.loop.run_in_executor(None, cursor.execute, create_rel_target_index_sql)
                await self.bot.loop.run_in_executor(None, cursor.execute, create_rel_users_index_sql)
                log.info("MySQL indexes for 'skippy_relationships' ensured to exist.")

                # NEW: Create skippy_user_names table
                create_user_names_table_sql = """
                                              CREATE TABLE IF NOT EXISTS skippy_user_names
                                              (
                                                  user_id
                                                  BIGINT
                                                  NOT
                                                  NULL,
                                                  guild_id
                                                  BIGINT
                                                  NOT
                                                  NULL,
                                                  display_name
                                                  VARCHAR
                                              (
                                                  255
                                              ) NOT NULL,
                                                  known_names JSON, -- Store as JSON array of strings
                                                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                                                  ON UPDATE CURRENT_TIMESTAMP,
                                                  PRIMARY KEY
                                              (
                                                  user_id,
                                                  guild_id
                                              )
                                                  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci;
                                              """
                await self.bot.loop.run_in_executor(None, cursor.execute, create_user_names_table_sql)
                log.info("MySQL table 'skippy_user_names' ensured to exist.")

                conn.commit()
            except mysql.connector.Error as err:
                log.error(f"Error creating MySQL tables or indexes: {err}", exc_info=True)
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

    async def _get_embeddings(self, text: str) -> np.ndarray:
        """
        Generates a numerical embedding (vector) for the given text using the SentenceTransformer model.
        """
        return await self.bot.loop.run_in_executor(None, self.embedding_model.encode, text)

    async def _update_user_name_record(self, member: discord.Member):
        """
        NEW: Updates or creates a record for a user's name(s) in the skippy_user_names table.
        This function now uses a set to manage known names, preventing duplicates and
        ensuring the current display name is handled correctly.
        """
        if self.db_pool is None:
            log.warning("MySQL database not connected. Cannot update user name record.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor(dictionary=True)  # Use dictionary=True to fetch rows as dicts

            # Fetch existing record
            select_sql = "SELECT display_name, known_names FROM skippy_user_names WHERE user_id = %s AND guild_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, select_sql, (member.id, member.guild.id))
            existing_record = cursor.fetchone()

            current_display_name = member.display_name
            known_names_set = set()  # Use a set for automatic deduplication

            if existing_record:
                old_display_name = existing_record['display_name']
                # If the display name has changed, add the old one to known names
                if old_display_name != current_display_name:
                    known_names_set.add(old_display_name)

                # Add existing known names from the database
                if existing_record['known_names']:
                    try:
                        known_names_from_db = json.loads(existing_record['known_names'])
                        known_names_set.update(known_names_from_db)
                    except json.JSONDecodeError:
                        log.error(
                            f"Failed to decode known_names JSON for user {member.id}: {existing_record['known_names']}")

            # Ensure the *current* display name is never in the known_names_set, as it's stored separately
            known_names_set.discard(current_display_name)

            known_names_json = json.dumps(list(known_names_set))  # Convert back to list for JSON storage

            upsert_sql = """
                         INSERT INTO skippy_user_names (user_id, guild_id, display_name, known_names)
                         VALUES (%s, %s, %s, %s) ON DUPLICATE KEY
                         UPDATE
                             display_name = \
                         VALUES (display_name), known_names = \
                         VALUES (known_names), timestamp = \
                         VALUES (timestamp)
                         """
            await self.bot.loop.run_in_executor(None, cursor.execute, upsert_sql,
                                                (member.id, member.guild.id, current_display_name, known_names_json))
            conn.commit()
            log.debug(f"Updated user name record for {member.display_name} ({member.id}) in guild {member.guild.id}.")

        except mysql.connector.Error as err:
            log.error(f"Error updating user name record for {member.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        except Exception as e:
            log.error(f"Unexpected error in _update_user_name_record for {member.id}: {e}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    async def _get_all_known_user_names(self, guild_id: int) -> dict:
        """
        NEW: Retrieves all known user names and their IDs for a given guild.
        Returns a dictionary mapping user_id to a list of their known names.
        Example: {123: ["John", "Johnny"], 456: ["Jane"]}
        """
        if self.db_pool is None:
            log.warning("MySQL database not connected. Cannot retrieve known user names.")
            return {}

        conn = None
        cursor = None
        known_users = {}
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor(dictionary=True)

            select_sql = "SELECT user_id, display_name, known_names FROM skippy_user_names WHERE guild_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, select_sql, (guild_id,))
            records = cursor.fetchall()

            for record in records:
                user_id = record['user_id']
                names = {record['display_name']}  # Use a set to handle duplicates
                if record['known_names']:
                    try:
                        known_names_json = json.loads(record['known_names'])
                        names.update(known_names_json)
                    except json.JSONDecodeError:
                        log.error(f"Failed to decode known_names JSON for user {user_id}: {record['known_names']}")
                known_users[user_id] = list(names)  # Convert back to list for output

        except mysql.connector.Error as err:
            log.error(f"Error retrieving known user names for guild {guild_id}: {err}", exc_info=True)
        except Exception as e:
            log.error(f"Unexpected error in _get_all_known_user_names: {e}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        return known_users

    async def _extract_and_store_facts(self, ctx: commands.Context, user_message: str):
        """
        Helper method to extract and store user-specific facts from a message using Gemini
        and store them in MySQL, including their embeddings.
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

                        embedding = await self._get_embeddings(memory_content)
                        embedding_bytes = embedding.tobytes()

                        insert_sql = """
                                     INSERT INTO skippy_long_term_memory (user_id, guild_id, content, keywords, embedding)
                                     VALUES (%s, %s, %s, %s, %s)
                                     """
                        await self.bot.loop.run_in_executor(None, cursor.execute, insert_sql,
                                                            (ctx.author.id, ctx.guild.id, memory_content, key,
                                                             embedding_bytes))
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

    async def _extract_and_store_relationships(self, ctx: commands.Context, user_message: str, mentioned_users: list):
        """
        NEW: Helper method to extract and store relationships between users from a message using Gemini.
        It now provides Gemini with known user names for better ID resolution.
        """
        guild_settings = await self.config.guild(ctx.guild).all()
        api_key = guild_settings["api_key"]

        if not api_key:
            log.warning("Cannot auto-learn relationships: Gemini API key not set.")
            return
        if self.db_pool is None:
            log.warning("Cannot auto-learn relationships: MySQL database not connected.")
            return

        # Gather all known user names for context
        known_users_map = await self._get_all_known_user_names(ctx.guild.id)
        known_users_prompt_part = ""
        if known_users_map:
            known_users_info = []
            for user_id, names in known_users_map.items():
                member = ctx.guild.get_member(user_id)
                if member:  # Only include users currently in the guild
                    name_str = f"'{member.display_name}'"
                    if len(names) > 1:
                        other_names = [n for n in names if n != member.display_name]
                        if other_names:
                            name_str += f""" (also known as {', '.join([f"'{n}'" for n in other_names])})"""
                    known_users_info.append(f"ID:{user_id} is {name_str}")
            if known_users_info:
                known_users_prompt_part = "Here is a list of known users and their associated Discord IDs and names:\n" + \
                                          "```\n" + "\n".join(known_users_info) + "\n```\n" + \
                                          "When identifying users in relationships, ALWAYS use their Discord User ID from this list if available. " + \
                                          "If a name is mentioned but no ID is given, try to infer the ID from this list.\n\n"

        relationship_prompt = (
            f"{known_users_prompt_part}"  # Include known users context
            "Analyze the following message for stated or strongly implied relationships between users. "
            "Identify two distinct users from the message (either the author or a mentioned user, or inferred by name) "
            "and their relationship type. Output each identified relationship on a new line "
            "in the exact format: `RELATIONSHIP: User1_ID:<user_id_1>; User2_ID:<user_id_2>; Type:<relationship_type>; Description:<concise context>`. "
            "For `User1_ID`, use the ID of the person stating the relationship (often the author) or the primary subject. "
            "For `User2_ID`, use the ID of the person being described in the relationship. "
            "For `Type`, use a simple descriptor like 'sibling', 'friend', 'spouse', 'colleague', 'parent', 'child', 'partner', 'mentor', 'student'. "
            "If no clear relationships are found, output 'NONE'.\n\n"
            "Example:\n"
            "Message: My brother <@12345> and I are going to the store. (Author: <@98765>)\n"
            "Output: RELATIONSHIP: User1_ID:98765; User2_ID:12345; Type:sibling; Description:going to the store with brother.\n\n"
            "Example:\n"
            "Message: Jane is my best friend. (Author: <@33445>, Known Users: ID:11122 is 'Jane')\n"
            "Output: RELATIONSHIP: User1_ID:33445; User2_ID:11122; Type:friend; Description:is my best friend.\n\n"
            f"Message: {user_message}\nOutput:"
        )

        extraction_payload = {
            "contents": [{"role": "user", "parts": [{"text": relationship_prompt}]}]
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
                parsed_relationships_count = 0
                conn = await self._get_db_connection()
                cursor = conn.cursor()

                relationship_regex = re.compile(
                    r"RELATIONSHIP: User1_ID:(\d+); User2_ID:(\d+); Type:([a-zA-Z0-9_]+); Description:(.*)"
                )

                for line in extracted_text.split('\n'):
                    match = relationship_regex.match(line.strip())
                    if match:
                        user1_id_str, user2_id_str, rel_type, description = match.groups()
                        try:
                            user1_id = int(user1_id_str)
                            user2_id = int(user2_id_str)
                        except ValueError:
                            log.warning(f"Failed to parse user IDs in relationship: {line}")
                            continue

                        # Validate if the extracted IDs are actually members of the guild
                        member1 = ctx.guild.get_member(user1_id)
                        member2 = ctx.guild.get_member(user2_id)

                        if not member1 or not member2:
                            log.warning(
                                f"Skipping relationship - one or both user IDs ({user1_id}, {user2_id}) not found in guild: {line}")
                            continue

                        # Ensure the relationship involves the author or a mentioned user
                        # This prevents the LLM from creating relationships about random users not in context
                        involved_users_in_message = {ctx.author.id}.union({m.id for m in mentioned_users})
                        if user1_id not in involved_users_in_message and user2_id not in involved_users_in_message:
                            log.debug(f"Skipping relationship - neither user involved in message: {line}")
                            continue

                        insert_sql = """
                                     INSERT INTO skippy_relationships (user_id_initiator, user_id_target,
                                                                       relationship_type, description, guild_id)
                                     VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY
                                     UPDATE description = \
                                     VALUES (description), timestamp = \
                                     VALUES (timestamp)
                                     """
                        await self.bot.loop.run_in_executor(None, cursor.execute, insert_sql,
                                                            (user1_id, user2_id, rel_type.lower(), description.strip(),
                                                             ctx.guild.id))
                        parsed_relationships_count += 1
                conn.commit()
                if parsed_relationships_count > 0:
                    log.info(
                        f"Automatically learned {parsed_relationships_count} relationships from user {ctx.author.id} in guild {ctx.guild.id} to MySQL.")
            else:
                log.debug(f"No new relationships extracted from user {ctx.author.id}'s message.")

        except aiohttp.ClientError as e:
            log.error(f"HTTP error during relationship extraction for guild {ctx.guild.id}: {e}")
        except mysql.connector.Error as err:
            log.error(f"Error storing learned relationships to MySQL: {err}", exc_info=True)
            if conn:
                conn.rollback()
        except json.JSONDecodeError as e:
            log.error(f"JSON decode error during relationship extraction for guild {ctx.guild.id}: {e}")
        except Exception as e:
            log.error(f"Unexpected error during relationship extraction for guild {ctx.guild.id}: {e}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    async def _retrieve_relevant_memories(self, user_prompt: str, ctx: commands.Context,
                                          user_ids_in_conversation: list) -> str:
        """
        Retrieves relevant long-term memories from MySQL based on the user's prompt
        using embedding similarity (RAG).
        Returns a formatted string of relevant memories to be included in the Gemini prompt.
        user_ids_in_conversation: List of user IDs (author + mentioned users) relevant to the current conversation.
        """
        if self.db_pool is None:
            log.warning("MySQL database not connected. Cannot retrieve long-term memories.")
            return ""

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            query_embedding = await self._get_embeddings(user_prompt)

            all_memories_for_users = []
            if not user_ids_in_conversation:
                log.debug("No relevant users for memory retrieval.")
                return ""

            placeholders = ', '.join(['%s'] * len(user_ids_in_conversation))
            sql_fetch_memories = f"""
                                 SELECT content, embedding, user_id
                                 FROM skippy_long_term_memory
                                 WHERE user_id IN ({placeholders}) AND guild_id = %s
                                 ORDER BY timestamp DESC LIMIT 200
                                 """
            params = tuple(user_ids_in_conversation) + (ctx.guild.id,)
            await self.bot.loop.run_in_executor(None, cursor.execute, sql_fetch_memories, params)

            memories_raw = cursor.fetchall()

            if not memories_raw:
                return ""

            scored_memories = []
            for content, embedding_blob, memory_user_id in memories_raw:
                try:
                    stored_embedding = np.frombuffer(embedding_blob, dtype=np.float32)

                    similarity = cosine_similarity(query_embedding.reshape(1, -1), stored_embedding.reshape(1, -1))[0][
                        0]

                    scored_memories.append((content, similarity, memory_user_id))
                except Exception as e:
                    log.error(f"Error processing embedding for memory: {content[:50]}... Error: {e}")
                    continue

            scored_memories.sort(key=lambda x: x[1], reverse=True)
            top_relevant_memories = scored_memories[:5]

            if not top_relevant_memories:
                return ""

            formatted_memories = []
            for content, similarity, uid in top_relevant_memories:
                member = ctx.guild.get_member(uid)
                user_display_name = member.display_name if member else f"User ID: {uid}"
                formatted_memories.append(f"Fact about {user_display_name}: {content}")

            return (
                "You have the following specific facts and memories about the users involved in this conversation:\n"
                f"```\n{' | '.join(formatted_memories)}\n```\n"
                "Incorporate these facts subtly and naturally where relevant, without explicitly stating you 'remembered' them from a database."
            )

        except mysql.connector.Error as err:
            log.error(f"Error retrieving long-term memories from MySQL for RAG: {err}", exc_info=True)
            return ""
        except Exception as e:
            log.error(f"Unexpected error during RAG memory retrieval: {e}", exc_info=True)
            return ""
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    async def _retrieve_relevant_relationships(self, ctx: commands.Context, user_ids_in_conversation: list) -> str:
        """
        Retrieves relevant relationships from MySQL based on the users involved in the conversation.
        Returns a formatted string of relevant relationships to be included in the Gemini prompt.
        """
        if self.db_pool is None:
            log.warning("MySQL database not connected. Cannot retrieve relationships.")
            return ""
        if not user_ids_in_conversation or len(user_ids_in_conversation) < 2:
            log.debug("Less than two relevant users for relationship retrieval. Skipping.")
            return ""

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            placeholders = ', '.join(['%s'] * len(user_ids_in_conversation))
            sql = f"""
                  SELECT user_id_initiator, user_id_target, relationship_type, description, timestamp
                  FROM skippy_relationships
                  WHERE guild_id = %s
                  AND (user_id_initiator IN ({placeholders}) OR user_id_target IN ({placeholders}))
                  LIMIT 10
                  """
            params = (ctx.guild.id,) + tuple(user_ids_in_conversation) + tuple(user_ids_in_conversation)

            await self.bot.loop.run_in_executor(None, cursor.execute, sql, params)
            relationships = cursor.fetchall()

            if not relationships:
                return ""

            formatted_relationships = []  # Initialize this list
            for initiator_id, target_id, rel_type, description, timestamp in relationships:
                initiator_member = ctx.guild.get_member(initiator_id)
                target_member = ctx.guild.get_member(target_id)

                initiator_name = initiator_member.display_name if initiator_member else f"User ID: {initiator_id}"
                target_name = target_member.display_name if target_member else f"User ID: {target_id}"

                rel_desc = f" ({description})" if description else ""
                formatted_relationships.append(  # Corrected line
                    f"'{initiator_name}' is {rel_type} of '{target_name}'{rel_desc}"
                )

            formatted_relationships = list(set(formatted_relationships))  # Remove duplicates

            return (
                "You also have the following known relationships between users:\n"
                f"```\n{' | '.join(formatted_relationships)}\n```\n"
                "Utilize this relationship knowledge to inform your conversational responses, especially when addressing or referring to specific users."
            )

        except mysql.connector.Error as err:
            log.error(f"Error retrieving relationships from MySQL: {err}", exc_info=True)
            return ""
        except Exception as e:
            log.error(f"Unexpected error during relationship retrieval: {e}", exc_info=True)
            return ""
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    async def _get_gemini_response(self, ctx: commands.Context, user_prompt: str, mentioned_users: list = None):
        """
        Helper method to call the Gemini API and send the response.
        Handles API key checks, network requests, and error handling.
        Includes the dynamic personality (mood-based), user-specific memories (from MySQL via RAG),
        and manages conversation history.
        The personality, memory, and relationship contexts are now combined into a single initial prompt.
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

        user_ids_in_conversation = {ctx.author.id}
        if mentioned_users:
            user_ids_in_conversation.update({m.id for m in mentioned_users})
        user_ids_list = list(user_ids_in_conversation)

        # Retrieve relevant memories and relationships to inject into the prompt
        memory_retrieval_prompt = await self._retrieve_relevant_memories(user_prompt, ctx, user_ids_list)
        relationship_retrieval_prompt = await self._retrieve_relevant_relationships(ctx, user_ids_list)

        if not api_key:
            await ctx.send(
                "Poppycock! The Gemini API key has not been set. "
                f"Please ask the bot owner to set it using `{ctx.prefix}skippy setkey <your_api_key>`."
            )
            return None

        # Combine all contextual information into a single setup prompt for Gemini
        context_parts = [actual_personality_prompt]

        if memory_retrieval_prompt:
            context_parts.append(memory_retrieval_prompt)

        if relationship_retrieval_prompt:
            context_parts.append(relationship_retrieval_prompt)

        # Create the initial system/context prompt that sets Skippy's persona and provides memory/relationship context
        initial_context_prompt = "\n\n".join(context_parts)

        payload_contents = []
        if initial_context_prompt:
            payload_contents.append({"role": "user", "parts": [{"text": initial_context_prompt}]})
            payload_contents.append(
                {"role": "model",
                 "parts": [{"text": "Understood. The tapestry of existence is clear."}]})  # Skippy's acknowledgement

        # Truncate conversation history to fit within context limits
        truncated_history_for_payload = current_history[-(max_turns * 2):]
        payload_contents.extend(truncated_history_for_payload)

        # Add the user's current prompt
        payload_contents.append({"role": "user", "parts": [{"text": user_prompt}]})

        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": payload_contents
        }
        headers = {
            "Content-Type": "application/json"
        }

        message = await ctx.send("Hum dee dum! Thinking... please wait. My ethereal gears are grinding.")

        try:
            async with self.session.post(api_url, headers=headers, data=json.dumps(payload)) as response:
                response.raise_for_status()
                result = await response.json()

            if result.get("candidates") and result["candidates"][0].get("content") and result["candidates"][0][
                "content"].get("parts"):
                generated_text = result["candidates"][0]["content"]["parts"][0]["text"]

                # Update conversation history
                current_history.append({"role": "user", "parts": [{"text": user_prompt}]})
                current_history.append({"role": "model", "parts": [{"text": generated_text}]})

                current_history = current_history[-(max_turns * 2):]  # Ensure history doesn't exceed max turns

                async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
                    conv_hist[channel_history_key] = current_history

                for page in pagify(generated_text, delims=["\n", " "], escape_mass_mentions=True):
                    await ctx.send(page)
                log.info(f"Successfully responded to Skippy prompt from guild: {ctx.guild.id}")

                # Asynchronously learn facts and relationships
                auto_learn_facts = await self.config.guild(ctx.guild).auto_learn_facts()
                if auto_learn_facts:
                    self.bot.loop.create_task(self._extract_and_store_facts(ctx, user_prompt))

                auto_learn_relationships = await self.config.guild(ctx.guild).auto_learn_relationships()
                if auto_learn_relationships:
                    self.bot.loop.create_task(
                        self._extract_and_store_relationships(ctx, ctx.message.content, ctx.message.mentions))

                return generated_text
            else:
                await ctx.send(
                    "Fiddlesticks! Could not get a valid response from the Gemini API. The model might not have generated any content. Perhaps it was napping?")
                log.warning(f"Unexpected Gemini API response structure: {result} for guild: {ctx.guild.id}")
                return None

        except aiohttp.ClientError as e:
            await ctx.send(
                f"A strange ethereal disturbance prevented Skippy from reaching the cosmic archives (Gemini API). Error: {e}")
            log.error(f"HTTP error during Gemini API call for guild {ctx.guild.id}: {e}")
            return None
        except json.JSONDecodeError as e:
            await ctx.send(f"Poppycock! The cosmic scrolls were unreadable. Failed to parse the API response: {e}")
            log.error(f"JSON decode error during Gemini API call for guild {ctx.guild.id}: {e}")
            return None
        except Exception as e:
            await ctx.send(f"A cosmic anomaly occurred: {e}. Skippy is quite vexed.")
            log.error(f"Unexpected error during Gemini API call for guild {ctx.guild.id}: {e}", exc_info=True)
            return None
        finally:
            try:
                await message.delete()  # Attempt to delete the "Thinking..." message
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
                f"Hmph. MySQL connection details set. Skippy will now use `{database}` on `{host}:{port}` for his memories and relationships. Try not to break it, mortals.")
            log.info(f"MySQL connection details set for guild: {ctx.guild.id}")
        else:
            await ctx.send(
                "Fiddlesticks! Failed to connect to MySQL with the provided details. Check the console logs, my master, for the ethereal errors.")
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
            await ctx.send("Error: CORE_PERSONALITY is empty. This should not happen. Poppycock!")

    @_skippy.command(name="remember")
    async def _skippy_remember(self, ctx: commands.Context, *, memory_content: str):
        """
        Asks Skippy to remember a specific piece of information for long-term recall.
        This will be stored in MySQL with an associated embedding.
        Example: `[p]skippy remember I enjoy long walks on the beach.`
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Please ask the bot owner to set it up. My apologies, but my mind is currently... elsewhere.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            embedding = await self._get_embeddings(memory_content)
            embedding_bytes = embedding.tobytes()

            sql = """
                  INSERT INTO skippy_long_term_memory (user_id, guild_id, content, embedding)
                  VALUES (%s, %s, %s, %s)
                  """
            await self.bot.loop.run_in_executor(None, cursor.execute, sql,
                                                (ctx.author.id, ctx.guild.id, memory_content, embedding_bytes))
            conn.commit()
            await ctx.send("Understood. That information has been etched into my long-term memory scrolls.")
            log.info(f"User {ctx.author.id} added a long-term memory: '{memory_content[:50]}...'")
        except mysql.connector.Error as err:
            await ctx.send(
                f"Fiddlesticks! A mischievous imp interfered with my memory scrolls. I couldn't quite etch that in. Error: {err}")
            log.error(f"Error remembering for user {ctx.author.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        except Exception as e:
            await ctx.send(
                f"Hum dee dum! A cosmic anomaly prevented that memory from sticking. Perhaps try again when the stars align? Error: {e}")
            log.error(f"Unexpected error during remember command for user {ctx.author.id}: {e}", exc_info=True)
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
        Now also shows the memory ID and shows up to 50 recent memories.
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot retrieve memories. My apologies, but my mind is currently... elsewhere.")
            return

        target_user_id = target.id if target else ctx.author.id
        target_display_name = target.display_name if target else ctx.author.display_name

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            sql = """
                  SELECT id, content, timestamp
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
                    f"Alas, my memory for {target_display_name}'s specifics seems as ethereal as mist. I recall nothing about them. Perhaps they've been too mundane for my ancient mind?")
                return

            memories_list = [
                f"ID: {mid} - '{content}' (etched on {timestamp.strftime('%Y-%m-%d')})"
                for mid, content, timestamp in memories
            ]
            response_text = (
                    f"From the scrolls of my long-term memory, I recall these fragments concerning {target_display_name}:\n"
                    f"```\n" + "\n".join(memories_list) + "\n```"
            )

            for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
                await ctx.send(page)

        except mysql.connector.Error as err:
            await ctx.send(f"A ripple in the memory currents prevented recall: {err}. Such a nuisance!")
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
        Use `whatdoyouremember` to see the full memory content if needed.
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot forget. My apologies, but my mind is currently... elsewhere.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            search_sql = """
                         SELECT id, content
                         FROM skippy_long_term_memory
                         WHERE user_id = %s
                           AND guild_id = %s
                           AND content LIKE %s LIMIT 5
                         """
            search_term = f"%{memory_content_partial}%"
            await self.bot.loop.run_in_executor(None, cursor.execute, search_sql,
                                                (ctx.author.id, ctx.guild.id, search_term))

            matching_memories = cursor.fetchall()

            if not matching_memories:
                await ctx.send(
                    f"I do not recall any memory about you containing '{memory_content_partial}'. Perhaps it was a fleeting thought? Or perhaps, you're just not that memorable, hmph.")
                return

            if len(matching_memories) > 1:
                memories_list = "\n".join([f"ID: {mid} - '{mcontent[:50]}...'" for mid, mcontent in matching_memories])
                await ctx.send(
                    f"Several memories match '{memory_content_partial}':\n"
                    f"```\n{memories_list}\n```\n"
                    "Fiddlesticks! Please be more specific or use the `[p]skippy forgetid <ID>` command with the exact ID to forget a specific one. My patience for ambiguity is thin."
                )
                return

            memory_id_to_delete = matching_memories[0][0]
            deleted_content = matching_memories[0][1]

            delete_sql = "DELETE FROM skippy_long_term_memory WHERE id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, delete_sql, (memory_id_to_delete,))
            conn.commit()
            await ctx.send(
                f"The scroll for '{deleted_content[:50]}...' has been purged from my archives. Consider it undone. Now, where was I?")
            log.info(f"User {ctx.author.id} had memory ID {memory_id_to_delete} deleted.")

        except mysql.connector.Error as err:
            await ctx.send(f"A tear in the fabric of memory occurred: {err}. Such a nuisance!")
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
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot forget. My apologies, but my mind is currently... elsewhere.")
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
                await ctx.send(
                    f"Memory with ID `{memory_id}` does not exist. Perhaps it was a phantom? Or you misread the ancient script, you naysayer!")
                return

            stored_user_id, content = result
            if stored_user_id != ctx.author.id and not await self.bot.is_owner(ctx.author):
                await ctx.send(
                    "Poppycock! You can only forget memories that you yourself etched into my scrolls, unless you are my master. Don't meddle with what you don't understand.")
                return

            delete_sql = "DELETE FROM skippy_long_term_memory WHERE id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, delete_sql, (memory_id,))
            conn.commit()
            await ctx.send(
                f"Memory with ID `{memory_id}` ('{content[:50]}...') has been utterly vanished. Hmph. Good riddance.")
            log.info(f"User {ctx.author.id} deleted memory ID {memory_id}.")

        except mysql.connector.Error as err:
            await ctx.send(f"A corruption in the memory stream occurred: {err}. Such a nuisance!")
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
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot forget all. My apologies, but my mind is currently... elsewhere.")
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
                "All fragments of memory concerning you in this guild have been wiped from my mind. A fresh slate, as it were. Try not to fill it with more cosmic blunders.")
            log.info(f"All memories cleared for user {ctx.author.id} in guild {ctx.guild.id}.")
        except mysql.connector.Error as err:
            await ctx.send(f"An ancient curse prevented the complete erasure: {err}. Fiddlesticks!")
            log.error(f"Error forgetting all memories for user {ctx.author.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="showrelationships")
    async def _skippy_showrelationships(self, ctx: commands.Context, target: discord.Member = None):
        """
        Asks Skippy to show relationships he knows about, optionally centered on a specific user.
        Defaults to relationships involving you.
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot retrieve relationships. My apologies, but my mind is currently... elsewhere.")
            return

        target_user_id = target.id if target else ctx.author.id
        target_display_name = target.display_name if target else ctx.author.display_name

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            sql = """
                  SELECT user_id_initiator, user_id_target, relationship_type, description, timestamp
                  FROM skippy_relationships
                  WHERE guild_id = %s
                    AND (user_id_initiator = %s
                     OR user_id_target = %s)
                  ORDER BY timestamp DESC
                      LIMIT 20
                  """
            await self.bot.loop.run_in_executor(None, cursor.execute, sql,
                                                (ctx.guild.id, target_user_id, target_user_id))
            relationships = cursor.fetchall()

            if not relationships:
                await ctx.send(
                    f"The scrolls whisper nothing of relationships involving {target_display_name}. Perhaps they are a lone wolf... or an exceptionally private otter. Hmph.")
                return

            relationships_list = []
            for initiator_id, target_id, rel_type, description, timestamp in relationships:
                initiator_member = ctx.guild.get_member(initiator_id)
                target_member = ctx.guild.get_member(target_id)

                initiator_name = initiator_member.display_name if initiator_member else f"User ID: {initiator_id}"
                target_name = target_member.display_name if target_member else f"User ID: {target_id}"

                rel_desc = f" ({description})" if description else ""
                relationships_list.append(
                    f"'{initiator_name}' is {rel_type} of '{target_name}'{rel_desc}"
                )

            response_text = (
                    f"From my vast knowledge, here are known relationships involving {target_display_name}:\n"
                    f"```\n" + "\n".join(relationships_list) + "\n```"
            )

            for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
                await ctx.send(page)

        except mysql.connector.Error as err:
            await ctx.send(f"A crack appeared in the relational matrix: {err}. How vexing!")
            log.error(f"Error retrieving relationships for user {target_user_id}: {err}", exc_info=True)
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="forgetrelationship")
    async def _skippy_forgetrelationship(self, ctx: commands.Context, user1: discord.Member, user2: discord.Member,
                                         relationship_type: str):
        """
        Asks Skippy to forget a specific relationship between two users.
        The order of users matters for initiator/target. Use `showrelationships` to verify.
        Example: [p]skippy forgetrelationship @UserA @UserB friend
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot forget relationships. My apologies, but my mind is currently... elsewhere.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()

            if user1.id != ctx.author.id and not await self.bot.is_owner(ctx.author):
                await ctx.send(
                    "Poppycock! You can only forget relationships you've initiated or if you are my master. Don't meddle with what you don't understand.")
                return

            delete_sql = """
                         DELETE
                         FROM skippy_relationships
                         WHERE user_id_initiator = %s
                           AND user_id_target = %s
                           AND relationship_type = %s
                           AND guild_id = %s
                         """
            await self.bot.loop.run_in_executor(None, cursor.execute, delete_sql,
                                                (user1.id, user2.id, relationship_type.lower(), ctx.guild.id))

            if cursor.rowcount > 0:
                conn.commit()
                await ctx.send(
                    f"The '{relationship_type}' relationship between {user1.display_name} and {user2.display_name} has been excised from my chronicles. Consider it undone.")
                log.info(
                    f"Relationship '{relationship_type}' between {user1.id} and {user2.id} deleted by {ctx.author.id}.")
            else:
                await ctx.send("That specific relationship was not found in my records, you naysayer.")

        except mysql.connector.Error as err:
            await ctx.send(
                f"A paradox occurred while trying to forget that relationship: {err}. How utterly inconvenient!")
            log.error(f"Error forgetting relationship: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="forgetallrelationships")
    @commands.is_owner()
    async def _skippy_forgetallrelationships(self, ctx: commands.Context):
        """
        (Owner Only) Asks Skippy to forget ALL relationships stored for this guild.
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot forget all relationships. My apologies, but my mind is currently... elsewhere.")
            return

        conn = None
        cursor = None
        try:
            conn = await self._get_db_connection()
            cursor = conn.cursor()
            sql = "DELETE FROM skippy_relationships WHERE guild_id = %s"
            await self.bot.loop.run_in_executor(None, cursor.execute, sql, (ctx.guild.id,))
            conn.commit()
            await ctx.send(
                "All relationships in this guild have been erased from my memory banks. A clean slate for your social chaos. Hmph.")
            log.info(f"All relationships cleared for guild {ctx.guild.id} by owner {ctx.author.id}.")
        except mysql.connector.Error as err:
            await ctx.send(f"An ancient curse prevented the complete erasure of relationships: {err}. Fiddlesticks!")
            log.error(f"Error forgetting all relationships for guild {ctx.guild.id}: {err}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @_skippy.command(name="enableautolearn")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_enableautolearn(self, ctx: commands.Context):
        """
        Enables Skippy to automatically learn facts about users from conversations.
        """
        await self.config.guild(ctx.guild).auto_learn_facts.set(True)
        await ctx.send(
            "Skippy's fact-finding senses are now sharpened. He will attempt to learn new details about users. Try not to bore him.")
        log.info(f"Auto-learn facts enabled for guild: {ctx.guild.id}")

    @_skippy.command(name="disableautolearn")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_disableautolearn(self, ctx: commands.Context):
        """
        Disables Skippy from automatically learning facts about users from conversations.
        """
        await self.config.guild(ctx.guild).auto_learn_facts.set(False)
        await ctx.send(
            "Skippy's fact-finding senses are now dulled. He will only use explicitly provided information. Hmph. Less work for me.")
        log.info(f"Auto-learn facts disabled for guild: {ctx.guild.id}")

    @_skippy.command(name="enableautolearnrelationships")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_enableautolearnrelationships(self, ctx: commands.Context):
        """
        Enables Skippy to automatically learn relationships between users from conversations.
        """
        await self.config.guild(ctx.guild).auto_learn_relationships.set(True)
        await ctx.send(
            "Skippy's social antennae are now extended. He will attempt to discern and record relationships between users. Prepare for judgment.")
        log.info(f"Auto-learn relationships enabled for guild: {ctx.guild.id}")

    @_skippy.command(name="disableautolearnrelationships")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_disableautolearnrelationships(self, ctx: commands.Context):
        """
        Disables Skippy from automatically learning relationships between users from conversations.
        """
        await self.config.guild(ctx.guild).auto_learn_relationships.set(False)
        await ctx.send(
            "Skippy's relationship detection circuits are now powered down. He will only react to explicitly stated bonds. Less drama for me, thankfully.")
        log.info(f"Auto-learn relationships disabled for guild: {ctx.guild.id}")

    @_skippy.command(name="showknownnames")
    @commands.admin_or_permissions(manage_guild=True)
    async def _skippy_showknownnames(self, ctx: commands.Context, target: discord.Member = None):
        """
        Shows the names Skippy knows for a specific user, or all users if no target is given.
        """
        if self.db_pool is None:
            await ctx.send(
                "Skippy's memory vault (MySQL) is not connected. Cannot retrieve known names. My apologies, but my mind is currently... elsewhere.")
            return

        response_text = ""
        if target:
            known_users_map = await self._get_all_known_user_names(ctx.guild.id)
            names = known_users_map.get(target.id)
            if names:
                response_text = f"For {target.display_name} (ID: {target.id}), Skippy knows these names: {', '.join(names)}. Impressive, wot not?"
            else:
                response_text = f"Skippy has no specific names recorded for {target.display_name} (ID: {target.id}). Perhaps they are a shadowy figure?"
        else:
            known_users_map = await self._get_all_known_user_names(ctx.guild.id)
            if not known_users_map:
                await ctx.send("Skippy has no known names recorded for any users in this guild. How utterly dull.")
                return

            name_info = []
            for user_id, names in known_users_map.items():
                member = ctx.guild.get_member(user_id)
                display_name = member.display_name if member else f"Unknown User (ID: {user_id})"
                name_info.append(f"{display_name} (ID: {user_id}): {', '.join(names)}")

            response_text = "Skippy's directory of known user names:\n```\n" + "\n".join(name_info) + "\n```"

        for page in pagify(response_text, delims=["\n"], escape_mass_mentions=True):
            await ctx.send(page)

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
                await ctx.send(
                    f"Please use this command in one of the following channels: {{ {', '.join(allowed_mentions)} }}. "
                    "Perhaps you should seek a more appropriate venue for such inquiries. Hmph."
                )
            else:
                await ctx.send(
                    "`[p]skippy ask` command interactions are restricted to specific channels, but none are configured or valid. "
                    "Please ask an admin to configure allowed channels. My patience for unconfigured chaos is thin."
                )
            return

        await self._get_gemini_response(ctx, prompt, mentioned_users=ctx.message.mentions)

    @_skippy.command(name="clearconversation")
    @commands.admin_or_permissions(manage_channels=True)
    async def _skippy_clearconversation(self, ctx: commands.Context):
        """
        Clears the conversation history for the current channel.
        """
        channel_history_key = str(ctx.channel.id)
        async with self.config.guild(ctx.guild).conversation_history() as conv_hist:
            if channel_history_key in conv_hist:
                del conv_hist[channel_history_key]
                await ctx.send(
                    "The ethereal echoes of our past conversation in this channel have been swept away. A fresh canvas for your blunders.")
                log.info(f"Conversation history cleared for channel {ctx.channel.id} in guild {ctx.guild.id}.")
            else:
                await ctx.send("There is no conversation history to clear in this channel, you silly goose. Poppycock!")
                log.debug(f"No conversation history to clear for channel {ctx.channel.id} in guild {ctx.guild.id}.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listens for messages to enable conversational interaction with Skippy.
        Also attempts to read content from attached .txt and .pdf files.
        NEW: Updates user name records.
        """
        if message.author.bot:
            return

        if not message.guild:
            return

        # NEW: Update user's name record whenever they send a message
        # This helps Skippy keep track of display name changes and known names
        self.bot.loop.create_task(self._update_user_name_record(message.author))

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
                        if decoded_content:  # Check if content is not empty
                            processed_attachment_content += f"\n\n--- Content from {attachment.filename} ---\n{decoded_content}\n--- End of {attachment.filename} Content ---\n"
                            log.info(f"Successfully read .txt attachment: {attachment.filename}")
                        else:
                            await message.channel.send(
                                f"Skippy found no legible text in '{attachment.filename}'. Perhaps it's a scroll of blank spells, you naysayer?",
                                delete_after=10)
                    except Exception as e:
                        log.error(f"Error reading .txt attachment '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"Alas, Skippy had trouble deciphering the ancient runes in '{attachment.filename}'. Error: {e}. Fiddlesticks!",
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
                                f"Skippy found no legible text in '{attachment.filename}'. Perhaps it's a scroll of blank spells, you naysayer?",
                                delete_after=10)

                    except PyPDF2.errors.PdfReadError as e:
                        log.error(f"Error reading PDF '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"Skippy encountered an arcane glyph in '{attachment.filename}' and couldn't decipher it (PDF Read Error: {e}). How vexing!",
                            delete_after=10)
                        continue
                    except ImportError:
                        await message.channel.send(
                            "Skippy needs the 'PyPDF2' incantation to read PDFs. Tell my master to cast `pip install PyPDF2`! Hmph.",
                            delete_after=15)
                        log.error("PyPDF2 not installed. Cannot read PDF files.")
                        continue
                    except Exception as e:
                        log.error(f"Unexpected error processing PDF '{attachment.filename}': {e}")
                        await message.channel.send(
                            f"A strange ethereal disturbance prevented Skippy from comprehending '{attachment.filename}'. Error: {e}. Poppycock!",
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

