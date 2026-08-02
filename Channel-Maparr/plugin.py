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
from datetime import datetime

# Import the fuzzy matcher module
from .fuzzy_matcher import FuzzyMatcher
from .progress_status import (
    build_status_message, load_progress, save_progress_atomic,
)
from .group_scope import (
    GroupScopeError,
    build_name_to_ids,
    is_ignored_name,
    is_ignored_name_tokens,
    parse_tokens,
    resolve_group_scope,
    split_rows_by_ignore,
)
from .wildcard_match import expand_patterns

# Django model imports
from apps.channels.models import Channel, ChannelGroup, Logo, Stream, ChannelStream
from apps.m3u.models import M3UAccount
from django.db import transaction
from core.utils import send_websocket_update

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.channel_mapparr")

# Plugin name prefix for all log messages
PLUGIN_LOG_PREFIX = "[Channel Mapparr]"

# Persistent progress file — ProgressTracker writes here so the Show Status
# action can report live state from any context, including after a worker
# restart mid-operation. Lives in /data because the plugin directory itself
# is owned by root and not writable by the dispatch uwsgi user.
PROGRESS_FILE = "/data/channel_mapparr_progress.json"

# Dispatcharr clips action toasts at roughly 280 characters from the MIDDLE
# with no visual marker, so a name list that enumerates every match of a
# wildcard ignore token (e.g. "Sport*") could silently truncate the more
# important parts of the message. Cap enumeration and fall back to a count.
_MAX_NAMES_IN_MESSAGE = 5


# Severity glyphs that validate_settings_action's report lines are built with.
# The final assembly classifies lines by these prefixes, so a new validation
# line MUST start with one of them or it will be silently dropped from the
# operator-facing output.
_VALIDATION_ERROR_GLYPH = "❌"      # cross mark
_VALIDATION_WARNING_GLYPH = "⚠"    # warning sign (with or without U+FE0F)


def _format_capped_name_list(names, limit=_MAX_NAMES_IN_MESSAGE):
    """Join up to `limit` names, then summarize the rest as a count."""
    names = list(names)
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f" and {len(names) - limit} more"
    return shown


