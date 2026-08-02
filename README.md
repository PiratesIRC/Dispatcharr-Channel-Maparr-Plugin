# Channel Mapparr
A Dispatcharr plugin that standardizes broadcast (OTA) and premium/cable channel names using network data and curated channel lists. It supports multiple country databases and offers advanced organization features. 

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/02-channel-mapparr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)

## Features
* **Multi-Country Support**: Load channel databases for AU, BR, CA, DE, ES, FR, IN, MX, NL, NO, UK, and US (42,900+ channels total), plus a bundled 1,915-station US FCC table (`networks.json`) for OTA callsign matching.
* **Alias Stage 0 Matching** (v1.26.1430910+): A 200+ entry curated alias map matches common variant names (e.g. `FNC` → `Fox News Channel`, `CSPAN 2` → `C-SPAN2`, `CA: RDS` → `Réseau des Sports (RDS) HD`) in O(1) before fuzzy stages run. Faster, more reviewable, and safer than fuzzy scoring on noisy provider strings. Users can extend with custom aliases.
* **Per-Channel Logos from tv-logos** (v1.26.1430910+): The **Apply Per-Channel Logos** action fuzzy-matches each channel name against the [tv-logo/tv-logos](https://github.com/tv-logo/tv-logos) GitHub repo and assigns per-channel artwork in bulk. Uses your selected country codes; channels with an existing logo are skipped.
* **Show Status Action** (v1.26.1430910+): A persistent progress file lets the **Show Status** button report live progress and ETA for any running or recently finished operation, without watching container logs.
* **M3U Stream Import**: Create channels from M3U streams with automatic category-based organization. Runs in background with progress tracking.
* **Category-Based Organization**: Automatically move channels into specific groups based on their content category (e.g., News, Sports, Entertainment).
* **OTA Broadcast Matching** (v1.26.1701952+): US local-affiliate streams (`ABC 5 (WEWS) CLEVELAND HD`, `FOX (KTVU)`) are matched by callsign against a bundled 1,915-station FCC table (`networks.json`) and renamed to the configured format, e.g. `ABC - OH Cleveland (WEWS)`, `FOX - CA Oakland (KTVU)`. The network is taken from the stream's own label when stated, so subchannels resolve correctly (`CBS 7 (WBBJ-DT3) → CBS - TN Jackson (WBBJ)`), and parenthesized callsigns that are also English words (`(KING)`, `(WOOD)`, `(WAVE)`) are recognized when they map to a real station.
* **Customizable OTA Formatting**: Use tags like `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}` to format broadcast channel names.
* **High-Performance Fuzzy Matching**: Token-based candidate pre-filtering with `rapidfuzz` integration matches 19K streams against 31K channels in seconds.
* **Match Sensitivity Presets**: Select from Relaxed (70), Normal (80), Strict (90), or Exact (95) sensitivity levels.
* **Advanced False-Positive Guards** (v1.26.1430910+): Subset, divergent-token, and numeric-sibling guards prevent `BBC One` matching `BBC Two`, `Sky Cinema Disney` matching `Sky Cinema Decades`, and `ABC News` matching `BBC News`. Trailing-number anchoring rejects `ESPN 1` vs `ESPN 2` confusion.
* **Smart Normalization** (v1.26.1430910+): CamelCase splitting (`JusticeCentral.TV` → `Justice Central TV`), number-word folding (`BBC Three` ↔ `BBC 3`), East/West zone preservation (`Cartoon Network (W)` → `Cartoon Network West`), and multi-token country-prefix stripping (`CA FR: RDS` → `RDS`).
* **Provider-Junk Normalization** (v1.26.1650854+): Strips stylized-Unicode decoration (superscript / small-capital tier markers), maps emoji used as letters (`beIN SP⚽RTS` → `SPORTS`), and removes numeric resolution markers (`720p`/`3840P`) before matching — so noisy IPTV stream names normalize to the same form as clean channel-database names. ASCII names are guaranteed untouched. Also handles **box-bar bouquet tags and delimiters** (`┃`, `│`): a leading `┃…┃` provider tag is stripped (`┃CANAL+┃ NPO 1` → `NPO 1`) and box bars are recognized as country/provider-prefix delimiters.
* **Non-Latin Script Support**: Names written in Cyrillic, CJK, or Arabic are preserved through normalization (NFKD-folded and kept by Unicode alphanumeric class) instead of being erased to an empty string, so they compare on their own characters rather than falsely matching everything.
* **Callsign Denylist** (v1.26.1430910+): A 50-word denylist of K/W-shaped English words (WITH, WATCH, WWE, KING, ...) prevents false callsign extraction from program titles like "Bizarre Foods *with* Andrew Zimmern". A denylisted word in parentheses is still accepted when it maps to a real station (v1.26.1701952+), so genuine `(KING)`/`(WOOD)`/`(WAVE)` affiliates match while prose like "King of the Hill" does not.
* **Per-Channel Help Text** (v1.26.1430910+): Every settings field carries a one-sentence explanation visible in the Dispatcharr UI.
* **Normalization Caching**: Pre-computed normalizations avoid redundant processing across matching loops.
* **Configurable Ignored Tags**: Define a custom list of tags to be removed from channel names before matching.
* **Default-Logo Action**: Bulk apply a single default logo to channels without artwork.
* **CSV Export**: Preview renaming, categorization, and import changes with detailed dry-run reports.
* **Background Threading**: Long-running operations (M3U import, organize) run in background threads with progress tracking via WebSocket.
* **Atomic File Writes**: CSV exports use temp files with atomic rename to prevent corrupt partial writes.
* **Rate Limiting**: Configurable delay between database writes during large imports (None/Low/Medium/High).
* **Emailed Reports** (v1.26.2141433+): Optionally email a report of each run, as an HTML page and/or a CSV, delivered by the [Newsflasharr](https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin) plugin. **The emailed report is built separately from the CSV exports and never contains your M3U source names**, which the exports in `/data/exports` do carry in their settings header. Channel and stream names are additionally scrubbed of M3U account names and IP addresses, and the scrub fails closed: if the account lookup fails, no report is built rather than one sent unredacted. The HTML table sorts by clicking a column heading, with numeric columns compared as numbers. Off by default; a separate **Email Report Now** button sends one on demand and refuses, visibly, when the mail could not actually arrive.

### The emailed report

![An emailed Channel Mapparr rename preview: a summary card showing plugin version, generation time, databases loaded, dry-run state, match sensitivity and row count, above a sortable table of channel numbers, groups, current and new names, status and match method](docs/images/emailed-report.jpg)

*Sample data. Click any column heading to sort by it; numeric columns are compared as numbers, so
channel 18 sorts before 31 rather than after 102. The plugin settings that name your M3U sources
are deliberately absent from this page.*

## Requirements
* Dispatcharr v0.20.0+
* Internet access, only for **Apply Per-Channel Logos (tv-logos)**, which fetches the logo file list from GitHub. Every other action works fully offline; the plugin no longer checks for its own updates.
* The [Newsflasharr](https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin) plugin, only if you want **emailed reports**. It is what actually sends the mail. Channel Mapparr does not require it: with Newsflasharr absent or disabled, nothing is sent and nothing fails.

## Installation
1. Log in to Dispatcharr's web UI.
2. Navigate to **Plugins**.
3. Click **Import Plugin** and upload the plugin zip file.
4. Enable the plugin after installation.

## Updating the Plugin
To update Channel Mapparr from a previous version:

### 1. Remove Old Version
1. Navigate to **Plugins** in Dispatcharr.
2. Click the trash icon next to the old Channel Mapparr plugin.
3. Confirm deletion.

### 2. Restart Dispatcharr
```bash
docker restart dispatcharr
```

### 3. Install New Version
1. Log back into Dispatcharr.
2. Navigate to **Plugins**.
3. Click **Import Plugin** and upload the new plugin zip file.
4. Enable the plugin after installation.

### 4. Verify Installation
1. Check that the new version number appears in the plugin list.
2. Reconfigure your settings if needed.
3. Run **Validate Settings** to confirm everything is working.

## Settings Reference

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **Channel Databases** | `string` | `US` | Comma-separated country codes (AU, BR, CA, DE, ES, FR, IN, MX, NL, NO, UK, US). |
| **Match Sensitivity** | `select` | `normal` | Relaxed (70), Normal (80), Strict (90), Exact (95). |
| **Channel Groups to Process** | `string` | - | Comma-separated group names for renaming operations. Empty = all groups. Use "Channel Groups to Ignore" to exclude instead. |
| **Channel Groups to Ignore** | `string` | - | Comma-separated, supports `*` and `?` wildcards, case-insensitive. Channels in these groups are excluded from renaming, tagging, logos and Organize by Category, regardless of "Channel Groups to Process". Organize skips a target group that is ignored; Import M3U Streams refuses to run if its destination group is ignored. Does not apply to Import's stream matching or duplicate detection. A typo that matches no group refuses the run rather than silently processing everything. |
| **Channel Groups for Category Organization** | `string` | - | Comma-separated group names for category sorting. Empty = all groups. |
| **M3U Source** | `select` | `All sources` | Filter streams to a specific M3U account. |
| **M3U Group Filter** | `string` | - | Pre-match filter by M3U group-title. |
| **Category Filter** | `string` | - | Post-match filter by database category. |
| **Custom Import Group Name** | `string` | - | Override category-based group naming for imports. |
| **OTA Channel Name Format** | `string` | `{NETWORK} - {STATE} {CITY} ({CALLSIGN})` | Format template for broadcast channels. |
| **Suffix for Unknown Channels** | `string` | ` [Unk]` | Suffix to append to unmatched channels. |
| **Ignored Tags** | `string` | `[4K], [FHD], [HD], [SD], [Unknown], [Unk], [Slow], [Dead]` | Tags removed before matching (handles `[]` and `()`). |
| **Default Logo** | `string` | - | Logo display name from Dispatcharr's Logos page. |
| **Dry Run Mode** | `boolean` | `false` | Preview changes without modifying anything. |
| **Rate Limiting** | `select` | `None` | Delay between DB writes (None/Low/Medium/High). |
| **Send notifications to Newsflasharr** | `boolean` | `false` | Master switch for emailed reports. Requires the Newsflasharr plugin, which is what sends the mail. What routes where is configured in Newsflasharr's own routing rules, keyed on this plugin's name. |
| **Email A Report After** | `select` | `every_run` | `never`, or every run that produces an export. Organize by Category reports only in Dry Run, because a real run of it produces no export. Does nothing unless the switch above is on. |
| **Email Report Format** | `select` | `html` | `html`, `csv`, or `both`. A notification carries one attachment, so `both` sends two emails per run rather than one email with two files. Both files are written to `/data/channel_mapparr_reports` either way; this only decides which are emailed. |

## Recommended Action Order

The action buttons are listed in the recommended execution order:

1. **Validate Settings** - Check DB connectivity and settings.
2. **Load & Process Channels** - Scan groups and determine standardized names.
3. **Rename Channels** - Apply names (or CSV preview in Dry Run).
4. **Tag Unknown Channels** - Append suffix to unmatched channels.
5. **Apply Default Logo** - Assign one default logo to all channels without one.
6. **Apply Per-Channel Logos (tv-logos)** - Fuzzy-match each channel to the [tv-logo/tv-logos](https://github.com/tv-logo/tv-logos) repo and assign individual artwork.
7. **Organize by Category** - Move channels into category groups (or CSV preview).
8. **Import M3U Streams** - Create channels from M3U streams (or CSV preview).
9. **Show Status** - Display live progress / ETA for the most recent operation (no destructive effect).
10. **Email Report Now** - Build a report from the last processed channels and queue it for email (no destructive effect). It refuses, visibly, when Newsflasharr is absent, disabled, missing its email settings, missing a routing rule for this plugin, or when its collector is not running. Queued means written to Newsflasharr's queue, not yet in your inbox.
11. **Clear CSV Exports** - Delete all plugin CSV files. It cannot reach the emailed reports, which live in a different directory.

Rename before Import ensures duplicate detection is accurate (standardized names prevent duplicates). The two logo actions are independent — use Default Logo for a fast fallback, or Per-Channel Logos for individualized artwork.

"Channel Groups to Ignore" (v1.26.2071409+) is a scope setting, not a step of its own; it applies across all eleven actions above wherever they read or write channel groups (it does not affect Import M3U Streams' stream matching or duplicate detection). Set it once before running Validate Settings, which reports the resolved exclusion.

## Match Pipeline

Each stream is run through four stages in order — the first stage that produces a confident match wins:

| Stage | Method | When it fires | Why |
|---|---|---|---|
| **0** | **Alias** | Normalized stream name is a key in the curated alias map | O(1), highest confidence — short-circuits fuzzy entirely for known variants. |
| **1** | **Exact / very-high similarity** (≥97%) | After normalization, stream and candidate are identical or within a few characters | Catches near-perfect matches instantly with token-overlap guard against `ABC News` vs `BBC News`. |
| **2** | **Substring** | One string contains the other AND lengths within 75% | Handles prefixed/suffixed variants like `US: HBO HD` ↔ `HBO`. |
| **3** | **Fuzzy token-sort** | Levenshtein after token normalization, length-scaled threshold + subset/divergent/numeric guards | Catches reordered words and minor spelling differences. |

If all four miss, the stream is reported as unmatched (and tagged with the configured suffix if you run **Tag Unknown Channels**).

## Performance

Channel Mapparr uses several optimization layers for fast matching:

1. **Alias index** (O(1) hash) - 200+ curated variant→canonical mappings checked before fuzzy.
2. **Exact lookup** (O(1) hash) - catches near-identical matches instantly.
3. **Normalized lookup** (O(1) hash) - matches after stripping tags, prefixes, and noise.
4. **Token-indexed fuzzy matching** - inverted index reduces candidates from 31K to ~50-200 before fuzzy comparison.
5. **`rapidfuzz` C extension** - 10-100x faster than pure-Python Levenshtein when available.
6. **Early termination** - skips impossible matches via length pre-check and row-level abort.
7. **tv-logos filelist cache** - per-session cache on the GitHub fetch so re-running per-channel logo assignment doesn't repeat the API call.

Benchmark: 19,147 streams matched against 31,621 channels in **6 seconds**.

## File Locations
* **Processing Cache**: `/data/channel_mapparr_loaded_channels.json`
* **Import Results**: `/data/channel_mapparr_m3u_import_results.json`
* **Progress File** (v1.26.1430910+): `/data/channel_mapparr_progress.json` — read by the **Show Status** action.
* **Exports**: `/data/exports/` (CSV previews). These carry a settings header naming your configured M3U sources, and are never emailed.
* **Emailed Reports** (v1.26.2141433+): `/data/channel_mapparr_reports/` — the HTML and CSV files built for sending. The newest 8 of each are kept, and a file younger than 40 minutes is never deleted, because a delivery retry re-reads the attachment from disk.

The plugin no longer writes a version cache: the self-update check was removed in v1.26.2071908 because it ran on Dispatcharr's per-request hot path. If `/data/channel_mapparr_version_check.json` exists on your installation it is left over from an older build and can be deleted.

## Troubleshooting
* **"Logo not found"**: Ensure you are using the logo's *display name* from the Dispatcharr Logos page, not the filename.
* **"No match found"**: Try lowering the Match Sensitivity to Normal or Relaxed if channels are being skipped. If a specific channel is repeatedly mismatched, consider adding it to `aliases.py` so it hits Stage 0 instead.
* **Database Loading Errors**: Ensure the `Channel Databases` setting uses valid 2-letter country codes (e.g., `US`, `UK`).
* **Slow matching**: Install `rapidfuzz` in your Dispatcharr container for 10-100x faster fuzzy matching. Check logs for "Using rapidfuzz" vs "Using built-in Levenshtein".
* **Worker timeout on Organize**: Ensure you're running v1.26.1001200+ which runs organize in a background thread.
* **Per-channel logos returns "No file lists could be fetched"**: GitHub anonymous API is rate-limited to 60 req/hr/IP. If you've run other GitHub tooling recently you may need to wait an hour, or set up an authenticated proxy.
* **Show Status reports "no operation has run yet"** even after running an action: ensure `/data/` is writable by the Dispatcharr `dispatch` user. The plugin writes `/data/channel_mapparr_progress.json` on every action tick.
* **No report email arrives**: run **Validate Settings**, which reports the reason as a warning. The usual cause is that Newsflasharr has no routing rule sending this plugin to email, in which case the report is delivered to whatever Newsflasharr's default channel is, and without its attachment, because attachments are email-only. Add a rule matching source `channel-mapparr` and event `usage_report`. Reload Newsflasharr's settings page before editing it: a stale browser tab re-posts its old form state and silently reverts rules.
* **The email arrives with no attachment**: the report exceeded Newsflasharr's 1 MB attachment limit, or the file was missing when delivery was attempted. Newsflasharr records this and still sends the message. Large runs are capped at 2000 rows and the report says so at the top, naming the complete export file.
* **Sorting does nothing in the HTML report**: mail clients strip scripts, so sorting works when you save the attachment and open it in a browser. Every row is present either way; only the reordering is lost.
* **Emailed reports stop after a plugin update**: the master switch is `Send notifications to Newsflasharr`, and it is off by default. Confirm it is still on.

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
