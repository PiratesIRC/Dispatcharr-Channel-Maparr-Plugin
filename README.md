# Channel Mapparr
A Dispatcharr plugin that standardizes broadcast (OTA) and premium/cable channel names using network data and curated channel lists. It supports multiple country databases and offers advanced organization features. [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)

## Features
* **Multi-Country Support**: Load channel databases for multiple regions, including US, UK, CA, AU, BR, DE, ES, FR, IN, and MX.
* **Category-Based Organization**: Automatically move channels into specific groups based on their content category (e.g., News, Sports, Entertainment).
* **Customizable OTA Formatting**: Use tags like `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}` to format broadcast channel names.
* **Configurable Fuzzy Matching**: Adjust the matching sensitivity threshold to fine-tune accuracy for channel name variations.
* **Automatic Update Checking**: Checks for plugin updates on GitHub and notifies via the settings UI.
* **Intelligent Matching Logic**: Handles regional indicators, quality tags, and country suffixes while preventing false positives.
* **Configurable Ignored Tags**: Define a custom list of tags to be removed from channel names before matching.
* **Logo Management**: Bulk apply default logos to channels without artwork.
* **CSV Export**: Preview renaming and grouping changes before applying them with detailed dry-run reports.
* **Group Filtering**: Target specific channel groups for processing or organization.
* **Performance Optimization**: Features API token caching and WebSocket frontend refreshes for efficient operation.

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* Internet access (for version checking and initial database loading)

## Installation
1.  Log in to Dispatcharr's web UI.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the plugin zip file.
4.  Enable the plugin after installation.

## Updating the Plugin
To update Channel Mapparr from a previous version:

### 1. Remove Old Version
1.  Navigate to **Plugins** in Dispatcharr.
2.  Click the trash icon next to the old Channel Mapparr plugin.
3.  Confirm deletion.

### 2. Restart Dispatcharr
1.  Log out of Dispatcharr.
2.  Restart the Docker container:
    ```bash
    docker restart dispatcharr
    ```

### 3. Install New Version
1.  Log back into Dispatcharr.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the new plugin zip file.
4.  Enable the plugin after installation.

### 4. Verify Installation
1.  Check that the new version number appears in the plugin list.
2.  Reconfigure your settings if needed.
3.  Run **Load/Process Channels** to test the update.

## Settings Reference

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://127.0.0.1:9191`). |
| **Dispatcharr Admin Username**| `string` | - | Username for API authentication. |
| **Dispatcharr Admin Password**| `password`| - | Password for API authentication. |
| **Channel Databases** | `string` | `US` | Comma-separated country codes (e.g., `US, UK, CA`). |
| **Fuzzy Match Threshold** | `number` | `85` | Minimum similarity score (0-100) for matching. Higher values require closer matches. |
| **Channel Groups to Process** | `string` | - | Comma-separated group names for renaming operations. Empty = all groups. |
| **Channel Groups for Category Organization** | `string` | - | Comma-separated group names for category sorting. Empty = all groups. |
| **OTA Channel Name Format** | `string` | `{NETWORK} - {STATE} {CITY} ({CALLSIGN})` | Format template for OTA channels. Available tags: `{NETWORK}`, `{STATE}`, `{CITY}`, `{CALLSIGN}`. |
| **Ignored Tags** | `string` | `[4K], [FHD], ...` | Comma-separated list of tags to remove before matching (handles `[]` and `()`). |
| **Suffix for Unknown Channels**| `string` | ` [Unk]` | Suffix to append to unmatched channels. |
| **Default Logo** | `string` | - | Logo display name from Dispatcharr's logo manager to apply to channels without logos. |

## Usage Guide

### Renaming Channels
1.  **Configure Settings**: Set your Dispatcharr credentials, select your **Channel Databases** (e.g., `US, UK`), and adjust the **Fuzzy Match Threshold** if needed.
2.  **Load and Process**: Run **Load/Process Channels**. This loads the selected databases and attempts to match your current channels.
3.  **Preview**: Run **Preview Changes (Dry Run)** to generate a CSV report of proposed name changes.
4.  **Apply**: Run **Rename Channels** to apply the standardized names.

### Organizing by Category
1.  **Configure Groups**: Optionally set **Channel Groups for Category Organization** to limit which channels are moved.
2.  **Preview**: Run **Category Groups Dry Run**. This exports a CSV showing which channels will be moved and if new groups (e.g., "News", "Sports") need to be created.
3.  **Apply**: Run **Organize Channels by Category** to move channels into their respective category groups.

### Managing Logos and Unknowns
* **Apply Default Logos**: If you have channels missing artwork, configure a **Default Logo** name and run this action to fill in the gaps.
* **Unknown Channels**: Use **Add Suffix to Unknown Channels** to tag channels that could not be matched against the loaded databases.

## Action Reference

| Action | Description |
| :--- | :--- |
| **Load/Process Channels** | Load channels from API and match against selected country databases. |
| **Preview Changes (Dry Run)** | Export a CSV showing proposed renames and match sources. |
| **Rename Channels** | Apply standardized names to matched channels. |
| **Add Suffix to Unknown Channels**| Tag unmatched channels with the configured suffix. |
| **Apply Default Logos** | Bulk assign a logo to channels without artwork. |
| **Category Groups Dry Run** | Export a CSV showing proposed channel moves based on categories. |
| **Organize Channels by Category** | Move channels to groups based on their category (creates groups if needed). |
| **Clear CSV Exports** | Delete all CSV export files created by the plugin. |

## File Locations
* **Processing Cache**: `/data/channel_mapparr_loaded_channels.json`
* **Version Cache**: `/data/channel_mapparr_version_check.json`
* **Exports**: `/data/exports/` (CSV previews and reports)

## CSV Export Format
The plugin generates CSVs for both renaming and categorization previews.

**Renaming Preview:**
* **dbase**: Indicates the source of the match (e.g., `Broadcast (OTA)`, `Premium/Cable`).
* **match_method**: Details the specific logic used (e.g., `Callsign Match`, `Fuzzy Match - score: 92`).

**Category Preview:**
* **New Group**: The target group based on the channel's category.
* **Group Exists**: Indicates if the plugin needs to create a new group via the API.

## Performance Notes
* **Token Caching**: API tokens are cached for 30 minutes to reduce authentication overhead.
* **WebSocket Updates**: The plugin triggers a WebSocket frontend refresh upon completion, ensuring the UI updates immediately without a full page reload.
* **Bulk Operations**: Channel updates are performed in bulk to minimize API calls.

## Troubleshooting
* **"Logo not found"**: Ensure you are using the logo's *display name* from the Dispatcharr Logos page, not the filename.
* **"No match found"**: Try lowering the **Fuzzy Match Threshold** slightly (e.g., to 75 or 80) if channels are being skipped due to minor spelling differences.
* **Database Loading Errors**: Ensure the `Channel Databases` setting uses valid 2-letter country codes (e.g., `US`, `UK`).
* **Update Checks**: If the version check fails, ensure your container has internet access to reach GitHub.

## License
This plugin integrates with Dispatcharr's plugin system and follows its licensing terms.
