# Channel Mapparr
A Dispatcharr plugin that standardizes US broadcast (OTA) and premium/cable channel names using FCC network data and curated channel lists.

## Features
* **Dual Database Matching**: Automatically identifies channels using both `networks.json` (OTA) and `channels.txt` (premium/cable).
* **Customizable OTA Formatting**: Use tags like `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}` to format broadcast channel names.
* **Intelligent Fuzzy Matching**: Handles channel name variations, regional indicators (East/West), quality tags (`[HD]`/`[FHD]`/`[SD]`), and country suffixes.
* **Logo Management**: Bulk apply default logos to channels without artwork.
* **CSV Export**: Preview changes before applying with detailed dry-run reports.
* **Group Filtering**: Target specific channel groups or process all channels.
* **Unknown Channel Tagging**: Mark unmatched channels with configurable suffixes.

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* `networks.json` file (FCC broadcast station database)
* `channels.txt` file (premium/cable channel list)

## Installation
1.  Log in to Dispatcharr's web UI.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the plugin zip file.
4.  Place `networks.json` and `channels.txt` in the plugin directory.
5.  Enable the plugin after installation.

## Settings Reference

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://127.0.0.1:9191`). |
| **Dispatcharr Admin Username**| `string` | - | Username for API authentication. |
| **Dispatcharr Admin Password**| `password`| - | Password for API authentication. |
| **Channel Groups** | `string` | - | Comma-separated group names, empty = all groups. |
| **OTA Channel Name Format** | `string` | `{NETWORK} - {STATE} {CITY} ({CALLSIGN})` | Format template for OTA channels. Available tags: `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}`. |
| **Suffix for Unknown Channels**| `string` | ` [Unk]` | Suffix to append to unmatched channels (OTA and premium/cable). |
| **Default Logo** | `string` | - | Logo display name from Dispatcharr's logo manager to apply to channels without logos. |

## Usage Guide

### Step-by-Step Workflow
1.  **Configure Authentication**
    * Enter your Dispatcharr URL, username, and password.
    * Optionally specify **Channel Groups** (leave empty to process all).
    * Customize OTA format and unknown channel suffix.
    * Click **Save Settings**.
2.  **Load and Process Channels**
    * Click **Run** on `Load/Process Channels`.
    * The plugin attempts to match channels using `networks.json` first, then `channels.txt`.
    * Review the summary showing OTA vs. premium/cable matches.
3.  **Preview Changes (Dry Run)**
    * Click **Run** on `Preview Changes (Dry Run)`.
    * Exports a CSV to `/data/exports/channel_mapparr_preview_YYYYMMDD_HHMMSS.csv`.
    * Review proposed changes with database source indicators.
4.  **Apply Changes**
    * Click **Run** on `Rename Channels` to standardize names.
    * A rename report is saved to `/data/exports/channel_mapparr_renamed_YYYYMMDD_HHMMSS.csv`.
    * Optionally run `Add Suffix to Unknown Channels` for unmatched channels.
5.  **Apply Logos (Optional)**
    * Configure **Default Logo** with a display name from the logo manager.
    * Click **Run** on `Apply Default Logos`.
    * This only applies to channels without existing logos.

## Channel Matching Logic

### OTA Channels (`networks.json`)
* Extracts callsigns from channel names (patterns like `(KABC)`, `WXYZ`, etc.).
* Looks up station data in the FCC database.
* Formats using a customizable template with network, location, and callsign.
* Skips channels with "WEST" or "EAST" alone (not valid callsigns).

### Premium/Cable Channels (`channels.txt`)
* Two-stage fuzzy matching process:
    * High-confidence match (97%+ similarity after normalization).
    * Number variation handling (matches "HBO 2" → "HBO2").
* Preserves regional indicators: `(East)`, `(West)`.
* Preserves quality tags: `[HD]`, `[FHD]`, `[SD]`, `[Slow]`.
* Preserves extra tags: `(CX)`, `(USA)`, etc.
* Removes "USA" suffix except for "USA Network".
* Ignores bracketed content and quality indicators during matching.

## Example Transformations

### OTA Channels
* **Before:** `ABC 7 New York WABC`
* **After:** `ABC - NY New York (WABC)`

### Premium/Cable Channels
* **Before:** `HBO 2 (East) [HD]`
* **After:** `HBO2 (East) [HD]`
<br>
* **Before:** `Cinemax Moviemax East [SD]`
* **After:** `MovieMax (East) [SD]`
<br>
* **Before:** `5 Star Max USA [FHD]`
* **After:** `5StarMax [FHD]`

