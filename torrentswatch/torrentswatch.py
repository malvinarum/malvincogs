import discord
import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
from redbot.core import commands, Config, app_commands, checks
from redbot.core.utils.chat_formatting import box
from discord.ext import tasks

log = logging.getLogger("red.torrentswatch")

DEFAULT_GUILD_SETTINGS = {
    "sonarr_url": None,
    "sonarr_key": None,
    "radarr_url": None,
    "radarr_key": None,
    "channel_id": None,
    "message_id": None,
    "update_interval": 60,
    "enabled": False
}


class TorrentsWatch(commands.Cog):
    """
    A cog to monitor Sonarr/Radarr download queues in a static embed.
    Now with ETAs, Global Speed, and Status Icons!
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.session = aiohttp.ClientSession()
        self._watch_loop_task = None

    async def cog_load(self):
        log.info("TorrentsWatch cog loaded. Starting loop.")
        self._watch_loop_task = self.watch_loop.start()

    async def cog_unload(self):
        log.info("TorrentsWatch cog unloaded. Stopping loop.")
        if self._watch_loop_task:
            self.watch_loop.cancel()
        if self.session:
            await self.session.close()

    def _generate_progress_bar(self, percent: float, length: int = 10) -> str:
        percent = min(1.0, max(0.0, percent))
        filled = int(length * percent)
        return "▓" * filled + "░" * (length - filled)

    def _format_size(self, size_bytes: float) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / 1024 ** 2:.1f} MB"
        else:
            return f"{size_bytes / 1024 ** 3:.2f} GB"

    async def _fetch_queue(self, url: str, key: str, app_type: str):
        if not url or not key: return []
        if not url.startswith("http"): url = f"http://{url}"
        if not url.endswith("/"): url += "/"

        endpoint = f"{url}api/v3/queue"
        params = {"apikey": key}

        try:
            async with self.session.get(endpoint, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    records = data.get("records", [])
                    for r in records: r["source"] = app_type
                    return records
                return []
        except Exception:
            return []

    async def _fetch_history(self, url: str, key: str, app_type: str):
        if not url or not key: return []
        if not url.startswith("http"): url = f"http://{url}"
        if not url.endswith("/"): url += "/"

        endpoint = f"{url}api/v3/history"
        params = {"apikey": key, "page": 1, "pageSize": 5, "sortKey": "date", "sortDir": "desc", "eventType": "grabbed"}

        try:
            async with self.session.get(endpoint, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    records = data.get("records", [])
                    for r in records: r["source"] = app_type
                    return records
        except Exception:
            return []
        return []

    async def _build_embed(self, queue_items: list, history_items: list) -> discord.Embed:
        if not queue_items and not history_items:
            return discord.Embed(
                title="📥 Torrents Watch",
                description="😴 System idle. No active downloads.",
                color=discord.Color.dark_grey(),
                timestamp=datetime.now()
            )

        embed = discord.Embed(title="📥 Download Queue", color=discord.Color.blue())

        # --- AGGREGATE STATS ---
        # Calculate total speed if available in the records?
        # Sonarr Queue records have 'timeleft' but rarely explicit 'speed'.
        # However, sometimes 'timeleft' format is "00:05:00".

        if queue_items:
            queue_items.sort(key=lambda x: x.get("sizeleft", x.get("sizeLeft", 0)))

            field_val = ""
            for item in queue_items[:8]:
                title = item.get("title", "Unknown")
                size = item.get("size", item.get("Size", 1))
                size_left = item.get("sizeleft", item.get("sizeLeft", 0))
                status = item.get("status", "Unknown")
                tracked_status = item.get("trackedDownloadStatus", "")
                source = item.get("source", "?")
                time_left = item.get("timeleft", "00:00:00")

                # Status Logic & Emojis
                state_emoji = "⏬"
                status_text = status

                if status.lower() == "warning" or tracked_status.lower() == "warning":
                    state_emoji = "⚠️"
                    msgs = item.get("statusMessages", []) or item.get("messages", [])
                    if msgs and len(msgs) > 0:
                        status_text = msgs[0].get("title", "Warning")
                    else:
                        status_text = "Warning (Check Client)"
                elif status.lower() == "paused":
                    state_emoji = "⏸️"
                elif status.lower() == "queued":
                    state_emoji = "⏳"

                # Clean Title
                if len(title) > 35: title = title[:33] + "..."

                # Progress Math
                percent = 1.0 - (size_left / size) if size > 0 else 0.0
                bar = self._generate_progress_bar(percent, length=10)

                # Source Icon
                src_emoji = "📺" if source == "Sonarr" else "🎬"

                # Format ETA string
                eta_str = ""
                if time_left and time_left != "00:00:00":
                    # If it looks like a time duration
                    eta_str = f" • ETA: {time_left}"

                field_val += (
                    f"{src_emoji} **{title}**\n"
                    f"`{bar}` {int(percent * 100)}% • {self._format_size(size_left)} left\n"
                    f"{state_emoji} **{status_text}**{eta_str}\n"
                )

            if len(queue_items) > 8:
                field_val += f"\n*...and {len(queue_items) - 8} more items.*"

            embed.add_field(name="Active Downloads", value=field_val, inline=False)
        else:
            embed.add_field(name="Active Downloads", value="*Queue is empty.*", inline=False)

        if history_items:
            history_items.sort(key=lambda x: x.get("date", ""), reverse=True)
            hist_val = ""
            for item in history_items[:5]:
                event = item.get("eventType", "Unknown")

                title = "Unknown"
                if "movie" in item:
                    title = item["movie"]["title"]
                elif "series" in item:
                    title = item["series"]["title"]
                elif "sourceTitle" in item:
                    title = item["sourceTitle"]

                if len(title) > 40: title = title[:38] + "..."

                dt_str = item.get("date", "")
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                    time_str = f"<t:{ts}:R>"
                except:
                    time_str = ""

                # Event Emojis
                ev_emoji = "ℹ️"
                if event == "grabbed":
                    ev_emoji = "🛒"  # Shopping cart for grabbed
                elif event == "downloadFolderImported":
                    ev_emoji = "✅"
                elif event == "downloadFailed":
                    ev_emoji = "❌"

                hist_val += f"{ev_emoji} **{title}** ({event})\n{time_str}\n"

            embed.add_field(name="Recent History", value=hist_val, inline=False)

        embed.set_footer(text=f"TorrentsWatch • Last Updated: {datetime.now().strftime('%H:%M:%S')}")
        return embed

    @tasks.loop(seconds=60)
    async def watch_loop(self):
        await self.bot.wait_until_ready()
        all_guilds = await self.config.all_guilds()
        for guild_id, settings in all_guilds.items():
            if not settings["enabled"]: continue

            channel_id = settings["channel_id"]
            if not channel_id: continue
            channel = self.bot.get_channel(channel_id)
            if not channel: continue

            sonarr_q = await self._fetch_queue(settings["sonarr_url"], settings["sonarr_key"], "Sonarr")
            radarr_q = await self._fetch_queue(settings["radarr_url"], settings["radarr_key"], "Radarr")
            sonarr_h = await self._fetch_history(settings["sonarr_url"], settings["sonarr_key"], "Sonarr")
            radarr_h = await self._fetch_history(settings["radarr_url"], settings["radarr_key"], "Radarr")

            combined_q = sonarr_q + radarr_q
            combined_h = sonarr_h + radarr_h

            embed = await self._build_embed(combined_q, combined_h)

            message_id = settings["message_id"]
            if message_id:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    msg = await channel.send(embed=embed)
                    await self.config.guild(discord.Object(id=guild_id)).message_id.set(msg.id)
                except Exception:
                    pass
            else:
                msg = await channel.send(embed=embed)
                await self.config.guild(discord.Object(id=guild_id)).message_id.set(msg.id)

    @commands.group(name="torrentswatch", aliases=["tw"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def torrentswatch(self, ctx: commands.Context):
        """Manage the TorrentsWatch dashboard."""
        pass

    @torrentswatch.command(name="setup")
    async def tw_setup(self, ctx: commands.Context):
        """Interactive setup."""
        await ctx.send("Enter Sonarr URL or 'skip':")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            if msg.content.lower() != 'skip':
                await self.config.guild(ctx.guild).sonarr_url.set(msg.content.strip())
                await ctx.send("Enter Sonarr API Key:")
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
                await self.config.guild(ctx.guild).sonarr_key.set(msg.content.strip())
        except asyncio.TimeoutError:
            return await ctx.send("Timed out.")

        await ctx.send("Enter Radarr URL or 'skip':")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            if msg.content.lower() != 'skip':
                await self.config.guild(ctx.guild).radarr_url.set(msg.content.strip())
                await ctx.send("Enter Radarr API Key:")
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
                await self.config.guild(ctx.guild).radarr_key.set(msg.content.strip())
        except asyncio.TimeoutError:
            return await ctx.send("Timed out.")
        await ctx.send("Configuration saved.")

    @torrentswatch.command(name="setchannel")
    async def tw_setchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the dashboard channel."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(None)
        await ctx.send(f"Dashboard will appear in {channel.mention}.")

    @torrentswatch.command(name="toggle")
    async def tw_toggle(self, ctx: commands.Context):
        """Enable/Disable the loop."""
        curr = await self.config.guild(ctx.guild).enabled()
        new = not curr
        await self.config.guild(ctx.guild).enabled.set(new)
        await ctx.send(f"TorrentsWatch is now {'Enabled' if new else 'Disabled'}.")


async def setup(bot):
    await bot.add_cog(TorrentsWatch(bot))