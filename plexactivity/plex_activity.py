import discord
import aiohttp
import asyncio
import logging
from datetime import datetime
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

    def _format_speed(self, speed_bytes: float) -> str:
        if speed_bytes < 1024:
            return f"{speed_bytes:.0f} B/s"
        elif speed_bytes < 1024 ** 2:
            return f"{speed_bytes / 1024:.1f} KB/s"
        else:
            return f"{speed_bytes / (1024 ** 2):.1f} MB/s"

    async def _fetch_queue(self, url: str, key: str, app_type: str):
        if not url or not key: return []

        # Ensure URL has protocol
        if not url.startswith("http"): url = f"http://{url}"
        if not url.endswith("/"): url += "/"

        endpoint = f"{url}api/v3/queue"
        params = {"apikey": key}

        try:
            async with self.session.get(endpoint, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    records = data.get("records", [])
                    # Tag them so we know source
                    for r in records: r["source"] = app_type
                    return records
                else:
                    log.warning(f"Failed to fetch {app_type} queue: {resp.status}")
                    return []
        except Exception as e:
            log.error(f"Error fetching {app_type} queue: {e}")
            return []

    async def _fetch_history(self, url: str, key: str, app_type: str):
        if not url or not key: return []
        if not url.startswith("http"): url = f"http://{url}"
        if not url.endswith("/"): url += "/"

        endpoint = f"{url}api/v3/history"
        params = {"apikey": key, "page": 1, "pageSize": 5, "sortKey": "date", "sortDir": "desc", "eventType": "grabbed"}
        # eventType 1 = Grabbed, 3 = Import? API docs vary, usually we want 'grabbed' or 'downloadFolderImported'
        # Let's try getting general history and filtering or just showing the last few actions

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
                description="😴 No active downloads or recent history.",
                color=discord.Color.dark_grey(),
                timestamp=datetime.now()
            )

        total_speed = sum(item.get("trackedDownloadStatus", "Ok") == "Ok" and
                          item.get("trackedDownloadState", "") == "Downloading" and
                          # Note: Sonarr v3 queue doesn't always give speed directly in top level,
                          # but let's look for it or estimate. Actually, Queue item has 'sizeleft' and time...
                          # For simplicity, Sonarr Queue endpoint often lacks direct 'current speed' per item
                          # unless we query the download client.
                          # We will display "Size Remaining" instead if speed isn't handy.
                          0 for item in queue_items)

        # Wait, Sonarr/Radarr Queue object usually has: title, size, sizeleft, status, trackedDownloadState
        # Speed is often on the DownloadClient, not the Queue item itself easily.
        # We will focus on Progress %.

        embed = discord.Embed(title="📥 Download Queue", color=discord.Color.blue())

        # --- QUEUE SECTION ---
        if queue_items:
            # Sort by time left or progress? Let's do progress.
            queue_items.sort(key=lambda x: x.get("sizeleft", 0))

            field_val = ""
            for item in queue_items[:8]:  # Limit to 8 to prevent overflow
                title = item.get("title", "Unknown")
                size = item.get("size", 1)
                size_left = item.get("sizeleft", 0)
                status = item.get("status", "Unknown")
                source = item.get("source", "?")

                # Clean title
                if len(title) > 40: title = title[:38] + "..."

                percent = 1.0 - (size_left / size) if size > 0 else 0.0
                bar = self._generate_progress_bar(percent, length=12)

                emoji = "📺" if source == "Sonarr" else "🎬"

                field_val += f"{emoji} **{title}**\n`{bar}` {int(percent * 100)}% • {status}\n"

            if len(queue_items) > 8:
                field_val += f"...and {len(queue_items) - 8} more."

            embed.add_field(name="Active Downloads", value=field_val, inline=False)
        else:
            embed.add_field(name="Active Downloads", value="*Queue is empty.*", inline=False)

        # --- HISTORY SECTION ---
        # Combine and sort active history by date
        if history_items:
            history_items.sort(key=lambda x: x.get("date", ""), reverse=True)

            hist_val = ""
            for item in history_items[:5]:
                source = item.get("source", "?")
                event = item.get("eventType", "Unknown")

                # Source Title is usually in sourceTitle or just use the series/movie title
                # Radarr: movie -> title
                # Sonarr: series -> title
                title = "Unknown"
                if "movie" in item:
                    title = item["movie"]["title"]
                elif "series" in item:
                    title = item["series"]["title"]
                elif "sourceTitle" in item:
                    title = item["sourceTitle"]

                if len(title) > 45: title = title[:43] + "..."

                # Timestamp
                dt_str = item.get("date", "")
                # Try to parse iso format
                try:
                    # 2025-05-07T14:22:13.123Z
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    ts = int(dt.timestamp())
                    time_str = f"<t:{ts}:R>"
                except:
                    time_str = ""

                emoji = "🟢" if event == "grabbed" else "📂" if event == "downloadFolderImported" else "ℹ️"

                hist_val += f"{emoji} **{title}** ({event})\n{time_str}\n"

            embed.add_field(name="Recent Activity", value=hist_val, inline=False)

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

            # Fetch Data
            sonarr_q = await self._fetch_queue(settings["sonarr_url"], settings["sonarr_key"], "Sonarr")
            radarr_q = await self._fetch_queue(settings["radarr_url"], settings["radarr_key"], "Radarr")

            # Ideally we'd fetch history too, but let's keep it simple for now or add it if you want
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
        """Interactive setup for Sonarr/Radarr."""
        await ctx.send("Enter Sonarr URL (e.g. http://192.168.1.50:8989) or 'skip':")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            if msg.content.lower() != 'skip':
                await self.config.guild(ctx.guild).sonarr_url.set(msg.content.strip())
                await ctx.send("Enter Sonarr API Key:")
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
                await self.config.guild(ctx.guild).sonarr_key.set(msg.content.strip())
        except asyncio.TimeoutError:
            return await ctx.send("Timed out.")

        await ctx.send("Enter Radarr URL (e.g. http://192.168.1.50:7878) or 'skip':")
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
        await self.config.guild(ctx.guild).message_id.set(None)  # Reset msg to post new one
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