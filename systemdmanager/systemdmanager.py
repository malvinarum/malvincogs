from redbot.core import commands, Config
import discord
from discord.ext import tasks
import subprocess
from datetime import datetime


class SystemdControlView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.user_can_manage(interaction.user):
            return True
        await interaction.response.send_message("⛔ Access Denied: You are not authorized.", ephemeral=True)
        return False

    async def generate_options(self):
        """Generates dropdown options based on the services saved in Config."""
        services = await self.cog.config.services()
        options = []

        if not services:
            return [discord.SelectOption(label="No services configured", value="none", default=True)]

        for service in services:
            # We get a quick status just for the emoji
            status = self.cog.get_systemctl_status(service)
            is_active = status == "active"
            emoji = "🟢" if is_active else "🔴"

            options.append(discord.SelectOption(
                label=service,
                description=f"Status: {status}",
                value=service,
                emoji=emoji
            ))

        return options[:25]  # Discord limit

    @discord.ui.select(
        placeholder="Select a service to manage...",
        min_values=1,
        max_values=1,
        custom_id="systemd_control:select_service",
        options=[discord.SelectOption(label="Loading...", value="loading")]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        service_name = select.values[0]
        if service_name in ["none", "loading"]:
            await interaction.response.send_message("No valid service selected.", ephemeral=True)
            return

        # Fetch detailed status for the embed
        status = self.cog.get_systemctl_status(service_name)
        color = 0x43b581 if status == "active" else 0xf04747

        embed = discord.Embed(title=f"⚙️ {service_name}", color=color)
        embed.add_field(name="Current Status", value=f"**{status.upper()}**", inline=True)
        embed.set_footer(text=f"Systemd Manager • {datetime.now().strftime('%H:%M:%S')}")

        view = ServiceActionView(self.cog, service_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄",
                       custom_id="systemd_control:refresh")
    async def refresh_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Trigger an immediate update loop
        await self.cog.update_dashboard()
        await interaction.followup.send("Refreshing dashboard...", ephemeral=True)


class ServiceActionView(discord.ui.View):
    def __init__(self, cog, service_name):
        super().__init__(timeout=60)
        self.cog = cog
        self.service_name = service_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await self.cog.user_can_manage(interaction.user):
            return True
        await interaction.response.send_message("⛔ Access Denied.", ephemeral=True)
        return False

    async def run_command(self, interaction, action):
        await interaction.response.defer()
        try:
            # THE MAGIC: sudo systemctl <action> <service>
            cmd = ["sudo", "systemctl", action, self.service_name]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                await interaction.followup.send(f"✅ Successfully sent `{action}` to `{self.service_name}`",
                                                ephemeral=True)
                # Force update the main dashboard so the status change reflects immediately
                await self.cog.update_dashboard()
            else:
                await interaction.followup.send(f"❌ Failed: {result.stderr}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️")
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_command(interaction, "start")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔁")
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_command(interaction, "restart")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.run_command(interaction, "stop")


class SystemdManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9812374129)
        self.config.register_global(
            services=[],
            authorized_users=[],
            dashboard_channel=None,
            dashboard_message=None
        )

        self.view = SystemdControlView(self)
        self.bot.add_view(self.view)
        self.dashboard_loop.start()

    def cog_unload(self):
        self.dashboard_loop.cancel()

    async def user_can_manage(self, user) -> bool:
        """Owner OR an explicitly authorized user may control services."""
        if await self.bot.is_owner(user):
            return True
        authorized = await self.config.authorized_users()
        return user.id in authorized

    def get_systemctl_status(self, service_name):
        """Runs systemctl is-active and returns the string (active, inactive, failed)."""
        try:
            # We use is-active because it returns a simple single-word string
            cmd = ["sudo", "systemctl", "is-active", service_name]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception:
            return "error"

    async def get_dashboard_embed(self):
        services = await self.config.services()

        embed = discord.Embed(title="🐧 Systemd Services", color=0x2b2d31)
        # Discord automatically localizes this timestamp to "Today at XX:XX" for the user
        embed.timestamp = datetime.now()
        embed.set_footer(text="Server Control • Last Updated")

        if not services:
            embed.description = "No services configured. Use `!systemd add <name>`."
            return embed

        status_text = ""
        running_count = 0

        for service in services:
            status = self.get_systemctl_status(service)
            if status == "active":
                icon = "🟢"
                running_count += 1
            elif status == "failed":
                icon = "🔴"
            else:
                icon = "⚪"  # inactive or unknown

            status_text += f"{icon} **{service}** (`{status}`)\n"

        embed.description = status_text

        # Add a summary field
        embed.add_field(name="Summary", value=f"Running: {running_count}/{len(services)}", inline=False)

        return embed

    async def update_dashboard(self):
        """Logic to find and edit the dashboard message."""
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

            # Update options in case services were added/removed
            new_options = await self.view.generate_options()
            self.view.children[0].options = new_options

            await message.edit(embed=embed, view=self.view)
        except discord.NotFound:
            # Message was deleted, clear config
            await self.config.dashboard_message.set(None)
        except Exception as e:
            print(f"Failed to update Systemd panel: {e}")

    @tasks.loop(seconds=60)
    async def dashboard_loop(self):
        await self.update_dashboard()

    @dashboard_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    @commands.group(name="systemd")
    @commands.is_owner()
    async def systemd_group(self, ctx):
        """Manage systemd services."""
        pass

    @systemd_group.command(name="add")
    async def add_service(self, ctx, service_name: str):
        """Add a service to the monitoring list."""
        async with self.config.services() as services:
            if service_name in services:
                await ctx.send(f"⚠️ `{service_name}` is already in the list.")
                return
            services.append(service_name)

        await ctx.send(f"✅ Added `{service_name}`. The panel will update shortly.")
        await self.update_dashboard()

    @systemd_group.command(name="remove")
    async def remove_service(self, ctx, service_name: str):
        """Remove a service from the monitoring list."""
        async with self.config.services() as services:
            if service_name not in services:
                await ctx.send(f"⚠️ `{service_name}` is not in the list.")
                return
            services.remove(service_name)

        await ctx.send(f"🗑️ Removed `{service_name}`.")
        await self.update_dashboard()

    @systemd_group.command(name="allow")
    async def allow_user(self, ctx, user: discord.User):
        """Authorize a user to control services via the panel."""
        async with self.config.authorized_users() as users:
            if user.id in users:
                await ctx.send(f"⚠️ {user.mention} is already authorized.")
                return
            users.append(user.id)
        await ctx.send(f"✅ {user.mention} can now manage the services.")

    @systemd_group.command(name="deny")
    async def deny_user(self, ctx, user: discord.User):
        """Revoke a user's permission to control services."""
        async with self.config.authorized_users() as users:
            if user.id not in users:
                await ctx.send(f"⚠️ {user.mention} is not in the authorized list.")
                return
            users.remove(user.id)
        await ctx.send(f"🗑️ Revoked access for {user.mention}.")

    @systemd_group.command(name="allowed")
    async def list_allowed(self, ctx):
        """List users authorized to control services."""
        users = await self.config.authorized_users()
        if not users:
            await ctx.send("No extra users authorized. (Owner always has access.)")
            return
        lines = []
        for uid in users:
            member = ctx.guild.get_member(uid) if ctx.guild else None
            lines.append(f"• {member.mention if member else f'`{uid}`'}")
        await ctx.send("**Authorized users:**\n" + "\n".join(lines))

    @systemd_group.command(name="panel")
    async def spawn_panel(self, ctx, channel: discord.TextChannel = None):
        """Spawn the persistent Systemd panel."""
        target_channel = channel or ctx.channel

        embed = await self.get_dashboard_embed()
        # Initialize options
        self.view.children[0].options = await self.view.generate_options()

        msg = await target_channel.send(embed=embed, view=self.view)

        # Save location to config
        await self.config.dashboard_channel.set(target_channel.id)
        await self.config.dashboard_message.set(msg.id)

        if target_channel != ctx.channel:
            await ctx.send(f"✅ Panel created in {target_channel.mention}")