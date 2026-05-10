# Channel Mapparr
A Dispatcharr plugin that standardizes broadcast (OTA) and premium/cable channel names using network data and curated channel lists. It supports multiple country databases and offers advanced organization features. 

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/02-channel-mapparr/)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)

## Features
* **Multi-Country Support**: Load channel databases for AU, BR, CA, DE, ES, FR, IN, MX, NL, UK, and US (42,800+ channels total).
* **M3U Stream Import**: Create channels from M3U streams with automatic category-based organization. Runs in background with progress tracking.
* **Category-Based Organization**: Automatically move channels into specific groups based on their content category (e.g., News, Sports, Entertainment).
* **Customizable OTA Formatting**: Use tags like `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}` to format broadcast channel names.
* **High-Performance Fuzzy Matching**: Token-based candidate pre-filtering with `rapidfuzz` integration matches 19K streams against 31K channels in seconds.
* **Match Sensitivity Presets**: Select from Relaxed (70), Normal (80), Strict (90), or Exact (95) sensitivity levels.
* **False-Positive Guards**: Length-scaled thresholds and token overlap checks prevent incorrect matches on short or similar names.
* **Normalization Caching**: Pre-computed normalizations avoid redundant processing across matching loops.
* **Configurable Ignored Tags**: Define a custom list of tags to be removed from channel names before matching.
* **Logo Management**: Bulk apply default logos to channels without artwork.
* **CSV Export**: Preview renaming, categorization, and import changes with detailed dry-run reports.
* **Background Threading**: Long-running operations (M3U import, organize) run in background threads with progress tracking via WebSocket.
* **Atomic File Writes**: CSV exports use temp files with atomic rename to prevent corrupt partial writes.
* **Rate Limiting**: Configurable delay between database writes during large imports (None/Low/Medium/High).

## Requirements
* Dispatcharr v0.20.0+
* Internet access (for version checking)

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
| **Channel Databases** | `string` | `US` | Comma-separated country codes (AU, BR, CA, DE, ES, FR, IN, MX, NL, UK, US). |
| **Match Sensitivity** | `select` | `normal` | Relaxed (70), Normal (80), Strict (90), Exact (95). |
| **Channel Groups to Process** | `string` | - | Comma-separated group names for renaming operations. Empty = all groups. |
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

## Recommended Action Order

The action buttons are listed in the recommended execution order:

1. **Validate Settings** - Check DB connectivity and settings.
2. **Load & Process Channels** - Scan groups and determine standardized names.
3. **Rename Channels** - Apply names (or CSV preview in Dry Run).
4. **Tag Unknown Channels** - Append suffix to unmatched channels.
5. **Apply Logos** - Assign default logo to channels without one.
6. **Organize by Category** - Move channels into category groups (or CSV preview).
7. **Import M3U Streams** - Create channels from M3U streams (or CSV preview).
8. **Clear CSV Exports** - Delete all plugin CSV files.

Rename before Import ensures duplicate detection is accurate (standardized names prevent duplicates).

## Performance

Channel Mapparr uses several optimization layers for fast matching:

1. **Exact lookup** (O(1) hash) - catches most real matches instantly.
2. **Normalized lookup** (O(1) hash) - matches after stripping tags, prefixes, and noise.
3. **Token-indexed fuzzy matching** - inverted index reduces candidates from 31K to ~50-200 before fuzzy comparison.
4. **`rapidfuzz` C extension** - 10-100x faster than pure-Python Levenshtein when available.
5. **Early termination** - skips impossible matches via length pre-check and row-level abort.

Benchmark: 19,147 streams matched against 31,621 channels in **6 seconds**.

## File Locations
* **Processing Cache**: `/data/channel_mapparr_loaded_channels.json`
* **Version Cache**: `/data/channel_mapparr_version_check.json`
* **Import Results**: `/data/channel_mapparr_m3u_import_results.json`
* **Exports**: `/data/exports/` (CSV previews and reports)

## Troubleshooting
* **"Logo not found"**: Ensure you are using the logo's *display name* from the Dispatcharr Logos page, not the filename.
* **"No match found"**: Try lowering the Match Sensitivity to Normal or Relaxed if channels are being skipped.
* **Database Loading Errors**: Ensure the `Channel Databases` setting uses valid 2-letter country codes (e.g., `US`, `UK`).
* **Slow matching**: Install `rapidfuzz` in your Dispatcharr container for 10-100x faster fuzzy matching. Check logs for "Using rapidfuzz" vs "Using built-in Levenshtein".
* **Worker timeout on Organize**: Ensure you're running v1.26.1001200+ which runs organize in a background thread.

## License
This plugin integrates with Dispatcharr's plugin system and follows its licensing terms.
