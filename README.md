# Malvinarum's Redbot Cogs 🚀

Welcome to the "Enterprise Grade" utility cogs for Red Discord Bot! Built for power users running home labs, game servers, and media empires. Basically, cool stuff for your server! 😎

## 📦 The Cogs

### 🎮 Draper Bundle (`draperbundle`)
**New!** A massive suite of community and gaming utilities created by [DraperSniper](https://github.com/Drapersniper), (updated & maintained by Malvinarum).

* **Gaming Profiles (`[p]gprofile`):** Let users set up profiles with their region, timezone, and linked game accounts (Steam, Battle.net, etc.). Auto-manages roles based on region!
* **PC Specs (`[p]specs`):** Allow members to flex their rigs. Tracks CPU, GPU, RAM, and more.
* **Live Status (`[p]splaying`):** See exactly who is playing what game, watching movies, or listening to Spotify across your server in real-time.
* **Dynamic Voice (`[p]dynamicset`):** Create voice channels that automatically spawn new rooms when they get full.
* **Game Stats (`[p]gstats`):** Retrieve player statistics for supported games (e.g., Battlefield V).

### 🐳 Docker Manager (`dockermanager`)
**New!** Manage your Docker containers without leaving Discord.

![Docker Manager Panel](https://github.com/user-attachments/assets/aee20dcb-eaa5-484d-ac3b-ea4c080ea257)

* **Interactive Panel:** A persistent dashboard showing container status (🟢 Running / 🔴 Stopped).
* **Control:** Start, Stop, and Restart containers via dropdowns and buttons.
* **Secure:** Locked to the bot owner to prevent unauthorized access.
* **Req:** Requires `pip install docker` on the host machine and access to the docker socket.

### 🐧 Systemd Manager (`systemdmanager`)
**New!** Control Linux services directly from chat. Perfect for managing game servers or web backends.

![Systemd Manager Panel](https://github.com/user-attachments/assets/9fdb87ed-ee40-4e88-a44e-3acdcae4071f)

* **Service Control:** Start, Stop, and Restart services (e.g., `nginx`, `plexmediaserver`) via a persistent panel.
* **Dynamic Config:** Add services to the monitoring list on the fly (`[p]systemd add <service>`).
* **Real-time Status:** Visual indicators for Active/Failed/Inactive states.
* **Req:** Bot user requires `sudo` (NOPASSWD) privileges for `/bin/systemctl`.

### 📡 Stream Sentry (`streamsentry`)
**New!** Automated promotion and archiving for your community's content creators.

* **Go-Live Alerts:** Auto-posts stream cards when whitelisted members go live.
* **Live Role:** Automatically assigns and removes a "Now Live" role.
* **Clip Vault:** Detects Twitch/Medal/YouTube clips in chat and archives them to a dedicated channel.

### 🦖 PalworldWatch (`palworldwatch`)
Your mission control for Palworld! Keep an eye on your server stats like a hawk.

![Palworld Watch](https://github.com/user-attachments/assets/4b68288e-294c-47ff-b0a8-1c8ea8354d2c)

* **Live Telemetry:** Watch FPS, CPU, and RAM usage in real-time via the REST API and process monitoring.
* **Population:** See who's online and their levels.
* **Visuals:** Custom images for when the server is up or down.

### 🎬 Plex Activity (`plexactivity`)
The ultimate "Now Playing" dashboard. Flex your media library!

![Plex Activity](https://github.com/user-attachments/assets/642babb5-889e-4f4d-b2ab-510fdcf0620d)

* **Rich Metadata:** Grabs movie posters from TMDB and audiobook covers from Google Books.
* **Tech Specs:** Nerd stats! See transcoding status, bitrate, and player device.
* **Context:** Distinguishes between "Watching" a movie, "Listening" to an audiobook, or binging a TV show.
* **Multi-User:** Tracks everyone streaming at once with user mapping support.

### 📥 Torrents Watch (`torrentswatch`)
A set-it-and-forget-it download monitor for **qBittorrent**.

![Torrents Watch](https://github.com/user-attachments/assets/e1960704-6286-4611-9406-dddbc9ddd96e)

* **Live Stats:** Real-time download/upload speeds, progress bars, and ETAs.
* **Smart Sorting:** Prioritizes active downloads and errors so you see what matters.
* **Auth Support:** Handles cookie-based authentication for newer qBittorrent versions.

### 🖥️ System Monitor (`systemmonitor`)
Check your server's pulse at a glance.

![System Monitor](https://github.com/user-attachments/assets/fee81530-25c7-4e1d-a0fb-2d11b7d4cbf1)

* **Hardware:** Keeps tabs on CPU temps, load averages, and RAM usage.
* **Process Hogs:** Identifies top processes consuming your resources.
* **Network:** Real-time upload/download bandwidth tracking.

### 📰 RSS Feed (`rssfeed`)
A smarter news ticker for your server.

* **Filtering:** Only see what you want with `include`/`exclude` keywords.
* **Rich Media:** Aggressively hunts for images to create beautiful embeds.
* **Social:** Estimates reading time and can ping specific roles on new posts.

### 🐦 XFeed (`xfeed`)
Track social media without breaking the bank (or API limits).

* **Budget Mode:** Smart polling designed to stay within the strict Free Tier API limits.
* **Filters:** Ignore the noise with keyword filtering before posts reach your channel.

---

## 🚀 Installation

To get these running, you'll need [Red Discord Bot V3](https://docs.discord.red/en/stable/).

### Step 1: Add the Repository

Load the downloader and add the repo:
[p]repo add malvincogs https://github.com/malvinarum/malvincogs

### Step 2: Install a Cog

Pick the cog you want and install it.
*Note: `draperbundle` installs multiple cogs at once.*
[p]cog install malvincogs <cog_name>

### Step 3: Load and Configure

Load the cog and check its specific setup command.

**Common Setup Commands:**
* `[p]dockerpanel` (Spawns Docker Control)
* `[p]systemd panel` (Spawns Systemd Control)
* `[p]streamset` (Stream Sentry Config)
* `[p]pw setup` (Palworld)
* `[p]tw setup` (Torrents)
* `[p]sysmon setchannel` (System Monitor)
* `[p]rss add` (RSS Feed)
* `[p]gprofile setup` (Draper Bundle Profiles)

---
*Maintained by Malvinarum.*