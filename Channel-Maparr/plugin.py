"""
Channel Mapparr Plugin
Standardizes US broadcast (OTA) and premium/cable channel names.
"""

import copy
import logging
import csv
import os
import re
import json
import time
import tempfile
import threading
import urllib.request
import urllib.error
from datetime import datetime

# Import the fuzzy matcher module
from .fuzzy_matcher import FuzzyMatcher

# Django model imports
from apps.channels.models import Channel, ChannelGroup, Logo, Stream, ChannelStream
from apps.m3u.models import M3UAccount
from django.db import transaction
from core.utils import send_websocket_update

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.channel_mapparr")

# Plugin name prefix for all log messages
PLUGIN_LOG_PREFIX = "[Channel Mapparr]"


class PluginConfig:
    """Configuration constants for Channel Maparr."""

    PLUGIN_VERSION = "1.26.1001200"

    # Channel Database Settings
    DEFAULT_CHANNEL_DATABASES = "US"

    # Fuzzy Matching Settings
    DEFAULT_FUZZY_MATCH_THRESHOLD = 80
    SENSITIVITY_MAP = {
        "relaxed": 70,
        "normal": 80,
        "strict": 90,
        "exact": 95,
    }

    # Channel Naming Settings
    DEFAULT_OTA_FORMAT = "{NETWORK} - {STATE} {CITY} ({CALLSIGN})"
    DEFAULT_UNKNOWN_SUFFIX = " [Unk]"
    DEFAULT_IGNORED_TAGS = "[4K], [FHD], [HD], [SD], [Unknown], [Unk], [Slow], [Dead]"

    # File Paths
    RESULTS_FILE = "/data/channel_mapparr_loaded_channels.json"
    VERSION_CHECK_FILE = "/data/channel_mapparr_version_check.json"
    EXPORT_DIR = "/data/exports"

    # Rate Limiting
    RATE_LIMIT_NONE = 0.0
    RATE_LIMIT_LOW = 0.1
    RATE_LIMIT_MEDIUM = 0.5
    RATE_LIMIT_HIGH = 2.0

    # ETA estimation (seconds per item)
    ESTIMATED_SECONDS_PER_STREAM_MATCH = 0.5


