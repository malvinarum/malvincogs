import discord
from discord.ext import commands, tasks
import docker
from datetime import datetime
import io

# --- CONFIGURATION ---
# If you want the panel to auto-resume updating after a bot restart,
# copy the Channel ID where you want the panel to live and paste it here.
# If you leave this as None, you must run !dockerpanel to start the loop.
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
        options=[discord.SelectOption(label="Loading...", value="loading")]  # Placeholder
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        container_name = select.values[0]
        if container_name in ["none", "loading", "error"]:
            await interaction.response.send_message("No valid container selected.", ephemeral=True)
            return

        try:
            container = self.docker_client.containers.get(container_name)

            # Create a specific control view for this container
            # You could add Start/Stop/Restart buttons here in a separate ephemeral view
            status_color = 0x43b581 if container.status == "running" else 0xf04747

            embed = discord.Embed(title=f"📦 {container.name}", color=status_color)
            embed.add_field(name="Status", value=container.status.upper(), inline=True)
            embed.add_field(name="ID", value=container.short_id, inline=True)
            embed.add_field(name="Image", value=container.image.tags[0] if container.image.tags else "Unknown",
                            inline=False)

            # Action Buttons for the selected container
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
        # The loop handles the heavy lifting, this just acknowledges the click
        # and triggers an immediate update if possible, or just lets the user know.
        await interaction.response.defer()
        # You could manually trigger the Cog's update_panel method here if you link them,
        # but deferring is usually enough as the loop runs often.
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

        # List first 10 containers in the embed text for quick glance
        status_text = ""
        for container in containers[:10]:
            icon = "🟢" if container.status == "running" else "🔴"
            # Format: 🟢 **container_name** (running)
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

        # Try to find the message if we don't have it (e.g. after restart)
        if self.dashboard_message is None and DOCKER_CHANNEL_ID:
            try:
                channel = self.bot.get_channel(DOCKER_CHANNEL_ID)
                if channel:
                    # We have to look for the last message from the bot or rely on the user running command once.
                    # For safety, this loop waits for the command to set self.dashboard_message,
                    # OR you could hardcode a message ID if you want extreme persistence.
                    pass
            except Exception:
                pass

        if self.dashboard_message:
            try:
                embed = await self.get_system_embed()

                # Update the dropdown options dynamically
                new_options = await self.view.generate_latest_options()

                # We have to access the Select item in the View children
                # The Select menu is the first item (index 0) based on class definition order
                select_menu = self.view.children[0]
                select_menu.options = new_options

                await self.dashboard_message.edit(embed=embed, view=self.view)
            except discord.NotFound:
                # Message was deleted
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
        # Send loading state
        embed = await self.get_system_embed()

        # Generate initial options
        new_options = await self.view.generate_latest_options()
        self.view.children[0].options = new_options

        self.dashboard_message = await ctx.send(embed=embed, view=self.view)

        # If user configured the ID, let's print it to console to help them verify
        if DOCKER_CHANNEL_ID and ctx.channel.id != DOCKER_CHANNEL_ID:
            print(
                f"⚠️ Warning: Created panel in channel {ctx.channel.id}, but config DOCKER_CHANNEL_ID is {DOCKER_CHANNEL_ID}.")


async def setup(bot):
    await bot.add_cog(DockerManager(bot))