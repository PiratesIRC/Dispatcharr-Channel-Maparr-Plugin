# Channel Mapparr
A Dispatcharr plugin that standardizes broadcast (OTA) and premium/cable channel names using network data and curated channel lists. It supports multiple country databases and offers advanced organization features. 

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/02-channel-mapparr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/PiratesIRC)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)

## Features

**Naming**
* **Broadcast (OTA) station matching** for United States local affiliates, by callsign, against a bundled 1,915-station table published by the Federal Communications Commission. `ABC 5 (WEWS) CLEVELAND HD` becomes `ABC - OH Cleveland (WEWS)`. Subchannels resolve to their own network, and callsigns that are also ordinary English words are handled without false positives.
* **Customizable name format** using `{NETWORK}`, `{STATE}`, `{CITY}` and `{CALLSIGN}`.
* **Curated channel databases** for 12 countries, about 42,900 channels: AU, BR, CA, DE, ES, FR, IN, MX, NL, NO, UK, US.
* **Suffix for unmatched channels**, so what did not match is easy to find later.

**Matching**
* **A four-stage pipeline**: a 200-entry alias map, then exact, then substring, then fuzzy token matching. The alias stage resolves common variants such as `FNC` to `Fox News Channel` without any scoring.
* **Four sensitivity presets**, from Relaxed to Exact.
* **False-positive guards** that stop `BBC One` matching `BBC Two`, `ESPN 1` matching `ESPN 2`, and `ABC News` matching `BBC News`.
* **Provider-junk normalization**: stylized Unicode, emoji used as letters, resolution markers, bouquet tags and country prefixes are all removed before matching. Plain ASCII names are left untouched.
* **Non-Latin script support**: Cyrillic, CJK and Arabic names are preserved rather than reduced to nothing.
* **Fast**: token-based pre-filtering matches 19,000 streams against 31,000 channels in seconds.

**Organizing**
* **Category-based grouping**: move channels into groups by content category.
* **M3U stream import**: create channels from streams, organized by category, in the background.
* **Scoping**: process only named groups, or exclude groups with wildcard patterns. A pattern matching nothing refuses the run rather than silently processing everything.
* **Logos**: one default logo in bulk, or per-channel artwork fuzzy-matched against the [tv-logo/tv-logos](https://github.com/tv-logo/tv-logos) repository.

**Seeing what happened**
* **Dry Run Mode**: every mutating action writes a CSV of what it would have done, and changes nothing.
* **Show Status**: live progress and estimated time for the running or most recent operation.
* **Emailed reports**: optionally email each run as an HTML page and/or a CSV, delivered by the [Newsflasharr](https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin) plugin. Off by default. **The emailed report never contains your M3U source names**, which the CSV exports do carry in their settings header, and channel names are additionally scrubbed of M3U account names and IP addresses. The HTML page opens as an index with its table in a collapsed section, and sorts by clicking a column heading. Newsflasharr's own Show Status action shows how many reports this plugin has built.

See the **[user guide](docs/USER-GUIDE.md)** for how to use these, and for every setting.

### The emailed report

![An emailed Channel Mapparr rename preview: a summary card showing plugin version, generation time, databases loaded, dry-run state, match sensitivity and row count, above a sortable table of channel numbers, groups, current and new names, status and match method](docs/images/emailed-report.jpg)

*Sample data. Click any column heading to sort by it; numeric columns are compared as numbers, so
channel 18 sorts before 31 rather than after 102. The plugin settings that name your M3U sources
are deliberately absent from this page.*

**This screenshot predates v1.26.2170831 and shows the older layout.** The page now opens with the
logo beside the title and the table inside a section headed **Results** that starts collapsed, so
you click that heading to see the rows. The content of the table is unchanged.

## Requirements
* Dispatcharr v0.20.0+
* Internet access, only for **Apply Per-Channel Logos (tv-logos)**, which fetches the logo file list from GitHub. Every other action works fully offline; the plugin no longer checks for its own updates.
* The [Newsflasharr](https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin) plugin, only if you want **emailed reports**. It is what actually sends the mail. Channel Mapparr does not require it: with Newsflasharr absent or disabled, nothing is sent and nothing fails.

## Installation
1. Log in to Dispatcharr's web UI.
2. Navigate to **Plugins**.
3. Click **Import Plugin** and upload the plugin zip file.
4. Enable the plugin after installation.

Upgrading from an earlier version has its own short procedure: see
[Updating the plugin](docs/USER-GUIDE.md#updating-the-plugin).

## Documentation

| Document | What it covers |
| :--- | :--- |
| **[User guide](docs/USER-GUIDE.md)** | Step-by-step walkthroughs: a first run, scoping which channels are touched, how OTA names are built, and setting up emailed reports end to end. |
| **[Changelog](docs/CHANGELOG.md)** | What changed between versions, and why. |
| **[Development notes](docs/DEVELOPMENT.md)** | How the plugin is put together and how to run the tests. |
| **[Open work](docs/TODO.md)** | Known gaps and planned improvements. |

## Disclaimer

**Channel Mapparr provides no television content of any kind.** It supplies no channels, no
playlists, no streams, no electronic programme guide data and no provider accounts, and it
contains no list of where to obtain any of those. It renames and organizes channel entries that
already exist in **your** Dispatcharr installation, matching them against bundled reference data:
curated per-country channel lists and a table of United States broadcast station callsigns
published by the Federal Communications Commission.

The plugin never contacts a media provider. It never opens, fetches, decodes, records, restreams
or redistributes any stream. It reads stream *names* in order to match them, and never the streams
themselves. The only outbound connection it makes is to GitHub, and only when you run **Apply
Per-Channel Logos**, which fetches a public list of logo filenames. Emailed reports, if you enable
them, are handed to the Newsflasharr plugin, which sends them to destinations **you** configured.

**You are responsible for what you connect Dispatcharr to.** Whether a particular provider,
subscription, playlist or stream is lawful for you to use depends on your agreement with that
provider and on the law where you live. Use only sources you are authorised to use. Nothing in this
project is intended to enable, encourage or assist access to content you have no right to access.

All product names, channel names, network names, callsigns, trademarks and registered trademarks
mentioned in this project or appearing in its examples or bundled reference data are the property
of their respective owners. This project is an independent, community-built plugin. It is not
affiliated with, endorsed by, or sponsored by any television network, broadcaster, streaming
service or IPTV provider, and it is not affiliated with the Dispatcharr project beyond being a
plugin written for it.

## License
This plugin integrates with Dispatcharr's plugin system and follows its licensing terms.