## Action Reference

### Core Actions
| Action | Description |
| :--- | :--- |
| **Load/Process Channels** | Load channels from groups and match against databases. |
| **Preview Changes (Dry Run)** | Export a CSV showing proposed renames with database sources. |
| **Rename Channels** | Apply standardized names to matched channels. |
| **Add Suffix to Unknown Channels**| Tag unmatched channels with the configured suffix. |
| **Apply Default Logos** | Bulk assign a logo to channels without artwork. |

## File Locations
* **Processing Cache**: `/data/channel_mapparr_loaded_channels.json`
* **Preview Export**: `/data/exports/channel_mapparr_preview_YYYYMMDD_HHMMSS.csv`
* **Rename Report**: `/data/exports/channel_mapparr_renamed_YYYYMMDD_HHMMSS.csv`
* **Plugin Files**:
    * `/data/plugins/channel_mapparr/plugin.py`
    * `/data/plugins/channel_mapparr/networks.json`
    * `/data/plugins/channel_mapparr/channels.txt`

## CSV Export Format

| Column | Description |
| :--- | :--- |
| **channel_id** | Internal Dispatcharr channel ID. |
| **channel_number** | Channel number. |
| **channel_group** | Current group name. |
| **current_name** | Original channel name. |
| **new_name** | Proposed/applied name. |
| **status** | `Renamed` or `Skipped`. |
| **dbase** | Database used: `networks.json`, `channels.txt`, or `none`. |
| **reason** | Skip reason if applicable. |

## Logo Management
The plugin uses Dispatcharr's logo manager **display names** (not filenames):

1.  Navigate to **Logos** in the Dispatcharr UI.
2.  Find your desired default logo.
3.  Copy the **display name** (e.g., `abc-logo-2013-garnet-us`).
4.  Enter it in the plugin's **Default Logo** setting.
5.  Run the **Apply Default Logos** action.

**Note:** Logos are only applied to channels that either have no logo or use the "Default" logo (ID 0).

## Troubleshooting

### Common Issues
* **"Logo not found in logo manager"**
    * Use the logo's **display name** from Dispatcharr's Logos page, not the filename.
    * Check logs for available logo names (the first 30 are listed).
    * Ensure the logo has been uploaded to Dispatcharr.
* **"No suffix configured"**
    * If the default value is not working, manually enter ` [Unk]` (with a leading space) in settings.
    * Save settings before running actions.
* **"No groups found"**
    * Verify channel groups exist in Dispatcharr.
    * Check that group names are spelled correctly (case-sensitive).
    * Leave the field empty to process all groups.
* **Channels not matching**
    * Verify `networks.json` and `channels.txt` are in the plugin directory.
    * Check logs for callsign extraction and matching attempts.
    * Use the **Preview** export to see which database was attempted.

### Debugging Commands
```bash
# Check plugin files
docker exec dispatcharr ls -la /data/plugins/channel_mapparr/

# Monitor plugin activity
docker logs dispatcharr | grep -i channel_mapparr

# View processing cache
docker exec dispatcharr cat /data/channel_mapparr_loaded_channels.json

## Data Sources

### `networks.json` Format
An FCC broadcast station database with fields:
* `callsign`: Station identifier (e.g., KABC-TV)
* `community_served_city`: City name
* `community_served_state`: Two-letter state code
* `network_affiliation`: Network name(s)
* `tv_virtual_channel`: Virtual channel number
* `facility_id`: FCC facility identifier

### `channels.txt` Format
A plain text file with one channel name per line:
HBO
HBO2
Cinemax
5StarMax
MovieMax

## Performance Notes
* Matching is sequential, not parallel.
* Pagination support for large logo collections (2500+ logos).
* API bulk operations for efficient channel updates.
* Automatic M3U refresh triggers GUI updates.

## Version History
**v0.1 (Initial Release)**
* Dual database matching (OTA + premium/cable).
* Customizable OTA name formatting.
* Fuzzy matching with regional/quality tag preservation.
* Logo management integration.
* CSV export with database source tracking.

## Contributing
When reporting issues, please include:
* Dispatcharr version
* Sample channel names that fail to match
* Relevant container logs
* Contents of the preview CSV (first 10 rows)

## License
This plugin integrates with Dispatcharr's plugin system and follows its licensing terms.
