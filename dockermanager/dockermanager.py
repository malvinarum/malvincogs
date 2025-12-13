from redbot.core import commands, Config
import discord
from discord.ext import tasks
import docker
from datetime import datetime


class DockerControlView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Lock to Bot Owner"""
        if await interaction.client.is_owner(interaction.user):
            return True
        await interaction.response.send_message("⛔ You do not have permission to control Docker.", ephemeral=True)
        return False

    async def generate_options(self):
        """Generates dropdown options from current Docker state."""
        if not self.cog.docker_client:
            return [discord.SelectOption(label="Docker Connection Failed", value="error")]

        try:
            # Get ALL containers (running and stopped)
            containers = self.cog.docker_client.containers.list(all=True)
            containers.sort(key=lambda x: x.name)

            options = []
            if not containers:
                return [discord.SelectOption(label="No Containers Found", value="none", default=True)]

            # Limit to 25 for Discord API
            for container in containers[:25]:
                is_running = container.status == "running"
                status_emoji = "🟢" if is_running else "🔴"

                # Create a concise label/desc
                label = f"{container.name}"[:25]
                desc = f"{status_emoji} {container.status.upper()} | {container.short_id}"

                options.append(discord.SelectOption(
                    label=label,
                    description=desc,
                    value=container.name,
                    emoji=status_emoji
                ))

            return options
        except Exception as e:
            print(f"Error generating options: {e}")
            return [discord.SelectOption(label="Error fetching list", value="error")]

    @discord.ui.select(
        placeholder="Select a container to manage...",
        min_values=1,
        max_values=1,
        custom_id="docker_control:select_container",
        options=[discord.SelectOption(label="Loading...", value="loading")]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        container_name = select.values[0]
        if container_name in ["none", "loading", "error"]:
            await interaction.response.send_message("No valid container selected.", ephemeral=True)
            return

        # Create the sub-view for this specific container
        view = ContainerActionView(self.cog, container_name)

        # Get fresh status for the embed
        try:
            container = self.cog.docker_client.containers.get(container_name)
            color = 0x43b581 if container.status == "running" else 0xf04747
            embed = discord.Embed(title=f"📦 {container.name}", color=color)
            embed.add_field(name="Status", value=container.status.upper(), inline=True)
            embed.add_field(name="ID", value=container.short_id, inline=True)
            embed.add_field(name="Image", value=str(container.image.tags[0]) if container.image.tags else "Unknown",
                            inline=False)

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except docker.errors.NotFound:
            await interaction.response.send_message(f"Container `{container_name}` not found.", ephemeral=True)

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        custom_id="docker_control:force_refresh"
    )
    async def refresh_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.cog.update_dashboard()
        await interaction.followup.send("Refreshing dashboard...", ephemeral=True)


class ContainerActionView(discord.ui.View):
    """Ephemeral view to manage a specific container."""

    def __init__(self, cog, container_name):
        super().__init__(timeout=60)
        self.cog = cog
        self.container_name = container_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await interaction.client.is_owner(interaction.user):
            return True
        await interaction.response.send_message("⛔ Access Denied.", ephemeral=True)
        return False

    async def run_action(self, interaction, action):
        await interaction.response.defer()
        try:
            container = self.cog.docker_client.containers.get(self.container_name)

            if action == "start":
                container.start()
            elif action == "stop":
                container.stop()
            elif action == "restart":
                container.restart()

            await interaction.followup.send(f"✅ Sent `{action}` command to `{self.container_name}`", ephemeral=True)
            # Force update the main dashboard so the status light changes immediately
            await self.cog.update_dashboard()

        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_action(interaction, "start")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔁")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_action(interaction, "restart")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_action(interaction, "stop")


class DockerManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Config setup for persistence
        self.config = Config.get_conf(self, identifier=85720938475)
        self.config.register_global(
            dashboard_channel=None,
            dashboard_message=None
        )

        try:
            self.docker_client = docker.from_env()
            print("🐳 Docker Client Connected")
        except Exception as e:
            print(f"❌ Failed to connect to Docker: {e}")
            self.docker_client = None

        self.view = DockerControlView(self)
        self.bot.add_view(self.view)
        self.dashboard_loop.start()

    def cog_unload(self):
        self.dashboard_loop.cancel()

    async def get_dashboard_embed(self):
        """Generates the main dashboard embed."""
        if not self.docker_client:
            return discord.Embed(title="Docker Error", description="Client not connected.", color=0xff0000)

        containers = self.docker_client.containers.list(all=True)
        # Sort for consistent display order
        containers.sort(key=lambda x: x.name)

        running_count = sum(1 for c in containers if c.status == 'running')
        total_count = len(containers)

        embed = discord.Embed(title="🐳 Docker Mission Control", color=0x2b2d31)
        embed.timestamp = datetime.now()
        embed.set_footer(text="Pleiades System Monitor • Last Updated")

        # Summary Stats
        embed.add_field(name="Running", value=f"🟢 {running_count}", inline=True)
        embed.add_field(name="Stopped", value=f"🔴 {total_count - running_count}", inline=True)
        embed.add_field(name="Total", value=f"📦 {total_count}", inline=True)

        # List containers (up to 15 to avoid hitting embed limits)
        status_text = ""
        for container in containers[:15]:
            icon = "🟢" if container.status == "running" else "🔴"
            status_text += f"{icon} **{container.name}**\n"

        if len(containers) > 15:
            status_text += f"*...and {len(containers) - 15} more*"

        embed.description = status_text if status_text else "No containers found."
        return embed

    async def update_dashboard(self):
        """Fetches the message from Config and updates it."""
        data = await self.config.all()
        channel_id = data['dashboard_channel']
        message_id = data['dashboard_message']

        if not channel_id or not message_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)
            embed = await self.get_dashboard_embed()

            # Generate fresh options for the dropdown
            new_options = await self.view.generate_options()
            self.view.children[0].options = new_options

            await message.edit(embed=embed, view=self.view)
        except discord.NotFound:
            # Message was deleted, clear config so we stop trying
            await self.config.dashboard_message.set(None)
        except Exception as e:
            print(f"Failed to update Docker panel: {e}")

    @tasks.loop(seconds=60)
    async def dashboard_loop(self):
        await self.update_dashboard()

    @dashboard_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="dockerpanel")
    @commands.is_owner()
    async def docker_panel(self, ctx, channel: discord.TextChannel = None):
        """
        Spawns the persistent Docker Mission Control panel.
        Usage: !dockerpanel [channel]
        """
        target_channel = channel or ctx.channel

        embed = await self.get_dashboard_embed()

        # Populate initial options
        self.view.children[0].options = await self.view.generate_options()

        msg = await target_channel.send(embed=embed, view=self.view)

        # Save exact location to Config
        await self.config.dashboard_channel.set(target_channel.id)
        await self.config.dashboard_message.set(msg.id)

        if target_channel != ctx.channel:
            await ctx.send(f"✅ Docker Panel spawned in {target_channel.mention}")


async def setup(bot):
    await bot.add_cog(DockerManager(bot))