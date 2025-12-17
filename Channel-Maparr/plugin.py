"""
Channel Mapparr Plugin
Standardizes US broadcast (OTA) and premium/cable channel names.
"""

import logging
import csv
import os
import re
import requests
import json
import time
import urllib.request
import urllib.error
from datetime import datetime
from glob import glob

# Import the fuzzy matcher module
from .fuzzy_matcher import FuzzyMatcher

# Django model imports
from apps.channels.models import Channel

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.channel_mapparr")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

# Plugin name prefix for all log messages
PLUGIN_LOG_PREFIX = "[Channel Mapparr]"

class Plugin:
    """Channel Mapparr Plugin"""

    name = "Channel Mapparr"
    version = "0.5.1"
    description = "Standardizes US broadcast (OTA) and premium/cable channel names using network data and channel lists."

    # ========================================
    # CONFIGURATION DEFAULTS
    # ========================================
    # Modify these values to change plugin defaults

    # Channel Database Settings
    DEFAULT_CHANNEL_DATABASES = "US"

    # Fuzzy Matching Settings
    DEFAULT_FUZZY_MATCH_THRESHOLD = 85  # Minimum similarity score (0-100)
    INITIAL_MATCH_THRESHOLD = 85  # Used during initialization, overridden by settings

    # Channel Naming Settings
    DEFAULT_OTA_FORMAT = "{NETWORK} - {STATE} {CITY} ({CALLSIGN})"
    DEFAULT_UNKNOWN_SUFFIX = " [Unk]"
    DEFAULT_IGNORED_TAGS = "[4K], [FHD], [HD], [SD], [Unknown], [Unk], [Slow], [Dead]"

    # API Token Cache Settings
    TOKEN_CACHE_DURATION = 1800  # 30 minutes in seconds

    # File Paths
    RESULTS_FILE = "/data/channel_mapparr_loaded_channels.json"
    VERSION_CHECK_FILE = "/data/channel_mapparr_version_check.json"
    EXPORT_DIR = "/data/exports"

    # ========================================

    # Settings rendered by UI
    @property
    def fields(self):
        """Dynamically generate fields list with version check"""
        # Check for updates from GitHub
        version_message = "Checking for updates..."
        try:
            # Check if we should perform a version check (once per day)
            if self._should_check_for_updates():
                # Perform the version check
                latest_version = self._get_latest_version("PiratesIRC", "Dispatcharr-Channel-Maparr-Plugin")

                # Check if it's an error message
                if latest_version.startswith("Error"):
                    version_message = f"⚠️ Could not check for updates: {latest_version}"
                else:
                    # Save the check result
                    self._save_version_check(latest_version)

                    # Compare versions
                    current = self.version
                    # Remove 'v' prefix if present in latest_version
                    latest_clean = latest_version.lstrip('v')

                    if current == latest_clean:
                        version_message = f"✅ You are up to date (v{current})"
                    else:
                        version_message = f"🔔 Update available! Current: v{current} → Latest: {latest_version}"
            else:
                # Use cached version info
                if self.cached_version_info:
                    latest_version = self.cached_version_info['latest_version']
                    current = self.version
                    latest_clean = latest_version.lstrip('v')

                    if current == latest_clean:
                        version_message = f"✅ You are up to date (v{current})"
                    else:
                        version_message = f"🔔 Update available! Current: v{current} → Latest: {latest_version}"
                else:
                    version_message = "ℹ️ Version check will run on next page load"
        except Exception as e:
            LOGGER.debug(f"{PLUGIN_LOG_PREFIX} Error during version check: {e}")
            version_message = f"⚠️ Error checking for updates: {str(e)}"

        # Build the fields list dynamically
        return [
            {
                "id": "version_status",
                "label": "📦 Plugin Version Status",
                "type": "info",
                "help_text": version_message
            },
            {
                "id": "dispatcharr_url",
                "label": "🌐 Dispatcharr URL",
                "type": "string",
                "default": "",
                "placeholder": "http://192.168.1.10:9191",
                "help_text": "URL of your Dispatcharr instance (from your browser's address bar). Example: http://127.0.0.1:9191",
            },
        {
            "id": "dispatcharr_username",
            "label": "👤 Dispatcharr Admin Username",
            "type": "string",
            "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "dispatcharr_password",
            "label": "🔑 Dispatcharr Admin Password",
            "type": "string",
            "input_type": "password",
            "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "channel_databases",
            "label": "📚 Channel Databases (comma-separated country codes)",
            "type": "string",
            "default": self.DEFAULT_CHANNEL_DATABASES,
            "placeholder": "US, UK, CA, AU",
            "help_text": "Select which channel databases to load. Available: AU (Australia, v2025-11-10), BR (Brazil, v2025-11-11), CA (Canada, v2025-11-10), DE (Germany, v2025-11-10), ES (Spain, v2025-11-10), FR (France, v2025-11-25), IN (India, v2025-11-10), MX (Mexico, v2025-11-10), UK (United Kingdom, v2025-11-10), US (United States, v2025-10-30). Example: US, UK, CA",
        },
        {
            "id": "fuzzy_match_threshold",
            "label": "🎯 Fuzzy Match Threshold",
            "type": "number",
            "default": self.DEFAULT_FUZZY_MATCH_THRESHOLD,
            "placeholder": str(self.DEFAULT_FUZZY_MATCH_THRESHOLD),
            "help_text": f"Minimum similarity score (0-100) for fuzzy matching. Higher values require closer matches. Default: {self.DEFAULT_FUZZY_MATCH_THRESHOLD}",
        },
        {
            "id": "selected_groups",
            "label": "📂 Channel Groups to Process (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "Locals, News, Entertainment",
            "help_text": "Apply renaming and logo actions only to specific channel groups. Leave empty to apply to all groups.",
        },
        {
            "id": "category_groups",
            "label": "📁 Channel Groups for Category Organization (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "Locals, News, Entertainment",
            "help_text": "Source groups for category-based organization. Channels in these groups will be moved to new groups based on their category in channels.json. Leave empty to apply to all groups.",
        },
        {
            "id": "ota_format",
            "label": "📺 OTA Channel Name Format",
            "type": "string",
            "default": self.DEFAULT_OTA_FORMAT,
            "placeholder": self.DEFAULT_OTA_FORMAT,
            "help_text": "Format for OTA channel names. Available tags: {NETWORK}, {STATE}, {CITY}, {CALLSIGN}. Channels missing required fields will be skipped.",
        },
        {
            "id": "unknown_suffix",
            "label": "🏷️ Suffix for Unknown Channels",
            "type": "string",
            "default": self.DEFAULT_UNKNOWN_SUFFIX,
            "placeholder": self.DEFAULT_UNKNOWN_SUFFIX,
            "help_text": "Suffix to append to channels that cannot be matched (OTA and premium/cable). Leave empty for no suffix.",
        },
        {
            "id": "ignored_tags",
            "label": "🚫 Ignored Tags (comma-separated)",
            "type": "string",
            "default": self.DEFAULT_IGNORED_TAGS,
            "placeholder": self.DEFAULT_IGNORED_TAGS,
            "help_text": "Tags in brackets or parentheses to ignore/remove. Case-insensitive. Examples: [HD], (H), [4K]. Separate with commas.",
        },
        {
            "id": "default_logo",
            "label": "🖼️ Default Logo",
            "type": "string",
            "default": "",
            "placeholder": "abc-logo-2013-garnet-us",
            "help_text": "Logo display name from Dispatcharr's logo manager (not the filename). Find the exact name in Dispatcharr's Logos page. Leave empty to skip logo assignment.",
        },
        ]
    
    # Actions for Dispatcharr UI
    actions = [
        {
            "id": "load_and_process_channels",
            "label": "Load/Process Channels",
            "description": "Load channels from groups and determine new names",
        },
        {
            "id": "preview_changes",
            "label": "Preview Changes (Dry Run)",
            "description": "Export a CSV showing which channels would be renamed and their new names",
        },
        {
            "id": "rename_channels",
            "label": "Rename Channels",
            "description": "Apply the standardized names to channels",
            "confirm": { "required": True, "title": "Rename Channels?", "message": "This will rename channels to the standardized format. This action is irreversible. Continue?" }
        },
        {
            "id": "rename_unknown_channels",
            "label": "Add Suffix to Unknown Channels",
            "description": "Add suffix to channels that could not be matched (OTA and premium/cable)",
            "confirm": { "required": True, "title": "Rename Unknown Channels?", "message": "This will append the configured suffix to unmatched channels. Continue?" }
        },
        {
            "id": "apply_logos",
            "label": "Apply Default Logos",
            "description": "Apply default logo to channels without logos",
            "confirm": { "required": True, "title": "Apply Logos?", "message": "This will apply the default logo to channels that do not have a logo assigned. Continue?" }
        },
        {
            "id": "category_groups_dry_run",
            "label": "Category Groups Dry Run",
            "description": "Export a CSV showing which channels would be moved to which category-based groups",
        },
        {
            "id": "organize_by_category",
            "label": "Organize Channels by Category",
            "description": "Create groups based on category names and move matching channels to those groups",
            "confirm": { "required": True, "title": "Organize by Category?", "message": "This will create new groups (if needed) and move channels to category-based groups. Continue?" }
        },
        {
            "id": "clear_csv_exports",
            "label": "Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "confirm": { "required": True, "title": "Clear CSV Exports?", "message": "This will delete all CSV export files created by this plugin. Continue?" }
        },
    ]
    
    def __init__(self):
        self.loaded_channels = []
        self.processing_status = {"current": 0, "total": 0, "status": "idle", "start_time": None}
        self.results_file = self.RESULTS_FILE
        self.group_name_map = {}

        # API token cache state
        self.cached_api_token = None
        self.token_cache_time = None
        self.token_cache_duration = self.TOKEN_CACHE_DURATION

        # Version check cache state
        self.version_check_file = self.VERSION_CHECK_FILE
        self.cached_version_info = None

        # Initialize the fuzzy matcher (will load databases on first use)
        plugin_dir = os.path.dirname(__file__)
        self.matcher = FuzzyMatcher(plugin_dir=plugin_dir, match_threshold=self.INITIAL_MATCH_THRESHOLD, logger=LOGGER)

        LOGGER.info(f"{PLUGIN_LOG_PREFIX} {self.name} Plugin v{self.version} initialized")

        # Import version from fuzzy_matcher module
        try:
            from . import fuzzy_matcher
            LOGGER.info(f"{PLUGIN_LOG_PREFIX} Using fuzzy_matcher.py v{fuzzy_matcher.__version__}")
        except:
            LOGGER.info(f"{PLUGIN_LOG_PREFIX} Using fuzzy_matcher.py")

    def _get_latest_version(self, owner, repo):
        """
        Fetches the latest release tag name from GitHub using only Python's standard library.
        Returns the version string or an error message.
        """
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        # Add a user-agent to avoid potential 403 Forbidden errors
        headers = {
            'User-Agent': 'Dispatcharr-Plugin-Version-Checker'
        }

        try:
            # Create a request object with headers
            req = urllib.request.Request(url, headers=headers)

            # Make the request and open the URL with a timeout
            with urllib.request.urlopen(req, timeout=5) as response:
                # Read the response and decode it as UTF-8
                data = response.read().decode('utf-8')

                # Parse the JSON string
                json_data = json.loads(data)

                # Get the tag name
                latest_version = json_data.get("tag_name")

                if latest_version:
                    return latest_version
                else:
                    return "Error: 'tag_name' key not found."

        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:
                return f"Error: Repo not found or has no releases."
            else:
                return f"HTTP error: {http_err.code}"
        except Exception as e:
            # Catch other errors like timeouts
            return f"Error: {str(e)}"

    def _should_check_for_updates(self):
        """
        Check if we should perform a version check (once per day).
        Returns True if we should check, False otherwise.
        Also loads and caches the last check data.
        """
        try:
            if os.path.exists(self.version_check_file):
                with open(self.version_check_file, 'r') as f:
                    data = json.load(f)
                    last_check_time = data.get('last_check_time')
                    cached_latest_version = data.get('latest_version')

                    if last_check_time and cached_latest_version:
                        # Check if last check was within 24 hours
                        last_check_dt = datetime.fromisoformat(last_check_time)
                        now = datetime.now()
                        time_diff = now - last_check_dt

                        if time_diff.total_seconds() < 86400:  # 24 hours in seconds
                            # Use cached data
                            self.cached_version_info = {
                                'latest_version': cached_latest_version,
                                'last_check_time': last_check_time
                            }
                            return False  # Don't check again

            # Either file doesn't exist, or it's been more than 24 hours
            return True

        except Exception as e:
            LOGGER.debug(f"{PLUGIN_LOG_PREFIX} Error checking version check time: {e}")
            return True  # Check if there's an error

    def _save_version_check(self, latest_version):
        """Save the version check result to disk with timestamp"""
        try:
            data = {
                'latest_version': latest_version,
                'last_check_time': datetime.now().isoformat()
            }
            with open(self.version_check_file, 'w') as f:
                json.dump(data, f, indent=2)
            LOGGER.debug(f"{PLUGIN_LOG_PREFIX} Saved version check: {latest_version}")
        except Exception as e:
            LOGGER.debug(f"{PLUGIN_LOG_PREFIX} Error saving version check: {e}")

    def _generate_csv_settings_header(self, settings):
        """Generate CSV header comments with plugin settings (excluding credentials)"""
        # Exclude sensitive settings
        excluded_fields = {'dispatcharr_url', 'dispatcharr_username', 'dispatcharr_password'}

        # Map field IDs to their labels
        field_labels = {
            'channel_databases': 'Channel Databases',
            'fuzzy_match_threshold': 'Fuzzy Match Threshold',
            'selected_groups': 'Channel Groups to Process',
            'category_groups': 'Channel Groups for Category Organization',
            'ota_format': 'OTA Channel Name Format',
            'unknown_suffix': 'Suffix for Unknown Channels',
            'ignored_tags': 'Ignored Tags',
            'default_logo': 'Default Logo'
        }

        header_lines = []
        header_lines.append("# Channel Mapparr Plugin Settings")
        header_lines.append(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        header_lines.append(f"# Plugin Version: {self.version}")
        header_lines.append("#")

        # Add each setting
        for field_id, label in field_labels.items():
            if field_id not in excluded_fields:
                value = settings.get(field_id, '')
                if value:
                    header_lines.append(f"# {label}: {value}")
                else:
                    header_lines.append(f"# {label}: (not set)")

        header_lines.append("#")
        return '\n'.join(header_lines) + '\n'

    def _get_api_token(self, settings, logger):
        """Get an API access token using username and password with caching."""

        # 1. Check Cache
        if self.cached_api_token and self.token_cache_time:
            elapsed_time = time.time() - self.token_cache_time
            if elapsed_time < self.token_cache_duration:
                logger.info(f"{PLUGIN_LOG_PREFIX} Using cached API token (age: {int(elapsed_time)}s / {self.token_cache_duration}s)")
                return self.cached_api_token, None
            else:
                logger.info(f"{PLUGIN_LOG_PREFIX} Cached API token expired (age: {int(elapsed_time)}s), requesting new token")

        # 2. Prepare Request
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        username = settings.get("dispatcharr_username", "")
        password = settings.get("dispatcharr_password", "")

        if not all([dispatcharr_url, username, password]):
            return None, "Dispatcharr URL, Username, and Password must be configured in the plugin settings."

        try:
            url = f"{dispatcharr_url}/api/accounts/token/"
            payload = {"username": username, "password": password}

            logger.info(f"{PLUGIN_LOG_PREFIX} Attempting to authenticate with Dispatcharr at: {url}")

            # 3. Execute Request
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access")

            if not access_token:
                logger.error(f"{PLUGIN_LOG_PREFIX} No access token returned from API")
                return None, "Login successful, but no access token was returned by the API."

            # 4. Write to Cache
            self.cached_api_token = access_token
            self.token_cache_time = time.time()

            logger.info(f"{PLUGIN_LOG_PREFIX} Successfully obtained new API access token (cached for {self.token_cache_duration}s / {self.token_cache_duration // 60} minutes)")
            return access_token, None

        except requests.exceptions.RequestException as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Request error during authentication: {e}")
            return None, f"Network error occurred while authenticating: {e}"
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Unexpected error during authentication: {e}")
            return None, f"Unexpected error during authentication: {e}"

    def _get_api_data(self, endpoint, token, settings, logger, paginated=False):
        """Helper to perform GET requests to the Dispatcharr API with 401 handling."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

        try:
            all_results = []

            while url:
                response = requests.get(url, headers=headers, timeout=30)

                # 5. Invalidation Logic - Handle 401 Unauthorized
                if response.status_code == 401:
                    logger.error(f"{PLUGIN_LOG_PREFIX} API token expired or invalid (401 Unauthorized)")
                    # Invalidate cached token immediately
                    self.cached_api_token = None
                    self.token_cache_time = None
                    raise Exception("API authentication failed. Token may have expired. Please retry the action.")

                response.raise_for_status()
                json_data = response.json()

                # Handle paginated responses
                if isinstance(json_data, dict) and 'results' in json_data:
                    results = json_data.get('results', [])
                    all_results.extend(results)

                    # Check for next page
                    url = json_data.get('next') if paginated else None
                else:
                    # Non-paginated response
                    return json_data

            return all_results if paginated else all_results

        except requests.exceptions.RequestException as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} API request failed for {endpoint}: {e}")
            raise Exception(f"API request failed: {e}")

    def _patch_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform PATCH requests to the Dispatcharr API with 401 handling."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        try:
            response = requests.patch(url, headers=headers, json=payload, timeout=60)

            # Handle 401 Unauthorized
            if response.status_code == 401:
                logger.error(f"{PLUGIN_LOG_PREFIX} API token expired or invalid (401 Unauthorized)")
                # Invalidate cached token immediately
                self.cached_api_token = None
                self.token_cache_time = None
                raise Exception("API authentication failed. Token may have expired. Please retry the action.")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} API PATCH request failed for {endpoint}: {e}")
            raise Exception(f"API PATCH request failed: {e}")

    def _post_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform POST requests to the Dispatcharr API with 401 handling."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            # Handle 401 Unauthorized
            if response.status_code == 401:
                logger.error(f"{PLUGIN_LOG_PREFIX} API token expired or invalid (401 Unauthorized)")
                # Invalidate cached token immediately
                self.cached_api_token = None
                self.token_cache_time = None
                raise Exception("API authentication failed. Token may have expired. Please retry the action.")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} API POST request failed for {endpoint}: {e}")
            raise Exception(f"API POST request failed: {e}")

    def _trigger_frontend_refresh(self, settings, logger):
        """Trigger frontend channel list refresh via WebSocket"""
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            if channel_layer:
                # Send WebSocket message to trigger frontend refresh
                async_to_sync(channel_layer.group_send)(
                    "dispatcharr_updates",
                    {
                        "type": "channels.updated",
                        "message": "Channel visibility updated by Channel Mapparr"
                    }
                )
                logger.info(f"{PLUGIN_LOG_PREFIX} Frontend refresh triggered via WebSocket")
                return True
        except Exception as e:
            logger.warning(f"{PLUGIN_LOG_PREFIX} Could not trigger frontend refresh: {e}")
        return False

    def _load_channel_data(self, settings, logger):
        """Load channel data from selected country database files."""
        # Get selected country codes from settings
        channel_databases_str = settings.get("channel_databases", "US").strip()

        if not channel_databases_str:
            logger.warning(f"{PLUGIN_LOG_PREFIX} No channel databases selected, defaulting to US")
            channel_databases_str = "US"

        # Parse country codes
        country_codes = [code.strip().upper() for code in channel_databases_str.split(',') if code.strip()]

        if not country_codes:
            logger.error(f"{PLUGIN_LOG_PREFIX} Invalid channel_databases setting: '{channel_databases_str}'")
            return False

        # Get and apply fuzzy match threshold from settings
        fuzzy_threshold = settings.get("fuzzy_match_threshold", self.DEFAULT_FUZZY_MATCH_THRESHOLD)
        try:
            fuzzy_threshold = int(fuzzy_threshold)
            # Validate threshold is in range
            if fuzzy_threshold < 0 or fuzzy_threshold > 100:
                logger.warning(f"{PLUGIN_LOG_PREFIX} Invalid fuzzy match threshold {fuzzy_threshold}, using default {self.DEFAULT_FUZZY_MATCH_THRESHOLD}")
                fuzzy_threshold = self.DEFAULT_FUZZY_MATCH_THRESHOLD
        except (ValueError, TypeError):
            logger.warning(f"{PLUGIN_LOG_PREFIX} Invalid fuzzy match threshold format, using default {self.DEFAULT_FUZZY_MATCH_THRESHOLD}")
            fuzzy_threshold = self.DEFAULT_FUZZY_MATCH_THRESHOLD

        # Update matcher threshold
        self.matcher.match_threshold = fuzzy_threshold
        logger.info(f"{PLUGIN_LOG_PREFIX} Fuzzy match threshold set to: {fuzzy_threshold}")

        logger.info(f"{PLUGIN_LOG_PREFIX} Loading channel databases: {', '.join(country_codes)}")

        # Use fuzzy matcher to reload databases
        success = self.matcher.reload_databases(country_codes=country_codes)

        if success:
            logger.info(f"{PLUGIN_LOG_PREFIX} Successfully loaded {len(self.matcher.broadcast_channels)} broadcast and {len(self.matcher.premium_channels)} premium channels")
        else:
            logger.error(f"{PLUGIN_LOG_PREFIX} Failed to load channel databases")

        return success

    def _parse_network_affiliation(self, network_affiliation):
        """Extract first network from affiliation string, removing everything after comma or parenthesis."""
        if not network_affiliation:
            return None
        
        # Remove D<number>- prefix if present
        network_affiliation = re.sub(r'^D\d+-', '', network_affiliation)
        
        # Remove any callsign prefixes with D1, D2 pattern
        network_affiliation = re.sub(r'^[KW][A-Z]{3,4}(?:-(?:TV|CD|LP|DT|LD))?\s+D\d+\s*-\s*', '', network_affiliation)
        
        # Remove channel numbers and subchannel info
        network_affiliation = re.sub(r'^(.*?)\s+(?:CH\s+)?\d+(?:\.\d+)?(?:/.*)?$', r'\1', network_affiliation)
        
        # Remove any leading numbers and dots/spaces
        network_affiliation = re.sub(r'^\d+\.?\d*\s+', '', network_affiliation)
        
        # Take only first word/network before semicolon, slash, comma, or parenthesis
        network_affiliation = re.split(r'[;/,\(]', network_affiliation)[0].strip()
        
        # Remove "Television Network" or "TV Network" suffix
        network_affiliation = re.sub(r'\s+(?:Television\s+)?Network\s*$', '', network_affiliation, flags=re.IGNORECASE).strip()
        
        # Convert to uppercase
        network_affiliation = network_affiliation.upper()
        
        return network_affiliation if network_affiliation else None


    def _format_ota_name(self, station_data, format_string, callsign):
        """
        Format OTA channel name using the provided format string.
        Returns None if any required field is missing.
        """
        # Parse format string to find required fields
        required_fields = re.findall(r'\{(\w+)\}', format_string)

        # Get data from station
        network_raw = station_data.get('network_affiliation', '').strip()
        network = self._parse_network_affiliation(network_raw)
        city = station_data.get('community_served_city', '').title()
        state = station_data.get('community_served_state', '').upper()
        display_callsign = self.matcher.normalize_callsign(callsign)

        # Build replacement map
        replacements = {
            'NETWORK': network,
            'CITY': city,
            'STATE': state,
            'CALLSIGN': display_callsign
        }

        # Check if all required fields have values
        for field in required_fields:
            if field not in replacements or not replacements[field]:
                return None

        # Replace all placeholders
        result = format_string
        for field, value in replacements.items():
            result = result.replace(f'{{{field}}}', value)

        return result

    def run(self, action, params, context):
        """Main plugin entry point"""
        LOGGER.info(f"{self.name} run called with action: {action}")
        
        try:
            settings = context.get("settings", {})
            logger = context.get("logger", LOGGER)
            
            action_map = {
                "load_and_process_channels": self.load_and_process_channels_action,
                "preview_changes": self.preview_changes_action,
                "rename_channels": self.rename_channels_action,
                "rename_unknown_channels": self.rename_unknown_channels_action,
                "apply_logos": self.apply_logos_action,
                "category_groups_dry_run": self.category_groups_dry_run_action,
                "organize_by_category": self.organize_by_category_action,
                "clear_csv_exports": self.clear_csv_exports_action,
            }
            
            if action not in action_map:
                return {"status": "error", "message": f"Unknown action: {action}"}
            
            return action_map[action](settings, logger)
                
        except Exception as e:
            self.processing_status['status'] = 'idle'
            LOGGER.error(f"Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def load_and_process_channels_action(self, settings, logger):
        """Load channels from API and process them with channel data."""
        try:
            import json

            # Load channel data from selected country databases
            channels_loaded = self._load_channel_data(settings, logger)

            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded. Please check your channel_databases setting and ensure the files exist."}
            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}

            logger.info(f"{PLUGIN_LOG_PREFIX} Loading channels from Dispatcharr API...")
            
            # Get all groups first to build name-to-id mapping
            all_groups = self._get_api_data("/api/channels/groups/", token, settings, logger)
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}
            self.group_name_map = group_id_to_name
            
            # Filter by selected groups if specified
            selected_groups_str = settings.get("selected_groups", "").strip()
            if selected_groups_str:
                input_names = {name.strip() for name in selected_groups_str.split(',') if name.strip()}
                valid_names = {n for n in input_names if n in group_name_to_id}
                invalid_names = input_names - valid_names
                target_group_ids = {group_name_to_id[name] for name in valid_names}
                
                if not target_group_ids:
                    return {"status": "error", "message": f"None of the specified groups could be found: {', '.join(invalid_names)}"}
                
                logger.info(f"{PLUGIN_LOG_PREFIX} Target group IDs: {target_group_ids}")
            else:
                target_group_ids = set(group_name_to_id.values())
                valid_names = set(group_name_to_id.keys())
            
            # Fetch all channels and filter by group ID
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            
            channels_to_process = [
                ch for ch in all_channels 
                if ch.get('channel_group_id') in target_group_ids
            ]
            logger.info(f"{PLUGIN_LOG_PREFIX} Filtered to {len(channels_to_process)} channels in groups: {selected_groups_str if selected_groups_str else 'all groups'}")
            
            # Store channels with proper group names
            for channel in channels_to_process:
                group_id = channel.get('channel_group_id')
                channel['_group_name'] = group_id_to_name.get(group_id, 'No Group')
            
            self.loaded_channels = channels_to_process
            
            # Process channels
            logger.info(f"{PLUGIN_LOG_PREFIX} Processing {len(self.loaded_channels)} channels...")
            self.processing_status = {
                "current": 0,
                "total": len(self.loaded_channels),
                "status": "running",
                "start_time": datetime.now().isoformat()
            }
            
            renamed_channels = []
            skipped_channels = []
            ota_format = settings.get("ota_format", self.DEFAULT_OTA_FORMAT)

            # Parse ignored tags from settings
            ignored_tags_str = settings.get("ignored_tags", self.DEFAULT_IGNORED_TAGS)
            ignored_tags_list = [tag.strip() for tag in ignored_tags_str.split(',') if tag.strip()]
            
            # Also create versions with parentheses for tags that use brackets
            expanded_ignored_tags = []
            for tag in ignored_tags_list:
                expanded_ignored_tags.append(tag)
                # If tag is in brackets, also add parentheses version
                if tag.startswith('[') and tag.endswith(']'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"({inner})")
                # If tag is in parentheses, also add brackets version
                elif tag.startswith('(') and tag.endswith(')'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"[{inner}]")
            
            ignored_tags_list = expanded_ignored_tags
            
            # Track matching statistics
            debug_stats = {
                "ota_attempted": 0,
                "ota_matched": 0,
                "premium_attempted": 0,
                "premium_matched": 0,
                "skipped_empty_normalized": 0,
                "skipped_already_correct": 0,
                "skipped_no_match": 0
            }
            
            for i, channel in enumerate(self.loaded_channels):
                self.processing_status["current"] = i + 1
                
                # Periodic progress logging every 50 channels
                if (i + 1) % 50 == 0 or (i + 1) == len(self.loaded_channels):
                    logger.info(f"{PLUGIN_LOG_PREFIX} Processing progress: {i + 1}/{len(self.loaded_channels)} channels...")
                
                current_name = channel.get('name', '').strip()
                channel_id = channel.get('id')
                channel_number = channel.get('channel_number', '')
                group_id = channel.get('channel_group_id')
                group_name = channel.get('_group_name', 'No Group')
                
                new_name = None
                matcher_used = None
                skip_reason = None
                
                # Try OTA matching first (broadcast channels)
                ota_callsign_found = False
                match_method = None
                if self.matcher.broadcast_channels:
                    debug_stats["ota_attempted"] += 1
                    callsign, station = self.matcher.match_broadcast_channel(current_name)

                    if callsign:
                        ota_callsign_found = True

                        if station:
                            new_name = self._format_ota_name(station, ota_format, callsign)
                            if new_name:
                                matcher_used = "Broadcast (OTA)"
                                match_method = "OTA - Callsign Match"
                                debug_stats["ota_matched"] += 1
                            else:
                                skip_reason = "Missing required fields for OTA format"
                        else:
                            skip_reason = f"Callsign {callsign} not in channel databases"

                # If OTA match failed BUT a valid callsign was found, do NOT try premium matching
                # Only try premium matching if no callsign was found at all
                if not new_name and self.matcher.premium_channels and not ota_callsign_found:
                    debug_stats["premium_attempted"] += 1

                    # Extract tags to preserve them
                    regional, extra_tags, quality_tags = self.matcher.extract_tags(current_name, ignored_tags_list)

                    # Use fuzzy matcher to find best match
                    matched_premium, score, match_type = self.matcher.fuzzy_match(
                        current_name,
                        self.matcher.premium_channels,
                        ignored_tags_list
                    )

                    if matched_premium:
                        new_name = self.matcher.build_final_channel_name(matched_premium, regional, extra_tags, quality_tags)
                        matcher_used = "Premium/Cable"

                        # match_type contains detailed info like "fuzzy (92)", "exact", etc.
                        if match_type:
                            if "fuzzy" in str(match_type).lower():
                                match_method = f"Fuzzy Match - {match_type} (score: {score})"
                            elif match_type == "exact":
                                match_method = f"Exact Match (score: {score})"
                            else:
                                match_method = f"Premium - {match_type} (score: {score})"
                        else:
                            match_method = f"Premium Match (score: {score})"
                        debug_stats["premium_matched"] += 1
                        if not skip_reason:
                            skip_reason = None
                
                # Determine if this channel should be renamed or skipped
                if new_name and new_name != current_name:
                    renamed_channels.append({
                        'channel_id': channel_id,
                        'channel_number': channel_number,
                        'channel_group': group_name,
                        'current_name': current_name,
                        'new_name': new_name,
                        'status': 'Renamed',
                        'matcher': matcher_used,
                        'match_method': match_method,
                        'reason': ''
                    })
                else:
                    if new_name == current_name:
                        skip_reason = "Already in correct format"
                        debug_stats["skipped_already_correct"] += 1
                    elif not skip_reason:
                        skip_reason = "No match found in channels.json"
                        debug_stats["skipped_no_match"] += 1
                    
                    skipped_channels.append({
                        'channel_id': channel_id,
                        'channel_number': channel_number,
                        'channel_group': group_name,
                        'current_name': current_name,
                        'new_name': current_name,
                        'status': 'Skipped',
                        'matcher': 'none',
                        'match_method': 'No Match',
                        'reason': skip_reason
                    })
            
            self.processing_status['status'] = 'complete'
            
            # Log completion
            logger.info(f"{PLUGIN_LOG_PREFIX} Processing complete. {len(renamed_channels)} to rename, {len(skipped_channels)} skipped.")
            
            # Combine results
            all_results = renamed_channels + skipped_channels
            
            # Save processed results
            with open(self.results_file, 'w') as f:
                json.dump({
                    "processed_at": datetime.now().isoformat(),
                    "total_channels_loaded": len(self.loaded_channels),
                    "channels_to_rename": len(renamed_channels),
                    "channels_skipped": len(skipped_channels),
                    "debug_stats": debug_stats,
                    "changes": all_results
                }, f, indent=2)
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Processing complete. {len(renamed_channels)} to rename, {len(skipped_channels)} skipped.")
            
            # Build success message with summary
            message_parts = [
                f"✓ Successfully processed {len(self.loaded_channels)} channels.",
                f"\n**Summary:**",
                f"• Channels to rename: {len(renamed_channels)}",
                f"• Channels skipped: {len(skipped_channels)}",
                f"\n**Match Statistics:**",
                f"• OTA matches: {debug_stats['ota_matched']} / {debug_stats['ota_attempted']} attempted",
                f"• Premium matches: {debug_stats['premium_matched']} / {debug_stats['premium_attempted']} attempted",
                f"\nUse 'Preview Changes (Dry Run)' to export a CSV of the changes, or 'Rename Channels' to apply them."
            ]
            
            return {"status": "success", "message": "\n".join(message_parts)}
            
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error loading and processing channels: {e}")
            return {"status": "error", "message": f"Error loading and processing channels: {e}"}

    def preview_changes_action(self, settings, logger):
        """Export a CSV showing the preview of channel renaming changes."""
        try:
            import json
            
            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}
            
            with open(self.results_file, 'r') as f:
                data = json.load(f)
            
            all_changes = data.get('changes', [])
            
            if not all_changes:
                return {"status": "success", "message": "No changes to preview."}
            
            # Create export directory if it does not exist
            export_dir = self.EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)
            
            # Create timestamp for filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"channel_mapparr_preview_{timestamp}.csv"
            csv_path = os.path.join(export_dir, csv_filename)
            
            # Write CSV
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                # Write settings header as comments
                csvfile.write(self._generate_csv_settings_header(settings))

                fieldnames = ['Channel ID', 'Channel Number', 'Group', 'Current Name', 'New Name', 'Status', 'Matcher', 'Match Method', 'Reason']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()
                for change in all_changes:
                    writer.writerow({
                        'Channel ID': change.get('channel_id', ''),
                        'Channel Number': change.get('channel_number', ''),
                        'Group': change.get('channel_group', ''),
                        'Current Name': change.get('current_name', ''),
                        'New Name': change.get('new_name', ''),
                        'Status': change.get('status', ''),
                        'Matcher': change.get('matcher', ''),
                        'Match Method': change.get('match_method', ''),
                        'Reason': change.get('reason', '')
                    })
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Preview CSV exported to {csv_path}")
            
            renamed_count = sum(1 for c in all_changes if c.get('status') == 'Renamed')
            skipped_count = sum(1 for c in all_changes if c.get('status') == 'Skipped')
            
            return {
                "status": "success", 
                "message": f"✓ Preview exported to: {csv_filename}\n\n{renamed_count} channels will be renamed, {skipped_count} will be skipped."
            }
            
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error exporting preview: {e}")
            return {"status": "error", "message": f"Error exporting preview: {e}"}

    def rename_channels_action(self, settings, logger):
        """Apply the standardized names to channels."""
        try:
            import json
            
            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}
            
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            with open(self.results_file, 'r') as f:
                data = json.load(f)
            
            all_changes = data.get('changes', [])
            channels_to_rename = [c for c in all_changes if c.get('status') == 'Renamed']
            
            if not channels_to_rename:
                return {"status": "success", "message": "No channels need to be renamed."}
            
            # Create payload for bulk update
            payload = [{'id': ch['channel_id'], 'name': ch['new_name']} for ch in channels_to_rename]
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Renaming {len(payload)} channels...")
            self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            self._trigger_frontend_refresh(settings, logger)
            
            message_parts = [f"✓ Successfully renamed {len(payload)} channels."]
            if channels_to_rename:
                message_parts.append("\n**Sample Changes:**")
                for change in channels_to_rename[:5]:
                    message_parts.append(f"• '{change['current_name']}' → '{change['new_name']}'")
                if len(channels_to_rename) > 5:
                    message_parts.append(f"...and {len(channels_to_rename) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error renaming channels: {e}")
            return {"status": "error", "message": f"Error renaming channels: {e}"}

    def rename_unknown_channels_action(self, settings, logger):
        """Append suffix to channels that could not be matched (OTA and premium/cable)."""
        try:
            import json
            
            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}
            
            # Get suffix with default fallback matching the field default
            suffix = settings.get("unknown_suffix", self.DEFAULT_UNKNOWN_SUFFIX)
            
            # Log what we received
            logger.info(f"{PLUGIN_LOG_PREFIX} Suffix setting value: '{suffix}' (length: {len(suffix)})")
            
            # Only reject if suffix is None or empty after strip
            if not suffix or not suffix.strip():
                return {"status": "error", "message": "No suffix configured. Please set 'Suffix for Unknown Channels' in plugin settings. Default is ' [Unk]' (with leading space)."}
            
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            with open(self.results_file, 'r') as f:
                data = json.load(f)
            
            all_changes = data.get('changes', [])
            skipped_channels = [c for c in all_changes if c.get('status') == 'Skipped']
            
            if not skipped_channels:
                return {"status": "success", "message": "No unknown channels to rename."}
            
            # Create payload with suffix appended to current name
            payload = [{'id': ch['channel_id'], 'name': ch['current_name'] + suffix} for ch in skipped_channels]
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Adding suffix '{suffix}' to {len(payload)} unknown channels...")
            self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            self._trigger_frontend_refresh(settings, logger)
            
            message_parts = [f"✓ Successfully added suffix '{suffix}' to {len(payload)} unknown channels."]
            if skipped_channels:
                message_parts.append("\n**Sample Changes:**")
                for change in skipped_channels[:5]:
                    new_name = change['current_name'] + suffix
                    message_parts.append(f"• '{change['current_name']}' → '{new_name}'")
                if len(skipped_channels) > 5:
                    message_parts.append(f"...and {len(skipped_channels) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error renaming unknown channels: {e}")
            return {"status": "error", "message": f"Error renaming unknown channels: {e}"}

    def apply_logos_action(self, settings, logger):
        """Apply default logo to channels without logos."""
        try:
            import json
            
            default_logo = settings.get("default_logo", "").strip()
            
            if not default_logo:
                return {"status": "error", "message": "No default logo configured. Please set 'Default Logo' in plugin settings."}
            
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Get all logos from API with pagination
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching all logos from API (with pagination)...")
            all_logos = self._get_api_data("/api/channels/logos/", token, settings, logger, paginated=True)
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetched {len(all_logos)} total logos from API")
            
            # Find the logo entry matching the display name
            logo_id = None
            for logo in all_logos:
                logo_name = logo.get('name', '')
                
                # Case-insensitive exact match
                if logo_name.lower() == default_logo.lower():
                    logo_id = logo.get('id')
                    logger.info(f"{PLUGIN_LOG_PREFIX} Found logo: '{logo_name}' (ID: {logo_id})")
                    break
            
            if not logo_id:
                logger.error(f"{PLUGIN_LOG_PREFIX} Could not find logo '{default_logo}' in logo manager")
                logger.info(f"{PLUGIN_LOG_PREFIX} Searched through {len(all_logos)} logos")
                logger.info(f"{PLUGIN_LOG_PREFIX} Available logo names (first 30):")
                for logo in all_logos[:30]:
                    logger.info(f"{PLUGIN_LOG_PREFIX}   - '{logo.get('name', '')}'")
                
                return {
                    "status": "error", 
                    "message": f"Logo '{default_logo}' not found in logo manager.\n\nSearched through {len(all_logos)} logos. Check the Dispatcharr logs to see available logo names."
                }
            
            # Fetch FRESH channel data from API (not from cache)
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching current channel data from API...")
            
            # Get groups to filter
            selected_groups_str = settings.get("selected_groups", "").strip()
            if selected_groups_str:
                all_groups = self._get_api_data("/api/channels/groups/", token, settings, logger)
                group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
                input_names = {name.strip() for name in selected_groups_str.split(',') if name.strip()}
                target_group_ids = {group_name_to_id[name] for name in input_names if name in group_name_to_id}
            else:
                target_group_ids = None
            
            # Get all channels
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            
            # Filter by groups if specified
            if target_group_ids:
                all_channels = [ch for ch in all_channels if ch.get('channel_group_id') in target_group_ids]
            
            # Filter channels without logos or with "Default" logo (ID 0)
            channels_without_logos = []
            for ch in all_channels:
                channel_logo_id = ch.get('logo_id')
                # Check if no logo, empty logo, or logo ID is 0/None (Default)
                if channel_logo_id is None or channel_logo_id == 0 or channel_logo_id == '0':
                    channels_without_logos.append(ch)
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Found {len(channels_without_logos)} channels without logos (or with Default logo)")
            
            if not channels_without_logos:
                return {"status": "success", "message": "All channels already have logos assigned."}
            
            # Create payload with logo_id field (not logo)
            payload = [{'id': ch['id'], 'logo_id': int(logo_id)} for ch in channels_without_logos]
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Applying logo ID {logo_id} to {len(payload)} channels...")
            logger.info(f"{PLUGIN_LOG_PREFIX} Sample payload: {payload[0] if payload else 'N/A'}")
            
            result = self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            logger.info(f"{PLUGIN_LOG_PREFIX} Bulk update response: {result}")
            
            self._trigger_frontend_refresh(settings, logger)
            
            message_parts = [f"✓ Successfully applied logo '{default_logo}' (ID: {logo_id}) to {len(payload)} channels."]
            
            if channels_without_logos:
                message_parts.append("\n**Sample Channels:**")
                for ch in channels_without_logos[:5]:
                    message_parts.append(f"• {ch.get('name', 'Unknown')}")
                if len(channels_without_logos) > 5:
                    message_parts.append(f"...and {len(channels_without_logos) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error applying logos: {e}")
            return {"status": "error", "message": f"Error applying logos: {e}"}

    def category_groups_dry_run_action(self, settings, logger):
        """Export a CSV showing which channels would be moved to which category-based groups."""
        try:
            import json

            # Load channel data to get categories
            channels_loaded = self._load_channel_data(settings, logger)
            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded."}
            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Get all groups and channels
            all_groups = self._get_api_data("/api/channels/groups/", token, settings, logger)
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}
            
            # Filter by category groups if specified
            category_groups_str = settings.get("category_groups", "").strip()
            if category_groups_str:
                input_names = {name.strip() for name in category_groups_str.split(',') if name.strip()}
                valid_names = {n for n in input_names if n in group_name_to_id}
                target_group_ids = {group_name_to_id[name] for name in valid_names}
                
                if not target_group_ids:
                    return {"status": "error", "message": f"None of the specified category groups could be found."}
            else:
                target_group_ids = set(group_name_to_id.values())
            
            # Get all channels and filter by group
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            channels_to_process = [
                ch for ch in all_channels 
                if ch.get('channel_group_id') in target_group_ids
            ]
            
            # Build category mapping from channel databases
            # For broadcast channels: map by callsign
            category_map_callsign = {}
            for channel_data in self.matcher.broadcast_channels:
                callsign = channel_data.get('callsign', '').strip()
                category = channel_data.get('category', '').strip()
                if callsign and category:
                    # Also store without suffix
                    base_callsign = self.matcher.normalize_callsign(callsign)
                    category_map_callsign[callsign] = category
                    if base_callsign != callsign:
                        category_map_callsign[base_callsign] = category

            # For premium channels: map by channel name
            category_map_premium = {}
            for channel_data in self.matcher.premium_channels_full:
                channel_name = channel_data.get('channel_name', '').strip()
                category = channel_data.get('category', '').strip()
                if channel_name and category:
                    category_map_premium[channel_name.lower()] = (channel_name, category)
            
            # Get ignored tags for normalization
            ignored_tags_str = settings.get("ignored_tags", self.DEFAULT_IGNORED_TAGS)
            ignored_tags_list = [tag.strip() for tag in ignored_tags_str.split(',') if tag.strip()]
            
            # Expand ignored tags
            expanded_ignored_tags = []
            for tag in ignored_tags_list:
                expanded_ignored_tags.append(tag)
                if tag.startswith('[') and tag.endswith(']'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"({inner})")
                elif tag.startswith('(') and tag.endswith(')'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"[{inner}]")
            ignored_tags_list = expanded_ignored_tags
            
            # Process channels and determine moves
            moves = []
            for channel in channels_to_process:
                channel_name = channel.get('name', '')
                channel_id = channel.get('id')
                current_group_id = channel.get('channel_group_id')
                current_group_name = group_id_to_name.get(current_group_id, 'No Group')
                
                category = None
                match_type = None
                match_value = None
                
                # Try broadcast channel matching first (by callsign)
                callsign, station = self.matcher.match_broadcast_channel(channel_name)
                if callsign and callsign in category_map_callsign:
                    category = category_map_callsign[callsign]
                    match_type = "Broadcast (Callsign)"
                    match_value = callsign

                # If not a broadcast channel, try premium channel matching (by name)
                if not category:
                    # Try exact match first
                    normalized_name = self.matcher.normalize_name(channel_name, ignored_tags_list)

                    if normalized_name.lower() in category_map_premium:
                        matched_name, category = category_map_premium[normalized_name.lower()]
                        match_type = "Premium (Exact)"
                        match_value = matched_name
                    else:
                        # Try fuzzy matching
                        matched_premium, score, fuzzy_match_type = self.matcher.fuzzy_match(
                            channel_name,
                            self.matcher.premium_channels,
                            ignored_tags_list
                        )

                        if matched_premium and matched_premium.lower() in category_map_premium:
                            matched_name, category = category_map_premium[matched_premium.lower()]
                            match_type = f"Premium (Fuzzy - score: {score})"
                            match_value = matched_name
                
                # If we found a category, add to moves
                if category:
                    new_group_name = category
                    
                    # Check if group exists
                    group_exists = new_group_name in group_name_to_id
                    
                    # Only add to moves if the group is different
                    if new_group_name != current_group_name:
                        moves.append({
                            'channel_id': channel_id,
                            'channel_name': channel_name,
                            'current_group': current_group_name,
                            'new_group': new_group_name,
                            'category': category,
                            'match_type': match_type,
                            'match_value': match_value,
                            'group_exists': 'Yes' if group_exists else 'No (will be created)'
                        })
            
            if not moves:
                return {"status": "success", "message": "No channels need to be moved to category-based groups."}
            
            # Create export directory
            export_dir = self.EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)
            
            # Create CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"channel_mapparr_category_groups_preview_{timestamp}.csv"
            csv_path = os.path.join(export_dir, csv_filename)
            
            with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                # Write settings header as comments
                csvfile.write(self._generate_csv_settings_header(settings))

                fieldnames = ['Channel ID', 'Channel Name', 'Current Group', 'New Group', 'Category', 'Match Type', 'Match Value', 'Group Exists']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()
                for move in moves:
                    writer.writerow({
                        'Channel ID': move['channel_id'],
                        'Channel Name': move['channel_name'],
                        'Current Group': move['current_group'],
                        'New Group': move['new_group'],
                        'Category': move['category'],
                        'Match Type': move['match_type'],
                        'Match Value': move['match_value'],
                        'Group Exists': move['group_exists']
                    })
            
            logger.info(f"{PLUGIN_LOG_PREFIX} Category groups preview CSV exported to {csv_path}")
            
            # Count new groups that need to be created
            new_groups_needed = sum(1 for m in moves if m['group_exists'] == 'No (will be created)')
            
            # Count by match type
            broadcast_count = sum(1 for m in moves if 'Broadcast' in m['match_type'])
            premium_count = sum(1 for m in moves if 'Premium' in m['match_type'])
            
            return {
                "status": "success",
                "message": f"✓ Preview exported to: {csv_filename}\n\n{len(moves)} channels will be moved ({broadcast_count} broadcast, {premium_count} premium).\n{new_groups_needed} new groups will be created."
            }
            
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error generating category groups preview: {e}")
            return {"status": "error", "message": f"Error generating category groups preview: {e}"}

    def organize_by_category_action(self, settings, logger):
        """Create groups based on category names and move matching channels to those groups."""
        try:
            import json

            # Load channel data to get categories
            channels_loaded = self._load_channel_data(settings, logger)
            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded."}
            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Get all groups and channels
            all_groups = self._get_api_data("/api/channels/groups/", token, settings, logger)
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}
            
            # Filter by category groups if specified
            category_groups_str = settings.get("category_groups", "").strip()
            if category_groups_str:
                input_names = {name.strip() for name in category_groups_str.split(',') if name.strip()}
                valid_names = {n for n in input_names if n in group_name_to_id}
                target_group_ids = {group_name_to_id[name] for name in valid_names}
                
                if not target_group_ids:
                    return {"status": "error", "message": f"None of the specified category groups could be found."}
            else:
                target_group_ids = set(group_name_to_id.values())
            
            # Get all channels and filter by group
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            channels_to_process = [
                ch for ch in all_channels 
                if ch.get('channel_group_id') in target_group_ids
            ]
            
            # Build category mapping from channel databases
            # For broadcast channels: map by callsign
            category_map_callsign = {}
            for channel_data in self.matcher.broadcast_channels:
                callsign = channel_data.get('callsign', '').strip()
                category = channel_data.get('category', '').strip()
                if callsign and category:
                    # Also store without suffix
                    base_callsign = self.matcher.normalize_callsign(callsign)
                    category_map_callsign[callsign] = category
                    if base_callsign != callsign:
                        category_map_callsign[base_callsign] = category

            # For premium channels: map by channel name
            category_map_premium = {}
            for channel_data in self.matcher.premium_channels_full:
                channel_name = channel_data.get('channel_name', '').strip()
                category = channel_data.get('category', '').strip()
                if channel_name and category:
                    category_map_premium[channel_name.lower()] = (channel_name, category)
            
            # Get ignored tags for normalization
            ignored_tags_str = settings.get("ignored_tags", self.DEFAULT_IGNORED_TAGS)
            ignored_tags_list = [tag.strip() for tag in ignored_tags_str.split(',') if tag.strip()]
            
            # Expand ignored tags
            expanded_ignored_tags = []
            for tag in ignored_tags_list:
                expanded_ignored_tags.append(tag)
                if tag.startswith('[') and tag.endswith(']'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"({inner})")
                elif tag.startswith('(') and tag.endswith(')'):
                    inner = tag[1:-1]
                    expanded_ignored_tags.append(f"[{inner}]")
            ignored_tags_list = expanded_ignored_tags
            
            # Process channels and determine moves
            moves = []
            groups_needed = set()
            
            for channel in channels_to_process:
                channel_name = channel.get('name', '')
                channel_id = channel.get('id')
                current_group_id = channel.get('channel_group_id')
                current_group_name = group_id_to_name.get(current_group_id, 'No Group')
                
                category = None

                # Try broadcast channel matching first (by callsign)
                callsign, station = self.matcher.match_broadcast_channel(channel_name)
                if callsign and callsign in category_map_callsign:
                    category = category_map_callsign[callsign]

                # If not a broadcast channel, try premium channel matching (by name)
                if not category:
                    # Try exact match first
                    normalized_name = self.matcher.normalize_name(channel_name, ignored_tags_list)

                    if normalized_name.lower() in category_map_premium:
                        matched_name, category = category_map_premium[normalized_name.lower()]
                    else:
                        # Try fuzzy matching
                        matched_premium, score, fuzzy_match_type = self.matcher.fuzzy_match(
                            channel_name,
                            self.matcher.premium_channels,
                            ignored_tags_list
                        )

                        if matched_premium and matched_premium.lower() in category_map_premium:
                            matched_name, category = category_map_premium[matched_premium.lower()]
                
                # If we found a category, add to moves
                if category:
                    new_group_name = category
                    
                    # Track groups that need to be created
                    if new_group_name not in group_name_to_id:
                        groups_needed.add(new_group_name)
                    
                    # Only add to moves if the group is different
                    if new_group_name != current_group_name:
                        moves.append({
                            'channel_id': channel_id,
                            'channel_name': channel_name,
                            'new_group_name': new_group_name
                        })
            
            if not moves:
                return {"status": "success", "message": "No channels need to be moved to category-based groups."}
            
            # Create new groups if needed
            created_groups = []
            for group_name in groups_needed:
                logger.info(f"{PLUGIN_LOG_PREFIX} Creating new group: {group_name}")
                try:
                    new_group = self._post_api_data(
                        "/api/channels/groups/",
                        token,
                        {"name": group_name},
                        settings,
                        logger
                    )
                    group_id = new_group.get('id')
                    group_name_to_id[group_name] = group_id
                    created_groups.append(group_name)
                    logger.info(f"{PLUGIN_LOG_PREFIX} Created group '{group_name}' with ID {group_id}")
                except Exception as e:
                    logger.error(f"{PLUGIN_LOG_PREFIX} Failed to create group '{group_name}': {e}")
            
            # Build payload for bulk update
            payload = []
            for move in moves:
                new_group_id = group_name_to_id.get(move['new_group_name'])
                if new_group_id:
                    payload.append({
                        'id': move['channel_id'],
                        'channel_group_id': new_group_id
                    })
            
            if not payload:
                return {"status": "error", "message": "Failed to create necessary groups. Please check logs."}
            
            # Apply the moves
            logger.info(f"{PLUGIN_LOG_PREFIX} Moving {len(payload)} channels to category-based groups...")
            self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            self._trigger_frontend_refresh(settings, logger)
            
            message_parts = [f"✓ Successfully organized {len(payload)} channels by category."]
            
            if created_groups:
                message_parts.append(f"\n**New Groups Created:** {', '.join(created_groups)}")
            
            message_parts.append(f"\n**Sample Moves:**")
            for move in moves[:5]:
                message_parts.append(f"• '{move['channel_name']}' → {move['new_group_name']}")
            if len(moves) > 5:
                message_parts.append(f"...and {len(moves) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}
            
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error organizing channels by category: {e}")
            return {"status": "error", "message": f"Error organizing channels by category: {e}"}

    def clear_csv_exports_action(self, settings, logger):
        """Delete all CSV export files created by this plugin"""
        try:
            export_dir = self.EXPORT_DIR
            
            if not os.path.exists(export_dir):
                return {
                    "status": "success",
                    "message": "No export directory found. No files to delete."
                }
            
            # Find all CSV files created by this plugin
            deleted_count = 0
            
            for filename in os.listdir(export_dir):
                if filename.startswith("channel_mapparr_") and filename.endswith(".csv"):
                    filepath = os.path.join(export_dir, filename)
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        logger.info(f"{PLUGIN_LOG_PREFIX} Deleted CSV file: {filename}")
                    except Exception as e:
                        logger.warning(f"{PLUGIN_LOG_PREFIX} Failed to delete {filename}: {e}")
            
            if deleted_count == 0:
                return {
                    "status": "success",
                    "message": "No CSV export files found to delete."
                }
            
            return {
                "status": "success",
                "message": f"Successfully deleted {deleted_count} CSV export file(s)."
            }
            
        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error clearing CSV exports: {e}")
            return {"status": "error", "message": f"Error clearing CSV exports: {e}"}

# Export fields and actions for Dispatcharr plugin system
fields = Plugin.fields
actions = Plugin.actions

# Additional exports for Dispatcharr plugin system compatibility
plugin = Plugin()
plugin_instance = Plugin()

# Alternative export names in case Dispatcharr looks for these
channel_mapparr = Plugin()
CHANNEL_MAPPARR = Plugin()