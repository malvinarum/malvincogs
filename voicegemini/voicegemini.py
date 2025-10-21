import discord
import asyncio
import io
import json
import time
import wave
import aiohttp
import base64  # <-- ADDED: Standard library for base64 encoding/decoding
from redbot.core import commands, Config
from typing import Optional

# --- API Constants (For Structured Calls) ---
# NOTE: The API key should be securely stored in Redbot config, not hardcoded.
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TEXT_MODEL = "gemini-2.5-flash-preview-09-2025"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
# Default sample rate returned by gemini-2.5-flash-preview-tts is 24000 Hz
TTS_SAMPLE_RATE = 24000
TTS_MIME_TYPE = f"audio/L16;rate={TTS_SAMPLE_RATE};channels=1"


# This utility function creates a simple in-memory WAV file header
# from raw PCM data, which is necessary for Discord to play the audio correctly.
def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> io.BytesIO:
    """Wraps raw PCM audio data in a WAV container."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit (2 bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    wav_io.seek(0)
    return wav_io


class VoiceGemini(commands.Cog):
    """
    A Redbot cog that integrates the Gemini API with voice channels for conversational AI.
    """

    def __init__(self, bot):
        self.bot = bot
        # Configuration setup for storing API keys
        self.config = Config.get_conf(self, identifier=6084601438902573, force_registration=True)
        default_global = {"api_key": ""}
        self.config.register_global(**default_global)

        # Dictionary to hold voice clients: guild_id -> discord.VoiceClient
        self.voice_clients = {}

    async def _get_api_key(self) -> Optional[str]:
        """Retrieves the API key from the Redbot config."""
        return await self.config.api_key()

    async def _make_api_call(self, url: str, payload: dict, is_audio_request: bool = False) -> Optional[bytes]:
        """
        Handles API interaction with exponential backoff and error handling.
        Returns bytes for audio calls, or JSON response for text calls.
        """
        api_key = await self._get_api_key()
        if not api_key:
            raise ValueError("Gemini API key not set. Use `[p]vset api_key`.")

        headers = {'Content-Type': 'application/json'}
        max_retries = 5

        async with aiohttp.ClientSession() as session:
            for attempt in range(max_retries):
                try:
                    full_url = f"{url}?key={api_key}"
                    async with session.post(full_url, headers=headers, json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            candidate = data.get("candidates", [{}])[0]

                            if is_audio_request:
                                # Extract raw audio bytes from the response
                                part = candidate.get("content", {}).get("parts", [{}])[0]
                                audio_data = part.get("inlineData", {}).get("data")

                                if audio_data:
                                    # FIX: Use standard base64 decoding
                                    return base64.b64decode(audio_data)
                                else:
                                    raise ValueError("Failed to extract audio data from API response.")

                            else:
                                # Extract text response
                                text = candidate.get("content", {}).get("parts", [{}])[0].get("text")
                                return text

                        elif response.status == 429 or response.status >= 500:
                            # Rate limit or server error: retry with backoff
                            if attempt < max_retries - 1:
                                wait_time = 2 ** attempt
                                await asyncio.sleep(wait_time)
                            else:
                                response_text = await response.text()
                                raise commands.UserFeedbackCheckFailure(
                                    f"API failed after {max_retries} attempts (Status: {response.status}). Response: {response_text[:100]}"
                                )
                        else:
                            # Other client/API errors
                            response_text = await response.text()
                            raise commands.UserFeedbackCheckFailure(
                                f"Gemini API returned an error (Status: {response.status}): {response_text}"
                            )

                except aiohttp.ClientError as e:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        await asyncio.sleep(wait_time)
                    else:
                        raise commands.UserFeedbackCheckFailure(f"Network error communicating with Gemini API: {e}")

                except Exception as e:
                    # Catch the custom ValueError/commands.UserFeedbackCheckFailure from inside the try block
                    if isinstance(e, commands.UserFeedbackCheckFailure) or isinstance(e, ValueError):
                        raise e
                    # Otherwise, re-raise as a general failure
                    raise commands.UserFeedbackCheckFailure(f"An unexpected error occurred during API call: {e}")
        return None

    async def _tts_audio_source(self, ctx: commands.Context, prompt: str) -> Optional[discord.FFmpegPCMAudio]:
        """
        Generates text via LLM, then synthesizes audio via TTS, and prepares an
        FFmpegPCMAudio source for Discord.
        """

        # 1. LLM Call (Text Generation)
        await ctx.send(f"🤖 Thinking about your request...")
        llm_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],  # Use grounding for better responses
            "systemInstruction": {"parts": [{
                                                "text": "You are a helpful, cheerful, and concise voice assistant for a Discord bot. Respond directly to the user's query."}]}
        }

        text_url = f"{API_BASE}/{TEXT_MODEL}:generateContent"

        try:
            llm_text = await self._make_api_call(text_url, llm_payload, is_audio_request=False)
        except commands.UserFeedbackCheckFailure as e:
            await ctx.send(f"LLM Error: {e}")
            return None

        if not llm_text:
            await ctx.send("The LLM did not generate a response.")
            return None

        # 2. TTS Call (Audio Generation)
        await ctx.send(f"🗣️ Gemini says: *{llm_text}*")

        tts_payload = {
            "contents": [{"parts": [{"text": llm_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}
                }
            }
        }
        tts_url = f"{API_BASE}/{TTS_MODEL}:generateContent"

        try:
            pcm_bytes = await self._make_api_call(tts_url, tts_payload, is_audio_request=True)
        except commands.UserFeedbackCheckFailure as e:
            await ctx.send(f"TTS Error: {e}")
            return None

        # 3. Prepare Audio Source
        wav_io = _pcm_to_wav(pcm_bytes, TTS_SAMPLE_RATE)

        # Discord requires an audio source. We use FFmpeg to read the in-memory WAV file.
        # The '-f wav -i pipe:0' tells FFmpeg to read a WAV file from the standard input (pipe).
        # The 'before_options' is often necessary for proper streaming from raw data.
        audio_source = discord.FFmpegPCMAudio(
            wav_io.read(),
            pipe=True,
            options=f'-f {TTS_MIME_TYPE.split(";")[0].split("/")[1]} -i pipe:0',
            before_options="-nostdin -y"
        )
        return audio_source

    @commands.group(name="vset")
    async def vset(self, ctx: commands.Context):
        """Settings for the VoiceGemini cog."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @vset.command(name="api_key")
    @commands.is_owner()
    async def vset_api_key(self, ctx: commands.Context, key: str):
        """Sets the Gemini API key."""
        await self.config.api_key.set(key)
        await ctx.send("Gemini API key has been set successfully.")

    @commands.command(name="vjoin")
    async def vjoin(self, ctx: commands.Context):
        """Connects the bot to your current voice channel."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("You must be in a voice channel to use this command.")

        channel = ctx.author.voice.channel
        guild_id = ctx.guild.id

        if guild_id in self.voice_clients and self.voice_clients[guild_id].is_connected():
            await self.voice_clients[guild_id].move_to(channel)
        else:
            try:
                vc = await channel.connect()
                self.voice_clients[guild_id] = vc
            except asyncio.TimeoutError:
                return await ctx.send("Connection to voice channel timed out.")
            except discord.ClientException:
                return await ctx.send("I am already in a voice channel.")
            except RuntimeError as e:
                if 'PyNaCl' in str(e):
                    return await ctx.send(
                        "**Voice Error:** The PyNaCl library is missing. "
                        "Please install it in your Redbot environment using:\n"
                        "```bash\npip install pynacl\n```"
                    )
                else:
                    raise  # Re-raise any other unexpected RuntimeErrors

        await ctx.send(f"Connected to voice channel: **{channel.name}**")

    @commands.command(name="vleave")
    async def vleave(self, ctx: commands.Context):
        """Disconnects the bot from the current voice channel."""
        guild_id = ctx.guild.id
        if guild_id in self.voice_clients and self.voice_clients[guild_id].is_connected():
            await self.voice_clients[guild_id].disconnect()
            del self.voice_clients[guild_id]
            await ctx.send("Disconnected from voice channel.")
        else:
            await ctx.send("I am not currently in a voice channel.")

    @commands.command(name="vtalk")
    async def vtalk(self, ctx: commands.Context, *, prompt: str):
        """
        Asks Gemini a question and streams the audio response to the voice channel.

        Usage: [p]vtalk What is the capital of France?
        """
        guild_id = ctx.guild.id

        # 1. Check Voice Status
        if guild_id not in self.voice_clients or not self.voice_clients[guild_id].is_connected():
            return await ctx.send(f"I am not in a voice channel. Use `[p]vjoin` first.")

        vc = self.voice_clients[guild_id]

        if vc.is_playing():
            return await ctx.send("I'm currently speaking! Please wait your turn.")

        # 2. Get Audio Source (LLM -> TTS)
        audio_source = await self._tts_audio_source(ctx, prompt)

        if not audio_source:
            # Error message already sent by _tts_audio_source
            return

        # 3. Play Audio
        vc.play(audio_source, after=lambda e: print(f'Player error: {e}') if e else None)

        # Wait for playback to finish
        while vc.is_playing():
            await asyncio.sleep(1)

        await ctx.send("Finished speaking.")

    def cog_unload(self):
        """Ensures all voice clients are disconnected when the cog is unloaded."""
        for vc in self.voice_clients.values():
            if vc.is_connected():
                self.bot.loop.create_task(vc.disconnect())
