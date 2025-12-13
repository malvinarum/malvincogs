from redbot.core import commands
import discord
from discord.ext import tasks
import docker
from datetime import datetime

# --- CONFIGURATION ---
DOCKER_CHANNEL_ID = None  # e.g., 123456789012345678


class DockerControlView(discord.ui.View):
    def __init__(self, docker_client):
        super().__init__(timeout=None)
        self.docker_client = docker_client

    async def generate_latest_options(self):
        """Helper to dynamically generate dropdown options based on current containers."""
        try:
            containers = self.docker_client.containers.list(all=True)
            options = []

            # Sort containers by name for consistency
            containers.sort(key=lambda x: x.name)

            # Limit to 25 because Discord Select menus max out at 25 options
            for container in containers[:25]:
                status_emoji = "🟢" if container.status == "running" else "🔴"
                # Truncate label if too long (max 100 chars)
                label = f"{container.name}"[:25]
                desc = f"{status_emoji} {container.status.upper()} | {container.short_id}"

                options.append(discord.SelectOption(
                    label=label,
                    description=desc,
                    value=container.name,
                    emoji=status_emoji
                ))

            if not options:
                options.append(discord.SelectOption(label="No Containers Found", value="none", default=True))

            return options
        except Exception as e:
            print(f"Error generating options: {e}")
            return [discord.SelectOption(label="Error fetching containers", value="error")]

    @discord.ui.select(
        placeholder="Select a container to manage...",
        min_values=1,
        max_values=1,
        custom_id="docker_mission_control:select_container",
        options=[discord.SelectOption(label="Loading...", value="loading")]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        container_name = select.values[0]
        if container_name in ["none", "loading", "error"]:
            await interaction.response.send_message("No valid container selected.", ephemeral=True)
            return

        try:
            container = self.docker_client.containers.get(container_name)

            status_color = 0x43b581 if container.status == "running" else 0xf04747

            embed = discord.Embed(title=f"📦 {container.name}", color=status_color)
            embed.add_field(name="Status", value=container.status.upper(), inline=True)
            embed.add_field(name="ID", value=container.short_id, inline=True)
            embed.add_field(name="Image", value=container.image.tags[0] if container.image.tags else "Unknown",
                            inline=False)

            view = ContainerActionView(container_name)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except docker.errors.NotFound:
            await interaction.response.send_message(f"Container `{container_name}` no longer exists.", ephemeral=True)

    @discord.ui.button(
        label="Force Refresh",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        custom_id="docker_mission_control:force_refresh"
    )
    async def refresh_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.followup.send("Refresh request received. Panel will update shortly.", ephemeral=True)


class ContainerActionView(discord.ui.View):
    """Ephemeral view to manage a specific container selected from the dropdown"""

    def __init__(self, container_name):
        super().__init__(timeout=60)
        self.container_name = container_name
        self.docker_client = docker.from_env()

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.danger, emoji="🔁")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            container = self.docker_client.containers.get(self.container_name)
            container.restart()
            await interaction.followup.send(f"✅ Restarted `{self.container_name}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


class DockerManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            self.docker_client = docker.from_env()
            print("🐳 Docker Client Connected")
        except Exception as e:
            print(f"❌ Failed to connect to Docker: {e}")
            self.docker_client = None

        self.dashboard_message = None

        # Register the view for persistence
        if self.docker_client:
            self.view = DockerControlView(self.docker_client)
            self.bot.add_view(self.view)
            self.update_stats_loop.start()

    def cog_unload(self):
        self.update_stats_loop.cancel()

    async def get_system_embed(self):
        """Generates the main dashboard embed"""
        if not self.docker_client:
            return discord.Embed(title="Docker Error", description="Client not connected.", color=0xff0000)

        containers = self.docker_client.containers.list(all=True)
        running_count = sum(1 for c in containers if c.status == 'running')
        total_count = len(containers)

        embed = discord.Embed(title="🐳 Docker Mission Control", color=0x2b2d31, timestamp=datetime.now())
        embed.set_footer(text="Pleiades System Monitor")

        # Summary Stats
        embed.add_field(name="Running", value=f"🟢 {running_count}", inline=True)
        embed.add_field(name="Stopped", value=f"🔴 {total_count - running_count}", inline=True)
        embed.add_field(name="Total", value=f"📦 {total_count}", inline=True)

        status_text = ""
        for container in containers[:10]:
            icon = "🟢" if container.status == "running" else "🔴"
            status_text += f"{icon} **{container.name}**\n"

        if len(containers) > 10:
            status_text += f"*...and {len(containers) - 10} more*"

        embed.description = status_text if status_text else "No containers found."
        return embed

    @tasks.loop(seconds=60)
    async def update_stats_loop(self):
        """Updates the dashboard message periodically"""
        if not self.docker_client:
            return

        if self.dashboard_message is None and DOCKER_CHANNEL_ID:
            try:
                channel = self.bot.get_channel(DOCKER_CHANNEL_ID)
                # Logic to recover message would go here if we tracked message ID
                pass
            except Exception:
                pass

        if self.dashboard_message:
            try:
                embed = await self.get_system_embed()
                new_options = await self.view.generate_latest_options()

                select_menu = self.view.children[0]
                select_menu.options = new_options

                await self.dashboard_message.edit(embed=embed, view=self.view)
            except discord.NotFound:
                self.dashboard_message = None
            except Exception as e:
                print(f"Failed to update Docker panel: {e}")

    @update_stats_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="dockerpanel")
    @commands.is_owner()
    async def docker_panel(self, ctx):
        """Spawns the persistent Docker Mission Control panel"""
        embed = await self.get_system_embed()

        new_options = await self.view.generate_latest_options()
        self.view.children[0].options = new_options

        self.dashboard_message = await ctx.send(embed=embed, view=self.view)

        if DOCKER_CHANNEL_ID and ctx.channel.id != DOCKER_CHANNEL_ID:
            print(
                f"⚠️ Warning: Created panel in channel {ctx.channel.id}, but config DOCKER_CHANNEL_ID is {DOCKER_CHANNEL_ID}.")


async def setup(bot):
    await bot.add_cog(DockerManager(bot))