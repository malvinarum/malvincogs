import discord
import logging
import asyncio
import docker
import functools
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.malvincogs.dockermanager")


# --- UTILITY FUNCTIONS ---

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
        # Get Stats if running
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
            # Status Emoji
            emoji = "🟢" if c.status == "running" else "🔴"
            desc = f"ID: {c.short_id} | {c.status.upper()}"
            options.append(discord.SelectOption(
                label=c.name[:100],
                value=c.id,  # Use full ID for value
                description=desc,
                emoji=emoji
            ))

        super().__init__(placeholder="Select a container to manage...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        container_id = self.values[0]
        # Send ephemeral control panel
        view = ContainerControlView(self.cog, container_id)
        await interaction.response.send_message(
            f"🎮 **Management Console**: `{container_id[:12]}`",
            view=view,
            ephemeral=True
        )


class DockerDashboardView(discord.ui.View):
    """The Main Dashboard View (Embeds + Dropdown)."""

    def __init__(self, cog, ctx):
        super().__init__(timeout=None)  # Persistent-ish
        self.cog = cog
        self.ctx = ctx
        self.message = None
        self._task = None
        self._updating = False

    async def start(self):
        await self.refresh_view()
        self._task = asyncio.create_task(self.update_loop())

    async def update_loop(self):
        await self.cog.bot.wait_until_ready()
        while True:
            await asyncio.sleep(30.0)  # 30s refresh for stats
            try:
                if self.message: await self.refresh_view()
            except Exception:
                if self.message: break  # Stop if message deleted

    async def refresh_view(self):
        if self._updating: return
        self._updating = True

        try:
            client = await self.cog._get_client()
            if not client: return

            # 1. Get Containers (Top 25 max due to Select Menu limit)
            containers = await self.cog.bot.loop.run_in_executor(
                None, lambda: client.containers.list(all=True)[:25]
            )

            # 2. Parallel Fetch Stats
            tasks = [
                self.cog.bot.loop.run_in_executor(None, functools.partial(get_container_snapshot, c))
                for c in containers
            ]
            results = await asyncio.gather(*tasks)

            embeds = []

            # --- Build Embeds ---
            if not results:
                embeds.append(discord.Embed(description="No containers found.", color=discord.Color.dark_grey()))

            # We limit embeds to 10 (Discord Limit).
            # If > 10 containers, they show in Dropdown but not all have embeds.
            for container, stats in results[:10]:

                # --- Stats Formatting ---
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

                    # Net I/O
                    net = stats.get('networks', {})
                    rx = sum(v['rx_bytes'] for v in net.values())
                    tx = sum(v['tx_bytes'] for v in net.values())

                    stats_text += f"\n**CPU:** `{cpu_pct:.1f}%`  **RAM:** `{mem_usage / 1024 / 1024:.0f}MB` ({mem_pct:.0f}%)"
                    # stats_text += f"\n**Net:** ⬇️`{rx/1024/1024:.1f}MB` ⬆️`{tx/1024/1024:.1f}MB`"

                # Truncate image name
                image_name = container.image.tags[0].split(':')[0] if container.image.tags else "unknown"
                if len(image_name) > 25: image_name = image_name[:24] + "…"

                embed = discord.Embed(title=f"{status_icon} {container.name}", description=stats_text, color=color)
                embed.set_footer(text=f"ID: {container.short_id} • {image_name}")
                embeds.append(embed)

            # --- Build View (Dropdown) ---
            self.clear_items()

            # Add Select Menu
            if containers:
                self.add_item(ContainerSelect(self.cog, containers))

            # Add Force Refresh Button
            refresh_btn = discord.ui.Button(label="Force Refresh", style=discord.ButtonStyle.secondary, emoji="🔃",
                                            custom_id="force_refresh")
            refresh_btn.callback = self.on_refresh_click
            self.add_item(refresh_btn)

            # --- Send/Edit ---
            if not self.message:
                self.message = await self.ctx.send(embeds=embeds, view=self)
            else:
                await self.message.edit(embeds=embeds, view=self)

        except discord.NotFound:
            if self._task: self._task.cancel()
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
        default_global = {"base_url": None}
        self.config.register_global(**default_global)
        self.client = None
        self.dashboard_view = None

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
        """Opens the live Mission Control dashboard with stats."""
        client = await self._get_client()
        if not client: return await ctx.send("❌ Connection failed.")

        # Stop old task if running to prevent duplicates
        if self.dashboard_view and self.dashboard_view._task:
            self.dashboard_view._task.cancel()

        self.dashboard_view = DockerDashboardView(self, ctx)
        await self.dashboard_view.start()

    @docker_group.command(name="config")
    async def docker_config(self, ctx: commands.Context, url: str = None):
        """Set remote host URL (e.g. tcp://1.2.3.4:2375). Empty to reset."""
        await self.config.base_url.set(url)
        self.client = None
        await ctx.tick()