import discord
import logging
import asyncio
import docker
import functools
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.malvincogs.dockermanager")


class DockerActionView(discord.ui.View):
    """
    A persistent view that refreshes the docker status automatically.
    """

    def __init__(self, cog, ctx, client):
        super().__init__(timeout=300)  # Increased timeout to 5 mins since update is slower
        self.cog = cog
        self.ctx = ctx
        self.client = client
        self.message = None
        self._task = None
        self._updating = False

    async def start(self):
        """Starts the update loop."""
        # Initial render
        await self.refresh_view()
        # Start background loop
        self._task = asyncio.create_task(self.update_loop())

    async def on_timeout(self):
        """Cleanup when the view times out."""
        if self._task:
            self._task.cancel()
        if self.message:
            try:
                # Disable buttons
                for child in self.children:
                    child.disabled = True
                await self.message.edit(view=self)
            except:
                pass

    async def update_loop(self):
        """Background task to refresh the dashboard."""
        await self.cog.bot.wait_until_ready()
        while True:
            await asyncio.sleep(60.0)  # Update interval: 60 seconds
            try:
                await self.refresh_view()
            except Exception as e:
                log.error(f"Dashboard refresh error: {e}")
                # If message is deleted, stop loop
                if self.message is None:
                    break

    async def refresh_view(self):
        if self._updating: return
        self._updating = True

        try:
            # Fetch container list (Blocking IO -> Executor)
            # Limit to 10 because Discord allows max 10 embeds per message
            containers = await self.cog.bot.loop.run_in_executor(
                None,
                lambda: self.client.containers.list(all=True)[:10]
            )

            embeds = []
            self.clear_items()

            if not containers:
                embeds.append(discord.Embed(title="Docker Dashboard", description="No containers found.",
                                            color=discord.Color.light_grey()))

            for c in containers:
                # --- Embed Construction ---
                status_icon = "🟢" if c.status == "running" else "🔴" if c.status == "exited" else "🟡"
                color = discord.Color.green() if c.status == "running" else discord.Color.red() if c.status == "exited" else discord.Color.gold()

                image_tag = c.image.tags[0] if c.image.tags else c.image.short_id
                # Clean up image tag for display
                if ":" in image_tag: image_tag = image_tag.split(":")[0]
                if len(image_tag) > 20: image_tag = image_tag[:19] + "…"

                embed = discord.Embed(
                    title=f"{status_icon} {c.name}",
                    color=color
                )
                embed.add_field(name="Image", value=f"`{image_tag}`", inline=True)
                embed.add_field(name="Status", value=f"`{c.status.upper()}`", inline=True)
                # Add ID to footer to be explicit
                embed.set_footer(text=f"ID: {c.short_id}")

                embeds.append(embed)

                # --- Button Construction ---
                # We limit buttons to 25. 10 containers * 2 buttons = 20. Fits nicely.

                # Button 1: Toggle State
                if c.status == "running":
                    style = discord.ButtonStyle.danger
                    label = "Stop"
                    emoji = "⏹️"
                    action = "stop"
                else:
                    style = discord.ButtonStyle.success
                    label = "Start"
                    emoji = "▶️"
                    action = "start"

                btn_toggle = discord.ui.Button(
                    style=style,
                    label=label,
                    emoji=emoji,
                    custom_id=f"{action}:{c.name}",
                    row=len(embeds) - 1 if len(embeds) <= 5 else 4  # Simple row logic to try and group them
                )
                btn_toggle.callback = functools.partial(self.on_button_click, action=action, container_id=c.name)
                self.add_item(btn_toggle)

                # Button 2: Restart (Only if running)
                if c.status == "running":
                    btn_restart = discord.ui.Button(
                        style=discord.ButtonStyle.secondary,
                        label="Restart",
                        emoji="🔄",
                        custom_id=f"restart:{c.name}",
                        row=len(embeds) - 1 if len(embeds) <= 5 else 4
                    )
                    btn_restart.callback = functools.partial(self.on_button_click, action="restart",
                                                             container_id=c.name)
                    self.add_item(btn_restart)

            # Send or Edit
            if not self.message:
                self.message = await self.ctx.send(content="**Docker Mission Control**", embeds=embeds, view=self)
            else:
                await self.message.edit(embeds=embeds, view=self)

        except discord.NotFound:
            self._task.cancel()  # Message deleted
        except Exception as e:
            log.error(f"Error building dashboard: {e}")
        finally:
            self._updating = False

    async def on_button_click(self, interaction: discord.Interaction, action: str, container_id: str):
        # Acknowledge immediately to prevent "Interaction Failed"
        await interaction.response.defer()

        try:
            container = self.client.containers.get(container_id)

            # Execute blocking docker action in thread
            def do_docker_work():
                if action == "start":
                    container.start()
                elif action == "stop":
                    container.stop()
                elif action == "restart":
                    container.restart()

            await self.cog.bot.loop.run_in_executor(None, do_docker_work)

            # Trigger immediate refresh
            await self.refresh_view()

        except Exception as e:
            await interaction.followup.send(f"❌ Failed to {action} {container_id}: {e}", ephemeral=True)


