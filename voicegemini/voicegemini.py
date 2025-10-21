import discord
import asyncio
import io
import json
import time
import wave
import aiohttp
import base64
from redbot.core import commands, Config
from typing import Optional
from discord.sinks import WaveSink, Sink  # Import necessary voice sinks components
from discord.ext.commands.errors import UserFeedbackCheckFailure  # Import for cleaner error handling

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
    A Redbot cog that integrates the Gemini API with voice channels for conversational AI,
    supporting both text-to-speech and speech-to-text interactions.
    """

    def __init__(self, bot):
        self.bot = bot
        # Configuration setup for storing API keys
        self.config = Config.get_conf(self, identifier=6084601438902573, force_registration=True)
        default_global = {"api_key": ""}
        self.config.register_global(**default_global)

        # Dictionary to hold voice clients: guild_id -> discord.VoiceClient
        self.voice_clients = {}
        # Dictionary to store ongoing recording states: guild_id -> {sink: WaveSink, task: asyncio.Task}
        self.recording_states = {}

        # --- API Helper Functions ---

    async def _get_api_key(self) -> Optional[str]:
        """Retrieves the API key from the Redbot config."""
        return await self.config.api_key()

    async def _make_api_call(self, url: str, payload: dict, is_audio_request: bool = False,
                             is_multimodal: bool = False) -> Optional[bytes]:
        """
        Handles API interaction with exponential backoff and error handling.
        Returns bytes for audio calls, or JSON response for text/multimodal calls.
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
                                part = candidate.get("content", {}).get("parts", [{}])[0]
                                audio_data = part.get("inlineData", {}).get("data")

                                if audio_data:
                                    # Use standard base64 decoding
                                    return base64.b64decode(audio_data)
                                else:
                                    # Added more specific error message for audio data extraction
                                    raise ValueError(
                                        "Failed to extract audio data from TTS API response. Check API response structure.")

                            else:  # Text or Multimodal response
                                text = candidate.get("content", {}).get("parts", [{}])[0].get("text")
                                return text

                        elif response.status == 429 or response.status >= 500:
                            # Rate limit or server error: retry with backoff
                            if attempt < max_retries - 1:
                                wait_time = 2 ** attempt
                                await asyncio.sleep(wait_time)
                            else:
                                response_text = await response.text()
                                raise UserFeedbackCheckFailure(
                                    f"API failed after {max_retries} attempts (Status: {response.status}). Response: {response_text[:100]}"
                                )
                        else:
                            # Other client/API errors
                            response_text = await response.text()
                            raise UserFeedbackCheckFailure(
                                f"Gemini API returned an error (Status: {response.status}): {response_text}"
                            )

                except aiohttp.ClientError as e:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        await asyncio.sleep(wait_time)
                    else:
                        raise UserFeedbackCheckFailure(f"Network error communicating with Gemini API: {e}")

                except Exception as e:
                    # Catch the custom ValueError/UserFeedbackCheckFailure from inside the try block
                    if isinstance(e, UserFeedbackCheckFailure) or isinstance(e, ValueError):
                        raise e
                    # Otherwise, re-raise as a general failure
                    raise UserFeedbackCheckFailure(f"An unexpected error occurred during API call: {e}")
        return None

    # --- STT & LLM Processing ---

    async def _stt_and_llm_call(self, ctx: commands.Context, audio_data: bytes, user_id: int) -> Optional[str]:
        """
        Transcribes audio data and gets an LLM text response using multimodal input.
        """

        # 1. Base64 encode the WAV data
        base64_audio = base64.b64encode(audio_data).decode('utf-8')

        await ctx.send(f"🎙️ Transcribing and thinking...")

        llm_payload = {
            "contents": [{
                "parts": [
                    {"inlineData": {
                        "mimeType": "audio/wav",  # Must specify the correct MIME type
                        "data": base64_audio
                    }},
                    {"text": "Transcribe this audio, then answer the question or command naturally. Be concise."}
                ]
            }],
            "tools": [{"google_search": {}}],
            "systemInstruction": {"parts": [{
                                                "text": "You are a helpful, cheerful, and concise voice assistant for a Discord bot. Respond directly to the user's query."}]}
        }

        text_url = f"{API_BASE}/{TEXT_MODEL}:generateContent"

        try:
            llm_text = await self._make_api_call(text_url, llm_payload, is_audio_request=False, is_multimodal=True)
            return llm_text
        except UserFeedbackCheckFailure as e:
            await ctx.send(f"Speech-to-Text Error: {e}")
            return None

    async def _play_response(self, ctx: commands.Context, prompt: str):
        """Wrapper to get TTS audio and play it in the voice channel."""
        vc = self.voice_clients.get(ctx.guild.id)

        if vc.is_playing():
            await ctx.send("I'm currently speaking! Please wait your turn.")
            return

        # 2. Get Audio Source (TTS)
        # Note: _tts_audio_source already sends the "Gemini says" message
        audio_source = await self._tts_audio_source(ctx, prompt)

        if not audio_source:
            return

        # 3. Play Audio
        vc.play(audio_source, after=lambda e: print(f'Player error: {e}') if e else None)

        # Wait for playback to finish
        while vc.is_playing():
            await asyncio.sleep(1)

        await ctx.send("Finished speaking.")

    # --- TTS Helper Function ---

    async def _tts_audio_source(self, ctx: commands.Context, prompt: str) -> Optional[discord.FFmpegPCMAudio]:
        """
        Synthesizes audio via TTS and prepares an FFmpegPCMAudio source for Discord.
        (Does NOT perform LLM generation, assumes prompt is the final text.)
        """

        # Send text to chat before initiating the slow TTS API call
        await ctx.send(f"🗣️ Gemini says: *{prompt}*")

        tts_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
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
        except UserFeedbackCheckFailure as e:
            await ctx.send(f"TTS Error: {e}")
            return None

        # 3. Prepare Audio Source
        # The WaveSink automatically creates WAV headers, but the raw API output is PCM.
        # We must use _pcm_to_wav utility to create the WAV header for FFmpeg.
        wav_io = _pcm_to_wav(pcm_bytes, TTS_SAMPLE_RATE)

        audio_source = discord.FFmpegPCMAudio(
            wav_io.read(),
            pipe=True,
            # Note: FFmpegPCMAudio can take a file-like object directly, but for
            # clarity and ensuring the pipe is correctly interpreted, we use options.
            options=f'-f {TTS_MIME_TYPE.split(";")[0].split("/")[1]} -i pipe:0',
            before_options="-nostdin -y"
        )
        return audio_source

    # --- SINK CALLBACK ---

    def _finished_recording(self, sink: Sink, guild_id: int):
        """Called by discord.py when recording is stopped."""
        self.bot.loop.create_task(self._process_recording_and_respond(sink, guild_id))

    async def _process_recording_and_respond(self, sink: Sink, guild_id: int):
        """Aggregates recorded audio and sends it to the Gemini API."""

        # Safely remove the state before processing to prevent race conditions
        state = self.recording_states.pop(guild_id, None)
        if not state:
            return

            # Create a mock context for sending messages/playing audio
        guild = self.bot.get_guild(guild_id)
        if not guild: return

        channel_id = state.get('channel_id')
        channel = guild.get_channel(channel_id) if channel_id else guild.text_channels[
            0] if guild.text_channels else None

        if not channel: return

        # Note: We must ensure the user object for the mock context is the user who spoke.
        # WaveSink stores audio data keyed by user ID. We use the first ID found as the "speaker" for the context.
        user_id = next(iter(sink.audio_data.keys()), None)
        user = guild.get_member(user_id) if user_id else self.bot.user

        class MockContext:
            def __init__(self, bot, guild, channel, author):
                self.bot = bot
                self.guild = guild
                self.channel = channel
                self.author = author
                self.prefix = 'v'
                self.command = None

            async def send(self, content, **kwargs):
                return await self.channel.send(content, **kwargs)

            async def reply(self, content, **kwargs):
                return await self.channel.send(content, **kwargs)

        mock_ctx = MockContext(self.bot, guild, channel, user)

        # --- Process Audio ---
        if not sink.audio_data:
            await mock_ctx.send(f"@{user.display_name}: Recording stopped, but no audio data was received.")
            return

        # Get audio data for the user who spoke
        audio_file = sink.audio_data.get(user_id)

        if not audio_file:
            await mock_ctx.send("Could not retrieve audio from recording.")
            return

        # Read the raw WAV file bytes
        audio_data = audio_file.file.getvalue()

        # 1. STT and LLM Call
        llm_text = await self._stt_and_llm_call(mock_ctx, audio_data, user_id)

        if llm_text:
            # 2. TTS and Playback
            await self._play_response(mock_ctx, llm_text)

    # --- Commands ---

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
        # Stop recording if active before leaving
        if guild_id in self.recording_states:
            # Use internal method for cleanup, but pass guild_id instead of ctx for safety
            # We call the stop recording method with force_guild_id=guild_id
            await self._stop_recording(ctx, force_guild_id=guild_id)

        if guild_id in self.voice_clients and self.voice_clients[guild_id].is_connected():
            await self.voice_clients[guild_id].disconnect()
            del self.voice_clients[guild_id]
            await ctx.send("Disconnected from voice channel.")
        else:
            await ctx.send("I am not currently in a voice channel.")

    @commands.command(name="vtalk")
    async def vtalk(self, ctx: commands.Context, *, prompt: str):
        """
        (LLM MODE) Sends a text prompt to Gemini, gets a text response, and streams
        the audio response to the voice channel.
        Usage: [p]vtalk What is the capital of France?
        """
        guild_id = ctx.guild.id

        if guild_id not in self.voice_clients or not self.voice_clients[guild_id].is_connected():
            return await ctx.send(f"I am not in a voice channel. Use `[p]vjoin` first.")

        # 1. LLM Call (Text Generation)
        await ctx.send(f"🤖 Thinking about your text request...")
        llm_payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "systemInstruction": {"parts": [{
                                                "text": "You are a helpful, cheerful, and concise voice assistant for a Discord bot. Respond directly to the user's query."}]}
        }

        text_url = f"{API_BASE}/{TEXT_MODEL}:generateContent"

        try:
            llm_text = await self._make_api_call(text_url, llm_payload, is_audio_request=False)
        except UserFeedbackCheckFailure as e:
            await ctx.send(f"LLM Error: {e}")
            return None

        if not llm_text:
            await ctx.send("The LLM did not generate a response.")
            return

        # 2. TTS and Playback
        await self._play_response(ctx, llm_text)

    @commands.command(name="vsay")
    async def vsay(self, ctx: commands.Context, *, text: str):
        """
        (TTS MODE) Converts raw text directly to speech in the voice channel
        without using the LLM.
        Usage: [p]vsay Hello World!
        """
        guild_id = ctx.guild.id

        if guild_id not in self.voice_clients or not self.voice_clients[guild_id].is_connected():
            return await ctx.send(f"I am not in a voice channel. Use `[p]vjoin` first.")

        vc = self.voice_clients.get(ctx.guild.id)

        if vc.is_playing():
            return await ctx.send("I'm currently speaking! Please wait your turn.")

        # 1. Get Audio Source (TTS)
        audio_source = await self._tts_audio_source(ctx, text)

        if not audio_source:
            return

        # 2. Play Audio
        vc.play(audio_source, after=lambda e: print(f'Player error: {e}') if e else None)

        # Wait for playback to finish
        while vc.is_playing():
            await asyncio.sleep(1)

        await ctx.send("Finished saying.")

    @commands.command(name="vrecord")
    async def vrecord(self, ctx: commands.Context):
        """
        Starts voice recording for STT/LLM interaction (max 10 seconds).
        Use [p]vstop to finish recording.
        """
        guild_id = ctx.guild.id

        vc = self.voice_clients.get(guild_id)
        if not vc:
            return await ctx.send(f"I am not in a voice channel. Use `[p]vjoin` first.")

        if vc.is_playing() or guild_id in self.recording_states:
            return await ctx.send(
                "I am already busy speaking or recording. Use `[p]vstop` to end the current recording.")

        # Use WaveSink to collect audio data for each user in WAV format
        sink = WaveSink()
        # Pass the callback function to be called when recording is stopped
        # FIX: Removed the incorrect third positional argument (ctx.channel.id) from start_recording
        vc.start_recording(sink, lambda s: self._finished_recording(s, guild_id))

        # Store state and start a timeout task (10 seconds max recording)
        timeout_task = self.bot.loop.create_task(self._recording_timeout(ctx, 10))

        self.recording_states[guild_id] = {
            'sink': sink,
            'timeout_task': timeout_task,
            'channel_id': ctx.channel.id  # Save the text channel to respond to
        }

        await ctx.send("🔴 **Recording started!** Speak now (max 10 seconds). Use `[p]vstop` to end early.")

    async def _recording_timeout(self, ctx: commands.Context, duration: int):
        """Stops recording after a set duration."""
        await asyncio.sleep(duration)
        if ctx.guild.id in self.recording_states:
            # Pass ctx to _stop_recording for context (sends the stop message)
            await self._stop_recording(ctx, timeout=True)

    async def _stop_recording(self, ctx: commands.Context, timeout=False, force_guild_id: Optional[int] = None):
        """Stops the recording process and triggers audio processing."""
        # Use provided guild_id for cog_unload/vleave, otherwise use context's guild_id
        guild_id = force_guild_id if force_guild_id is not None else ctx.guild.id

        if guild_id not in self.recording_states:
            if not timeout and force_guild_id is None:  # Only send message if user manually triggered stop and it wasn't active
                return await ctx.send("No active recording to stop.")
            return  # Silent exit on timeout/vleave if state is already cleared

        # We MUST retrieve the state before potentially clearing it, as the
        # _finished_recording callback (which clears state) might run concurrently
        state = self.recording_states.get(guild_id)

        if state and 'timeout_task' in state:
            # Cancel the timeout task regardless of how the recording is stopped
            state['timeout_task'].cancel()

        try:
            vc = self.voice_clients.get(guild_id)
            if vc:
                # Stop recording, which triggers the _finished_recording callback
                vc.stop_recording()

            if not timeout and force_guild_id is None:
                await ctx.send("⏹️ Recording stopped. Processing your request...")

        except KeyError:
            # Voice client not found, but state was present. Clean up state just in case.
            pass
        finally:
            # Ensure state is cleaned up after the recording has been told to stop
            # Note: _process_recording_and_respond will also pop the state, this is a safety measure
            if guild_id in self.recording_states:
                del self.recording_states[guild_id]

    @commands.command(name="vstop")
    async def vstop(self, ctx: commands.Context):
        """Stops the current voice recording."""
        await self._stop_recording(ctx)

    def cog_unload(self):
        """Ensures all voice clients are disconnected and recordings are stopped."""
        for vc in self.voice_clients.values():
            if vc.is_connected():
                self.bot.loop.create_task(vc.disconnect())

        # Cancel all active recording timeout tasks
        for state in self.recording_states.values():
            if 'timeout_task' in state:
                state['timeout_task'].cancel()
        self.recording_states.clear()
