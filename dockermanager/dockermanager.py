import discord
import logging
import asyncio
import docker
from datetime import datetime
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.malvincogs.dockermanager")


class DockerManager(commands.Cog):
    """
    Manage Docker containers from Discord.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        default_global = {
            "base_url": None,  # For remote docker hosts (tcp://1.2.3.4:2375)
            "status_channel": None
        }
        self.config.register_global(**default_global)

        # We initialize the client lazily to avoid crashing if docker isn't running
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

    @docker_group.command(name="status", aliases=["ps", "list"])
    async def docker_ps(self, ctx: commands.Context, all: bool = False):
        """List containers. Default shows running only."""
        client = await self._get_client()
        if not client:
            return await ctx.send("❌ Could not connect to Docker daemon.")

        async with ctx.typing():
            try:
                containers = client.containers.list(all=all)
                if not containers:
                    return await ctx.send("No containers found.")

                # Formatting
                headers = ["ID", "NAME", "STATUS", "IMAGE"]
                data = []
                for c in containers:
                    # Truncate ID
                    c_id = c.short_id
                    name = c.name
                    status = c.status
                    image = c.image.tags[0] if c.image.tags else c.image.short_id
                    if len(image) > 20: image = image[:17] + "..."

                    data.append([c_id, name, status, image])

                from tabulate import tabulate
                table = tabulate(data, headers=headers, tablefmt="simple")

                for page in pagify(table):
                    await ctx.send(box(page, lang="prolog"))

            except Exception as e:
                await ctx.send(f"Error fetching containers: {e}")

    @docker_group.command(name="start")
    async def docker_start(self, ctx: commands.Context, container_name: str):
        """Start a container."""
        await self._container_action(ctx, container_name, "start")

    @docker_group.command(name="stop")
    async def docker_stop(self, ctx: commands.Context, container_name: str):
        """Stop a container."""
        await self._container_action(ctx, container_name, "stop")

    @docker_group.command(name="restart")
    async def docker_restart(self, ctx: commands.Context, container_name: str):
        """Restart a container."""
        await self._container_action(ctx, container_name, "restart")

    async def _container_action(self, ctx, name, action):
        client = await self._get_client()
        if not client: return await ctx.send("❌ Docker connection failed.")

        try:
            container = client.containers.get(name)
            await ctx.send(f"⏳ Attempting to {action} **{name}**...")

            # Run blocking docker calls in executor
            def do_action():
                if action == "start":
                    container.start()
                elif action == "stop":
                    container.stop()
                elif action == "restart":
                    container.restart()

            await self.bot.loop.run_in_executor(None, do_action)
            await ctx.send(f"✅ Container **{name}** {action}ed.")

        except docker.errors.NotFound:
            await ctx.send(f"❌ Container `{name}` not found.")
        except Exception as e:
            await ctx.send(f"❌ Error during {action}: {e}")

    @docker_group.command(name="stats")
    async def docker_stats(self, ctx: commands.Context, container_name: str):
        """Get live stats for a container (CPU/RAM)."""
        client = await self._get_client()
        if not client: return await ctx.send("❌ Docker connection failed.")

        try:
            container = client.containers.get(container_name)
            if container.status != "running":
                return await ctx.send(f"⚠️ Container **{container_name}** is {container.status}.")

            msg = await ctx.send("⏳ Fetching stats stream (this takes a second)...")

            # Get stats (stream=False gets a snapshot)
            stats = await self.bot.loop.run_in_executor(None, lambda: container.stats(stream=False))

            # CPU Calc
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage'][
                'total_usage']
            system_cpu_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
            number_cpus = stats['cpu_stats']['online_cpus']
            cpu_percent = (cpu_delta / system_cpu_delta) * number_cpus * 100.0 if system_cpu_delta > 0 else 0.0

            # Memory Calc
            mem_usage = stats['memory_stats']['usage']
            mem_limit = stats['memory_stats']['limit']
            mem_percent = (mem_usage / mem_limit) * 100.0

            # Net IO
            net_io = stats.get('networks', {})
            rx = sum(v['rx_bytes'] for v in net_io.values())
            tx = sum(v['tx_bytes'] for v in net_io.values())

            embed = discord.Embed(title=f"🐳 {container.name}", color=discord.Color.blue())
            embed.add_field(name="Status", value=container.status.title())
            embed.add_field(name="ID", value=container.short_id)
            embed.add_field(name="Image", value=container.image.tags[0] if container.image.tags else "None")

            embed.add_field(name="CPU Usage", value=f"{cpu_percent:.2f}%")
            embed.add_field(name="Memory",
                            value=f"{mem_usage / (1024 ** 2):.2f}MB / {mem_limit / (1024 ** 2):.2f}MB ({mem_percent:.2f}%)")
            embed.add_field(name="Net I/O", value=f"⬇️ {rx / (1024 ** 2):.2f}MB | ⬆️ {tx / (1024 ** 2):.2f}MB")

            await msg.edit(content=None, embed=embed)

        except docker.errors.NotFound:
            await ctx.send(f"❌ Container `{container_name}` not found.")
        except Exception as e:
            await ctx.send(f"❌ Error fetching stats: {e}")

    @docker_group.command(name="config")
    async def docker_config(self, ctx: commands.Context, url: str = None):
        """Set a remote Docker host URL. Leave empty to reset to local socket."""
        if url:
            await self.config.base_url.set(url)
            await ctx.send(f"✅ Docker host set to `{url}`. Attempting connection...")
        else:
            await self.config.base_url.set(None)
            await ctx.send("✅ Docker host reset to local socket. Attempting connection...")

        # Force reconnect
        self.client = None
        client = await self._get_client()
        if client:
            await ctx.send("✅ Connected successfully.")
        else:
            await ctx.send("❌ Connection failed. Check logs.")