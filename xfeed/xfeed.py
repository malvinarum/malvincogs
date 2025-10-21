import discord
from discord.ext import commands
import aiohttp
import asyncio
import io
import struct
import math
import base64
from typing import Optional, Union


# --- Utility Functions for Audio Conversion ---

# The Gemini TTS API returns signed 16-bit PCM data (L16) at a sample rate of 24000 Hz.
# Discord's voice client requires this audio data to be wrapped in a WAV container to stream correctly.

def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, bit_depth: int = 16) -> io.BytesIO:
    """
    Converts raw PCM audio data into a complete WAV file structure in memory.
    """
    if not pcm_data:
        raise ValueError("PCM data is empty.")

    # WAV header constants
    num_channels = 1
    byte_rate = sample_rate * num_channels * (bit_depth // 8)
    block_align = num_channels * (bit_depth // 8)
    data_size = len(pcm_data)
    file_size = 36 + data_size

    wav_buffer = io.BytesIO()

    # RIFF header
    wav_buffer.write(b'RIFF')
    wav_buffer.write(struct.pack('<I', file_size))
    wav_buffer.write(b'WAVE')

    # fmt sub-chunk
    wav_buffer.write(b'fmt ')
    wav_buffer.write(struct.pack('<I', 16))  # Subchunk size (16 for PCM)
    wav_buffer.write(struct.pack('<H', 1))  # Audio Format (1 for PCM)
    wav_buffer.write(struct.pack('<H', num_channels))
    wav_buffer.write(struct.pack('<I', sample_rate))
    wav_buffer.write(struct.pack('<I', byte_rate))
    wav_buffer.write(struct.pack('<H', block_align))
    wav_buffer.write(struct.pack('<H', bit_depth))

    # data sub-chunk
    wav_buffer.write(b'data')
    wav_buffer.write(struct.pack('<I', data_size))
    wav_buffer.write(pcm_data)

    wav_buffer.seek(0)
    return wav_buffer


# --- Gemini Voice Cog Class ---

class VoiceGemini(commands.Cog):
    """
    A Redbot cog that uses the Gemini API for text-to-speech in voice channels,
    now incorporating the Gemini LLM for generating conversational responses first.
    """

    LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    TTS_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent"

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        """Clean up resources on cog unload."""
        asyncio.create_task(self.session.close())

    async def _get_api_key(self, ctx: commands.Context) -> Optional[str]:
        """Retrieve the Gemini API key securely from Redbot's config."""
        settings = await self.bot.get_shared_api_tokens("google")
        api_key = settings.get("api_key")
        if not api_key:
            await ctx.send(
                "❌ **Gemini API Key Missing:** Please set your Google AI API key using "
                f"`{ctx.prefix}set api google api_key <your_key_here>`."
            )
            return None
        return api_key

    # --- API Helper for LLM and TTS ---
    async def _make_api_call(self, url: str, api_key: str, payload: dict, status_msg: discord.Message, step_name: str,
                             max_retries: int = 3) -> tuple[Optional[Union[str, bytes]], Optional[str]]:
        """Handles API calls with exponential backoff and error reporting."""

        headers = {'Content-Type': 'application/json'}

        for attempt in range(max_retries):
            try:
                async with self.session.post(
                        f"{url}?key={api_key}",
                        json=payload,
                        headers=headers
                ) as response:

                    if response.status != 200:
                        await status_msg.edit(
                            content=f"❌ **{step_name} API Error:** Failed with status code {response.status}. Attempt {attempt + 1}/{max_retries}.")
                        response_text = await response.text()
                        print(f"{step_name} API Error Response: {response_text}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(math.pow(2, attempt))
                        continue

                    data = await response.json()
                    candidate = data.get('candidates', [{}])[0]

                    # LLM Text Extraction
                    if url == self.LLM_API_URL:
                        text = candidate.get('content', {}).get('parts', [{}])[0].get('text')
                        if text:
                            return text, None

                    # TTS Audio Extraction (base64 -> bytes)
                    elif url == self.TTS_API_URL:
                        part = candidate.get('content', {}).get('parts', [{}])[0]
                        audio_data_b64 = part.get('inlineData', {}).get('data')
                        if audio_data_b64:
                            return base64.b64decode(audio_data_b64), None
                        else:
                            error_message = f"❌ **{step_name} Error:** No content returned from the API."
                            return None, error_message


            except aiohttp.ClientError as e:
                error_message = f"❌ **{step_name} Connection Error:** An issue occurred connecting to the API. Error: {e}"
                if attempt < max_retries - 1:
                    await asyncio.sleep(math.pow(2, attempt))
                continue
            except Exception as e:
                error_message = f"❌ **{step_name} Processing Error:** An unexpected error occurred: {e}"
                return None, error_message

        # If all attempts fail
        return None, f"❌ Failed to complete {step_name} after multiple attempts."

    # --- Discord Voice Commands ---

    @commands.command()
    async def join(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel]):
        """
        Tells the bot to join a voice channel. If no channel is specified,
        it joins the voice channel you are currently in.
        """
        if not ctx.author.voice:
            return await ctx.send("You must be in a voice channel to use this command or specify one.")

        target_channel = channel or ctx.author.voice.channel

        if ctx.voice_client:
            if ctx.voice_client.channel == target_channel:
                return await ctx.send(f"I am already in **{target_channel.name}**.")

            await ctx.voice_client.move_to(target_channel)
        else:
            await target_channel.connect()

        await ctx.send(f"Connected to **{target_channel.name}**! Use `{ctx.prefix}speak` to make me talk.")

    @commands.command()
    async def leave(self, ctx: commands.Context):
        """
        Tells the bot to leave the voice channel it is currently in.
        """
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("Disconnected from voice channel.")
        else:
            await ctx.send("I'm not currently in a voice channel.")

    # --- Gemini Speak Command (Core Logic) ---

    @commands.command()
    @commands.cooldown(1, 7, commands.BucketType.user)
    async def speak(self, ctx: commands.Context, *, text_prompt: str):
        """
        Generates a conversational response using Gemini, then speaks it in the
        voice channel via the dedicated TTS service.
        """
        if not ctx.voice_client or not ctx.voice_client.is_connected():
            return await ctx.send(
                f"I need to be in a voice channel first. Use `{ctx.prefix}join` to invite me."
            )

        if ctx.voice_client.is_playing():
            return await ctx.send("Please wait for the current audio to finish before speaking again.")

        api_key = await self._get_api_key(ctx)
        if not api_key:
            return

        status_msg = await ctx.send("🧠 Generating conversational response...")

        # --- STEP 1: Generate Text Response using LLM (Gemini-2.5-Flash) ---
        llm_payload = {
            "contents": [{
                "parts": [
                    {"text": f"Answer the user's question concisely and conversationally. User query: {text_prompt}"}]
            }],
            "tools": [{"google_search": {}}]  # Grounding for up-to-date answers
        }

        # Note: We expect 'str' from the LLM call
        llm_response_text, error = await self._make_api_call(
            self.LLM_API_URL,
            api_key,
            llm_payload,
            status_msg,
            "LLM Generation"
        )

        if error:
            # The helper already edited the status_msg with the error
            return

            # Ensure we treat it as a string response
        llm_response_text = str(llm_response_text)

        if not llm_response_text:
            return await status_msg.edit(content="❌ LLM failed to generate a response.")

        await status_msg.edit(
            content=f"📝 **Response Generated:** \"{llm_response_text[:100]}...\" \n🔊 Converting to speech...")

        # --- STEP 2: Convert Text Response to Speech (TTS) ---
        tts_payload = {
            "contents": [{"parts": [{"text": llm_response_text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": "Kore"}
                    }
                }
            }
        }

        # Note: We expect 'bytes' from the TTS call
        pcm_data, error = await self._make_api_call(
            self.TTS_API_URL,
            api_key,
            tts_payload,
            status_msg,
            "TTS Conversion"
        )

        if error:
            # The helper already edited the status_msg with the error
            return

        if not pcm_data or not isinstance(pcm_data, bytes):
            # pcm_data should be bytes here
            return await status_msg.edit(content="❌ TTS failed to generate audio data.")

        # --- STEP 3: Convert PCM data to streamable WAV format and play ---

        # 3. Convert PCM data to streamable WAV format
        try:
            wav_stream = _pcm_to_wav(pcm_data)
        except ValueError as e:
            await status_msg.edit(content=f"❌ **Audio Conversion Error:** {e}")
            return

        # 4. Play the audio stream in the voice channel
        try:
            # We use FFmpegPCMAudio from a stream source
            audio_source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(wav_stream, pipe=True)
            )

            ctx.voice_client.play(audio_source, after=lambda e: print(f'Player error: {e}') if e else None)

            await status_msg.edit(content=f"🗣️ Speaking response: \"{llm_response_text[:100]}...\"")

            # Wait until playback is finished before clearing the status
            while ctx.voice_client.is_playing():
                await asyncio.sleep(1)

            # Clean up message (optional)
            await status_msg.delete()

        except discord.ClientException as e:
            await status_msg.edit(content=f"❌ **Discord Client Error:** {e}. (Is FFmpeg installed and accessible?)")
        except Exception as e:
            await status_msg.edit(content=f"❌ **Playback Error:** An error occurred during audio playback: {e}")


def setup(bot):
    """Redbot setup function."""
    bot.add_cog(VoiceGemini(bot))
