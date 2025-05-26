# SystemMonitor Cog

The **SystemMonitor** cog is a Redbot cog that monitors your system’s CPU, memory, disk, and network usage in real-time. It displays these stats as an embedded message in a designated Discord channel, updating automatically every minute. The cog uses [psutil](https://github.com/giampaolo/psutil) to fetch system metrics, and makes use of Redbot's configuration system to persist your channel settings.

## Features

- **Real-Time System Monitoring**:  
  Tracks CPU usage, memory consumption, aggregated disk usage across all mounted partitions, and network speeds (upload/download in Mbps).

- **Persistent Display**:  
  Updates a single embedded message in a configured Discord channel to keep your system information accessible and uncluttered.

- **Dynamic Channel Setting**:  
  Easily change the channel where updates are posted using the `systemmonitorset` command.

- **Manual Reporting**:  
  Invoke the system report on demand with the `system` command.

## Installation
[p]repo add malvincogs https://github.com/malvinarum/malvincogs/
[p]cog install malvincogs systemmonitor
[p]load systemmonitor

## Usage
[p]systemmonitorset #channel -- (for autoupdate message)
[p]system -- to manually trigger systemusage message (in the same channel command is run)
