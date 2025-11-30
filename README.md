# Malvin & Gemini Inc. Cogs 🚀

Welcome to the "Enterprise Grade" utility cogs for Red Discord Bot! Built for power users running home labs, game servers, and media empires. Basically, cool stuff for your server! 😎

## 📦 The Cogs

### 🦖 PalworldWatch (`palworldwatch`)

Your mission control for Palworld! Keep an eye on your server stats like a hawk.

* **Live Telemetry:** Watch FPS, CPU, and RAM usage in real-time. No more guessing!
* **Population:** See who's online and their levels.
* **Visuals:** Custom images for when the server is up or down. Fancy!

### 🎬 Plex Activity (`plexactivity`)

The ultimate "Now Playing" dashboard. Flex your media library!

* **Rich Metadata:** Grabs movie posters from TMDB and audiobook covers from Google Books/iTunes. Looking good!
* **Tech Specs:** Nerd stats! See transcoding status, bitrate, and player device.
* **Context:** It knows the difference between "Watching" a movie and "Listening" to an audiobook. Smart!
* **Multi-User:** Tracks everyone streaming at once.

### 📥 Torrents Watch (`torrentswatch`)

A set-it-and-forget-it download monitor.

* **Integrations:** Hooks right into **qBittorrent** (the real MVP) or Sonarr/Radarr.
* **Live Stats:** Download speeds, progress bars, and ETAs. All the good stuff.
* **Deduplication:** Filters out duplicate entries so your list stays clean.

### 🖥️ System Monitor (`systemmonitor`)

Check your server's pulse at a glance.

* **Hardware:** Keeps tabs on CPU temps, load averages, and RAM. Don't let it melt!
* **Process Hogs:** Find out what's eating all your resources.
* **Network:** Real-time upload/download tracking.

### 📰 RSS Feed (`rssfeed`)

A smarter news ticker for your server.

* **Filtering:** Only see what you want with `include`/`exclude` keywords.
* **Rich Media:** Pulls images for beautiful embeds. No more wall of text!
* **Social:** Estimates reading time and can even ping roles.

### 🐦 XFeed (`xfeed`)

Track social media without breaking the bank (or API limits).

* **Budget Mode:** Smart polling to stay within free tier limits. Phew!
* **Filters:** Ignore the noise with keyword filtering.

## 🚀 Installation

To get these running, you'll need [Red Discord Bot V3](https://docs.discord.red/en/stable/).

### Step 1: Add the Repository

First things first, load the downloader and add the repo:

[p]load downloader
[p]repo add malvincogs https://github.com/malvinarum/malvincogs/

### Step 2: Install a Cog

Pick the cog you want and install it:

[p]cog install malvincogs <cog_name>

*Example:*
[p]cog install malvincogs plexactivity

### Step 3: Load and Configure

Load it up and follow the setup instructions!

[p]load plexactivity
[p]plex setup

*Maintained by Malvin. Powered by Python & Sarcasm.*