class PluginConfig:
    """Configuration constants for Channel Maparr."""

    PLUGIN_VERSION = "1.26.2141319"

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
    EXPORT_DIR = "/data/exports"

    # Emailed reports, delivered by the Newsflasharr plugin.
    # The master toggle is OFF by default on purpose: a released plugin must not
    # begin writing into another plugin's queue the moment it is upgraded.
    DEFAULT_NOTIFY_ENABLED = False
    # There is no scheduler in this plugin, so "scheduled" is not an option.
    DEFAULT_NOTIFY_REPORT_ON = "every_run"
    # One format, so one run sends one email. A notification carries a single
    # attachment, and an attachment bearing event bypasses Newsflasharr's hourly
    # cap and its quiet hours, so the email count cannot be throttled from the
    # service side.
    DEFAULT_NOTIFY_REPORT_FORMAT = "html"

    # tv-logos GitHub repo for per-channel logo lookup
    TV_LOGOS_REPO = "tv-logo/tv-logos"
    TV_LOGOS_BRANCH = "main"
    COUNTRY_DIR_MAP = {
        "US": "united-states", "CA": "canada", "UK": "united-kingdom",
        "GB": "united-kingdom", "AU": "australia", "DE": "germany",
        "FR": "france", "IT": "italy", "ES": "spain", "MX": "mexico",
        "BR": "brazil", "IN": "india", "IE": "ireland", "NL": "netherlands",
        "NO": "norway",
    }

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
            self._persist({
                "status": "running",
                "action": self.action_id,
                "current": self.processed_items,
                "total": self.total_items,
                "start_time": self.start_time,
                "updated_at": now,
            })

    def finish(self, summary=None):
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
        self._persist({
            "status": "done",
            "action": self.action_id,
            "current": self.processed_items,
            "total": self.total_items,
            "start_time": self.start_time,
            "finished_at": time.time(),
            "summary": summary or f"Processed {self.processed_items}/{self.total_items} in {eta_str}",
        })

    def _persist(self, data):
        try:
            save_progress_atomic(PROGRESS_FILE, data)
        except Exception as exc:
            # WARNING on first failure (silent failures hide a broken Show
            # Status action). Suppress repeats — update() ticks per-item on
            # 17K+ stream runs and would otherwise flood the log.
            if not getattr(self, "_persist_warned", False):
                self.logger.warning(f"{PLUGIN_LOG_PREFIX} progress file write failed at {PROGRESS_FILE}: {exc}")
                self._persist_warned = True
            else:
                self.logger.debug(f"{PLUGIN_LOG_PREFIX} progress file write retry failed: {exc}")

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
        """Build the fields list. Reads the DB for M3U source options.

        There is deliberately NO version display and NO update check here. This
        property is on Dispatcharr's per-request hot path, and it used to make a
        live call to GitHub's releases API (plus a /data cache write) every time
        the settings page was read, so plugin settings could not render without
        outbound network access and a slow or hung GitHub stalled the request.
        The installed version is already shown by Dispatcharr's own plugin card,
        so repeating it as a settings field was noise.
        `tests/test_plugin_contract.py::test_no_update_check_remains` and
        `::test_no_version_field_in_the_settings_form` keep it that way.
        """
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
                "help_text": "Comma-separated. Limits rename/logo actions to these groups. Leave empty for all. Use 'Channel Groups to Ignore' to exclude instead.",
            },
            {
                "id": "ignore_groups",
                "label": "Channel Groups to Ignore",
                "type": "string",
                "default": "",
                "placeholder": "Teamarr, PPV*",
                "help_text": (
                    "Comma-separated. Channels in these groups are excluded from "
                    "renaming, tagging, logos and Organize by Category, regardless "
                    "of 'Channel Groups to Process' or 'Category Organization "
                    "Groups'. Supports * and ? wildcards; matching is "
                    "case-insensitive. An entry that matches no channel group "
                    "refuses most actions. Organize by Category skips an ignored "
                    "target group and continues. Import M3U Streams does not "
                    "check entries against every group; it only refuses if its "
                    "own target group is ignored."
                ),
            },
            {
                "id": "category_groups",
                "label": "Category Organization Groups",
                "type": "string",
                "default": "",
                "placeholder": "Locals, News, Entertainment",
                "help_text": "Source groups for category-based reorganization. Leave empty for all. Use 'Channel Groups to Ignore' to exclude instead.",
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
            {
                "id": "notify_enabled",
                "label": "Send notifications to Newsflasharr",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_NOTIFY_ENABLED,
                "help_text": "Requires the Newsflasharr plugin, which is what "
                             "actually sends the mail. What routes where is "
                             "configured in Newsflasharr's routing rules, keyed on "
                             "this plugin's name. Channel Mapparr does not require "
                             "Newsflasharr to be installed: with it absent or "
                             "disabled, nothing is sent and nothing fails.",
            },
            {
                "id": "notify_report_on",
                "label": "Email A Report After",
                "type": "select",
                "default": PluginConfig.DEFAULT_NOTIFY_REPORT_ON,
                "options": [
                    {"value": "never", "label": "Never, do not email reports"},
                    {"value": "every_run", "label": "Every run that produces an export"},
                ],
                "help_text": "Which runs email a report. The emailed report is built "
                             "specifically for sending: it never contains your M3U "
                             "source names, which the CSV exports in /data/exports do "
                             "contain in their settings header. Organize by Category "
                             "reports only in Dry Run, because a real run of it "
                             "produces no export. This setting does nothing unless "
                             "Send notifications to Newsflasharr is on above.",
            },
            {
                "id": "notify_report_format",
                "label": "Email Report Format",
                "type": "select",
                "default": PluginConfig.DEFAULT_NOTIFY_REPORT_FORMAT,
                "options": [
                    {"value": "html", "label": "HTML page only, one email"},
                    {"value": "csv", "label": "CSV only, one email"},
                    {"value": "both", "label": "Both, which arrives as two emails"},
                ],
                "help_text": "Which report file to email. A notification carries one "
                             "attachment, so choosing both sends two separate emails "
                             "per run rather than one email with two files. The HTML "
                             "page is easier to read and the CSV is easier to sort and "
                             "filter. Both files are written to "
                             "/data/channel_mapparr_reports either way; this setting "
                             "only decides which are emailed.",
            },
        ]

    # Actions for Dispatcharr UI. `label` is the action title; `button_label`
    # is the text on the actual button (otherwise Dispatcharr renders "Run").
    actions = [
        {
            "id": "validate_settings",
            "label": "Validate Settings",
            "description": "Check database connectivity, channel databases, and settings before running any action.",
            "button_label": "\u2705 Validate",
            "button_variant": "outline",
            "button_color": "blue",
        },
        {
            "id": "load_and_process_channels",
            "label": "Load & Process Channels",
            "description": "Scan channel groups and determine standardized names.",
            "button_label": "\u25b6 Load & Process",
            "button_variant": "filled",
            "button_color": "green",
        },
        {
            "id": "rename_channels",
            "label": "Rename Channels",
            "description": "Apply standardized names to processed channels. Dry Run exports a CSV preview instead.",
            "button_label": "\u270f\ufe0f Rename",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will rename channels to the standardized format. This action is irreversible. Continue?"},
        },
        {
            "id": "rename_unknown_channels",
            "label": "Tag Unknown Channels",
            "description": "Append the configured suffix to unmatched OTA and premium channels.",
            "button_label": "\u2696 Tag Unknowns",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will append the configured suffix to unmatched channels. Continue?"},
        },
        {
            "id": "apply_logos",
            "label": "Apply Default Logo",
            "description": "Assign the configured default logo to channels that don't have one.",
            "button_label": "\u2b50 Apply Default Logo",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will apply the default logo to channels without a logo. Continue?"},
        },
        {
            "id": "apply_tv_logos",
            "label": "Apply Per-Channel Logos (tv-logos)",
            "description": "Fuzzy-match each channel name to the tv-logo/tv-logos GitHub repo and assign per-channel logos. Uses the country codes from Channel Databases. Channels with an existing logo are left alone.",
            "button_label": "\u2756 Apply Per-Channel Logos",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will fetch tv-logos and assign per-channel logos to channels without one. Continue?"},
        },
        {
            "id": "organize_by_category",
            "label": "Organize by Category",
            "description": "Move channels into category-based groups. Runs in background. Dry Run exports a CSV preview.",
            "button_label": "\u2630 Organize",
            "button_variant": "filled",
            "button_color": "green",
            "confirm": {"message": "This will create new groups (if needed) and move channels to category-based groups. Continue?"},
            "background": True,
        },
        {
            "id": "import_m3u_streams",
            "label": "Import M3U Streams",
            "description": "Create channels from M3U streams organized by category. Runs in background. Dry Run exports a CSV preview.",
            "button_label": "\u21e9 Import Streams",
            "button_variant": "filled",
            "button_color": "violet",
            "confirm": {"message": "This will create new channels from M3U streams and organize them into groups. Duplicates get suffixes. Continue?"},
        },
        {
            "id": "plugin_status",
            "label": "Show Status",
            "description": "Show live progress and ETA for the most recent or running operation. Reads a persistent progress file so you can check without watching container logs.",
            "button_label": "\u24d8 Status",
             "button_variant": "outline", "button_color": "blue",
        },
        {
            "id": "email_report_now",
            "label": "Email Report Now",
            "button_label": "✉ Email Now",
            "button_variant": "outline",
            "button_color": "cyan",
            "description": "Build a report from the last processed channels and "
                           "queue it for email. Requires the Newsflasharr plugin "
                           "installed and enabled, its email settings configured, "
                           "and a routing rule sending this plugin to email. This "
                           "button checks all of that first and refuses rather than "
                           "queueing a report nobody receives. It changes nothing. "
                           "Queued means written to Newsflasharr's queue, not yet in "
                           "your inbox. It does NOT prove the automatic path works, "
                           "because it runs here in the web worker using the settings "
                           "currently on screen.",
        },
        {
            "id": "clear_csv_exports",
            "label": "Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin.",
            "button_label": "\u2717 Clear CSVs",
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

    def _generate_csv_settings_header(self, settings):
        """Generate CSV header comments with plugin settings"""
        # Map field IDs to their labels
        field_labels = {
            'channel_databases': 'Channel Databases',
            'match_sensitivity': 'Match Sensitivity',
            'selected_groups': 'Channel Groups to Process',
            'ignore_groups': 'Channel Groups to Ignore',
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
    # EMAILED REPORTS (via the Newsflasharr plugin)
    #
    # Everything here is written so a failure is REPORTED rather than thrown.
    # Reporting is not this plugin's real work, and a bug in the report path must
    # never break the run that produced the data.
    #
    # The modules are imported lazily rather than at the top of this file so that
    # a deploy which somehow missed one of them degrades to "no reports" instead
    # of breaking the whole plugin at import time.
    # ========================================

    @staticmethod
    def _notify_client():
        """The vendored Newsflasharr client. Never hand-edit the vendored copy."""
        try:
            from . import notify_client
        except ImportError:
            import notify_client
        return notify_client

    @staticmethod
    def _notify_bridge():
        """This plugin's emit layer."""
        try:
            from . import notify_bridge
        except ImportError:
            import notify_bridge
        return notify_bridge

    @staticmethod
    def _reports():
        """The report model and renderers."""
        try:
            from . import reports
        except ImportError:
            import reports
        return reports

    def _report_dir(self):
        """Where report files are written. A method so a test can redirect it."""
        return self._reports().REPORT_DIR

    def _notify_send(self, **kwargs):
        """One seam in front of the vendored client's notify().

        A seam rather than a direct call so a test can observe what would be
        queued without needing a queue directory on disk.
        """
        return self._notify_client().notify(**kwargs)

    def _notifier_alive(self):
        """Is Newsflasharr's collector actually running?

        notify() CREATES the queue directory it writes into, so it returns True
        with Newsflasharr absent, disabled, or its collector dead, and the event
        then sits in a directory nobody reads. This is the one check between
        queueing and the inbox that an operator can act on.
        """
        try:
            return bool(self._notify_client().notifier_alive())
        except Exception:
            return False

    def _get_m3u_account_names(self, logger):
        """Return the M3U account names, or None when the lookup FAILED.

        None and [] are deliberately different. An empty list is a legitimate
        installation with no M3U accounts. None means the lookup raised, and the
        caller must refuse to build a report rather than send one whose scrub was
        a silent no-op: these names are the primary redaction input for an
        emailed report, not a backstop.

        This is a separate read on purpose. The `fields` property also lists M3U
        accounts, but it runs on Dispatcharr's per-request hot path and must not
        be called from here.
        """
        try:
            return [row["name"] for row in M3UAccount.objects.all().values("name")
                    if row.get("name")]
        except Exception as error:
            logger.warning(f"{PLUGIN_LOG_PREFIX} Could not read the M3U account "
                           f"names, so no report will be built: {error}")
            return None

    @staticmethod
    def _read_newsflasharr_config():
        """Read Newsflasharr's stored configuration. Returns None when absent.

        Read only on another plugin's configuration row, which is allowed;
        nothing here writes. Raises when the registry itself cannot be reached,
        so the caller can tell "not installed" from "could not look".
        """
        from apps.plugins.models import PluginConfig as StoredPluginConfig
        row = StoredPluginConfig.objects.filter(key="newsflasharr").first()
        if row is None:
            return None
        settings = getattr(row, "settings", None)
        return {"enabled": bool(getattr(row, "enabled", False)),
                "settings": settings if isinstance(settings, dict) else {}}

    def _newsflasharr_readiness(self):
        """Everything that must be true for an emailed report to actually arrive.

        Returns a list of blocking problems, empty when the path is clear.

        This exists because every one of these failures is otherwise invisible
        from this side. A missing routing rule in particular: the queue write
        succeeds, Newsflasharr records a delivery, and the mail is simply sent
        somewhere other than the inbox. Attachments are email only, so an
        unrouted report also arrives with no file at all.

        Never echo a settings VALUE here. smtp_password is one of the keys being
        checked and only its presence is ever reported.
        """
        bridge = self._notify_bridge()
        try:
            config = self._read_newsflasharr_config()
        except Exception as error:
            return [f"Could not read Newsflasharr's configuration: {error}"]
        if config is None:
            return ["Newsflasharr is not installed, and it is what actually "
                    "sends the mail."]

        problems = []
        if not config.get("enabled"):
            problems.append("Newsflasharr is installed but not enabled.")
        nf_settings = config.get("settings") or {}
        missing = [key for key in bridge.SMTP_REQUIRED
                   if not str(nf_settings.get(key) or "").strip()]
        if missing:
            problems.append("Newsflasharr's email settings are not complete "
                            "(missing: " + ", ".join(missing) + ").")
        elif not bridge.routes_to_smtp(nf_settings):
            problems.append(
                f"Newsflasharr has no routing rule sending {bridge.SOURCE}'s "
                f"{bridge.EVENT} to email, and email is not among its default "
                "channels, so the report would be delivered somewhere else and "
                "without its attachment.")
        return problems

    def _resolved_databases(self, settings):
        """The country codes that actually have a database file on disk.

        The raw `channel_databases` setting is free text and is never echoed into
        a report. Resolving it here means the report states what was really
        loaded rather than what somebody typed.
        """
        raw = str(settings.get("channel_databases")
                  or PluginConfig.DEFAULT_CHANNEL_DATABASES)
        plugin_dir = os.path.dirname(__file__)
        resolved = []
        for code in [part.strip().upper() for part in raw.split(",") if part.strip()]:
            if os.path.isfile(os.path.join(plugin_dir, f"{code}_channels.json")):
                resolved.append(code)
        return resolved

    def _build_and_emit_report(self, settings, logger, *, title, columns, rows,
                               export_filename=None, report_dir=None):
        """Build the report files and queue one notification per file.

        Returns {"sent", "skipped_reason", "blocking_error"}. Never raises.

        `blocking_error` is set only when the operator has asked for reports and
        the mail could not possibly arrive. That case has to reach the persistent
        red area of the plugin card, because a four second green toast is not a
        surface for it.

        Nothing is built when the report would not be sent. Building costs work,
        and there is no point paying for a report nobody will receive.
        """
        outcome = {"sent": 0, "skipped_reason": None, "blocking_error": None}
        try:
            bridge = self._notify_bridge()
            allowed, reason = bridge.should_emit(settings)
            if not allowed:
                outcome["skipped_reason"] = reason
                return outcome

            problems = self._newsflasharr_readiness()
            if problems:
                outcome["blocking_error"] = ("Report not queued. " + " ".join(problems))
                outcome["skipped_reason"] = outcome["blocking_error"]
                logger.warning(f"{PLUGIN_LOG_PREFIX} {outcome['blocking_error']}")
                return outcome

            account_names = self._get_m3u_account_names(logger)
            if account_names is None:
                outcome["skipped_reason"] = (
                    "the M3U account name lookup failed, so the report was not "
                    "built rather than sent without its redaction")
                return outcome

            reports = self._reports()
            now = time.time()
            model = reports.build_model(
                title, columns, rows,
                account_names=account_names,
                settings=settings,
                databases=self._resolved_databases(settings),
                version=getattr(self, "version", PluginConfig.PLUGIN_VERSION),
                now=now,
                export_filename=export_filename)
            written = reports.write_report(
                model, report_dir or self._report_dir(), now)
            if written.get("error"):
                logger.warning(f"{PLUGIN_LOG_PREFIX} Report not written: "
                               f"{written['error']}")
                outcome["skipped_reason"] = written["error"]
                return outcome

            emitted = bridge.emit_reports(self._notify_send, settings, written)
            outcome["sent"] = emitted["sent"]
            outcome["skipped_reason"] = emitted["skipped_reason"]
            logger.info(f"{PLUGIN_LOG_PREFIX} Report: {emitted['sent']} "
                        f"notification(s) queued for delivery")
        except Exception as error:
            logger.warning(f"{PLUGIN_LOG_PREFIX} Report emit suppressed: {error}")
            outcome["skipped_reason"] = (
                f"the report path raised and was contained: {error}")
        return outcome

    @staticmethod
    def _report_outcome_clause(outcome):
        """A very short clause to append to an action's own message.

        Dispatcharr shows roughly 280 characters of a toast, clipped from the
        middle with no ellipsis, so this must stay small. It returns an empty
        string when the operator has not switched notifications on, so somebody
        who never opted in never sees report chatter.

        It says QUEUED, never sent. A True from notify() means durably written to
        Newsflasharr's queue; delivery happens later on its retry ladder.
        """
        outcome = outcome or {}
        if outcome.get("sent"):
            return f"\nReport queued ({outcome['sent']})."
        reason = outcome.get("skipped_reason") or ""
        if not reason or "switched off" in reason:
            return ""
        return f"\nReport not queued: {reason}"

    def email_report_now_action(self, settings, logger):
        """Build a report from the last processed channels and queue it now.

        It BUILDS a fresh report rather than re-sending the newest files on disk.
        Re-sending races the pruner: the newest file on disk is by definition old
        enough to be prune eligible, so a later run could delete it while its
        mail was still being retried, and the attachment would silently vanish.

        It refuses BEFORE doing any work when the mail could not arrive, because
        a missing routing rule is otherwise invisible: the queue write succeeds
        and the mail is simply delivered somewhere else.

        It never writes any kind of "the automatic path ran" marker. Pressing a
        button must not be able to look like the ordinary path working.
        """
        try:
            bridge = self._notify_bridge()
            if not bridge.is_enabled(settings):
                return {"status": "error",
                        "error": "Send notifications to Newsflasharr is switched "
                                 "off, so there is nothing to email with."}

            problems = self._newsflasharr_readiness()
            if problems:
                return {"status": "error",
                        "error": "Report not queued. " + " ".join(problems)}

            if not self._notifier_alive():
                return {"status": "error",
                        "error": "Newsflasharr's collector is not running, so a "
                                 "queued report would sit unread. Check that the "
                                 "Newsflasharr plugin is enabled and its collector "
                                 "is running, then try again."}

            if not os.path.exists(self.results_file):
                return {"status": "error",
                        "error": "No processed channels found. Run "
                                 "'Load/Process Channels' first, then press this."}

            with open(self.results_file, "r") as handle:
                data = json.load(handle)
            changes = data.get("changes", [])
            if not changes:
                return {"status": "error",
                        "error": "The last run produced no channel changes, so "
                                 "there is nothing to report on."}

            # Pressing the button IS the request, so the "Email A Report After"
            # setting is overridden for this one call. The master toggle above is
            # NOT overridden: that one is the operator's opt in.
            forced = dict(settings)
            forced["notify_report_on"] = "every_run"

            outcome = self._build_and_emit_report(
                forced, logger,
                title="Rename preview",
                columns=self._RENAME_REPORT_COLUMNS,
                rows=changes)

            if outcome["blocking_error"]:
                return {"status": "error", "error": outcome["blocking_error"]}
            if not outcome["sent"]:
                return {"status": "error",
                        "error": "Report not queued: "
                                 + (outcome["skipped_reason"] or "unknown reason")}
            return {"status": "success",
                    "message": f"Report queued ({outcome['sent']}). Queued means "
                               "written to Newsflasharr's queue, not yet in your "
                               "inbox. This does not prove the automatic path "
                               "works."}
        except Exception as error:
            logger.error(f"{PLUGIN_LOG_PREFIX} Email Report Now failed: {error}")
            return {"status": "error", "error": f"Email Report Now failed: {error}"}

    # The allow lists that decide what may leave the box. A row key absent from
    # these pairs is never copied into a report, whatever the row carries, so a
    # column added to a CSV writer later cannot start being emailed on its own.
    _RENAME_REPORT_COLUMNS = [
        ("channel_id", "Channel ID"),
        ("channel_number", "Channel Number"),
        ("channel_group", "Group"),
        ("current_name", "Current Name"),
        ("new_name", "New Name"),
        ("status", "Status"),
        ("matcher", "Matcher"),
        ("match_method", "Match Method"),
        ("reason", "Reason"),
    ]

    _CATEGORY_REPORT_COLUMNS = [
        ("channel_id", "Channel ID"),
        ("channel_name", "Channel Name"),
        ("current_group", "Current Group"),
        ("new_group", "New Group"),
        ("category", "Category"),
        ("match_type", "Match Type"),
        ("match_value", "Match Value"),
        ("group_exists", "Group Exists"),
    ]

    @staticmethod
    def _m3u_report_rows(matched_by_category, unmatched_streams):
        """Flatten the M3U import structures into report rows.

        The M3U account is deliberately NOT carried into the report. The CSV
        export records it as "M3U-<account id>", which is harmless in itself, but
        the report has no use for it and an allow list is only worth having if it
        stays narrow.
        """
        rows = []
        for category in sorted(matched_by_category or {}):
            for matched in matched_by_category[category]:
                stream = matched.get("stream", {})
                rows.append({
                    "stream_id": stream.get("id", ""),
                    "stream_name": stream.get("name", ""),
                    "priority": stream.get("priority", 0),
                    "match_type": matched.get("match_type", ""),
                    "match_method": matched.get("match_method", ""),
                    "category": category,
                    "target_group": category,
                    "will_import": "Yes",
                    "notes": "",
                })
        for unmatched in unmatched_streams or []:
            stream = unmatched.get("stream", {})
            rows.append({
                "stream_id": stream.get("id", ""),
                "stream_name": stream.get("name", ""),
                "priority": stream.get("priority", 0),
                "match_type": "",
                "match_method": "No match",
                "category": "",
                "target_group": "",
                "will_import": "No",
                "notes": unmatched.get("reason", ""),
            })
        return rows

    _M3U_REPORT_COLUMNS = [
        ("stream_id", "Stream ID"),
        ("stream_name", "Stream Name"),
        ("priority", "Priority"),
        ("match_type", "Match Type"),
        ("match_method", "Match Method"),
        ("category", "Category"),
        ("target_group", "Target Group"),
        ("will_import", "Will Import"),
        ("notes", "Notes"),
    ]

    # ========================================
    # ORM HELPER METHODS
    # ========================================

    def _get_all_groups(self, logger):
        """Fetch all channel groups via Django ORM."""
        return list(ChannelGroup.objects.all().values('id', 'name'))

    _INCLUDE_KEYS = frozenset({"selected_groups", "category_groups"})
    _INCLUDE_LABELS = {
        "selected_groups": "Channel Groups to Process",
        "category_groups": "Category Organization Groups",
    }

    def _resolve_group_scope(self, settings, logger, include_key):
        """Resolve the channel-group scope for an action.

        Raises GroupScopeError when the configured scope cannot be honoured; the
        caller turns that into a visible error via _scope_error_return.
        """
        if include_key not in self._INCLUDE_KEYS:
            raise ValueError(f"unknown include_key {include_key!r}")

        name_to_ids = build_name_to_ids(self._get_all_groups(logger))
        scope = resolve_group_scope(
            (settings.get(include_key) or ""),
            (settings.get("ignore_groups") or ""),
            name_to_ids,
            include_label=self._INCLUDE_LABELS[include_key],
        )
        logger.info(f"{PLUGIN_LOG_PREFIX} Scope: {scope.info}")
        for name in scope.out_of_scope_names:
            logger.info(
                f"{PLUGIN_LOG_PREFIX} Ignored group '{name}' was already outside "
                f"the selected scope - no effect."
            )
        return scope

    def _resolve_process_scope(self, settings, logger):
        """Scope for the scan / rename / logo actions."""
        return self._resolve_group_scope(settings, logger, "selected_groups")

    def _resolve_category_scope(self, settings, logger):
        """Scope for the Organize-by-Category actions."""
        return self._resolve_group_scope(settings, logger, "category_groups")

    @staticmethod
    def _scope_error_return(exc):
        """`error`, not `message` - `status` renders nowhere on the plugin card."""
        return {"status": "error", "error": str(exc)}

    def _ignore_tokens_error(self, settings, logger):
        """Refuse if 'Channel Groups to Ignore' names a group absent from the DB.

        The file-driven actions (Preview / Rename / Tag Unknown) resolve the
        exclusion via split_rows_by_ignore against the group names PRESENT IN
        THE RESULTS FILE, which deliberately never refuses on a token absent
        from that file - a stale file may legitimately contain no rows from a
        named group. But that means a typo'd token would otherwise pass those
        three actions silently while every DB-scoped action (which validates
        against every real group via resolve_group_scope) refuses. This
        validates the tokens against the database FIRST, before the
        file-scoped split, so all six mutating/preview actions agree on what
        counts as an unresolvable exclusion. Returns an error dict, or None
        when the tokens are fine (or there are none).
        """
        tokens = parse_tokens(settings.get("ignore_groups") or "")
        if not tokens:
            return None
        names = list(build_name_to_ids(self._get_all_groups(logger)))
        _, unmatched = expand_patterns(tokens, names, ci_plain=True)
        if unmatched:
            return {"status": "error", "error":
                    "These entries in 'Channel Groups to Ignore' match no "
                    f"channel group: {', '.join(unmatched)}."}
        return None

    def _get_all_channels(self, logger, group_ids=None, include_ungrouped=False):
        """Fetch channels via Django ORM, optionally filtered by group IDs.

        group_ids=None means "no scope" (every channel). An EMPTY set means "a
        scope that resolved to nothing" and returns nothing - `if group_ids:`
        collapsed those two cases and silently widened the scope to every channel
        in the database (bug-044).

        include_ungrouped keeps channels whose channel_group_id is NULL. A blank
        include filter used to pass group_ids=None, which included them; once an
        explicit id set is always passed they would silently vanish, and no
        exclusion can name a NULL group anyway.
        """
        qs = Channel.objects.all()
        scoped = group_ids is not None

        if scoped and not include_ungrouped:
            if not group_ids:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} Group scope resolved to zero groups - "
                    f"no channels will be processed."
                )
            qs = qs.filter(channel_group_id__in=group_ids)

        rows = list(qs.values(
            'id', 'name', 'channel_number', 'channel_group_id', 'logo_id'))

        if scoped and include_ungrouped:
            keep = set(group_ids)
            rows = [
                r for r in rows
                if r.get('channel_group_id') in keep
                or r.get('channel_group_id') is None
            ]
            if not group_ids:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} Group scope resolved to zero groups - "
                    f"only ungrouped channels will be processed."
                )
        return rows

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
        """Extract the primary network from a messy FCC affiliation string.

        The raw field varies a lot: subchannel maps ("CBS Ch 3.1, CW/MTN Ch
        3.2"), multi-net joins ("CBS & FOX", "CBS. FOX, CW", "ABC,CBS,CW"),
        callsign-prefixed ("KALB/NBC"), legacy D-prefixes ("D1-CBS"), and
        annotated ("ABC (main) CBS (multicast)"). We want the first real network
        token. (When the stream itself states a network, the caller overrides
        this via ``_format_ota_name(network_override=...)``.)
        """
        if not network_affiliation:
            return None

        s = network_affiliation.strip()

        # Legacy subchannel prefixes: "D1-CBS" / "WXYZ-TV D1 - CBS"
        s = re.sub(r'^D\d+-', '', s)
        s = re.sub(r'^[KW][A-Z]{3,4}(?:-(?:TV|CD|LP|DT|LD))?\s+D\d+\s*-\s*', '', s)

        # Drop subchannel position markers anywhere: "Ch 3.1", "CH 2.2", "Channel 5"
        s = re.sub(r'\b(?:CH|CHANNEL)\s*\d+(?:\.\d+)?', ' ', s, flags=re.IGNORECASE)

        # Drop parenthetical annotations: "(main)", "(multicast)"
        s = re.sub(r'\([^)]*\)', ' ', s)

        # Tokenize on network separators. NOT hyphen — that would split a "-TV"
        # callsign suffix; NOT bare digits handled above.
        tokens = [t for t in re.split(r'[;,/&.]|\s+', s) if t.strip()]

        # Drop leading callsign-shaped tokens ("KALB", "WXYZ-TV") — station, not network.
        while tokens and re.fullmatch(r'[KW][A-Z]{2,3}(?:-(?:TV|CD|LP|DT|LD)\d*)?', tokens[0].upper()):
            tokens.pop(0)

        if not tokens:
            return None

        network = re.sub(r'\s+(?:Television\s+)?Network\s*$', '', tokens[0], flags=re.IGNORECASE).strip()
        network = network.upper()

        return network if network else None


    # Broadcast networks a stream may explicitly state as its leading token. Used
    # to override the FCC station's primary affiliation when a provider labels a
    # subchannel — "US: CBS 7 (WBBJ-DT3)" carries CBS even though WBBJ's main
    # affiliation is ABC — and to dodge malformed multi-network affiliation strings.
    _STREAM_NETWORKS = frozenset({
        "ABC", "CBS", "NBC", "FOX", "CW", "PBS", "ION",
        "MYTV", "MYNETWORKTV", "TELEMUNDO", "UNIVISION", "UNIMAS",
    })

    def _extract_stream_network(self, channel_name):
        """Return the network a stream name explicitly claims, or None.

        Reads the leading token after an optional geo/provider prefix
        ("US: CBS 7 ..." -> "CBS"). Only recognized broadcast networks count, so a
        callsign-led name ("WABC-TV") returns None. A network used *as* the prefix
        ("CBS: ...") is not mistaken for a geo code and stripped.
        """
        if not channel_name:
            return None
        s = channel_name.strip()
        m_geo = re.match(r'^\s*[\[(]?([A-Za-z]{2,3})[\])]?\s*[:|]\s*(.*)$', s)
        if m_geo and m_geo.group(1).upper() not in self._STREAM_NETWORKS:
            s = m_geo.group(2)
        m = re.match(r'([A-Za-z]{2,12})', s)
        if not m:
            return None
        token = m.group(1).upper()
        return token if token in self._STREAM_NETWORKS else None

    def _format_ota_name(self, station_data, format_string, callsign, network_override=None):
        """
        Format OTA channel name using the provided format string.
        Returns None if any required field is missing.

        ``network_override`` (the network the stream itself states) wins over the
        FCC station's primary affiliation, which can disagree for subchannels.
        """
        # Parse format string to find required fields
        required_fields = re.findall(r'\{(\w+)\}', format_string)

        # Get data from station
        network_raw = station_data.get('network_affiliation', '').strip()
        network = network_override or self._parse_network_affiliation(network_raw)
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
                "apply_tv_logos": self.apply_tv_logos_action,
                "organize_by_category": self.organize_by_category_action,
                "import_m3u_streams": self.import_m3u_streams_action,
                "plugin_status": self.plugin_status_action,
                "email_report_now": self.email_report_now_action,
                "clear_csv_exports": self.clear_csv_exports_action,
            }

            handler = action_map.get(action)
            if not handler:
                logger.warning(f"{PLUGIN_LOG_PREFIX} Unknown action: {action}")
                return {"status": "error", "error": f"Unknown action: {action}"}

            logger.info(f"{PLUGIN_LOG_PREFIX} Action triggered: {action}")
            result = handler(settings, logger)

            status = result.get("status", "?") if isinstance(result, dict) else "ok"
            msg = (result.get("message") or result.get("error", ""))[:200] if isinstance(result, dict) else ""
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
            return {"status": "error", "error": str(e)}

    def load_and_process_channels_action(self, settings, logger):
        """Load channels from database and process them with channel data."""
        try:


            # Load channel data from selected country databases
            channels_loaded = self._load_channel_data(settings, logger)

            if not channels_loaded:
                return {"status": "error", "error": "Channel databases could not be loaded. Please check your channel_databases setting and ensure the files exist."}

            logger.info(f"{PLUGIN_LOG_PREFIX} Loading channels from database...")

            # Get all groups first to build the id-to-name mapping used below
            all_groups = self._get_all_groups(logger)
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}
            self.group_name_map = group_id_to_name

            # Resolve the group scope (include filter minus ignore_groups)
            try:
                scope = self._resolve_process_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )

            channels_to_process = all_channels
            logger.info(
                f"{PLUGIN_LOG_PREFIX} Filtered to {len(channels_to_process)} "
                f"channels ({scope.info})"
            )

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
                            # The network the stream states (e.g. a "US: CBS …"
                            # subchannel) overrides the station's FCC affiliation.
                            stream_network = self._extract_stream_network(current_name)
                            new_name = self._format_ota_name(station, ota_format, callsign,
                                                             network_override=stream_network)
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

            progress.finish(
                summary=f"{len(renamed_channels)} to rename, "
                        f"{len(skipped_channels)} skipped. Scope: {scope.info}")

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
            return {"status": "error", "error": f"Error loading and processing channels: {e}"}

    def preview_changes_action(self, settings, logger):
        """Export a CSV showing the preview of channel renaming changes."""
        try:


            if not os.path.exists(self.results_file):
                return {"status": "error", "error": "No processed channels found. Please run 'Load/Process Channels' first."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])

            # A token matching no DB group must refuse here too, or a typo
            # renames/tags every excluded channel while every other action
            # (which validates against the DB) refuses (blocker: fail-open).
            guard = self._ignore_tokens_error(settings, logger)
            if guard:
                return guard

            # Dry run must reflect the same exclusion the real run applies, or
            # the preview contradicts what Rename/Tag Unknown actually does.
            all_changes, ignored_rows = split_rows_by_ignore(
                all_changes, settings.get("ignore_groups"))

            if not all_changes:
                if ignored_rows:
                    return {"status": "success", "message":
                            f"No changes to preview; all {len(ignored_rows)} "
                            f"pending change(s) are in ignored groups."}
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
                    # Absence of a group's rows proves nothing on its own -
                    # record how many were excluded so the CSV is self-describing.
                    if ignored_rows:
                        csvfile.write(
                            f"# Excluded by ignore: {len(ignored_rows)} row(s)\n")
                os.replace(tmp_path, csv_path)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.info(f"{PLUGIN_LOG_PREFIX} Preview CSV exported to {csv_path}")

            renamed_count = sum(1 for c in all_changes if c.get('status') == 'Renamed')
            skipped_count = sum(1 for c in all_changes if c.get('status') == 'Skipped')

            preview_message = f"✓ Preview exported to: {csv_filename}\n\n{renamed_count} channels will be renamed, {skipped_count} will be skipped."
            if ignored_rows:
                preview_message += f"\n{len(ignored_rows)} row(s) in ignored groups were excluded."

            # The export is confirmed on disk above, so a report may now be built
            # from the same rows. It is built from the ROWS, never by re-reading
            # the CSV, whose settings header names the configured M3U sources.
            outcome = self._build_and_emit_report(
                settings, logger,
                title="Rename preview",
                columns=self._RENAME_REPORT_COLUMNS,
                rows=all_changes,
                export_filename=csv_filename)
            preview_message += self._report_outcome_clause(outcome)

            result = {"status": "success", "message": preview_message}
            if outcome["blocking_error"]:
                result["error"] = outcome["blocking_error"]
            return result

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error exporting preview: {e}")
            return {"status": "error", "error": f"Error exporting preview: {e}"}

    def rename_channels_action(self, settings, logger):
        """Apply the standardized names to channels."""
        try:


            # Check if dry run mode is enabled
            dry_run = settings.get("dry_run_mode", False)

            if dry_run:
                logger.info(f"{PLUGIN_LOG_PREFIX} Dry Run Mode enabled - calling preview_changes_action")
                return self.preview_changes_action(settings, logger)

            if not os.path.exists(self.results_file):
                return {"status": "error", "error": "No processed channels found. Please run 'Load/Process Channels' first."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])
            channels_to_rename = [c for c in all_changes if c.get('status') == 'Renamed']

            # A token matching no DB group must refuse here too (see
            # preview_changes_action for why) - this is the action that
            # actually writes the renames.
            guard = self._ignore_tokens_error(settings, logger)
            if guard:
                return guard

            # These actions replay a persisted file and never fetch channels, so
            # the exclusion has to be applied here too; the file may predate the
            # current ignore_groups value.
            channels_to_rename, ignored_rows = split_rows_by_ignore(
                channels_to_rename, settings.get("ignore_groups"))
            if ignored_rows:
                logger.info(
                    f"{PLUGIN_LOG_PREFIX} Skipped {len(ignored_rows)} channel(s) "
                    f"in ignored groups."
                )

            if not channels_to_rename:
                if ignored_rows:
                    return {"status": "success", "message":
                            f"No channels renamed; all {len(ignored_rows)} "
                            f"pending change(s) are in ignored groups."}
                return {"status": "success", "message": "No channels need to be renamed."}

            # Bulk update using ORM
            updates = [{'id': ch['channel_id'], 'name': ch['new_name']} for ch in channels_to_rename]

            logger.info(f"{PLUGIN_LOG_PREFIX} Renaming {len(updates)} channels...")
            self._bulk_update_channels(updates, ['name'], logger)
            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully renamed {len(updates)} channels."]
            if ignored_rows:
                message_parts.append(
                    f"Skipped {len(ignored_rows)} channel(s) in ignored groups.")
            if channels_to_rename:
                message_parts.append("\n**Sample Changes:**")
                for change in channels_to_rename[:5]:
                    message_parts.append(f"• '{change['current_name']}' → '{change['new_name']}'")
                if len(channels_to_rename) > 5:
                    message_parts.append(f"...and {len(channels_to_rename) - 5} more.")

            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error renaming channels: {e}")
            return {"status": "error", "error": f"Error renaming channels: {e}"}

    def rename_unknown_channels_action(self, settings, logger):
        """Append suffix to channels that could not be matched (OTA and premium/cable)."""
        try:


            if not os.path.exists(self.results_file):
                return {"status": "error", "error": "No processed channels found. Please run 'Load/Process Channels' first."}

            # Get suffix with default fallback matching the field default
            suffix = settings.get("unknown_suffix", PluginConfig.DEFAULT_UNKNOWN_SUFFIX)

            # Log what we received
            logger.info(f"{PLUGIN_LOG_PREFIX} Suffix setting value: '{suffix}' (length: {len(suffix)})")

            # Only reject if suffix is None or empty after strip
            if not suffix or not suffix.strip():
                return {"status": "error", "error": "No suffix configured. Please set 'Suffix for Unknown Channels' in plugin settings. Default is ' [Unk]' (with leading space)."}

            with open(self.results_file, 'r') as f:
                data = json.load(f)

            all_changes = data.get('changes', [])
            skipped_channels = [c for c in all_changes if c.get('status') == 'Skipped']

            # A token matching no DB group must refuse here too (see
            # preview_changes_action for why) - this is the action that
            # actually writes the "[Unk]" suffix renames.
            guard = self._ignore_tokens_error(settings, logger)
            if guard:
                return guard

            # These actions replay a persisted file and never fetch channels, so
            # the exclusion has to be applied here too; the file may predate the
            # current ignore_groups value.
            skipped_channels, ignored_rows = split_rows_by_ignore(
                skipped_channels, settings.get("ignore_groups"))
            if ignored_rows:
                logger.info(
                    f"{PLUGIN_LOG_PREFIX} Skipped {len(ignored_rows)} channel(s) "
                    f"in ignored groups."
                )

            if not skipped_channels:
                if ignored_rows:
                    return {"status": "success", "message":
                            f"No unknown channels renamed; all {len(ignored_rows)} "
                            f"pending change(s) are in ignored groups."}
                return {"status": "success", "message": "No unknown channels to rename."}

            # Bulk update using ORM
            updates = [{'id': ch['channel_id'], 'name': ch['current_name'] + suffix} for ch in skipped_channels]

            if settings.get("dry_run_mode", False):
                return {"status": "success", "message":
                        f"Dry Run: would tag {len(updates)} unknown channel(s) "
                        f"with suffix '{suffix}'. No changes written."}

            logger.info(f"{PLUGIN_LOG_PREFIX} Adding suffix '{suffix}' to {len(updates)} unknown channels...")
            self._bulk_update_channels(updates, ['name'], logger)
            self._trigger_frontend_refresh(settings, logger)

            message_parts = [f"✓ Successfully added suffix '{suffix}' to {len(updates)} unknown channels."]
            if ignored_rows:
                message_parts.append(
                    f"Skipped {len(ignored_rows)} channel(s) in ignored groups.")
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
            return {"status": "error", "error": f"Error renaming unknown channels: {e}"}

    def apply_logos_action(self, settings, logger):
        """Apply default logo to channels without logos."""
        try:


            default_logo = settings.get("default_logo", "").strip()

            if not default_logo:
                return {"status": "error", "error": "No default logo configured. Please set 'Default Logo' in plugin settings."}

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
                    "error": f"Logo '{default_logo}' not found in logo manager.\n\nSearched through {len(all_logos)} logos. Check the Dispatcharr logs to see available logo names."
                }

            # Fetch FRESH channel data from database
            logger.info(f"{PLUGIN_LOG_PREFIX} Fetching current channel data from database...")

            # Resolve the group scope (include filter minus ignore_groups)
            try:
                scope = self._resolve_process_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )

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

            if settings.get("dry_run_mode", False):
                return {"status": "success", "message":
                        f"Dry Run: would apply default logo to {len(updates)} "
                        f"channel(s). No changes written."}

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
            return {"status": "error", "error": f"Error applying logos: {e}"}

    def apply_tv_logos_action(self, settings, logger):
        """Assign per-channel logos by fuzzy-matching channel names to the
        tv-logo/tv-logos GitHub repo. Runs per country code from
        `channel_databases`; channels that already have a logo are left alone.
        Creates Logo entries in Dispatcharr pointing at raw.githubusercontent
        URLs and assigns them to channels in bulk."""
        try:
            from .logo_matcher import fetch_tv_logos_filelist, match_channel_to_logo, build_logo_url

            country_codes_str = settings.get("channel_databases", PluginConfig.DEFAULT_CHANNEL_DATABASES).strip()
            country_codes = [c.strip().upper() for c in country_codes_str.split(',') if c.strip()]
            if not country_codes:
                return {"status": "error", "error": "No country databases selected. Set 'Channel Databases' first."}

            try:
                scope = self._resolve_process_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )
            channels_without_logos = [
                ch for ch in all_channels
                if ch.get('logo_id') in (None, 0, '0')
            ]
            if not channels_without_logos:
                return {"status": "success", "message": "All targeted channels already have logos."}

            existing_logos_by_url = {logo.url: logo for logo in Logo.objects.all()}

            # Fetch logo file lists for each selected country, then try each
            # country until a match is found per channel. Cache per (repo,
            # branch, dir) for the session so re-running the action doesn't
            # hit the GitHub anonymous quota (60 req/hr/IP).
            if not hasattr(self, "_tv_logos_cache"):
                self._tv_logos_cache = {}
            country_filelists = []
            for cc in country_codes:
                country_dir = PluginConfig.COUNTRY_DIR_MAP.get(cc)
                if not country_dir:
                    logger.warning(f"{PLUGIN_LOG_PREFIX} No tv-logos directory mapping for '{cc}', skipping.")
                    continue
                cache_key = (PluginConfig.TV_LOGOS_REPO, PluginConfig.TV_LOGOS_BRANCH, country_dir)
                files = self._tv_logos_cache.get(cache_key)
                if files is None:
                    logger.info(f"{PLUGIN_LOG_PREFIX} Fetching tv-logos file list for {country_dir}...")
                    files = fetch_tv_logos_filelist(*cache_key)
                    if files:
                        self._tv_logos_cache[cache_key] = files
                logger.info(f"{PLUGIN_LOG_PREFIX} {len(files)} logos available in {country_dir}")
                if files:
                    country_filelists.append((cc.lower(), country_dir, files))

            if not country_filelists:
                return {"status": "error", "error": "No tv-logos file lists could be fetched. Check network access or repo path."}

            # Hoisted above the loop: a dry run must create NOTHING, including
            # the Logo catalog rows the real run creates on a first-time match
            # (previously created even under Dry Run, orphaning them in the
            # catalog if the operator then declined the real run).
            dry_run = settings.get("dry_run_mode", False)

            progress = ProgressTracker(len(channels_without_logos), "apply_tv_logos", logger)
            assigned = 0
            no_match = 0
            would_create = 0
            channel_updates = []

            for ch in channels_without_logos:
                name = ch.get('name', '')
                if not name:
                    progress.update()
                    continue
                matched_url = None
                for suffix, country_dir, files in country_filelists:
                    matched_file = match_channel_to_logo(name, files, suffix)
                    if matched_file:
                        matched_url = build_logo_url(
                            PluginConfig.TV_LOGOS_REPO, PluginConfig.TV_LOGOS_BRANCH,
                            country_dir, matched_file,
                        )
                        break
                if not matched_url:
                    no_match += 1
                    progress.update()
                    continue

                logo = existing_logos_by_url.get(matched_url)
                if not logo:
                    if dry_run:
                        # No Logo.objects.create - a dry run must write nothing.
                        would_create += 1
                        assigned += 1
                        progress.update()
                        continue
                    try:
                        logo = Logo.objects.create(name=name, url=matched_url)
                        existing_logos_by_url[matched_url] = logo
                    except Exception as exc:
                        logger.warning(f"{PLUGIN_LOG_PREFIX} Failed to create Logo for '{name}' (url={matched_url}): {exc}")
                        progress.update()
                        continue

                channel_updates.append({'id': ch['id'], 'logo_id': logo.id})
                assigned += 1
                progress.update()

            if dry_run:
                summary = (
                    f"Dry Run: would apply logos to {assigned} channel(s) "
                    f"({no_match} had no match, {would_create} would need a "
                    f"new Logo catalog entry). No changes written."
                )
                progress.finish(summary=f"{summary} Scope: {scope.info}")
                return {"status": "success", "message": summary}

            if channel_updates:
                self._bulk_update_channels(channel_updates, ['logo_id'], logger)
                self._trigger_frontend_refresh(settings, logger)

            summary = f"Assigned {assigned} logos, {no_match} channels had no match."
            progress.finish(summary=f"{summary} Scope: {scope.info}")
            return {"status": "success", "message": f"✓ {summary}"}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error applying tv-logos: {e}")
            return {"status": "error", "error": f"Error applying tv-logos: {e}"}

    def category_groups_dry_run_action(self, settings, logger):
        """Export a CSV showing which channels would be moved to which category-based groups."""
        try:


            # Load channel data to get categories
            channels_loaded = self._load_channel_data(settings, logger)
            if not channels_loaded:
                return {"status": "error", "error": "Channel databases could not be loaded."}

            # Get all groups and channels
            all_groups = self._get_all_groups(logger)
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}

            # Resolve the group scope (include filter minus ignore_groups)
            try:
                scope = self._resolve_category_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )
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
            ignored_targets = set()
            # Parsed once, not per-channel: is_ignored_name_tokens skips the
            # re-parse is_ignored_name would otherwise do on every iteration.
            ignore_tokens = parse_tokens(settings.get("ignore_groups") or "")
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

                    # The exclusion also forbids writing INTO a group - the
                    # preview must match what the real run would refuse to do.
                    if is_ignored_name_tokens(new_group_name, ignore_tokens):
                        ignored_targets.add(new_group_name)
                        continue

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
                message = "No channels need to be moved to category-based groups."
                if ignored_targets:
                    message += (
                        f" Skipped {len(ignored_targets)} ignored target group(s): "
                        f"{_format_capped_name_list(sorted(ignored_targets))}."
                    )
                return {"status": "success", "message": message}

            # Create export directory
            export_dir = PluginConfig.EXPORT_DIR
            os.makedirs(export_dir, exist_ok=True)

            # Create CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"channel_mapparr_category_groups_preview_{timestamp}.csv"
            csv_path = os.path.join(export_dir, csv_filename)

            # Written atomically (temporary file, then rename) like the other two
            # CSV writers. A plain open() leaves a TRUNCATED file at the final
            # path if the write fails part way, with no temporary file to clean
            # up, and that truncated file is the one an operator would later
            # believe is complete. It also means there is no single moment at
            # which the export is confirmed written, which is what the emailed
            # report below waits for.
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                                 dir=export_dir, suffix='.csv',
                                                 delete=False) as csvfile:
                    tmp_path = csvfile.name
                    # Write settings header as comments
                    csvfile.write(self._generate_csv_settings_header(settings))
                    # `scope` is already resolved above in this same function, so
                    # this is not a re-parse: record what the ignore/include
                    # filters actually MATCHED, not just the raw setting text
                    # already echoed by the header above.
                    csvfile.write(f"# Ignore resolved to: {scope.info}\n")

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
                os.replace(tmp_path, csv_path)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.info(f"{PLUGIN_LOG_PREFIX} Category groups preview CSV exported to {csv_path}")

            # Count new groups that need to be created
            new_groups_needed = sum(1 for m in moves if m['group_exists'] == 'No (will be created)')

            # Count by match type
            broadcast_count = sum(1 for m in moves if 'Broadcast' in m['match_type'])
            premium_count = sum(1 for m in moves if 'Premium' in m['match_type'])

            message = (
                f"✓ Preview exported to: {csv_filename}\n\n{len(moves)} channels will "
                f"be moved ({broadcast_count} broadcast, {premium_count} premium).\n"
                f"{new_groups_needed} new groups will be created."
            )
            if ignored_targets:
                message += (
                    f"\nSkipped {len(ignored_targets)} ignored target group(s): "
                    f"{_format_capped_name_list(sorted(ignored_targets))}."
                )

            # The export is confirmed on disk above. Only the Dry Run branch of
            # Organize by Category produces an export at all, so only this branch
            # can report; a real run has no rows to report on.
            outcome = self._build_and_emit_report(
                settings, logger,
                title="Category organization preview",
                columns=self._CATEGORY_REPORT_COLUMNS,
                rows=moves,
                export_filename=csv_filename)
            message += self._report_outcome_clause(outcome)

            result = {"status": "success", "message": message}
            if outcome["blocking_error"]:
                result["error"] = outcome["blocking_error"]
            return result

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error generating category groups preview: {e}")
            return {"status": "error", "error": f"Error generating category groups preview: {e}"}

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
                return {"status": "error", "error": "Channel databases could not be loaded."}

            # Get all groups and channels
            all_groups = self._get_all_groups(logger)
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            group_id_to_name = {g['id']: g['name'] for g in all_groups if 'name' in g and 'id' in g}

            # Resolve the group scope (include filter minus ignore_groups)
            try:
                scope = self._resolve_category_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )
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
            ignored_targets = set()
            # Parsed once, not per-channel: is_ignored_name_tokens skips the
            # re-parse is_ignored_name would otherwise do on every iteration.
            ignore_tokens = parse_tokens(settings.get("ignore_groups") or "")

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

                    # The exclusion also forbids writing INTO a group - never
                    # create or adopt a target the operator declared untouchable.
                    if is_ignored_name_tokens(new_group_name, ignore_tokens):
                        ignored_targets.add(new_group_name)
                        continue

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
                message = "No channels need to be moved to category-based groups."
                if ignored_targets:
                    message += (
                        f" Skipped {len(ignored_targets)} ignored target group(s): "
                        f"{_format_capped_name_list(sorted(ignored_targets))}."
                    )
                return {"status": "success", "message": message}

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
                return {"status": "error", "error": "Failed to create necessary groups. Please check logs."}

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

            if ignored_targets:
                message_parts.append(
                    f"\nSkipped {len(ignored_targets)} ignored target group(s): "
                    f"{_format_capped_name_list(sorted(ignored_targets))}."
                )

            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error organizing channels by category: {e}")
            return {"status": "error", "error": f"Error organizing channels by category: {e}"}

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

    @staticmethod
    def _check_group_destinations_not_ignored(names, ignore_value):
        """Refuse rather than create or adopt a group the operator declared untouchable.

        The scope filters channels out of a scan; this is the other direction -
        import must not write INTO a group listed in 'Channel Groups to Ignore'.
        """
        blocked = sorted({name for name in names if is_ignored_name(name, ignore_value)})
        if blocked:
            raise GroupScopeError(
                f"Import would create or write into group(s) listed in 'Channel "
                f"Groups to Ignore': {_format_capped_name_list(blocked)}. Change "
                f"the import target or remove them from the ignore list."
            )

    def _ensure_category_groups_exist(self, categories, settings, logger):
        """
        Ensure all category-based channel groups exist in Dispatcharr.
        Create missing groups via ORM.

        If m3u_custom_group_name is set, all categories will map to that single group.

        Returns:
            dict: Mapping of category name to group ID
        """
        # Check if custom group name is specified
        custom_group_name = (settings.get("m3u_custom_group_name") or "").strip()

        # The exclusion also forbids writing INTO a group. Refuse rather than
        # create or adopt a group the operator declared untouchable.
        self._check_group_destinations_not_ignored(
            [custom_group_name] if custom_group_name else list(categories),
            settings.get("ignore_groups"),
        )

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
                        'Match Type': '',
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
                return {"status": "error", "error": "No streams found in specified M3U sources"}

            # Step 2: Match streams to categories
            matched_by_category, unmatched_streams = self._match_streams_to_categories(
                streams, settings, logger
            )

            if not matched_by_category:
                return {
                    "status": "error",
                    "error": f"No streams matched to channel databases. {len(unmatched_streams)} unmatched streams."
                }

            # Step 3: Check which category groups exist
            categories = list(matched_by_category.keys())

            # The preview must match what the real run would refuse to do.
            custom_group_name = (settings.get("m3u_custom_group_name") or "").strip()
            try:
                self._check_group_destinations_not_ignored(
                    [custom_group_name] if custom_group_name else categories,
                    settings.get("ignore_groups"),
                )
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

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
            return {"status": "error", "error": f"Dry run failed: {str(e)}"}

    def _do_import_m3u_streams(self, settings, logger):
        """Core M3U import logic."""
        logger.info(f"{PLUGIN_LOG_PREFIX} Starting M3U stream import...")

        # Step 1: Fetch streams from M3U sources
        streams = self._fetch_streams_from_m3u_sources(settings, logger)

        if not streams:
            return {"status": "error", "error": "No streams found in specified M3U sources"}

        # Step 2: Match streams to categories
        matched_by_category, unmatched_streams = self._match_streams_to_categories(
            streams, settings, logger
        )

        if not matched_by_category:
            return {
                "status": "error",
                "error": f"No streams matched to channel databases. {len(unmatched_streams)} unmatched streams."
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

        message = (f"✓ M3U import complete!\n\n"
                   f"Channels created: {total_success}\n"
                   f"Failed: {total_failed}\n"
                   f"Unmatched streams skipped: {len(unmatched_streams)}\n"
                   f"Categories: {len(categories)}\n\n"
                   f"Results exported to: {csv_filename}")

        # Only the COMPLETED import reports, not the dry run, so one import
        # produces one report rather than two. The title says results, because
        # _export_m3u_import_preview hardcodes the word preview into the export
        # filename and header for both of its callers.
        outcome = self._build_and_emit_report(
            settings, logger,
            title="M3U import results",
            columns=self._M3U_REPORT_COLUMNS,
            rows=self._m3u_report_rows(matched_by_category, unmatched_streams),
            export_filename=csv_filename)
        message += self._report_outcome_clause(outcome)

        result = {"status": "success", "message": message}
        if outcome["blocking_error"]:
            result["error"] = outcome["blocking_error"]
        return result

    def _do_import_m3u_streams_bg(self, settings, logger):
        """Background wrapper for M3U import."""
        try:
            result = self._do_import_m3u_streams(settings, logger)
            self._last_bg_result = result
            msg = result.get("message") or result.get("error", "Import complete.")
            logger.info(f"{PLUGIN_LOG_PREFIX} IMPORT COMPLETED: {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": self.name,
                "message": msg
            })
        except Exception as e:
            self._last_bg_result = {"status": "error", "error": str(e)}
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

        # The real import runs in a background thread whose result the card
        # never shows, so the destination must be validated BEFORE
        # backgrounding or a refusal would be silently swallowed.
        custom_group_name = (settings.get("m3u_custom_group_name") or "").strip()
        if custom_group_name:
            try:
                self._check_group_destinations_not_ignored(
                    [custom_group_name], settings.get("ignore_groups"))
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

        if not self._try_start_thread(self._do_import_m3u_streams_bg, (copy.deepcopy(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}

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

            # 2b. Group scope (ignore_groups exclusion) - the first group
            # validation in this action. Kept to one capped line per branch so
            # a wildcard exclusion matching many groups cannot blow the ~280
            # char toast budget.
            #
            # ignore_dupe_error is set only when 2b's failure comes from the
            # ignore filter ITSELF (an unmatched token, or no groups exist at
            # all) - those two GroupScopeError messages don't mention
            # include_label, so _resolve_category_scope below would raise the
            # BYTE-IDENTICAL text and double-print/double-count one
            # misconfigured setting as "2 error(s)". The "excluded every
            # group that '<include_label>' selected" message DOES depend on
            # include_label (process vs category can differ), so that one is
            # deliberately NOT deduped here.
            ignore_dupe_error = None
            ignore_summary = None
            try:
                scope = self._resolve_process_scope(settings, logger)
                # Report only the names that actually removed something from
                # this run, never the raw ignored_names count - it is a
                # SUPERSET of out_of_scope_names, so a wildcard matching
                # nothing but already-out-of-scope groups would otherwise
                # print the same name list twice in one message.
                out_of_scope = set(scope.out_of_scope_names)
                effective = [n for n in scope.ignored_names if n not in out_of_scope]
                if effective:
                    # Also folded into the SUCCESS toast by the assembly below.
                    # Confirming what the exclusion actually resolved to is the
                    # reason it is surfaced here at all, and a clean run would
                    # otherwise report nothing but "OK".
                    ignore_summary = (f"Ignoring {len(effective)} group(s): "
                                      f"{_format_capped_name_list(effective)}")
                    validation_results.append(f"✅ Ignore: {ignore_summary}")
                if scope.out_of_scope_names:
                    # ONE warning for the whole condition, not one per name -
                    # this is benign (an ignore entry that already had no
                    # effect), and counting per-name made a healthy config
                    # read as "Validation completed with 10 warning(s)".
                    warning_count += 1
                    # No name list here: the names are already logged by
                    # _resolve_group_scope, and a capped list on top of the
                    # `effective` line above (which already fired) was the
                    # single offender that pushed a real operator's message
                    # over Dispatcharr's ~280 char clip.
                    validation_results.append(
                        f"⚠️ Ignore: {len(scope.out_of_scope_names)} "
                        f"name{'' if len(scope.out_of_scope_names) == 1 else 's'} "
                        f"had no effect (already outside the selected scope)")
            except GroupScopeError as exc:
                # The same exception covers an unresolvable INCLUDE filter
                # (e.g. a typo'd selected_groups) as well as an unresolvable
                # ignore filter, and every other consumer of this exception
                # surfaces its raw wording with no prefix - the message
                # already names the setting it came from (e.g. "'Channel
                # Groups to Process'"), so prepending "Ignore:" would
                # mislabel an include-filter typo as an ignore problem.
                validation_results.append(f"❌ {exc}")
                error_count += 1
                exc_text = str(exc)
                if ("Channel Groups to Ignore" in exc_text
                        and "excluded every group" not in exc_text):
                    ignore_dupe_error = exc_text

            # 2c. Category scope (category_groups) - without this, a
            # category_groups typo, or an exclusion that empties the category
            # scope, validated GREEN here and only failed RED on Organize by
            # Category. Only resolved when the setting is non-blank, so the
            # common case (no category filter configured) costs nothing;
            # when configured it costs exactly one line either way, to stay
            # inside the ~260-char regression budget alongside 2b. Skipped
            # entirely when 2b already reported the identical ignore-filter
            # failure (see ignore_dupe_error above) - re-resolving would just
            # print the same complaint twice and report "2 error(s)" for one
            # broken setting.
            category_groups_str = (settings.get("category_groups") or "").strip()
            if category_groups_str and ignore_dupe_error is None:
                try:
                    self._resolve_category_scope(settings, logger)
                    validation_results.append("✅ Category: OK")
                except GroupScopeError as exc:
                    validation_results.append(f"❌ {exc}")
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

            # 5. Emailed reports. Reported ONLY when something is wrong, which
            # matches the errors-and-warnings-only contract of this action. Every
            # line here MUST start with a recognised glyph: a line starting with
            # anything else is dropped from the operator-facing output AND trips
            # the bookkeeping-drift warning below on every single run.
            bridge = self._notify_bridge()
            for problem in bridge.unknown_setting_values(settings):
                validation_results.append(f"{_VALIDATION_WARNING_GLYPH} {problem}")
                warning_count += 1
            if bridge.is_enabled(settings):
                for problem in self._newsflasharr_readiness():
                    validation_results.append(
                        f"{_VALIDATION_WARNING_GLYPH} Emailed reports: {problem}")
                    warning_count += 1
                if self._get_m3u_account_names(logger) is None:
                    validation_results.append(
                        f"{_VALIDATION_WARNING_GLYPH} Emailed reports: the M3U "
                        "account name lookup is failing, so no report will be "
                        "built. Those names are what is removed from a report "
                        "before it is emailed.")
                    warning_count += 1

            # Report ONLY what the operator has to act on.
            #
            # Dispatcharr renders `error` persistently at the bottom of the
            # plugin card and `message` as a transient toast. Returning the
            # whole readout in `error` therefore parked a wall of mostly-OK
            # lines under the settings form on every failure. So: a failure
            # returns the failing lines and nothing else, and a clean run says
            # so in a toast and leaves nothing behind.
            #
            # Severity is read from the glyph each line is built with, which is
            # the contract for every append above. `_VALIDATION_ERROR_GLYPH`
            # and `_VALIDATION_WARNING_GLYPH` name it so a new line cannot
            # quietly opt out, and the assertion below fails loudly if a
            # counter was incremented without a matching line (or vice versa).
            errors = [ln for ln in validation_results
                      if ln.startswith(_VALIDATION_ERROR_GLYPH)]
            warnings = [ln for ln in validation_results
                        if ln.startswith(_VALIDATION_WARNING_GLYPH)]

            if len(errors) != error_count or len(warnings) != warning_count:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} validate_settings bookkeeping drift: "
                    f"{error_count} error_count vs {len(errors)} error line(s), "
                    f"{warning_count} warning_count vs {len(warnings)} warning line(s)"
                )

            if errors:
                header = (f"Validation failed, {len(errors)} error(s)"
                          + (f" and {len(warnings)} warning(s)" if warnings else "")
                          + ":")
                return {"status": "error",
                        "error": "\n".join([header] + errors + warnings)}

            suffix = f" {ignore_summary}." if ignore_summary else ""

            if warnings:
                return {"status": "success",
                        "message": f"✅ Settings OK.{suffix}\n"
                                   f"{len(warnings)} warning(s):\n" + "\n".join(warnings)}

            return {"status": "success",
                    "message": f"✅ All settings validated successfully.{suffix}"}

        except Exception as e:
            logger.error(f"{PLUGIN_LOG_PREFIX} Error during settings validation: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "error",
                "error": f"Validation error: {e}\n\nSee logs for details."
            }

    def plugin_status_action(self, settings, logger):
        """Read the persistent progress file and return a user-facing summary."""
        progress = load_progress(PROGRESS_FILE)
        message = build_status_message(progress)
        return {"status": "success", "message": message}

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
            return {"status": "error", "error": f"Error clearing CSV exports: {e}"}