class DockerManager(commands.Cog):
    """
    Manage Docker containers from Discord.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        default_global = {
            "base_url": None,
            "status_channel": None
        }
        self.config.register_global(**default_global)
        self.client = None

    async def _get_client(self):
        """Get or initialize the Docker client."""
        if self.client:
            try:
                self.client.ping()
                return self.client
            except Exception:
                log.warning("Docker client lost connection. Reconnecting...")

        try:
            base_url = await self.config.base_url()
            if base_url:
                self.client = docker.DockerClient(base_url=base_url)
            else:
                self.client = docker.from_env()

            self.client.ping()
            return self.client
        except Exception as e:
            log.error(f"Failed to connect to Docker: {e}")
            return None

    @commands.group(name="docker", aliases=["container"])
    @commands.guild_only()
    @checks.is_owner()
    async def docker_group(self, ctx: commands.Context):
        """Docker Management Commands."""
        pass

    @docker_group.command(name="dashboard", aliases=["panel", "board"])
    async def docker_dashboard(self, ctx: commands.Context):
        """
        Opens the live Docker Mission Control dashboard.
        Updates automatically every 60 seconds.
        """
        client = await self._get_client()
        if not client:
            return await ctx.send("❌ Could not connect to Docker daemon.")

        view = DockerActionView(self, ctx, client)
        await view.start()

    @docker_group.command(name="status", aliases=["ps", "list"])
    async def docker_ps(self, ctx: commands.Context, all: bool = False):
        """List containers (CLI style)."""
        client = await self._get_client()
        if not client: return await ctx.send("❌ Docker Error.")

        async with ctx.typing():
            try:
                containers = client.containers.list(all=all)
                if not containers: return await ctx.send("No containers found.")

                headers = ["ID", "NAME", "STATUS", "IMAGE"]
                data = []
                for c in containers:
                    image = c.image.tags[0] if c.image.tags else c.image.short_id
                    if len(image) > 20: image = image[:17] + "..."
                    data.append([c.short_id, c.name, c.status, image])

                from tabulate import tabulate
                table = tabulate(data, headers=headers, tablefmt="simple")
                for page in pagify(table):
                    await ctx.send(box(page, lang="prolog"))
            except Exception as e:
                await ctx.send(f"Error: {e}")

    @docker_group.command(name="stats")
    async def docker_stats(self, ctx: commands.Context, container_name: str):
        """Get snapshot stats for a container."""
        client = await self._get_client()
        if not client: return await ctx.send("❌ Connection failed.")

        try:
            container = client.containers.get(container_name)
            if container.status != "running":
                return await ctx.send(f"⚠️ Container is {container.status}.")

            msg = await ctx.send("⏳ Measuring...")
            stats = await self.bot.loop.run_in_executor(None, lambda: container.stats(stream=False))

            # Calculations
            cpu_d = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
            sys_d = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            n_cpus = stats['cpu_stats']['online_cpus']
            cpu_pct = (cpu_d / sys_d) * n_cpus * 100.0 if sys_d > 0 else 0.0

            mem_use = stats['memory_stats']['usage']
            mem_lim = stats['memory_stats']['limit']
            mem_pct = (mem_use / mem_lim) * 100.0

            net = stats.get('networks', {})
            rx = sum(v['rx_bytes'] for v in net.values()) / (1024 ** 2)
            tx = sum(v['tx_bytes'] for v in net.values()) / (1024 ** 2)

            embed = discord.Embed(title=f"📊 Stats: {container.name}", color=discord.Color.blue())
            embed.add_field(name="CPU", value=f"{cpu_pct:.2f}%")
            embed.add_field(name="RAM",
                            value=f"{mem_use / (1024 ** 2):.1f}MB / {mem_lim / (1024 ** 2):.1f}MB ({mem_pct:.1f}%)")
            embed.add_field(name="Network", value=f"⬇️ {rx:.1f}MB | ⬆️ {tx:.1f}MB")

            await msg.edit(content=None, embed=embed)

        except Exception as e:
            await ctx.send(f"Error: {e}")

    @docker_group.command(name="config")
    async def docker_config(self, ctx: commands.Context, url: str = None):
        """Set remote host URL (e.g., tcp://1.2.3.4:2375). Empty to reset."""
        if url:
            await self.config.base_url.set(url)
            await ctx.send(f"✅ Host set to `{url}`.")
        else:
            await self.config.base_url.set(None)
            await ctx.send("✅ Host reset to local socket.")
        self.client = None  # Force reload