import discord
import logging
import asyncio
import docker
import functools
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.malvincogs.dockermanager")


# --- UTILITIES ---

def calculate_cpu_percent(d):
    """Calculate CPU % from Docker stats object."""
    try:
        cpu_count = len(d["cpu_stats"]["cpu_usage"]["percpu_usage"])
        cpu_percent = 0.0
        cpu_delta = float(d["cpu_stats"]["cpu_usage"]["total_usage"]) - \
                    float(d["precpu_stats"]["cpu_usage"]["total_usage"])
        system_delta = float(d["cpu_stats"]["system_cpu_usage"]) - \
                       float(d["precpu_stats"]["system_cpu_usage"])
        if system_delta > 0.0:
            cpu_percent = cpu_delta / system_delta * 100.0 * cpu_count
        return cpu_percent
    except KeyError:
        return 0.0


def get_container_snapshot(container):
    """Gets name, status, and stats snapshot for a single container."""
    try:
        stats = None
        if container.status == "running":
            stats = container.stats(stream=False)
        return container, stats
    except:
        return container, None


# --- VIEWS ---

class ContainerControlView(discord.ui.View):
    """
    Ephemeral view: The buttons that appear ONLY when you select a container.
    """

    def __init__(self, cog, container_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.cid = container_id

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_action(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_action(interaction, "stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.do_action(interaction, "restart")

    async def do_action(self, interaction, action):
        await interaction.response.defer(ephemeral=True)
        try:
            client = await self.cog._get_client()
            container = client.containers.get(self.cid)

            def work():
                if action == "start":
                    container.start()
                elif action == "stop":
                    container.stop()
                elif action == "restart":
                    container.restart()

            await self.cog.bot.loop.run_in_executor(None, work)
            await interaction.followup.send(f"✅ **{container.name}** {action}ed!", ephemeral=True)

            # Force dashboard refresh immediately
            if self.cog.dashboard_view:
                await self.cog.dashboard_view.refresh_view()

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class ContainerSelect(discord.ui.Select):
    def __init__(self, cog, containers):
        self.cog = cog
        options = []
        for c in containers:
            emoji = "🟢" if c.status == "running" else "🔴"
            desc = f"ID: {c.short_id} | {c.status.upper()}"
            options.append(discord.SelectOption(
                label=c.name[:100],
                value=c.id,
                description=desc,
                emoji=emoji
            ))

        super().__init__(placeholder="Select a container to manage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        container_id = self.values[0]
        view = ContainerControlView(self.cog, container_id)
        await interaction.response.send_message(
            f"🎮 **Management Console**: `{container_id[:12]}`",
            view=view,
            ephemeral=True
        )


class DockerDashboardView(discord.ui.View):
    """The Main Dashboard View."""

    def __init__(self, cog, channel):
        super().__init__(timeout=None)  # Persistent
        self.cog = cog
        self.channel = channel  # We store channel, not ctx, for persistence
        self.message = None
        self._task = None
        self._updating = False

    async def start(self, message=None):
        """Starts the view. If message is provided, it resumes an existing dashboard."""
        self.message = message
        # Trigger one immediate refresh
        await self.refresh_view()
        # Start the loop
        self._task = asyncio.create_task(self.update_loop())

    async def stop(self):
        if self._task: self._task.cancel()

    async def update_loop(self):
        await self.cog.bot.wait_until_ready()
        while True:
            await asyncio.sleep(30.0)  # 30s refresh
            try:
                # If we have a message reference, try to update it.
                if self.message: await self.refresh_view()
            except Exception as e:
                log.error(f"Dashboard Loop Error: {e}")
                # If message is deleted/not found, we can't do anything.
                # Ideally we check error type, but for now we just keep trying or break
                pass

    async def refresh_view(self):
        if self._updating: return
        self._updating = True

        try:
            client = await self.cog._get_client()
            if not client: return

            containers = await self.cog.bot.loop.run_in_executor(
                None, lambda: client.containers.list(all=True)[:25]
            )

            tasks = [
                self.cog.bot.loop.run_in_executor(None, functools.partial(get_container_snapshot, c))
                for c in containers
            ]
            results = await asyncio.gather(*tasks)

            embeds = []

            if not results:
                embeds.append(discord.Embed(description="No containers found.", color=discord.Color.dark_grey()))

            for container, stats in results[:10]:
                status_icon = "🟢" if container.status == "running" else "🔴" if container.status == "exited" else "🟡"
                color = discord.Color.from_rgb(46, 204,
                                               113) if container.status == "running" else discord.Color.from_rgb(231,
                                                                                                                 76, 60)

                stats_text = f"**Status:** {container.status.upper()}"

                if stats:
                    cpu_pct = calculate_cpu_percent(stats)
                    mem_usage = stats.get("memory_stats", {}).get("usage", 0)
                    mem_limit = stats.get("memory_stats", {}).get("limit", 1)
                    mem_pct = (mem_usage / mem_limit) * 100.0
                    stats_text += f"\n**CPU:** `{cpu_pct:.1f}%`  **RAM:** `{mem_usage / 1024 / 1024:.0f}MB` ({mem_pct:.0f}%)"

                image_name = container.image.tags[0].split(':')[0] if container.image.tags else "unknown"
                if len(image_name) > 25: image_name = image_name[:24] + "…"

                embed = discord.Embed(title=f"{status_icon} {container.name}", description=stats_text, color=color)
                embed.set_footer(text=f"ID: {container.short_id} • {image_name}")
                embeds.append(embed)

            self.clear_items()

            if containers:
                self.add_item(ContainerSelect(self.cog, containers))

            refresh_btn = discord.ui.Button(label="Force Refresh", style=discord.ButtonStyle.secondary, emoji="🔃",
                                            custom_id="force_refresh")
            refresh_btn.callback = self.on_refresh_click
            self.add_item(refresh_btn)

            # Send New or Edit Existing
            if not self.message:
                self.message = await self.channel.send(content="**Docker Mission Control**", embeds=embeds, view=self)
                # SAVE TO CONFIG
                await self.cog.config.dashboard_channel.set(self.channel.id)
                await self.cog.config.dashboard_message.set(self.message.id)
            else:
                await self.message.edit(embeds=embeds, view=self)

        except discord.NotFound:
            # Message was deleted manually
            if self._task: self._task.cancel()
            await self.cog.config.dashboard_channel.set(None)
            await self.cog.config.dashboard_message.set(None)
            self.message = None
        except Exception as e:
            log.error(f"Dashboard build error: {e}")
        finally:
            self._updating = False

    async def on_refresh_click(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.refresh_view()


# --- MAIN COG ---

class DockerManager(commands.Cog):
    """Manage Docker containers from Discord."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        default_global = {
            "base_url": None,
            "dashboard_channel": None,
            "dashboard_message": None
        }
        self.config.register_global(**default_global)
        self.client = None
        self.dashboard_view = None

    async def cog_load(self):
        """Called when bot loads. Revive dashboard."""
        log.info("DockerManager loading...")
        await self._get_client()  # Pre-load client
        await self._resume_dashboard()

    async def cog_unload(self):
        """Cleanup tasks."""
        if self.dashboard_view:
            await self.dashboard_view.stop()
        if self.client:
            self.client.close()

    async def _resume_dashboard(self):
        """Attempts to find and reconnect to the saved dashboard message."""
        chan_id = await self.config.dashboard_channel()
        msg_id = await self.config.dashboard_message()

        if not chan_id or not msg_id:
            return

        channel = self.bot.get_channel(chan_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(msg_id)
            log.info(f"Resuming Docker Dashboard on message {msg_id}")

            self.dashboard_view = DockerDashboardView(self, channel)
            # Reconnect view to the message object found
            await self.dashboard_view.start(message=message)

        except discord.NotFound:
            log.warning("Saved dashboard message not found. Clearing config.")
            await self.config.dashboard_channel.set(None)
            await self.config.dashboard_message.set(None)
        except Exception as e:
            log.error(f"Failed to resume dashboard: {e}")

    async def _get_client(self):
        if self.client:
            try:
                self.client.ping()
                return self.client
            except:
                pass
        try:
            url = await self.config.base_url()
            self.client = docker.DockerClient(base_url=url) if url else docker.from_env()
            self.client.ping()
            return self.client
        except Exception as e:
            log.error(f"Docker connect fail: {e}")
            return None

    @commands.group(name="docker", aliases=["container"])
    @commands.guild_only()
    @checks.is_owner()
    async def docker_group(self, ctx: commands.Context):
        """Docker Management Commands."""
        pass

    @docker_group.command(name="dashboard", aliases=["panel"])
    async def docker_dashboard(self, ctx: commands.Context):
        """
        Opens or moves the live Mission Control dashboard.
        Updates every 30 seconds. Persists across reboots.
        """
        client = await self._get_client()
        if not client: return await ctx.send("❌ Connection failed.")

        # Stop old task if running to prevent duplicates
        if self.dashboard_view:
            await self.dashboard_view.stop()
            # Optional: Delete old message to avoid clutter?
            # if self.dashboard_view.message:
            #     try: await self.dashboard_view.message.delete()
            #     except: pass

        self.dashboard_view = DockerDashboardView(self, ctx.channel)
        await self.dashboard_view.start()  # This creates a NEW message and saves IDs

    @docker_group.command(name="config")
    async def docker_config(self, ctx: commands.Context, url: str = None):
        """Set remote host URL (e.g. tcp://1.2.3.4:2375). Empty to reset."""
        await self.config.base_url.set(url)
        self.client = None
        await ctx.tick()