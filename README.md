# Malvinarum's RedBot Cogs 

A repository containing various utility cogs for the Red Discord Bot.

## Cogs in this Repository

### plexactivity

Monitors a Plex Media Server and posts notifications to a Discord channel when users start watching new content.

### rssfeed

An RSS/Atom feed reader to post updates from websites, blogs, or news sources directly into a Discord channel.

### systemmonitor

Provides commands to check the system status of the host machine running the bot, such as CPU, memory, and disk usage.

### xfeed

A dedicated cog for retrieving and posting content from a specific content source or social media platform (e.g., formerly Twitter/X) to a Discord channel.

## 🚀 Installation

To install any of these cogs, you need to have Red Discord Bot V3 running.

The entire process involves three main steps. Replace [p] with your bot's custom prefix (e.g., !, ?, or r:).

### Step 1: Add the Repository

You must first load the downloader cog and then add this repository to Red's list of sources.

Load the Downloader cog (if not already loaded):

[p]load downloader


Add the malvincogs repository:

[p]repo add malvincogs https://github.com/malvinarum/malvincogs/


You'll need to confirm that you trust the repository to continue.

### Step 2: Install the Cog

Replace [cog] with the name of the cog you want to install (e.g., plexactivity).

[p]cog install malvincogs [cog]


Example (to install plexactivity):

[p]cog install malvincogs plexactivity


### Step 3: Load the Cog

After installation, you need to load the cog to activate its commands.

[p]load [cog]


Example (to load plexactivity):

[p]load plexactivity


## ❓ Usage and Configuration

Once the cog is loaded, you can view the full list of commands and configuration options by using Red's built-in help command:

[p]help [cog]


### Example:

[p]help plexactivity


The help menu will detail the commands needed to set up API keys, select channels, and fully configure the cog's features
