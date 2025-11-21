import discord
from redbot.core import commands, Config, app_commands
from redbot.core.bot import Red
from typing import Literal, Optional, List, Dict, Any
import os
import io
import asyncio
# Import the google-genai SDK
from google import genai
from google.genai import types


# --- MOCK VOICE SINK IMPLEMENTATION ---
# This structure is needed to satisfy the discord.VoiceClient.start_recording signature,
# but the actual byte capture logic is mocked.
class FakeWaveSink:
    """
    A placeholder for discord.sinks.WaveSink.

    In a real implementation, this class would handle writing raw PCM
    data from all speaking users into an in-memory WAV stream compatible
    with the Gemini API. For this example, we mock the required structure
    and return a small set of mock audio bytes.
    """
    encoding = "wav"

    def __init__(self):
        # In a real implementation, this would collect audio data bytes
        self.audio_data = {}  # {user_id: io.BytesIO(wav_data)}

    @property
    def buffer(self):
        # Mocking 10KB of raw bytes.
        # For actual usage, this should contain valid, raw audio data.
        return io.BytesIO(
            b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00\x00' * 10)

    def cleanup(self):
        self.audio_data.clear()


# Global dictionary to track active recordings in guilds
active_recordings: Dict[int, discord.VoiceClient] = {}


# Utility function to initialize the Gemini client
def get_gemini_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


# --- COG IMPLEMENTATION ---

class VoiceGemini(commands.Cog):
    """
    A cog for interacting with the Gemini API using voice commands (voice-to-text-to-Gemini).

    The command structure is implemented, but reliable voice capture requires a
    fully compliant discord.py v2+ Voice Sink implementation.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Use a unique identifier for RedBot configuration
        self.config = Config.get_conf(self, identifier=678912345, force_registration=True)
        self.config.register_global(
            api_key="",
        )
        # Store active VoiceClients
        self.voice_clients: Dict[int, discord.VoiceClient] = {}

    async def _get_api_key(self) -> str:
        return await self.config.api_key()

    # --- Internal Voice and API Logic ---

    async def _once_done_callback(self, sink: FakeWaveSink, channel: discord.TextChannel, *args):
        """
        Callback function executed when recording stops.
        This handles disconnecting, cleaning up, and calling the Gemini API.
        """
        guild_id = channel.guild.id

        # Cleanup recording state
        if guild_id in active_recordings:
            del active_recordings[guild_id]

        # Disconnect the voice client
        vc: discord.VoiceClient = self.voice_clients.get(guild_id)
        if vc:
            await vc.disconnect()
            del self.voice_clients[guild_id]

        # Read the mock audio data
        audio_buffer = sink.buffer.read()

        if len(audio_buffer) < 50:  # Check if buffer size is too small (mock check)
            return await channel.send(
                "❌ **Recording failed:** No substantial audio data was captured. "
                "The bot's internal voice capture mechanism may need a proper Sink."
            )

        await channel.send("✅ Recording finished! Processing audio with Gemini...")

        # Perform the heavy Gemini processing
        await self._process_audio(audio_buffer, channel)

    async def _process_audio(self, audio_bytes: bytes, channel: discord.TextChannel):
        """Sends the audio data to the Gemini API for transcription and response."""
        api_key = await self._get_api_key()
        if not api_key:
            return await channel.send(
                "❌ Gemini API key is not set. Please set it using `[p]vg setkey <key>` or Red's `[p]set api`."
            )

        # The API call is synchronous, so we run it in a separate thread to prevent blocking
        def api_call():
            client = get_gemini_client(api_key)
            prompt = "Transcribe the audio recording, identify the user's core intent or question, and provide a helpful, concise answer based on that intent."

            # The mime_type must match the format of the bytes in the sink.buffer.
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    prompt,
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type='audio/wav',  # Assuming the sink produced WAV data
                    )
                ]
            )
            return response.text

        try:
            # Use asyncio.to_thread for a non-blocking synchronous API call
            gemini_response = await asyncio.to_thread(api_call)

            embed = discord.Embed(
                title="🗣️ Voice Gemini Analysis Complete",
                description=gemini_response,
                color=discord.Color.blue()
            )
            await channel.send(embed=embed)

        except Exception as e:
            await channel.send(f"❌ An error occurred during Gemini API processing: `{type(e).__name__}: {e}`")

    # --- Commands ---

    @commands.group(name="voicegemini", aliases=["vg"])
    async def voicegemini(self, ctx: commands.Context):
        """Commands for using Gemini via voice input."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @voicegemini.command(name="setkey", hidden=True)
    @commands.is_owner()
    async def vg_setkey(self, ctx: commands.Context, key: str):
        """Set your Gemini API key (Owner only)."""
        await self.config.api_key.set(key)
        await ctx.send(
            "✅ Gemini API key successfully stored. Remember to use Red's official `[p]set api` command for permanent configuration.")

    @voicegemini.command(name="join")
    async def vg_join(self, ctx: commands.Context):
        """Connects the bot to your current voice channel."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ You must be in a voice channel to use this command.")

        channel = ctx.author.voice.channel

        if ctx.voice_client:
            if ctx.voice_client.channel == channel:
                return await ctx.send("I'm already here!")
            try:
                await ctx.voice_client.move_to(channel)
                return await ctx.send(f"Moved to **{channel.name}**.")
            except asyncio.TimeoutError:
                return await ctx.send("❌ Could not move to the voice channel.")

        try:
            vc = await channel.connect()
            self.voice_clients[ctx.guild.id] = vc
            await ctx.send(f"Joined **{channel.name}**. Use `{ctx.prefix}vg listen` to start listening.")
        except Exception as e:
            # Catch exceptions like discord.errors.ClientException (if already connected elsewhere)
            await ctx.send(f"❌ An error occurred while joining: {e}")

    @voicegemini.command(name="listen", aliases=["start"])
    async def vg_listen(self, ctx: commands.Context):
        """Starts recording audio in the voice channel."""
        vc = ctx.voice_client
        if not vc:
            return await ctx.send(f"❌ I am not in a voice channel. Use `{ctx.prefix}vg join` first.")

        # --- FIX: Check for voice recording support ---
        if not hasattr(vc, 'start_recording'):
            return await ctx.send(
                "❌ **Voice Error:** This bot's Discord library version does not support `start_recording` for voice receiving. "
                "Ensure your bot is using a compatible `discord.py` version (v2+) and that the "
                "**voice** extension is correctly installed (e.g., you might need to run `pip install discord.py[voice]` or use a Pycord-based environment)."
            )
        # --------------------------------------------

        if ctx.guild.id in active_recordings:
            return await ctx.send("❌ Recording is already in progress.")

        # Instantiate the sink which collects the audio data
        sink = FakeWaveSink()

        # Start recording, passing the sink and the callback function
        vc.start_recording(
            sink,
            self._once_done_callback,
            ctx.channel  # Pass the text channel to the callback
        )

        active_recordings[ctx.guild.id] = vc
        await ctx.send(
            "🔴 **Recording started!** I'm listening to all speakers. "
            f"Use `{ctx.prefix}vg stop` to finish the recording and process with Gemini."
        )

    @voicegemini.command(name="stop")
    async def vg_stop(self, ctx: commands.Context):
        """Stops the audio recording and sends it to Gemini for analysis."""
        vc = ctx.voice_client
        if not vc:
            return await ctx.send("❌ I am not in a voice channel.")

        if ctx.guild.id not in active_recordings:
            return await ctx.send("❌ No recording is currently active. Use `vg listen` to start one.")

        # Stop recording, which non-blockingly triggers the `_once_done_callback`
        vc.stop_recording()

        # The callback handles the cleanup and Gemini processing.
        await ctx.send("⏹️ Stopping recording. Please wait for the analysis...")

    @voicegemini.command(name="leave", aliases=["disconnect"])
    async def vg_leave(self, ctx: commands.Context):
        """Disconnects the bot from the voice channel."""
        if ctx.guild.id in active_recordings:
            ctx.voice_client.stop_recording()  # Ensure recording is stopped first
            del active_recordings[ctx.guild.id]

        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            if ctx.guild.id in self.voice_clients:
                del self.voice_clients[ctx.guild.id]
            await ctx.send("👋 Disconnected from voice channel.")
        else:
            await ctx.send("❌ I'm not in a voice channel.")

    @voicegemini.command(name="test")
    async def vg_test(self, ctx: commands.Context):
        """Tests if the voicegemini cog is loaded and the API key is set."""
        key = await self._get_api_key()
        status = "✅ API Key Set" if key else "❌ API Key NOT Set (use `[p]vg setkey` or `[p]set api`)"
        await ctx.send(
            "VoiceGemini Cog Status:\n"
            f"- Load Status: Loaded\n"
            f"- API Status: {status}\n"
            f"- Voice Status: Recording functionality is using a mock-up and may not capture actual audio."
        )


# Required setup function for RedBot
async def setup(bot: Red):
    await bot.add_cog(VoiceGemini(bot))
