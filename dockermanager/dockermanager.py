import discord
import logging
import asyncio
import docker
import functools
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.malvincogs.dockermanager")


def calculate_cpu_percent(d):
    """
    Calculate CPU % from Docker stats object.
    Source: https://github.com/docker/cli/blob/master/cli/command/container/stats_helpers.go
    """
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


def get_stats_snapshot(container):
    """Blocking function to get a single snapshot of stats."""
    try:
        # stream=False gets a single snapshot
        stats = container.stats(stream=False)
        return container.name, stats
    except:
        return container.name, None


class DockerActionView(discord.ui.View):
    """
    A persistent view that refreshes the docker status automatically.
    """

    def __init__(self, cog, ctx, client):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.client = client
        self.message = None
        self._task = None
        self._updating = False

    async def start(self):
        await self.refresh_view()
        self._task = asyncio.create_task(self.update_loop())

    async def on_timeout(self):
        if self._task: self._task.cancel()
        if self.message:
            try:
                for child in self.children: child.disabled = True
                await self.message.edit(view=self)
            except:
                pass

    async def update_loop(self):
        await self.cog.bot.wait_until_ready()
        while True:
            await asyncio.sleep(60.0)
            try:
                await self.refresh_view()
            except Exception as e:
                log.error(f"Dashboard refresh error: {e}")
                if self.message is None: break

    async def refresh_view(self):
        if self._updating: return
        self._updating = True

        try:
            # 1. Get List of Containers
            containers = await self.cog.bot.loop.run_in_executor(
                None, lambda: self.client.containers.list(all=True)[:10]
            )

            # 2. Parallel Fetch Stats for Running Containers
            # Docker stats API can be slow, so we gather them concurrently
            running_containers = [c for c in containers if c.status == "running"]
            stats_map = {}

            if running_containers:
                tasks = [
                    self.cog.bot.loop.run_in_executor(None, functools.partial(get_stats_snapshot, c))
                    for c in running_containers
                ]
                results = await asyncio.gather(*tasks)
                stats_map = {name: data for name, data in results}

            embeds = []
            self.clear_items()

            if not containers:
                embeds.append(discord.Embed(description="No containers found.", color=discord.Color.light_grey()))

            for c in containers:
                # --- Stats Calculation ---
                stats_str = "Offline"
                color = discord.Color.red()
                status_icon = "🔴"

                if c.status == "running":
                    color = discord.Color.green()
                    status_icon = "🟢"

                    s = stats_map.get(c.name)
                    if s:
                        # CPU
                        cpu_usage = calculate_cpu_percent(s)

                        # RAM
                        mem_usage = s.get("memory_stats", {}).get("usage", 0)
                        mem_limit = s.get("memory_stats", {}).get("limit", 1)
                        mem_percent = (mem_usage / mem_limit) * 100.0

                        stats_str = f"**CPU:** `{cpu_usage:.1f}%`  **RAM:** `{mem_usage / 1024 / 1024:.0f}MB` ({mem_percent:.1f}%)"
                    else:
                        stats_str = "Fetching stats..."

                elif c.status == "exited":
                    stats_str = f"Exited ({c.attrs['State'].get('ExitCode', '?')})"

                # --- Embed ---
                # Truncate image name for cleaner look
                img = c.image.tags[0].split(':')[0] if c.image.tags else c.image.short_id
                if len(img) > 25: img = img[:24] + "…"

                embed = discord.Embed(
                    title=f"{status_icon} {c.name}",
                    description=stats_str,
                    color=color
                )
                embed.set_footer(text=f"ID: {c.short_id} • {img}")
                embeds.append(embed)

                # --- Buttons ---
                # Label is truncated to 8 chars to fit row
                label_name = c.name[:8]

                if c.status == "running":
                    self.add_item(discord.ui.Button(
                        style=discord.ButtonStyle.danger,
                        label=f"Stop {label_name}",
                        custom_id=f"stop:{c.name}",
                        emoji="⏹️"
                    ))
                    self.add_item(discord.ui.Button(
                        style=discord.ButtonStyle.secondary,
                        custom_id=f"restart:{c.name}",
                        emoji="🔄"
                    ))
                else:
                    self.add_item(discord.ui.Button(
                        style=discord.ButtonStyle.success,
                        label=f"Start {label_name}",
                        custom_id=f"start:{c.name}",
                        emoji="▶️"
                    ))

                # Assign callbacks dynamically
                for child in self.children:
                    if not child.callback:
                        # Extract action and id from custom_id
                        action, cid = child.custom_id.split(":")
                        child.callback = functools.partial(self.on_button_click, action=action, container_id=cid)

            # Send/Edit
            if not self.message:
                self.message = await self.ctx.send(content="**Docker Mission Control**", embeds=embeds, view=self)
            else:
                await self.message.edit(embeds=embeds, view=self)

        except discord.NotFound:
            self._task.cancel()
        except Exception as e:
            log.error(f"Dashboard build error: {e}")
        finally:
            self._updating = False

    async def on_button_click(self, interaction: discord.Interaction, action: str, container_id: str):
        await interaction.response.defer()
        try:
            container = self.client.containers.get(container_id)

            def do_work():
                if action == "start":
                    container.start()
                elif action == "stop":
                    container.stop()
                elif action == "restart":
                    container.restart()

            await self.cog.bot.loop.run_in_executor(None, do_work)
            await self.refresh_view()
        except Exception as e:
            await interaction.followup.send(f"Error: {e}", ephemeral=True)


class DockerManager(commands.Cog):
    """Manage Docker containers from Discord."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        default_global = {"base_url": None}
        self.config.register_global(**default_global)
        self.client = None

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

        # Cleanup old view if exists? (Optional logic here)
        view = DockerActionView(self, ctx, client)
        await view.start()

    @docker_group.command(name="config")
    async def docker_config(self, ctx: commands.Context, url: str = None):
        """Set remote host URL (e.g. tcp://1.2.3.4:2375). Empty to reset."""
        await self.config.base_url.set(url)
        self.client = None
        await ctx.tick()