class ProgressTracker:
    """Tracks operation progress with periodic logging and WebSocket updates."""

    def __init__(self, total_items, action_id, logger):
        self.total_items = max(total_items, 1)
        self.action_id = action_id
        self.logger = logger
        self.start_time = time.time()
        self.last_update_time = self.start_time
        # Adaptive interval: shorter for smaller jobs
        self.update_interval = 3 if total_items <= 50 else 5 if total_items <= 200 else 10
        self.processed_items = 0
        logger.info(f"{PLUGIN_LOG_PREFIX} [{action_id}] Starting: {total_items} items to process")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Channel Mapparr",
            "message": f"{action_id}: Starting ({total_items} items)"
        })

    def update(self, items_processed=1):
        self.processed_items += items_processed
        now = time.time()
        if now - self.last_update_time >= self.update_interval:
            self.last_update_time = now
            elapsed = now - self.start_time
            pct = (self.processed_items / self.total_items) * 100
            remaining = (elapsed / self.processed_items) * (self.total_items - self.processed_items) if self.processed_items > 0 else 0
            eta_str = self._format_eta(remaining)
            self.logger.info(
                f"{PLUGIN_LOG_PREFIX} [{self.action_id}] {pct:.0f}% "
                f"({self.processed_items}/{self.total_items}) - ETA: {eta_str}"
            )
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Channel Mapparr",
                "message": f"{self.action_id}: {pct:.0f}% ({self.processed_items}/{self.total_items}) - ETA: {eta_str}"
            })

    def finish(self):
        elapsed = time.time() - self.start_time
        eta_str = self._format_eta(elapsed)
        self.logger.info(
            f"{PLUGIN_LOG_PREFIX} [{self.action_id}] Complete: "
            f"{self.processed_items}/{self.total_items} in {eta_str}"
        )
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Channel Mapparr",
            "message": f"{self.action_id}: Complete ({self.processed_items}/{self.total_items}) in {eta_str}"
        })

    @staticmethod
    def _format_eta(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"


class SmartRateLimiter:
    """Rate limiting for DB operations."""

    DELAYS = {
        "none": PluginConfig.RATE_LIMIT_NONE,
        "low": PluginConfig.RATE_LIMIT_LOW,
        "medium": PluginConfig.RATE_LIMIT_MEDIUM,
        "high": PluginConfig.RATE_LIMIT_HIGH,
    }

    def __init__(self, setting_value="none"):
        self.delay = self.DELAYS.get(setting_value, 0.0)

    def wait(self):
        if self.delay > 0:
            time.sleep(self.delay)


class Plugin:
    """Channel Mapparr Plugin"""

    name = "Channel Mapparr"
    version = PluginConfig.PLUGIN_VERSION
    description = "Standardizes broadcast (OTA) and premium/cable channel names using network data and channel lists."

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

        # Discover M3U sources from database
        m3u_source_options = [{"value": "_all", "label": "All sources (no filter)"}]
        try:
            for acc in M3UAccount.objects.all().values('id', 'name').order_by('name'):
                m3u_source_options.append({"value": acc['name'], "label": acc['name']})
        except Exception:
            pass

        # Build the fields list dynamically
        return [
            {
                "id": "version_status",
                "label": "Plugin Version",
                "type": "info",
                "help_text": version_message
            },
            {
                "id": "channel_databases",
                "label": "Channel Databases",
                "type": "string",
                "default": PluginConfig.DEFAULT_CHANNEL_DATABASES,
                "placeholder": "US, UK, CA, AU",
                "help_text": "Comma-separated country codes. Available: AU, BR, CA, DE, ES, FR, IN, MX, NL, UK, US",
            },
            {
                "id": "match_sensitivity",
                "label": "Match Sensitivity",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "relaxed", "label": "Relaxed \u2014 more matches, more false positives"},
                    {"value": "normal", "label": "Normal \u2014 balanced"},
                    {"value": "strict", "label": "Strict \u2014 fewer matches, high confidence"},
                    {"value": "exact", "label": "Exact \u2014 near-exact matches only"},
                ],
                "help_text": "How closely stream names must match channel names. Lower = more matches but more errors.",
            },
            {
                "id": "selected_groups",
                "label": "Channel Groups to Process",
                "type": "string",
                "default": "",
                "placeholder": "Locals, News, Entertainment",
                "help_text": "Comma-separated. Limits rename/logo actions to these groups. Leave empty for all.",
            },
            {
                "id": "category_groups",
                "label": "Category Organization Groups",
                "type": "string",
                "default": "",
                "placeholder": "Locals, News, Entertainment",
                "help_text": "Source groups for category-based reorganization. Leave empty for all.",
            },
            {
                "id": "m3u_sources",
                "label": "M3U Source",
                "type": "select",
                "default": "_all",
                "options": m3u_source_options,
                "help_text": "Filter streams to a specific M3U source.",
            },
            {
                "id": "m3u_group_filter",
                "label": "M3U Group Filter",
                "type": "string",
                "default": "",
                "placeholder": "USA Premium, Sports, Movies",
                "help_text": "Comma-separated. Only process streams from these M3U groups (pre-match filter).",
            },
            {
                "id": "m3u_category_filter",
                "label": "Category Filter",
                "type": "string",
                "default": "",
                "placeholder": "Broadcast, Sports, News",
                "help_text": "Comma-separated. Only import matched streams with these database categories (post-match filter).",
            },
            {
                "id": "m3u_custom_group_name",
                "label": "Custom Import Group Name",
                "type": "string",
                "default": "",
                "placeholder": "My Custom Group",
                "help_text": "Place all imports into this single group instead of auto-organizing by category.",
            },
            {
                "id": "ota_format",
                "label": "OTA Name Format",
                "type": "string",
                "default": PluginConfig.DEFAULT_OTA_FORMAT,
                "placeholder": PluginConfig.DEFAULT_OTA_FORMAT,
                "help_text": "Tags: {NETWORK}, {STATE}, {CITY}, {CALLSIGN}. Channels missing fields are skipped.",
            },
            {
                "id": "unknown_suffix",
                "label": "Unknown Channel Suffix",
                "type": "string",
                "default": PluginConfig.DEFAULT_UNKNOWN_SUFFIX,
                "placeholder": PluginConfig.DEFAULT_UNKNOWN_SUFFIX,
                "help_text": "Appended to channels that cannot be matched. Leave empty to skip.",
            },
            {
                "id": "ignored_tags",
                "label": "Ignored Tags",
                "type": "string",
                "default": PluginConfig.DEFAULT_IGNORED_TAGS,
                "placeholder": PluginConfig.DEFAULT_IGNORED_TAGS,
                "help_text": "Comma-separated tags to strip before matching. e.g. [HD], (H), [4K]",
            },
            {
                "id": "default_logo",
                "label": "Default Logo",
                "type": "string",
                "default": "",
                "placeholder": "abc-logo-2013-garnet-us",
                "help_text": "Logo display name from Dispatcharr's Logos page. Leave empty to skip.",
            },
            {
                "id": "dry_run_mode",
                "label": "Dry Run Mode",
                "type": "boolean",
                "default": False,
                "help_text": "Preview changes without modifying anything. Actions export CSV reports instead.",
            },
            {
                "id": "rate_limiting",
                "label": "Rate Limiting",
                "type": "select",
                "default": "none",
                "options": [
                    {"value": "none", "label": "None \u2014 fastest"},
                    {"value": "low", "label": "Low \u2014 slight delay"},
                    {"value": "medium", "label": "Medium \u2014 moderate delay"},
                    {"value": "high", "label": "High \u2014 gentlest on database"},
                ],
                "help_text": "Delay between DB writes during large imports to reduce server load.",
            },
        ]

    # Actions for Dispatcharr UI
    actions = [
        {
            "id": "validate_settings",
            "label": "\u2705 Validate Settings",
            "description": "Check database connectivity, channel databases, and settings",
            "button_variant": "outline",
            "button_color": "blue",
        },
        {
            "id": "load_and_process_channels",
            "label": "\u25b6 Load & Process Channels",
            "description": "Scan channel groups and determine standardized names",
            "button_variant": "filled",
            "button_color": "green",
        },
        {
            "id": "rename_channels",
            "label": "\u270f\ufe0f Rename Channels",
            "description": "Apply standardized names. Dry Run exports a CSV preview instead.",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will rename channels to the standardized format. This action is irreversible. Continue?"},
        },
        {
            "id": "rename_unknown_channels",
            "label": "\u2696 Tag Unknown Channels",
            "description": "Append suffix to unmatched OTA and premium channels",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will append the configured suffix to unmatched channels. Continue?"},
        },
        {
            "id": "apply_logos",
            "label": "\u2b50 Apply Logos",
            "description": "Assign the default logo to channels that don't have one",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will apply the default logo to channels without a logo. Continue?"},
        },
        {
            "id": "organize_by_category",
            "label": "\u2630 Organize by Category",
            "description": "Move channels into category-based groups. Runs in background. Dry Run exports a CSV preview.",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will create new groups (if needed) and move channels to category-based groups. Continue?"},
            "background": True,
        },
        {
            "id": "import_m3u_streams",
            "label": "\u21e9 Import M3U Streams",
            "description": "Create channels from M3U streams organized by category. Runs in background. Dry Run exports a CSV preview.",
            "button_variant": "filled",
            "button_color": "violet",
            "confirm": {"message": "This will create new channels from M3U streams and organize them into groups. Duplicates get suffixes. Continue?"},
        },
        {
            "id": "clear_csv_exports",
            "label": "\u2717 Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "button_variant": "outline",
            "button_color": "red",
            "confirm": {"message": "Delete all Channel Mapparr CSV exports?"},
        },
    ]

    def __init__(self):
        self.loaded_channels = []
        self.results_file = PluginConfig.RESULTS_FILE
        self.group_name_map = {}

        # Version check cache state
        self.version_check_file = PluginConfig.VERSION_CHECK_FILE
        self.cached_version_info = None

        # Background threading
        self._thread = None
        self._thread_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_bg_result = None

        # Initialize the fuzzy matcher (will load databases on first use)
        plugin_dir = os.path.dirname(__file__)
        self.matcher = FuzzyMatcher(plugin_dir=plugin_dir, match_threshold=PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD, logger=LOGGER)

        LOGGER.info(f"{PLUGIN_LOG_PREFIX} {self.name} Plugin v{self.version} initialized")

        # Import version from fuzzy_matcher module
        try:
            from . import fuzzy_matcher
            LOGGER.info(f"{PLUGIN_LOG_PREFIX} Using fuzzy_matcher.py v{fuzzy_matcher.__version__}")
        except Exception:
            LOGGER.info(f"{PLUGIN_LOG_PREFIX} Using fuzzy_matcher.py")

    def _try_start_thread(self, target, args):
        """Atomically check if a thread is running and start a new one.
        Returns True if started, False if another operation is running."""
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=target, args=args, daemon=True)
            self._thread.start()
            return True

    def stop(self, context):
        """Called by Dispatcharr when the user requests cancellation."""
        self._stop_event.set()
        LOGGER.info(f"{PLUGIN_LOG_PREFIX} Stop requested. Cancelling current operation...")
        return {"status": "ok", "message": "Cancellation requested."}

    def _resolve_threshold(self, settings, logger):
        """Resolve match sensitivity setting to a numeric threshold."""
        sensitivity = settings.get("match_sensitivity", "normal")
        threshold = PluginConfig.SENSITIVITY_MAP.get(sensitivity)
        if threshold is None:
            # Fallback: legacy numeric field
            try:
                threshold = int(settings.get("fuzzy_match_threshold", PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD))
            except (ValueError, TypeError):
                threshold = PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD
        threshold = max(0, min(100, threshold))
        logger.info(f"{PLUGIN_LOG_PREFIX} Match sensitivity: {sensitivity} (threshold: {threshold})")
        return threshold

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
        """Generate CSV header comments with plugin settings"""
        # Map field IDs to their labels
        field_labels = {
            'channel_databases': 'Channel Databases',
            'match_sensitivity': 'Match Sensitivity',
            'selected_groups': 'Channel Groups to Process',
            'category_groups': 'Channel Groups for Category Organization',
            'm3u_sources': 'M3U Sources',
            'm3u_group_filter': 'M3U Group Filter',
            'm3u_category_filter': 'Channel Database Category Filter',
            'm3u_custom_group_name': 'Imported Channel Group Name',
            'dry_run_mode': 'Dry Run Mode',
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
            value = settings.get(field_id, '')
            if value:
                header_lines.append(f"# {label}: {value}")
            else:
                header_lines.append(f"# {label}: (not set)")

        header_lines.append("#")
        return '\n'.join(header_lines) + '\n'

    # ========================================
    # ORM HELPER METHODS
    # ========================================

    def _get_all_groups(self, logger):
        """Fetch all channel groups via Django ORM."""
        return list(ChannelGroup.objects.all().values('id', 'name'))

    def _get_all_channels(self, logger, group_ids=None):
        """Fetch channels via Django ORM, optionally filtered by group IDs."""
        qs = Channel.objects.all()
        if group_ids:
            qs = qs.filter(channel_group_id__in=group_ids)
        return list(qs.values('id', 'name', 'channel_number', 'channel_group_id', 'logo_id'))

    def _bulk_update_channels(self, updates, fields, logger):
        """Bulk update Channel instances.

        Args:
            updates: list of dicts with 'id' and fields to update
            fields: list of field names to update
            logger: logger instance
        """
        if not updates:
            return

        channel_ids = [u['id'] for u in updates]
        channels = {ch.id: ch for ch in Channel.objects.filter(id__in=channel_ids)}

        to_update = []
        for u in updates:
            ch = channels.get(u['id'])
            if ch:
                for field in fields:
                    if field in u:
                        setattr(ch, field, u[field])
                to_update.append(ch)

        if to_update:
            with transaction.atomic():
                Channel.objects.bulk_update(to_update, fields)
            logger.info(f"{PLUGIN_LOG_PREFIX} Bulk updated {len(to_update)} channels (fields: {', '.join(fields)})")

    def _get_or_create_group(self, name, logger):
        """Get or create a channel group by name."""
        group, created = ChannelGroup.objects.get_or_create(name=name)
        if created:
            logger.info(f"{PLUGIN_LOG_PREFIX} Created new group '{name}' (ID: {group.id})")
        return group

    def _get_all_logos(self, logger):
        """Fetch all logos via Django ORM."""
        return list(Logo.objects.all().values('id', 'name'))

    def _trigger_frontend_refresh(self, settings, logger):
        """Trigger frontend channel list refresh via WebSocket"""
        try:
            send_websocket_update('updates', 'update', {
                "type": "plugin",
                "plugin": self.name,
                "message": "Channels updated"
            })
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

        # Resolve match sensitivity to threshold
        fuzzy_threshold = self._resolve_threshold(settings, logger)
        self.matcher.match_threshold = fuzzy_threshold

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
        logger = context.get("logger", LOGGER)
        settings = context.get("settings", {})

        try:
            action_map = {
                "validate_settings": self.validate_settings_action,
                "load_and_process_channels": self.load_and_process_channels_action,
                "rename_channels": self.rename_channels_action,
                "rename_unknown_channels": self.rename_unknown_channels_action,
                "apply_logos": self.apply_logos_action,
                "organize_by_category": self.organize_by_category_action,
                "import_m3u_streams": self.import_m3u_streams_action,
                "clear_csv_exports": self.clear_csv_exports_action,
            }

            handler = action_map.get(action)
            if not handler:
                logger.warning(f"{PLUGIN_LOG_PREFIX} Unknown action: {action}")
                return {"status": "error", "message": f"Unknown action: {action}"}

            logger.info(f"{PLUGIN_LOG_PREFIX} Action triggered: {action}")
            result = handler(settings, logger)

            status = result.get("status", "?") if isinstance(result, dict) else "ok"
            msg = result.get("message", "")[:200] if isinstance(result, dict) else ""
            is_bg = result.get("background", False) if isinstance(result, dict) else False

            logger.info(f"{PLUGIN_LOG_PREFIX} Action complete: {action} -> {status} | {msg}")

            # Send GUI notification for non-background actions
            if not is_bg:
                send_websocket_update('updates', 'update', {
                    "type": "plugin", "plugin": self.name,
                    "message": f"{action}: {msg[:100]}" if msg else action
                })

            return result

        except Exception as e:
            LOGGER.exception(f"{PLUGIN_LOG_PREFIX} Error in action '{action}': {e}")
            return {"status": "error", "message": str(e)}

    def load_and_process_channels_action(self, settings, logger):
        """Load channels from database and process them with channel data."""
        try:


            # Load channel data from selected country databases
            channels_loaded = self._load_channel_data(settings, logger)

            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded. Please check your channel_databases setting and ensure the files exist."}

            logger.info(f"{PLUGIN_LOG_PREFIX} Loading channels from database...")

            # Get all groups first to build name-to-id mapping
            all_groups = self._get_all_groups(logger)
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
            all_channels = self._get_all_channels(logger, group_ids=target_group_ids if selected_groups_str else None)

            channels_to_process = all_channels
            logger.info(f"{PLUGIN_LOG_PREFIX} Filtered to {len(channels_to_process)} channels in groups: {selected_groups_str if selected_groups_str else 'all groups'}")

            # Store channels with proper group names
            for channel in channels_to_process:
                group_id = channel.get('channel_group_id')
                channel['_group_name'] = group_id_to_name.get(group_id, 'No Group')

            self.loaded_channels = channels_to_process

            # Process channels
            logger.info(f"{PLUGIN_LOG_PREFIX} Processing {len(self.loaded_channels)} channels...")

            renamed_channels = []
            skipped_channels = []
            ota_format = settings.get("ota_format", PluginConfig.DEFAULT_OTA_FORMAT)

            # Parse ignored tags from settings
            ignored_tags_str = settings.get("ignored_tags", PluginConfig.DEFAULT_IGNORED_TAGS)
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

            # Pre-compute normalizations for both loaded channels and premium channels
            if self.matcher.premium_channels:
                channel_names = [ch.get('name', '') for ch in self.loaded_channels if ch.get('name', '')]
                all_names = channel_names + self.matcher.premium_channels
                self.matcher.precompute_normalizations(all_names, ignored_tags_list)
                self.matcher.build_token_index(self.matcher.premium_channels, ignored_tags_list)

            progress = ProgressTracker(len(self.loaded_channels), "process_channels", logger)

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
                if self._stop_event.is_set():
                    logger.info(f"{PLUGIN_LOG_PREFIX} Channel processing cancelled by user.")
                    break

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

                    # Use fuzzy matcher with token-based pre-filtering
                    candidates = self.matcher.get_candidates(current_name, ignored_tags_list)
                    if candidates is None:
                        candidates = self.matcher.premium_channels
                    if candidates:
                        matched_premium, score, match_type = self.matcher.fuzzy_match(
                            current_name,
                            candidates,
                            ignored_tags_list
                        )
                    else:
                        matched_premium, score, match_type = None, 0, None

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

                progress.update()

            progress.finish()

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


            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])

            if not all_changes:
                return {"status": "success", "message": "No changes to preview."}

            # Create export directory if it does not exist
            export_dir = PluginConfig.EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)

            # Create timestamp for filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"channel_mapparr_preview_{timestamp}.csv"
            csv_path = os.path.join(export_dir, csv_filename)

            # Write CSV atomically (temp file + rename to prevent corrupt partial writes)
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                                 dir=export_dir, suffix='.csv', delete=False) as csvfile:
                    tmp_path = csvfile.name
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
                os.replace(tmp_path, csv_path)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

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


            # Check if dry run mode is enabled
            dry_run = settings.get("dry_run_mode", False)

            if dry_run:
                logger.info(f"{PLUGIN_LOG_PREFIX} Dry Run Mode enabled - calling preview_changes_action")
                return self.preview_changes_action(settings, logger)

            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])
            channels_to_rename = [c for c in all_changes if c.get('status') == 'Renamed']

            if not channels_to_rename:
                return {"status": "success", "message": "No channels need to be renamed."}

            # Bulk update using ORM
            updates = [{'id': ch['channel_id'], 'name': ch['new_name']} for ch in channels_to_rename]

            logger.info(f"{PLUGIN_LOG_PREFIX} Renaming {len(updates)} channels...")
            self._bulk_update_channels(updates, ['name'], logger)
            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully renamed {len(updates)} channels."]
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


            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}

            # Get suffix with default fallback matching the field default
            suffix = settings.get("unknown_suffix", PluginConfig.DEFAULT_UNKNOWN_SUFFIX)

            # Log what we received
            logger.info(f"{PLUGIN_LOG_PREFIX} Suffix setting value: '{suffix}' (length: {len(suffix)})")

            # Only reject if suffix is None or empty after strip
            if not suffix or not suffix.strip():
                return {"status": "error", "message": "No suffix configured. Please set 'Suffix for Unknown Channels' in plugin settings. Default is ' [Unk]' (with leading space)."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])
            skipped_channels = [c for c in all_changes if c.get('status') == 'Skipped']

            if not skipped_channels:
                return {"status": "success", "message": "No unknown channels to rename."}

            # Bulk update using ORM
            updates = [{'id': ch['channel_id'], 'name': ch['current_name'] + suffix} for ch in skipped_channels]

            logger.info(f"{PLUGIN_LOG_PREFIX} Adding suffix '{suffix}' to {len(updates)} unknown channels...")
            self._bulk_update_channels(updates, ['name'], logger)
            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully added suffix '{suffix}' to {len(updates)} unknown channels."]
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


            default_logo = settings.get("default_logo", "").strip()

            if not default_logo:
                return {"status": "error", "message": "No default logo configured. Please set 'Default Logo' in plugin settings."}

            # Get all logos from database
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching all logos from database...")
            all_logos = self._get_all_logos(logger)

            logger.info(f"{PLUGIN_LOG_PREFIX} Fetched {len(all_logos)} total logos from database")

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

            # Fetch FRESH channel data from database
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching current channel data from database...")

            # Get groups to filter
            selected_groups_str = settings.get("selected_groups", "").strip()
            target_group_ids = None
            if selected_groups_str:
                all_groups = self._get_all_groups(logger)
                group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
                input_names = {name.strip() for name in selected_groups_str.split(',') if name.strip()}
                target_group_ids = {group_name_to_id[name] for name in input_names if name in group_name_to_id}

            # Get all channels
            all_channels = self._get_all_channels(logger, group_ids=target_group_ids)

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

            # Bulk update using ORM
            updates = [{'id': ch['id'], 'logo_id': int(logo_id)} for ch in channels_without_logos]

            logger.info(f"{PLUGIN_LOG_PREFIX} Applying logo ID {logo_id} to {len(updates)} channels...")
            self._bulk_update_channels(updates, ['logo_id'], logger)

            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully applied logo '{default_logo}' (ID: {logo_id}) to {len(updates)} channels."]

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


            # Load channel data to get categories
            channels_loaded = self._load_channel_data(settings, logger)
            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded."}

            # Get all groups and channels
            all_groups = self._get_all_groups(logger)
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
            all_channels = self._get_all_channels(logger, group_ids=target_group_ids)
            channels_to_process = all_channels

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
            ignored_tags_str = settings.get("ignored_tags", PluginConfig.DEFAULT_IGNORED_TAGS)
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

            # Pre-compute normalizations for channel names AND premium channels (both needed for matching)
            channel_names_to_norm = [ch.get('name', '') for ch in channels_to_process if ch.get('name', '')]
            all_names = channel_names_to_norm + self.matcher.premium_channels
            self.matcher.precompute_normalizations(all_names, ignored_tags_list)

            # Build token index for fast fuzzy candidate pre-filtering
            self.matcher.build_token_index(self.matcher.premium_channels, ignored_tags_list)

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
                    # Try normalized match first (uses cache)
                    norm_lower, _ = self.matcher._get_cached_norm(channel_name, ignored_tags_list)

                    if norm_lower and norm_lower in category_map_premium:
                        matched_name, category = category_map_premium[norm_lower]
                        match_type = "Premium (Exact)"
                        match_value = matched_name
                    else:
                        # Try fuzzy matching with token-based pre-filtering
                        candidates = self.matcher.get_candidates(channel_name, ignored_tags_list)
                        if candidates is None:
                            candidates = self.matcher.premium_channels
                        if candidates:
                            matched_premium, score, fuzzy_match_type = self.matcher.fuzzy_match(
                                channel_name,
                                candidates,
                                ignored_tags_list
                            )
                        else:
                            matched_premium, score, fuzzy_match_type = None, 0, None

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
            export_dir = PluginConfig.EXPORT_DIR
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


            # Check if dry run mode is enabled
            dry_run = settings.get("dry_run_mode", False)

            if dry_run:
                logger.info(f"{PLUGIN_LOG_PREFIX} Dry Run Mode enabled - calling category_groups_dry_run_action")
                return self.category_groups_dry_run_action(settings, logger)

            # Load channel data to get categories
            channels_loaded = self._load_channel_data(settings, logger)
            if not channels_loaded:
                return {"status": "error", "message": "Channel databases could not be loaded."}

            # Get all groups and channels
            all_groups = self._get_all_groups(logger)
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
            all_channels = self._get_all_channels(logger, group_ids=target_group_ids)
            channels_to_process = all_channels

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
            ignored_tags_str = settings.get("ignored_tags", PluginConfig.DEFAULT_IGNORED_TAGS)
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

            # Pre-compute normalizations for channel names AND premium channels
            channel_names_to_norm = [ch.get('name', '') for ch in channels_to_process if ch.get('name', '')]
            all_names = channel_names_to_norm + self.matcher.premium_channels
            self.matcher.precompute_normalizations(all_names, ignored_tags_list)

            # Build token index for fast fuzzy candidate pre-filtering
            self.matcher.build_token_index(self.matcher.premium_channels, ignored_tags_list)

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
                    # Try normalized match first (uses cache)
                    norm_lower, _ = self.matcher._get_cached_norm(channel_name, ignored_tags_list)

                    if norm_lower and norm_lower in category_map_premium:
                        matched_name, category = category_map_premium[norm_lower]
                    else:
                        # Try fuzzy matching with token-based pre-filtering
                        candidates = self.matcher.get_candidates(channel_name, ignored_tags_list)
                        if candidates is None:
                            candidates = self.matcher.premium_channels
                        if candidates:
                            matched_premium, score, fuzzy_match_type = self.matcher.fuzzy_match(
                                channel_name,
                                candidates,
                                ignored_tags_list
                            )
                        else:
                            matched_premium, score, fuzzy_match_type = None, 0, None

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

            # Create new groups if needed using ORM
            created_groups = []
            for group_name in groups_needed:
                logger.info(f"{PLUGIN_LOG_PREFIX} Creating new group: {group_name}")
                try:
                    group = self._get_or_create_group(group_name, logger)
                    group_name_to_id[group_name] = group.id
                    created_groups.append(group_name)
                except Exception as e:
                    logger.error(f"{PLUGIN_LOG_PREFIX} Failed to create group '{group_name}': {e}")

            # Build updates for bulk update
            updates = []
            for move in moves:
                new_group_id = group_name_to_id.get(move['new_group_name'])
                if new_group_id:
                    updates.append({
                        'id': move['channel_id'],
                        'channel_group_id': new_group_id
                    })

            if not updates:
                return {"status": "error", "message": "Failed to create necessary groups. Please check logs."}

            # Apply the moves using ORM
            logger.info(f"{PLUGIN_LOG_PREFIX} Moving {len(updates)} channels to category-based groups...")
            self._bulk_update_channels(updates, ['channel_group_id'], logger)
            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully organized {len(updates)} channels by category."]

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

    # ========================================
    # M3U STREAM IMPORT METHODS
    # ========================================

    def _fetch_streams_from_m3u_sources(self, settings, logger):
        """
        Fetch streams from specified M3U sources via Django ORM.

        Returns:
            list: List of stream dicts with prioritization metadata
        """
        # Get M3U sources from settings
        m3u_sources_str = settings.get("m3u_sources", "").strip()

        if not m3u_sources_str or m3u_sources_str == "_all":
            # If empty, fetch from ALL M3U sources
            logger.info(f"{PLUGIN_LOG_PREFIX} No M3U sources specified, fetching from all M3U accounts")
            m3u_sources = None
        else:
            # Parse comma-separated M3U source names
            m3u_sources = [source.strip() for source in m3u_sources_str.split(',') if source.strip()]
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching streams from M3U sources: {', '.join(m3u_sources)}")

        all_streams = []

        if m3u_sources is None:
            # Fetch all streams (no filtering)
            logger.info(f"{PLUGIN_LOG_PREFIX} Querying all streams from database...")
            streams_qs = Stream.objects.only(
                'id', 'name', 'm3u_account_id', 'channel_group_id'
            ).all()

            for stream in streams_qs:
                stream_dict = {
                    'id': stream.id,
                    'name': stream.name if hasattr(stream, 'name') else str(stream),
                    'm3u_account': stream.m3u_account_id,
                    'channel_group': getattr(stream, 'channel_group_id', None),
                    'group_title': getattr(stream, 'group_title', None),
                    'priority': 0,
                }
                all_streams.append(stream_dict)

            logger.info(f"{PLUGIN_LOG_PREFIX} Successfully fetched {len(all_streams)} streams")
        else:
            # Fetch streams for each M3U source in priority order
            for priority_index, m3u_source in enumerate(m3u_sources):
                logger.info(f"{PLUGIN_LOG_PREFIX} Querying streams for M3U source: {m3u_source}")

                try:
                    streams_qs = Stream.objects.only(
                        'id', 'name', 'm3u_account_id', 'channel_group_id'
                    ).filter(m3u_account__name=m3u_source)

                    count = 0
                    for stream in streams_qs:
                        stream_dict = {
                            'id': stream.id,
                            'name': stream.name if hasattr(stream, 'name') else str(stream),
                            'm3u_account': stream.m3u_account_id,
                            'channel_group': getattr(stream, 'channel_group_id', None),
                            'group_title': getattr(stream, 'group_title', None),
                            'priority': priority_index,
                        }
                        all_streams.append(stream_dict)
                        count += 1

                    logger.info(f"{PLUGIN_LOG_PREFIX} Fetched {count} streams from '{m3u_source}'")
                except Exception as e:
                    logger.error(f"{PLUGIN_LOG_PREFIX} Failed to fetch streams from '{m3u_source}': {e}")
                    raise

        logger.info(f"{PLUGIN_LOG_PREFIX} Total streams fetched: {len(all_streams)}")

        # Fetch channel groups to get ID -> name mapping
        logger.info(f"{PLUGIN_LOG_PREFIX} Fetching channel groups to resolve group names...")
        all_groups = self._get_all_groups(logger)
        group_id_to_name = {g['id']: g['name'] for g in all_groups}
        logger.info(f"{PLUGIN_LOG_PREFIX} Loaded {len(group_id_to_name)} channel groups")

        # Resolve group names for each stream
        # Try channel_group FK first, fall back to group_title text field
        def _resolve_group_name(stream_dict):
            channel_group_id = stream_dict.get('channel_group')
            if channel_group_id and channel_group_id in group_id_to_name:
                return group_id_to_name[channel_group_id]
            # Fallback: use group_title if available (text field on some Stream models)
            group_title = stream_dict.get('group_title')
            if group_title:
                return group_title
            return None

        # Collect unique M3U groups for logging
        unique_groups = set()
        for stream in all_streams:
            group_name = _resolve_group_name(stream)
            if group_name:
                unique_groups.add(group_name)

        logger.info(f"{PLUGIN_LOG_PREFIX} Found {len(unique_groups)} unique M3U groups across all streams")
        if unique_groups:
            # Show first 20 groups as samples
            sample_groups = sorted(list(unique_groups))[:20]
            logger.info(f"{PLUGIN_LOG_PREFIX} Sample M3U groups: {', '.join(sample_groups)}")
            if len(unique_groups) > 20:
                logger.info(f"{PLUGIN_LOG_PREFIX} ...and {len(unique_groups) - 20} more groups")

        # Apply M3U group filter if specified
        m3u_group_filter_str = settings.get("m3u_group_filter", "").strip()

        if m3u_group_filter_str:
            # Parse allowed M3U groups
            allowed_groups = [group.strip() for group in m3u_group_filter_str.split(',') if group.strip()]
            allowed_groups_lower = {group.lower() for group in allowed_groups}

            logger.info(f"{PLUGIN_LOG_PREFIX} Applying M3U group filter (BEFORE matching): {', '.join(allowed_groups)}")

            # Filter streams by resolved group name
            filtered_streams = []
            for stream in all_streams:
                group_name = _resolve_group_name(stream)
                if group_name and group_name.lower() in allowed_groups_lower:
                    filtered_streams.append(stream)

            logger.info(f"{PLUGIN_LOG_PREFIX} M3U group filter: kept {len(filtered_streams)} streams, filtered out {len(all_streams) - len(filtered_streams)} streams")

            # If no streams matched, show helpful message
            if len(filtered_streams) == 0:
                logger.warning(f"{PLUGIN_LOG_PREFIX} No streams matched M3U group filter '{m3u_group_filter_str}'")
                logger.warning(f"{PLUGIN_LOG_PREFIX} Available groups are listed above. Check for spelling/case differences.")

            return filtered_streams

        return all_streams

    def _match_streams_to_categories(self, streams, settings, logger):
        """
        Match stream names to channel database and extract categories.

        Returns:
            tuple: (matched_by_category dict, unmatched_streams list)
        """
        # Load channel databases if not already loaded
        if not self._load_channel_data(settings, logger):
            return {}, []

        matched_by_category = {}
        unmatched_streams = []

        total_streams = len(streams)
        logger.info(f"{PLUGIN_LOG_PREFIX} Matching {total_streams} streams to channel databases...")

        # Build fast lookup dictionaries for exact and normalized matches
        ignored_tags_str = settings.get("ignored_tags", PluginConfig.DEFAULT_IGNORED_TAGS)
        ignored_tags = [tag.strip() for tag in ignored_tags_str.split(',') if tag.strip()]

        logger.info(f"{PLUGIN_LOG_PREFIX} Building fast lookup index for {len(self.matcher.premium_channels_full)} channels...")

        # Create lookup: normalized_name -> full_channel_data
        normalized_lookup = {}
        exact_lookup = {}

        for channel_data in self.matcher.premium_channels_full:
            channel_name = channel_data.get('channel_name', '')
            if not channel_name:
                continue

            # Exact match lookup
            exact_lookup[channel_name.lower()] = channel_data

            # Normalized match lookup (uses cache when available)
            norm_lower, _ = self.matcher._get_cached_norm(channel_name, ignored_tags)
            if norm_lower:
                normalized_lookup[norm_lower] = channel_data

        logger.info(f"{PLUGIN_LOG_PREFIX} Lookup index built: {len(exact_lookup)} exact, {len(normalized_lookup)} normalized entries")

        # Pre-compute normalizations for ALL names (streams + channels) in a single pass
        # Both must be in the cache for matching to work correctly
        stream_names = [s.get('name', '').strip() for s in streams if s.get('name', '').strip()]
        all_names = stream_names + self.matcher.premium_channels
        self.matcher.precompute_normalizations(all_names, ignored_tags)
        logger.info(f"{PLUGIN_LOG_PREFIX} Pre-computed normalizations for {len(stream_names)} streams + {len(self.matcher.premium_channels)} channels")

        # Build token index on channel names for fast candidate pre-filtering
        # This reduces fuzzy matching from O(streams * channels) to O(streams * ~50-200)
        self.matcher.build_token_index(self.matcher.premium_channels, ignored_tags)

        progress = ProgressTracker(total_streams, "match_streams", logger)

        # Resolve match sensitivity to threshold
        fuzzy_threshold = self._resolve_threshold(settings, logger)
        self.matcher.match_threshold = fuzzy_threshold

        for idx, stream in enumerate(streams):
            if self._stop_event.is_set():
                logger.info(f"{PLUGIN_LOG_PREFIX} Stream matching cancelled by user.")
                break

            stream_name = stream.get('name', '').strip()

            if not stream_name:
                unmatched_streams.append({
                    'stream': stream,
                    'reason': 'Empty stream name'
                })
                progress.update()
                continue

            # Try OTA broadcast match first
            callsign, ota_station = self.matcher.match_broadcast_channel(stream_name)

            if ota_station:
                category = ota_station.get('category', 'Broadcast')

                matched_stream = {
                    'stream': stream,
                    'matched_channel': ota_station,
                    'match_type': 'Broadcast (OTA)',
                    'match_method': f"Callsign: {callsign}",
                    'category': category
                }

                if category not in matched_by_category:
                    matched_by_category[category] = []
                matched_by_category[category].append(matched_stream)
                progress.update()
                continue

            # Try premium/cable match (exact first, then normalized, then fuzzy)
            premium_channel = None
            match_method = None

            # Try exact match (fastest)
            stream_name_lower = stream_name.lower()
            if stream_name_lower in exact_lookup:
                premium_channel = exact_lookup[stream_name_lower]
                match_method = "Exact match"

            # Try normalized match (fast, uses cache)
            if not premium_channel:
                norm_lower, _ = self.matcher._get_cached_norm(stream_name, ignored_tags)
                if norm_lower and norm_lower in normalized_lookup:
                    premium_channel = normalized_lookup[norm_lower]
                    match_method = "Normalized match"

            # Try fuzzy match if not matched yet and fuzzy matching is enabled
            # Use token index to pre-filter candidates (typically ~50-200 instead of 31K)
            if not premium_channel and fuzzy_threshold > 0:
                candidates = self.matcher.get_candidates(stream_name, ignored_tags)
                if candidates is None:
                    candidates = self.matcher.premium_channels  # fallback if no index
                if candidates:
                    matched_premium_name, score, match_type = self.matcher.fuzzy_match(
                        stream_name,
                        candidates,
                        ignored_tags
                    )
                else:
                    matched_premium_name, score, match_type = None, 0, None

                if matched_premium_name and score >= fuzzy_threshold:
                    premium_channel = next(
                        (ch for ch in self.matcher.premium_channels_full if ch['channel_name'] == matched_premium_name),
                        None
                    )
                    if premium_channel:
                        match_method = f"Fuzzy: {score}% ({match_type})"

            if premium_channel:
                category = premium_channel.get('category', 'Entertainment')

                matched_stream = {
                    'stream': stream,
                    'matched_channel': premium_channel,
                    'match_type': premium_channel.get('type', 'National'),
                    'match_method': match_method,
                    'category': category
                }

                if category not in matched_by_category:
                    matched_by_category[category] = []
                matched_by_category[category].append(matched_stream)
                progress.update()
                continue

            # No match found
            unmatched_streams.append({
                'stream': stream,
                'reason': 'No match in channel databases'
            })
            progress.update()

        progress.finish()
        logger.info(f"{PLUGIN_LOG_PREFIX} Matched {len(streams) - len(unmatched_streams)} streams, {len(unmatched_streams)} unmatched")

        # Apply category filter if specified
        category_filter_str = settings.get("m3u_category_filter", "").strip()

        if category_filter_str:
            # Parse allowed categories
            allowed_categories = [cat.strip() for cat in category_filter_str.split(',') if cat.strip()]
            allowed_categories_lower = {cat.lower() for cat in allowed_categories}

            logger.info(f"{PLUGIN_LOG_PREFIX} Applying category filter: {', '.join(allowed_categories)}")

            # Filter matched_by_category to only include allowed categories
            filtered_matched = {}
            filtered_count = 0
            total_before_filter = sum(len(matches) for matches in matched_by_category.values())

            for category, matches in matched_by_category.items():
                if category.lower() in allowed_categories_lower:
                    filtered_matched[category] = matches
                    filtered_count += len(matches)
                else:
                    # Move filtered out streams to unmatched with reason
                    for match in matches:
                        unmatched_streams.append({
                            'stream': match['stream'],
                            'reason': f"Category '{category}' not in filter list"
                        })

            logger.info(f"{PLUGIN_LOG_PREFIX} Category filter: kept {filtered_count} streams in {len(filtered_matched)} categories, filtered out {total_before_filter - filtered_count} streams")

            return filtered_matched, unmatched_streams

        return matched_by_category, unmatched_streams

    def _ensure_category_groups_exist(self, categories, settings, logger):
        """
        Ensure all category-based channel groups exist in Dispatcharr.
        Create missing groups via ORM.

        If m3u_custom_group_name is set, all categories will map to that single group.

        Returns:
            dict: Mapping of category name to group ID
        """
        # Check if custom group name is specified
        custom_group_name = settings.get("m3u_custom_group_name", "").strip()

        # Fetch existing groups
        existing_groups = self._get_all_groups(logger)
        group_name_to_id = {group['name']: group['id'] for group in existing_groups}

        category_to_group_id = {}

        # If custom group name is specified, use it for all categories
        if custom_group_name:
            logger.info(f"{PLUGIN_LOG_PREFIX} Using custom group name '{custom_group_name}' for all imported streams")

            group = self._get_or_create_group(custom_group_name, logger)
            custom_group_id = group.id

            # Map all categories to the custom group
            for category in categories:
                category_to_group_id[category] = custom_group_id
        else:
            # Use category-based organization (original behavior)
            for category in categories:
                if category in group_name_to_id:
                    # Group already exists
                    category_to_group_id[category] = group_name_to_id[category]
                    logger.info(f"{PLUGIN_LOG_PREFIX} Category group '{category}' already exists (ID: {group_name_to_id[category]})")
                else:
                    # Create new group
                    group = self._get_or_create_group(category, logger)
                    category_to_group_id[category] = group.id

        return category_to_group_id

    def _get_next_channel_number(self, logger):
        """
        Get the next available channel number (highest existing + 1).

        Returns:
            float: Next channel number to use
        """
        # Use ORM to find the highest channel number
        from django.db.models import Max

        result = Channel.objects.aggregate(max_num=Max('channel_number'))
        max_channel_num = result['max_num']

        if max_channel_num is None:
            return 1.0

        try:
            next_num = float(max_channel_num) + 1.0
        except (ValueError, TypeError):
            next_num = 1.0

        logger.info(f"{PLUGIN_LOG_PREFIX} Next channel number: {next_num}")
        return next_num

    def _detect_duplicate_channels(self, channel_name, existing_channels):
        """
        Check if a channel with this name already exists.
        Generate a unique suffix if needed.

        Returns:
            tuple: (is_duplicate: bool, unique_name: str)
        """
        existing_names = {ch['name'].lower() for ch in existing_channels}

        if channel_name.lower() not in existing_names:
            return False, channel_name

        # Channel name exists - need to add suffix
        # Try numbered suffixes: [1], [2], [3], etc.
        counter = 1
        while True:
            unique_name = f"{channel_name} [{counter}]"
            if unique_name.lower() not in existing_names:
                return True, unique_name
            counter += 1

    def _import_matched_streams(self, matched_by_category, category_to_group_id, settings, logger):
        """
        Import matched streams as channels in Dispatcharr using Django ORM.

        Returns:
            dict: Import results
        """
        # Fetch existing channels to detect duplicates
        existing_channels = list(Channel.objects.all().values('id', 'name'))

        # Get starting channel number
        next_channel_num = self._get_next_channel_number(logger)

        import_results = {
            'total_imported': 0,
            'imports': []
        }

        # Calculate total streams to import for progress tracking
        total_streams_to_import = sum(len(matches) for matches in matched_by_category.values())

        progress = ProgressTracker(total_streams_to_import, "import_streams", logger)
        rate_limiter = SmartRateLimiter(settings.get("rate_limiting", "none"))

        # Get group name mapping for logging
        all_groups = self._get_all_groups(logger)
        group_id_to_name = {g['id']: g['name'] for g in all_groups}

        # Prefetch all stream objects to avoid N+1 queries in the import loop
        all_stream_ids = set()
        for matched_streams in matched_by_category.values():
            for matched in matched_streams:
                all_stream_ids.add(matched['stream']['id'])
        stream_objects = {s.id: s for s in Stream.objects.filter(id__in=all_stream_ids)}

        # Sort categories for consistent ordering
        for category in sorted(matched_by_category.keys()):
            matched_streams = matched_by_category[category]
            group_id = category_to_group_id.get(category)

            if not group_id:
                logger.warning(f"{PLUGIN_LOG_PREFIX} No group ID for category '{category}', skipping")
                continue

            group_name = group_id_to_name.get(group_id, f"ID:{group_id}")
            logger.info(f"{PLUGIN_LOG_PREFIX} Importing {len(matched_streams)} streams from '{category}' category into group '{group_name}' (ID: {group_id})...")

            # Group streams by channel name to handle duplicates from different M3U sources
            streams_by_name = {}
            for matched in matched_streams:
                stream_name = matched['stream']['name']
                if stream_name not in streams_by_name:
                    streams_by_name[stream_name] = []
                streams_by_name[stream_name].append(matched)

            # Process each unique channel name
            for channel_base_name, stream_matches in streams_by_name.items():
                # Sort by priority (lower = earlier M3U source)
                stream_matches.sort(key=lambda x: x['stream']['priority'])

                # Process each stream (creates separate channels for duplicates)
                for matched in stream_matches:
                    if self._stop_event.is_set():
                        logger.info(f"{PLUGIN_LOG_PREFIX} Import cancelled by user.")
                        return {"status": "ok", "message": f"Import cancelled. {import_results['total_imported']} channels created before cancellation."}

                    stream = matched['stream']
                    stream_id = stream['id']
                    m3u_account_id = stream.get('m3u_account', 'Unknown')
                    m3u_source = f"M3U-{m3u_account_id}" if m3u_account_id != 'Unknown' else 'Unknown'

                    # Detect duplicates and generate unique name
                    is_duplicate, unique_channel_name = self._detect_duplicate_channels(
                        channel_base_name,
                        existing_channels
                    )

                    # If duplicate, add M3U source suffix
                    if is_duplicate:
                        unique_channel_name = f"{channel_base_name} [{m3u_source}-{stream_id}]"
                        # Check again in case this specific suffix exists
                        _, unique_channel_name = self._detect_duplicate_channels(
                            unique_channel_name,
                            existing_channels
                        )

                    try:
                        # Create channel using ORM
                        with transaction.atomic():
                            new_channel = Channel.objects.create(
                                name=unique_channel_name,
                                channel_number=next_channel_num,
                                channel_group_id=group_id,
                            )

                            # Link stream to channel (uses prefetched stream objects)
                            stream_obj = stream_objects.get(stream_id)
                            if stream_obj is None:
                                logger.warning(f"{PLUGIN_LOG_PREFIX} Stream {stream_id} not found (may have been deleted), skipping")
                                progress.update()
                                continue
                            ChannelStream.objects.create(
                                channel=new_channel,
                                stream=stream_obj,
                                order=0,
                            )

                        # Success
                        import_results['total_imported'] += 1
                        import_results['imports'].append({
                            'stream_name': stream['name'],
                            'stream_id': stream_id,
                            'channel_id': new_channel.id,
                            'channel_name': unique_channel_name,
                            'channel_number': next_channel_num,
                            'category': category,
                            'group_id': group_id,
                            'm3u_source': m3u_source,
                            'is_duplicate': is_duplicate,
                            'status': 'success'
                        })

                        # Add to existing channels to prevent duplicates in this batch
                        existing_channels.append({
                            'id': new_channel.id,
                            'name': unique_channel_name
                        })

                        next_channel_num += 1.0
                        rate_limiter.wait()

                    except Exception as e:
                        logger.error(f"{PLUGIN_LOG_PREFIX} Failed to create channel from stream {stream_id}: {e}")
                        import_results['imports'].append({
                            'stream_name': stream['name'],
                            'stream_id': stream_id,
                            'channel_name': unique_channel_name,
                            'category': category,
                            'm3u_source': m3u_source,
                            'status': 'failed',
                            'error': str(e)
                        })
                    progress.update()

        progress.finish()
        return import_results

    def _export_m3u_import_preview(self, matched_by_category, unmatched_streams, category_to_group_id, settings, logger):
        """
        Export CSV preview of M3U import.

        Returns:
            tuple: (csv_path, csv_filename)
        """
        # Create export directory
        export_dir = PluginConfig.EXPORT_DIR
        os.makedirs(export_dir, exist_ok=True)

        # Create timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"channel_mapparr_m3u_import_preview_{timestamp}.csv"
        csv_path = os.path.join(export_dir, csv_filename)

        # Write CSV atomically (temp file + rename to prevent corrupt partial writes)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=export_dir, suffix='.csv', delete=False) as csvfile:
                tmp_path = csvfile.name
                # Write settings header
                csvfile.write(self._generate_csv_settings_header(settings))
                csvfile.write("#\n")
                csvfile.write("# M3U Import Preview\n")
                csvfile.write("#\n")

                fieldnames = [
                    'Stream ID',
                    'Stream Name',
                    'M3U Source',
                    'Priority',
                    'Match Type',
                    'Match Method',
                    'Category',
                    'Target Group',
                    'Group Exists',
                    'Will Import',
                    'Notes'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

                # Write matched streams
                for category in sorted(matched_by_category.keys()):
                    matched_streams = matched_by_category[category]
                    group_exists = category in category_to_group_id

                    for matched in matched_streams:
                        stream = matched['stream']
                        m3u_account_id = stream.get('m3u_account', 'Unknown')
                        m3u_source = f"M3U-{m3u_account_id}" if m3u_account_id != 'Unknown' else 'Unknown'
                        writer.writerow({
                            'Stream ID': stream.get('id', ''),
                            'Stream Name': stream.get('name', ''),
                            'M3U Source': m3u_source,
                            'Priority': stream.get('priority', 0),
                            'Match Type': matched['match_type'],
                            'Match Method': matched['match_method'],
                            'Category': category,
                            'Target Group': category,
                            'Group Exists': 'Yes' if group_exists else 'No (will create)',
                            'Will Import': 'Yes',
                            'Notes': ''
                        })

                # Write unmatched streams
                for unmatched in unmatched_streams:
                    stream = unmatched['stream']
                    m3u_account_id = stream.get('m3u_account', 'Unknown')
                    m3u_source = f"M3U-{m3u_account_id}" if m3u_account_id != 'Unknown' else 'Unknown'
                    writer.writerow({
                        'Stream ID': stream.get('id', ''),
                        'Stream Name': stream.get('name', ''),
                        'M3U Source': m3u_source,
                        'Priority': stream.get('priority', 0),
                        'Match Type': 'None',
                        'Match Method': 'No match',
                        'Category': '',
                        'Target Group': '',
                        'Group Exists': 'N/A',
                        'Will Import': 'No',
                        'Notes': unmatched['reason']
                    })
            os.replace(tmp_path, csv_path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.info(f"{PLUGIN_LOG_PREFIX} M3U import preview exported to {csv_path}")
        return csv_path, csv_filename

    def _save_m3u_import_results(self, import_results, unmatched_streams, settings):
        """
        Save import results to JSON file for later reference.

        Returns:
            str: Path to results file
        """
        results_file = "/data/channel_mapparr_m3u_import_results.json"

        results_data = {
            'processed_at': datetime.now().isoformat(),
            'total_streams_processed': import_results['total_imported'] + len(unmatched_streams),
            'total_channels_created': import_results['total_imported'],
            'total_unmatched': len(unmatched_streams),
            'm3u_sources': settings.get('m3u_sources', '(all)'),
            'channel_databases': settings.get('channel_databases', 'US'),
            'imports': import_results['imports'],
            'unmatched_streams': unmatched_streams
        }

        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results_data, f, indent=2)

        return results_file

    def import_m3u_streams_dry_run_action(self, settings, logger):
        """
        Dry run action: Preview M3U stream import without making changes.
        """
        try:
            logger.info(f"{PLUGIN_LOG_PREFIX} Starting M3U import dry run...")

            # Step 1: Fetch streams from M3U sources
            streams = self._fetch_streams_from_m3u_sources(settings, logger)

            if not streams:
                return {"status": "error", "message": "No streams found in specified M3U sources"}

            # Step 2: Match streams to categories
            matched_by_category, unmatched_streams = self._match_streams_to_categories(
                streams, settings, logger
            )

            if not matched_by_category:
                return {
                    "status": "error",
                    "message": f"No streams matched to channel databases. {len(unmatched_streams)} unmatched streams."
                }

            # Step 3: Check which category groups exist
            categories = list(matched_by_category.keys())
            existing_groups = self._get_all_groups(logger)
            existing_group_names = {group['name'] for group in existing_groups}

            category_to_group_id = {}
            for category in categories:
                if category in existing_group_names:
                    group_id = next(g['id'] for g in existing_groups if g['name'] == category)
                    category_to_group_id[category] = group_id

            # Step 4: Export CSV preview
            csv_path, csv_filename = self._export_m3u_import_preview(
                matched_by_category,
                unmatched_streams,
                category_to_group_id,
                settings,
                logger
            )

            # Calculate statistics
            total_matched = sum(len(streams) for streams in matched_by_category.values())
            groups_to_create = len([cat for cat in categories if cat not in existing_group_names])

            return {
                "status": "success",
                "message": f"✓ Preview exported to: {csv_filename}\n\n"
                          f"Total streams: {len(streams)}\n"
                          f"Matched: {total_matched}\n"
                          f"Unmatched: {len(unmatched_streams)}\n"
                          f"Categories: {len(categories)}\n"
                          f"New groups to create: {groups_to_create}"
            }

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} M3U import dry run failed: {e}")
            return {"status": "error", "message": f"Dry run failed: {str(e)}"}

    def _do_import_m3u_streams(self, settings, logger):
        """Core M3U import logic."""
        logger.info(f"{PLUGIN_LOG_PREFIX} Starting M3U stream import...")

        # Step 1: Fetch streams from M3U sources
        streams = self._fetch_streams_from_m3u_sources(settings, logger)

        if not streams:
            return {"status": "error", "message": "No streams found in specified M3U sources"}

        # Step 2: Match streams to categories
        matched_by_category, unmatched_streams = self._match_streams_to_categories(
            streams, settings, logger
        )

        if not matched_by_category:
            return {
                "status": "error",
                "message": f"No streams matched to channel databases. {len(unmatched_streams)} unmatched streams."
            }

        # Step 3: Ensure category groups exist
        categories = list(matched_by_category.keys())
        category_to_group_id = self._ensure_category_groups_exist(
            categories, settings, logger
        )

        # Step 4: Import matched streams as channels
        import_results = self._import_matched_streams(
            matched_by_category,
            category_to_group_id,
            settings,
            logger,
        )

        # Step 5: Save results to JSON
        results_file = self._save_m3u_import_results(
            import_results,
            unmatched_streams,
            settings
        )

        # Step 6: Export CSV with final results
        csv_path, csv_filename = self._export_m3u_import_preview(
            matched_by_category,
            unmatched_streams,
            category_to_group_id,
            settings,
            logger
        )

        # Calculate statistics
        total_success = sum(1 for imp in import_results['imports'] if imp['status'] == 'success')
        total_failed = sum(1 for imp in import_results['imports'] if imp['status'] == 'failed')

        return {
            "status": "success",
            "message": f"✓ M3U import complete!\n\n"
                      f"Channels created: {total_success}\n"
                      f"Failed: {total_failed}\n"
                      f"Unmatched streams skipped: {len(unmatched_streams)}\n"
                      f"Categories: {len(categories)}\n\n"
                      f"Results exported to: {csv_filename}"
        }

    def _do_import_m3u_streams_bg(self, settings, logger):
        """Background wrapper for M3U import."""
        try:
            result = self._do_import_m3u_streams(settings, logger)
            self._last_bg_result = result
            msg = result.get("message", "Import complete.")
            logger.info(f"{PLUGIN_LOG_PREFIX} IMPORT COMPLETED: {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": self.name,
                "message": msg
            })
        except Exception as e:
            self._last_bg_result = {"status": "error", "message": str(e)}
            logger.exception(f"{PLUGIN_LOG_PREFIX} Import error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": self.name,
                "message": f"Import error: {e}"
            })

    def import_m3u_streams_action(self, settings, logger):
        """Import action: Create channels from M3U streams."""
        dry_run = settings.get("dry_run_mode", False)
        if dry_run:
            return self.import_m3u_streams_dry_run_action(settings, logger)

        if not self._try_start_thread(self._do_import_m3u_streams_bg, (copy.deepcopy(settings), logger)):
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}

        return {
            "status": "ok",
            "message": "M3U import started in background. Check notifications for progress.",
            "background": True,
        }

    def validate_settings_action(self, settings, logger):
        """Comprehensive validation of plugin settings and database connectivity"""
        validation_results = []
        error_count = 0
        warning_count = 0

        try:
            # 1. Test database connectivity
            db_status = "❓ Not tested"
            try:
                channel_count = Channel.objects.count()
                group_count = ChannelGroup.objects.count()
                logo_count = Logo.objects.count()
                stream_count = Stream.objects.count()
                db_status = f"✅ DB OK ({channel_count} channels, {group_count} groups, {logo_count} logos, {stream_count} streams)"
            except Exception as e:
                logger.error(f"{PLUGIN_LOG_PREFIX} Database connectivity error: {e}")
                db_status = f"❌ DB error: {str(e)[:50]}"
                error_count += 1

            validation_results.append(db_status)

            # 2. Validate channel databases
            channel_databases_str = settings.get("channel_databases", PluginConfig.DEFAULT_CHANNEL_DATABASES).strip()
            if not channel_databases_str:
                validation_results.append("❌ No databases configured")
                error_count += 1
            else:
                country_codes = [code.strip().upper() for code in channel_databases_str.split(',') if code.strip()]
                try:
                    success = self.matcher.reload_databases(country_codes=country_codes)
                    if success:
                        premium_count = len(self.matcher.premium_channels) if hasattr(self.matcher, 'premium_channels') else 0
                        validation_results.append(f"✅ DB: {', '.join(country_codes)} ({premium_count:,} channels)")
                    else:
                        validation_results.append("❌ DB load failed")
                        error_count += 1
                except Exception as e:
                    validation_results.append(f"❌ DB error")
                    error_count += 1

            # 3. M3U filters (only show count if configured)
            m3u_info = []

            m3u_group_filter = settings.get("m3u_group_filter", "").strip()
            if m3u_group_filter:
                group_count = len([g.strip() for g in m3u_group_filter.split(',') if g.strip()])
                m3u_info.append(f"{group_count} M3U group(s)")

            m3u_category_filter = settings.get("m3u_category_filter", "").strip()
            if m3u_category_filter:
                cat_count = len([c.strip() for c in m3u_category_filter.split(',') if c.strip()])
                m3u_info.append(f"{cat_count} categor{'y' if cat_count == 1 else 'ies'}")

            m3u_custom_group = settings.get("m3u_custom_group_name", "").strip()
            if m3u_custom_group:
                m3u_info.append(f"→ '{m3u_custom_group}'")

            if m3u_info:
                validation_results.append(f"ℹ️ Filters: {', '.join(m3u_info)}")

            # 4. Dry run mode
            dry_run = settings.get("dry_run_mode", False)
            if dry_run:
                validation_results.append("ℹ️ Dry Run: ON")

            # Generate summary
            if error_count == 0 and warning_count == 0:
                validation_results.insert(0, "✅ All settings validated successfully!")
                status = "success"
            elif error_count == 0:
                validation_results.insert(0, f"⚠️ Validation completed with {warning_count} warning(s)")
                status = "success"
            else:
                validation_results.insert(0, f"❌ Validation failed: {error_count} error(s), {warning_count} warning(s)")
                status = "error"

            validation_results.insert(1, "")

            message = "\n".join(validation_results)

            return {
                "status": status,
                "message": message
            }

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error during settings validation: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "error",
                "message": f"Validation error: {e}\n\nSee logs for details."
            }

    def clear_csv_exports_action(self, settings, logger):
        """Delete all CSV export files created by this plugin"""
        try:
            export_dir = PluginConfig.EXPORT_DIR

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
