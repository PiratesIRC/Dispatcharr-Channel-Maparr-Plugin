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
from .channel_seeder import allocate_channel_numbers, build_seed_plan
from .station_affiliation import primary_network
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


# Every OTA match is categorised here. The FCC station table carries no
# category field (pinned by tests/test_station_table.py), so the import path
# has exactly one category for broadcast stations.
OTA_IMPORT_CATEGORY = "Broadcast"


def _format_capped_name_list(names, limit=_MAX_NAMES_IN_MESSAGE):
    """Join up to `limit` names, then summarize the rest as a count."""
    names = list(names)
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f" and {len(names) - limit} more"
    return shown


class PluginConfig:
    """Configuration constants for Channel Maparr."""

    PLUGIN_VERSION = "1.26.2481147"

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
    # Append-only tally of channel names written by Rename Channels and Tag
    # Unknown Channels: one JSON line per run that wrote at least one name.
    RENAME_LEDGER_FILE = "/data/channel_maparr_rename_counts.jsonl"
    EXPORT_DIR = "/data/exports"

    # The export directory is SHARED with at least five other plugins. Measured
    # on the live installation: 65 stream_mapparr_ files, 26 epg_janitor_, 14
    # event_channel_managarr_, 10 lineuparr_ and 5 iptv_checker_results_
    # alongside our own. Every routine that deletes from it selects on BOTH of
    # these, so that matching on the .csv suffix alone cannot destroy another
    # project's data.
    CSV_EXPORT_PREFIX = "channel_mapparr_"
    CSV_EXPORT_SUFFIX = ".csv"
    SECONDS_PER_DAY = 86400
    # Zero keeps every file, so an installation that never asked for age-based
    # deletion loses nothing by upgrading.
    DEFAULT_CSV_RETENTION_DAYS = 0

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
                "id": "_section_matching",
                "label": "Matching and Scope",
                "type": "info",
                "description": (
                    "Which channel databases a run matches against, how close a name "
                    "has to be before it counts as a match, and which channel groups "
                    "are in scope. The two group settings are not opposites: Channel "
                    "Groups to Process narrows a run to the groups you name, while "
                    "Channel Groups to Ignore is a refusal that every action obeys, "
                    "including the ones that only replay a saved results file. A name "
                    "in Channel Groups to Ignore that matches no group refuses most "
                    "actions rather than being skipped, because a typo there would "
                    "otherwise quietly widen the run to everything. The actions that "
                    "replay a saved results file cannot refuse, because they never ask "
                    "the database which groups exist; they filter on the group name "
                    "stored in that file."
                ),
            },
            {
                "id": "channel_databases",
                "label": "Channel Databases",
                "type": "string",
                "default": PluginConfig.DEFAULT_CHANNEL_DATABASES,
                "placeholder": "US, UK, CA, AU",
                "help_text": "Comma-separated country codes deciding which channel databases are loaded. Available: AU, BR, CA, DE, ES, FR, IN, MX, NL, NO, UK, US. The same codes choose which tv-logos directories the per-channel logo action searches, so a country left out here is left out of both.",
            },
            {
                "id": "match_sensitivity",
                "label": "Match Sensitivity",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "relaxed",
                     "label": "Relaxed (70 of 100), more matches and more mistakes"},
                    {"value": "normal", "label": "Normal (80 of 100), balanced"},
                    {"value": "strict",
                     "label": "Strict (90 of 100), fewer matches and higher confidence"},
                    {"value": "exact",
                     "label": "Exact (95 of 100), near-exact matches only"},
                ],
                "help_text": "How close a name has to be before it counts as a match, "
                             "scored out of 100. A lower threshold finds more matches "
                             "and makes more mistakes. The threshold each choice uses "
                             "is recorded at the top of every CSV export.",
            },
            {
                "id": "selected_groups",
                "label": "Channel Groups to Process",
                "type": "string",
                "default": "",
                "placeholder": "Locals, News, Entertainment",
                "help_text": "Comma-separated channel group names. Limits renaming, tagging and logo actions to these groups. Leave empty to process every group. This setting includes; Channel Groups to Ignore excludes, and an exclusion wins.",
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
                "help_text": "Comma-separated group names that Organize by Category is allowed to move channels into or create. Leave empty to use the categories named in the channel database files. A group listed in Channel Groups to Ignore is skipped and the run continues.",
            },
            {
                "id": "_section_m3u_import",
                "label": "Import M3U Streams",
                "type": "info",
                "description": (
                    "The four settings Import M3U Streams uses to decide which "
                    "provider streams to bring in and where to put them. That action "
                    "creates one channel per STREAM, so a provider serving the same "
                    "station once per M3U account produces one channel per account "
                    "rather than one channel with several streams attached. Create "
                    "Channels From Streams, in the next section, is the one that "
                    "makes a single channel per station."
                ),
            },
            {
                "id": "m3u_sources",
                "label": "M3U Source",
                "type": "select",
                "default": "_all",
                "options": m3u_source_options,
                "help_text": "Which M3U account the import reads streams from. All sources reads every one. The list is built from Dispatcharr M3U Accounts when the settings page loads, so an account added since you opened this page will not appear until you reload it.",
            },
            {
                "id": "m3u_group_filter",
                "label": "M3U Group Filter",
                "type": "string",
                "default": "",
                "placeholder": "USA Premium, Sports, Movies",
                "help_text": "Comma-separated M3U group titles to import from. This filter is applied BEFORE matching, so a stream in a group not listed here is never looked at. Leave empty to read every group.",
            },
            {
                "id": "m3u_category_filter",
                "label": "Category Filter",
                "type": "string",
                "default": "",
                "placeholder": "Broadcast, Sports, News",
                "help_text": "Comma-separated category names from the channel database. This filter is applied AFTER matching, so a stream is matched first and then kept only if its category is listed. Leave empty to keep every category.",
            },
            {
                "id": "m3u_custom_group_name",
                "label": "Custom Import Group Name",
                "type": "string",
                "default": "",
                "placeholder": "My Custom Group",
                "help_text": "Put every imported channel in this one group instead of sorting them into groups by category. Leave empty to name the groups after the category each channel matched.",
            },
            {
                "id": "_section_create_channels",
                "label": "Create Channels From Streams",
                "type": "info",
                "description": (
                    "The settings for the action that creates one channel per "
                    "broadcast station, for streams no channel is using yet. It "
                    "attaches no streams: it creates the channels and stops, leaving "
                    "the stream assignment to another tool or to hand. Networks to "
                    "Skip reads what a station IS rather than every network its "
                    "record mentions, so a CBS station carrying a second network on a "
                    "subchannel is still treated as a CBS station and is not skipped."
                ),
            },
            {
                "id": "seed_source_groups",
                "label": "Stream Groups to Seed From",
                "type": "string",
                "default": "",
                "placeholder": "US| ABC, US| CBS",
                "help_text": (
                    "Used by Create Channels From Streams. Comma-separated names of "
                    "the stream groups to scan. Only streams in these groups that are "
                    "not attached to any channel are considered. Leave empty to do "
                    "nothing."
                ),
            },
            {
                "id": "seed_target_group",
                "label": "Channel Group to Create In",
                "type": "string",
                "default": "",
                "placeholder": "US: ABC",
                "help_text": (
                    "Used by Create Channels From Streams. The existing channel group "
                    "the new channels are created in. The group must already exist. A "
                    "group listed in Channel Groups to Ignore is refused."
                ),
            },
            {
                "id": "seed_start_number",
                "label": "First Channel Number to Use",
                "type": "string",
                "default": "",
                "placeholder": "5110",
                "help_text": (
                    "Used by Create Channels From Streams. The first channel number to "
                    "assign. Numbers already in use are skipped, so the run does not "
                    "need a free block. Leave empty to start above the highest channel "
                    "number on the system."
                ),
            },
            {
                "id": "seed_exclude_networks",
                "label": "Networks to Skip When Creating Channels",
                "type": "string",
                "default": "",
                "placeholder": "Telemundo, Univision",
                "help_text": (
                    "Used by Create Channels From Streams. Comma-separated network "
                    "names. A station carrying one of them is reported and skipped "
                    "rather than created. The network is read from the station "
                    "record and from the stream name, because a low power record "
                    "often names no network at all. Leave empty to create every "
                    "station that matches."
                ),
            },
            {
                "id": "_section_naming",
                "label": "Names, Tags and Logos",
                "type": "info",
                "description": (
                    "How a matched channel is named, what marks one that did not "
                    "match, and which logo it gets. Match by Market When No Callsign "
                    "is off by default and changes nothing for a channel whose name "
                    "already prints a callsign; it only affects names that state a "
                    "market and a channel number instead, and it accepts a station "
                    "only when exactly one fits. Ignored Tags are stripped before "
                    "matching rather than from the channel name itself."
                ),
            },
            {
                "id": "ota_format",
                "label": "OTA Name Format",
                "type": "string",
                "default": PluginConfig.DEFAULT_OTA_FORMAT,
                "placeholder": PluginConfig.DEFAULT_OTA_FORMAT,
                "help_text": "Template for the name given to a matched broadcast channel. The placeholders are {NETWORK}, {STATE}, {CITY} and {CALLSIGN}, and the default produces a name such as ABC - NY New York (WABC-TV). A channel whose station record is missing one of the placeholders you use is left alone rather than renamed with a gap.",
            },
            {
                "id": "ota_market_fallback",
                "label": "Match by Market When No Callsign",
                "type": "boolean",
                "default": False,
                "help_text": (
                    "When a name states a market and a channel number but no callsign, "
                    "such as ABC 9 HD [SYRACUSE], look the station up by market instead. "
                    "A station is accepted only when exactly one fits, so an ambiguous "
                    "market is left alone. Leave this off to match on callsigns only."
                ),
            },
            {
                "id": "unknown_suffix",
                "label": "Unknown Channel Suffix",
                "type": "string",
                "default": PluginConfig.DEFAULT_UNKNOWN_SUFFIX,
                "placeholder": PluginConfig.DEFAULT_UNKNOWN_SUFFIX,
                "help_text": "Text appended by Tag Unknown Channels to a channel the matcher could not identify. Include the leading space if you want one, as the default does. Leave empty to tag nothing.",
            },
            {
                "id": "ignored_tags",
                "label": "Ignored Tags",
                "type": "string",
                "default": PluginConfig.DEFAULT_IGNORED_TAGS,
                "placeholder": PluginConfig.DEFAULT_IGNORED_TAGS,
                "help_text": "Comma-separated tags removed from a name before it is matched, which leaves the channel name itself untouched. Write a tag in brackets or parentheses, such as [HD] or (PRIME), to match it literally; a bare word is matched on word boundaries.",
            },
            {
                "id": "default_logo",
                "label": "Default Logo",
                "type": "string",
                "default": "",
                "placeholder": "abc-logo-2013-garnet-us",
                "help_text": "The display name of a logo on the Dispatcharr Logos page, matched without regard to case. Apply Default Logo gives it to every channel in scope that has no logo yet. For a different logo per channel, use Apply Per-Channel Logos instead. Leave empty to skip.",
            },
            {
                "id": "_section_run_behaviour",
                "label": "Run Behaviour and Housekeeping",
                "type": "info",
                "description": (
                    "Settings that apply to any action rather than to one of them: "
                    "whether a run only previews its changes, how hard it works the "
                    "database while it runs, and how long its CSV exports are kept. "
                    "Dry Run Mode is what produces the preview CSV files; with it off "
                    "the same actions write to your channels. Old exports are deleted "
                    "only at the moment a new one is written, so nothing is removed on "
                    "a day you do not run the plugin."
                ),
            },
            {
                "id": "dry_run_mode",
                "label": "Dry Run Mode",
                "type": "boolean",
                "default": False,
                "help_text": "With this on, no action writes to the database. Rename Channels, Organize by Category, Import M3U Streams and Create Channels From Streams write a CSV preview to /data/exports. Tag Unknown Channels, Apply Default Logo and Apply Per-Channel Logos (tv-logos) report a count of what they would have done and write no file. Worth turning on the first time you run any of them with new settings.",
            },
            {
                "id": "rate_limiting",
                "label": "Rate Limiting",
                "type": "select",
                "default": "none",
                "options": [
                    {"value": "none", "label": "None, fastest"},
                    {"value": "low", "label": "Low, slight delay"},
                    {"value": "medium", "label": "Medium, moderate delay"},
                    {"value": "high", "label": "High, gentlest on the database"},
                ],
                "help_text": "How long to pause between database writes during a large "
                             "import, to leave Dispatcharr responsive while one runs. It "
                             "makes a run slower and changes nothing about the result.",
            },
            {
                "id": "csv_retention_days",
                "label": "Delete CSV Exports Older Than (Days)",
                "type": "number",
                "default": PluginConfig.DEFAULT_CSV_RETENTION_DAYS,
                "help_text": (
                    "Housekeeping for the CSV files this plugin writes to "
                    "/data/exports. After each export, this plugin's own exports "
                    "older than this many days are deleted. 0 keeps every file, "
                    "which is the default, so nothing is removed unless you ask "
                    "for it. The file just written is never deleted, and at least "
                    "one file always survives. Only files named "
                    "channel_mapparr_*.csv are touched, because that directory is "
                    "shared with other plugins. Clear CSV Exports still deletes "
                    "all of them regardless of this setting."
                ),
            },
            {
                "id": "_section_notifications",
                "label": "Emailed Reports",
                "type": "info",
                "description": (
                    "Whether this plugin hands a report to the Newsflasharr plugin, "
                    "after which runs, and in which format. Nothing is sent unless "
                    "Newsflasharr is installed and carries a routing rule matching "
                    "this plugin and its usage report event; without that rule the "
                    "report is delivered as plain text with no attachment, because "
                    "attachments are email only. This plugin has no scheduler, so a "
                    "report is only ever produced by a run you start yourself."
                ),
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
            "button_color": "red",
            "confirm": {"message": "This will rename channels to the standardized format. This action is irreversible. Continue?"},
        },
        {
            "id": "rename_unknown_channels",
            "label": "Tag Unknown Channels",
            "description": "Append the configured suffix to unmatched OTA and premium channels.",
            "button_label": "\u2696 Tag Unknowns",
            "button_variant": "filled",
            "button_color": "orange",
            "confirm": {"message": "This will append the configured suffix to unmatched channels. Continue?"},
        },
        {
            "id": "apply_logos",
            "label": "Apply Default Logo",
            "description": "Assign the configured default logo to channels that don't have one.",
            "button_label": "\u2b50 Apply Default Logo",
            "button_variant": "filled",
            "button_color": "orange",
            "confirm": {"message": "This will apply the default logo to channels without a logo. Continue?"},
        },
        {
            "id": "apply_tv_logos",
            "label": "Apply Per-Channel Logos (tv-logos)",
            "description": "Fuzzy-match each channel name to the tv-logo/tv-logos GitHub repo and assign per-channel logos. Uses the country codes from Channel Databases. Channels with an existing logo are left alone.",
            "button_label": "\u2756 Apply Per-Channel Logos",
            "button_variant": "filled",
            "button_color": "orange",
            "confirm": {"message": "This will fetch tv-logos and assign per-channel logos to channels without one. Continue?"},
        },
        {
            "id": "organize_by_category",
            "label": "Organize by Category",
            "description": "Move channels into category-based groups. Runs in the web request, so a large run can take a while before the page answers. Dry Run exports a CSV preview.",
            "button_label": "\u2630 Organize",
            "button_variant": "filled",
            "button_color": "red",
            "confirm": {"message": "This will create new groups (if needed) and move channels to category-based groups. Continue?"},
        },
        {
            "id": "import_m3u_streams",
            "label": "Import M3U Streams",
            "description": "Create channels from M3U streams organized by category. Runs in background. Dry Run exports a CSV preview.",
            "button_label": "\u21e9 Import Streams",
            "button_variant": "filled",
            "button_color": "orange",
            "confirm": {"message": "This will create new channels from M3U streams and organize them into groups. Duplicates get suffixes. Continue?"},
        },
        {
            "id": "create_channels_from_streams",
            "label": "Create Channels From Streams",
            "description": "Create one channel per broadcast station for streams that are not attached to any channel yet. Unlike Import M3U Streams this creates one channel per station rather than one per stream, and it attaches no streams. Runs in background. Dry Run exports a CSV preview.",
            "button_label": "✚ Create Channels",
            "button_variant": "filled",
            "button_color": "orange",
            "confirm": {"message": "This will create new channels in the target group. No streams are attached. Continue?"},
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
            "button_color": "orange",
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

    @staticmethod
    def has_no_logo(channel_row):
        """True when a channel row carries no logo the operator chose.

        Dispatcharr stores the absence of a logo as None, as 0, or as the string
        "0" depending on where the row came from, and 0 is the identifier of its
        own Default logo rather than a real one.

        This is the whole reason Apply Default Logo and Apply Per-Channel Logos
        are not marked as destructive: both select ONLY rows this returns True
        for, so neither can replace artwork somebody picked. It is a plain
        function so that fact can be tested directly rather than by searching
        this file for the two different spellings it used to have.
        """
        return channel_row.get("logo_id") in (None, 0, "0")

    @staticmethod
    def _yes_no(value):
        """Render a stored boolean as Yes or No.

        Dispatcharr stores some booleans as the strings "true" and "false", so a
        plain truth test reports the string "false" as on. Anything that is not
        recognisably true reads as No, because a boolean setting is either on or
        off and there is no third state to report.
        """
        if isinstance(value, str):
            return "Yes" if value.strip().lower() in ("true", "yes", "1") else "No"
        return "Yes" if value else "No"

    def _describe_setting(self, field, settings):
        """One preamble line for one declared setting, or None to leave it out.

        Reads the label from the field as the settings form declares it. The
        label map this replaced was written in release 0.5.0 and the settings
        were relabelled in release 1.26.1001200 without it, so six settings were
        printed under names the form does not use. Deriving the label rather than
        repeating it is what stops that happening again.
        """
        field_id = field.get("id") or ""
        label = field.get("label") or field_id
        if field.get("type") == "boolean":
            return f"# {label}: {self._yes_no(settings.get(field_id))}"

        stored = settings.get(field_id, "")
        if stored == "" or stored is None:
            return f"# {label}: (not set)"

        # A choice is recorded as the form words it, because the stored value
        # behind an option can be text that appears nowhere on the page: the M3U
        # source line used to read "_all" where the operator had picked an
        # option worded "All sources (no filter)".
        value = next((option.get("label") or stored
                      for option in field.get("options") or []
                      if option.get("value") == stored), stored)

        if field_id == "match_sensitivity":
            # The option wording already carries the number. This adds the one
            # thing a number alone does not say, which is which way is stricter.
            return f"# {label}: {value}. A higher number is stricter."
        if field_id == "csv_retention_days":
            # Parsed by the same function the deletion itself uses, so the two
            # cannot disagree about what the setting means.
            days = self._retention_days(stored)
            if days is not None and days <= 0:
                return f"# {label}: {value} (keep every export)"
            if days is not None:
                return (f"# {label}: {value} (this file is deleted after "
                        f"{days} day(s), once a later export runs)")
        return f"# {label}: {value}"

    def _generate_csv_settings_header(self, settings, title, summary):
        """Build the commented preamble that opens every CSV this plugin writes.

        ``title`` names the action that produced the file. ``summary`` is a list
        of (label, value) pairs saying what the run did, printed ABOVE the
        settings, because the result is what a reader wants first and the
        configuration is what they want second.

        Both are REQUIRED rather than defaulted. A preamble that can be built
        without them is a preamble a new writer can silently leave silent, and
        Python refusing the call is a better guard than a test that reads this
        file looking for the keywords.

        The whole preamble is plain ASCII on purpose: a spreadsheet opening a
        CSV under a different codepage turns a non-ASCII character into
        mojibake.
        """
        lines = [
            f"# Report: Channel Mapparr, {title}",
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Plugin Version: {self.version}",
            "# Lines beginning with a hash are this preamble, not data. Tell your",
            "# spreadsheet to skip them on import. The column headings are on the",
            "# first line that does not begin with a hash.",
            "#",
        ]

        if summary:
            lines.append("# What this run did")
            for label, value in summary:
                lines.append(f"# {label}: {value}")
            lines.append("#")

        # The settings are read from the plugin's own field declarations, in the
        # order the form shows them, so a relabelled setting cannot leave a stale
        # name behind here. Reading `fields` costs one small M3UAccount query,
        # which that property already guards, and an export happens a few times a
        # run rather than on any hot path.
        lines.append("# Settings used")
        try:
            declared = self.fields
        except Exception:  # pragma: no cover - fields guards its own query
            declared = []
        # The settings under the Emailed Reports heading decide whether an email
        # is sent, not what is in this file, so they are left out. Reading the
        # heading rather than a "notify_" name prefix means a setting added
        # there under any name is excluded, and a setting named notify_ that is
        # moved elsewhere is not.
        skipped_section = None
        for field in declared:
            if field.get("type") == "info":
                skipped_section = field.get("id")
                continue
            if skipped_section == "_section_notifications":
                continue
            described = self._describe_setting(field, settings)
            if described:
                lines.append(described)

        lines.append("#")
        return "\n".join(lines) + "\n"

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

    @staticmethod
    def _report_counter():
        """The published report counter that Newsflasharr's status readout reads.

        Lazy like the other report side modules, so a fault in it cannot stop the
        plugin loading.
        """
        try:
            from . import report_counter
        except ImportError:
            import report_counter
        return report_counter

    def _report_dir(self):
        """Where report files are written. A method so a test can redirect it."""
        return self._reports().REPORT_DIR

    def _counter_dir(self):
        """Where the published report count is written. A method so a test can
        redirect it, and read at call time rather than bound as a default, which
        would defeat that redirect."""
        return self._report_counter().COUNTER_DIR

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

            # Publish the count Newsflasharr's status readout displays. This is
            # LAST and inside its own guard on purpose. Everything above runs
            # inside one try whose handler reports "the report path raised", so
            # an increment placed between the written report and the queued
            # notification could, if it ever raised, stop the mail being sent and
            # make a successful run report an error. Nothing about a cosmetic
            # number may sit between a written report and its delivery. It counts
            # BUILDS, not deliveries, so a build whose delivery fails still
            # counts: emit_reports contains its own failures and does not raise.
            try:
                self._report_counter().bump(self._counter_dir(), logger)
            except Exception:
                pass
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
            return 0

        channel_ids = [u['id'] for u in updates]
        channels = {ch.id: ch for ch in Channel.objects.filter(id__in=channel_ids)}

        before = {}
        to_update = []
        for u in updates:
            ch = channels.get(u['id'])
            if ch:
                before[ch.id] = tuple(getattr(ch, f) for f in fields)
                for field in fields:
                    if field in u:
                        setattr(ch, field, u[field])
                to_update.append(ch)

        changed = sum(1 for ch in to_update if tuple(getattr(ch, f) for f in fields) != before[ch.id])
        if to_update:
            with transaction.atomic():
                Channel.objects.bulk_update(to_update, fields)
            logger.info(f"{PLUGIN_LOG_PREFIX} Bulk updated {len(to_update)} channels (fields: {', '.join(fields)})")
        return changed

    def _record_channels_renamed(self, count, mode, logger=None):
        """Append one line recording how many channel names a run wrote.

        Append only and never raises: a tally is worth less than the rename that
        produced it. Only a run that wrote at least one name records a line, so a
        preview, a dry run, a run with nothing to rename and a failed write leave
        no trace. Only an integer and a short mode string are recorded, never a
        channel name, group name or URL.
        """
        log = logger or LOGGER
        if isinstance(count, bool) or not isinstance(count, int):
            log.warning(f"{PLUGIN_LOG_PREFIX} Rename tally skipped: the count was "
                        f"{count!r}, which is not a whole number")
            return False
        if count <= 0:
            return False
        line = json.dumps({"ts": int(time.time()), "channels": count, "mode": mode})
        try:
            with open(PluginConfig.RENAME_LEDGER_FILE, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except Exception as exc:
            log.warning(f"{PLUGIN_LOG_PREFIX} Could not record the rename tally: {exc}")
            return False

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

        The prefix is not always a two or three letter country code. Providers
        also label a group with a whole word, such as "CITY: PBS KETC ST. LOUIS"
        or "PRIME: NBC ST. LOUIS NEWS (KSDK)", so the prefix pattern accepts up
        to 12 letters, matching the token pattern used for the network itself.
        Every name in ``_STREAM_NETWORKS`` is 11 letters or fewer, so a network
        used as the prefix is still recognized and left in place.
        """
        if not channel_name:
            return None
        s = channel_name.strip()
        m_geo = re.match(r'^\s*[\[(]?([A-Za-z]{2,12})[\])]?\s*[:|]\s*(.*)$', s)
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
                "create_channels_from_streams": self.create_channels_from_streams_action,
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
            # Resolved from the live settings dict, not from saved_settings or
            # instance state, and read once here rather than per channel.
            market_fallback = bool(settings.get("ota_market_fallback", False))

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
                    callsign, station = self.matcher.match_broadcast_channel(
                        current_name,
                        network=self._extract_stream_network(current_name),
                        allow_market_fallback=market_fallback,
                    )

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

            # Counted before the file is written so the preamble can open with
            # what the run did. The same two counts are shown in the toast below.
            renamed_count = sum(1 for c in all_changes if c.get('status') == 'Renamed')
            skipped_count = sum(1 for c in all_changes if c.get('status') == 'Skipped')

            # Write CSV atomically (temp file + rename to prevent corrupt partial writes)
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                                 dir=export_dir, suffix='.csv', delete=False) as csvfile:
                    tmp_path = csvfile.name
                    # Write settings header as comments
                    csvfile.write(self._generate_csv_settings_header(
                        settings,
                        title="Rename Channels preview",
                        summary=[("Channels to rename", renamed_count),
                                 ("Channels skipped", skipped_count)]))

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
                self._finish_csv_export(tmp_path, csv_path, settings)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.info(f"{PLUGIN_LOG_PREFIX} Preview CSV exported to {csv_path}")

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
            written = self._bulk_update_channels(updates, ['name'], logger)
            self._record_channels_renamed(written, "rename_channels", logger)
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
            written = self._bulk_update_channels(updates, ['name'], logger)
            self._record_channels_renamed(written, "rename_unknown_channels", logger)
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
            channels_without_logos = [ch for ch in all_channels
                                      if self.has_no_logo(ch)]

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
            channels_without_logos = [ch for ch in all_channels
                                      if self.has_no_logo(ch)]
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

            # Build category mapping from channel databases.
            #
            # There is deliberately NO callsign to category map. No record in
            # the FCC station table carries a category field, which
            # tests/test_station_table.py pins, so a map built from it was
            # always empty and every over the air channel fell through to the
            # premium path below. That dead map and its per channel lookup were
            # removed rather than made to work: the only category value the
            # over the air path has is the single string in
            # OTA_IMPORT_CATEGORY, so populating it would move every over the
            # air channel into one group named Broadcast.
            #
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

                # Classification is by channel name against the premium
                # databases. See the note above the premium map for why there
                # is no callsign branch here.
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
                    csvfile.write(self._generate_csv_settings_header(
                        settings,
                        title="Organize by Category preview",
                        summary=[("Channels to move", len(moves))]))
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
                self._finish_csv_export(tmp_path, csv_path, settings)
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

            # Build category mapping from channel databases.
            #
            # There is deliberately NO callsign to category map. No record in
            # the FCC station table carries a category field, which
            # tests/test_station_table.py pins, so a map built from it was
            # always empty and every over the air channel fell through to the
            # premium path below. That dead map and its per channel lookup were
            # removed rather than made to work: the only category value the
            # over the air path has is the single string in
            # OTA_IMPORT_CATEGORY, so populating it would move every over the
            # air channel into one group named Broadcast.
            #
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

                # Classification is by channel name against the premium
                # databases. See the note above the premium map for why there
                # is no callsign branch here.
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
            callsign, ota_station = self.matcher.match_broadcast_channel(
                stream_name,
                network=self._extract_stream_network(stream_name),
                allow_market_fallback=bool(settings.get("ota_market_fallback", False)),
            )

            if ota_station:
                category = OTA_IMPORT_CATEGORY

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

        # One row is written per matched stream and per unmatched stream, so
        # these two counts are what the reader sees below.
        matched_count = sum(len(v) for v in matched_by_category.values())

        # Write CSV atomically (temp file + rename to prevent corrupt partial writes)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=export_dir, suffix='.csv', delete=False) as csvfile:
                tmp_path = csvfile.name
                # Write settings header
                csvfile.write(self._generate_csv_settings_header(
                    settings,
                    title="Import M3U Streams preview",
                    summary=[("Streams to import", matched_count),
                             ("Streams with no match", len(unmatched_streams)),
                             ("Channel groups involved", len(matched_by_category))]))
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
            self._finish_csv_export(tmp_path, csv_path, settings)
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

    # ------------------------------------------------------------------
    # Create Channels From Streams
    #
    # One channel per STATION, for streams no channel uses yet. This is not
    # what Import M3U Streams does: that creates one channel per STREAM and
    # tells them apart with a suffix, so a provider carrying each station once
    # per M3U account produces four channels for one station, where the layout
    # here is one channel holding the four streams.
    #
    # No stream is attached. A channel created here is a target for a stream
    # matcher to fill in afterwards, and writing those links here would take
    # that decision away from the operator.
    # ------------------------------------------------------------------

    def _existing_channel_names(self):
        """Return every channel name in the database.

        Split out so a test can replace it without standing up a fake manager,
        and so the query is made once rather than per planned item.
        """
        return [row['name'] for row in Channel.objects.all().values('name')]

    def _collect_unattached_streams(self, source_group_names, logger):
        """Return stream rows in the named groups that no channel uses.

        A stream is unattached when no ChannelStream row references it. That is
        the same definition the interface shows, and it is what makes a second
        run of this action do nothing rather than duplicate its own work.
        """
        wanted = {name.strip() for name in source_group_names if name.strip()}
        group_ids = [g['id'] for g in self._get_all_groups(logger)
                     if g['name'] in wanted]
        if not group_ids:
            logger.warning(f"{PLUGIN_LOG_PREFIX} None of the named stream groups exist: "
                           f"{sorted(wanted)}")
            return []
        rows = list(Stream.objects.filter(channel_group_id__in=group_ids)
                    .values('id', 'name', 'm3u_account_id'))
        linked = set(ChannelStream.objects
                     .filter(stream_id__in=[r['id'] for r in rows])
                     .values_list('stream_id', flat=True))
        unattached = [r for r in rows if r['id'] not in linked]
        logger.info(f"{PLUGIN_LOG_PREFIX} {len(unattached)} of {len(rows)} streams in "
                    f"{len(group_ids)} group(s) are not attached to a channel")
        return unattached

    def _seed_resolver(self, settings, logger):
        """Return (resolve, exclude), both taking a stream name.

        ``resolve`` renders the same OTA name format the rename action uses, so
        a channel created here carries the name a rename would have given it.
        ``exclude`` reports whether the station belongs to a network the
        operator listed in Networks to Skip.

        Both read one memo of stream name to (name, station), so the station is
        looked up once per name rather than once per callable.

        Two signals decide the exclusion, and neither is enough alone. Measured
        on this installation: of 14 Telemundo stations reachable from the
        provider stream names, 13 carry a Telemundo affiliation on their FCC
        record while KUAN carries an empty one and is identifiable only from
        the word in its stream name. The affiliation field is read through
        station_affiliation.station_networks rather than a substring test,
        because that field is not maintained to a standard.
        """
        fmt = settings.get("ota_format") or PluginConfig.DEFAULT_OTA_FORMAT
        fallback = bool(settings.get("ota_market_fallback", False))
        skip_networks = {
            token.strip().upper()
            for token in (settings.get("seed_exclude_networks") or "").split(",")
            if token.strip()
        }
        memo = {}

        def _lookup(stream_name):
            if stream_name not in memo:
                network = self._extract_stream_network(stream_name)
                callsign, station = self.matcher.match_broadcast_channel(
                    stream_name, network=network, allow_market_fallback=fallback)
                name = None
                if station:
                    name = self._format_ota_name(station, fmt, callsign,
                                                 network_override=network)
                memo[stream_name] = (name, station)
            return memo[stream_name]

        def resolve(stream_name):
            return _lookup(stream_name)[0]

        def exclude(stream_name):
            if not skip_networks:
                return False
            station = _lookup(stream_name)[1]
            # The station's PRIMARY network, which is what the station IS, not
            # every network it merely carries. Most large stations run a second
            # network on a subchannel, so reading the whole field excludes the
            # station itself: measured on this installation, filtering on
            # Telemundo that way removed 21 stations of which 6 were ABC, CBS
            # or FOX affiliates carrying Telemundo on a subchannel.
            primary = primary_network((station or {}).get("network_affiliation", ""))
            on_record = {primary} if primary else set()
            # The station record is the better signal, but a low power record
            # often names no network at all, so the stream name is read too.
            in_name = {word for word in re.split(r"[^A-Za-z]+", stream_name.upper())
                       if word}
            return bool(skip_networks & (on_record | in_name))

        return resolve, exclude

    def create_channels_from_streams_action(self, settings, logger):
        """Create one channel per station for streams that have no channel."""
        source_names = [n.strip() for n
                        in (settings.get("seed_source_groups") or "").split(",")
                        if n.strip()]
        target_name = (settings.get("seed_target_group") or "").strip()

        if not source_names:
            return {"status": "error",
                    "error": "Set Stream Groups to Seed From before running this action."}
        if not target_name:
            return {"status": "error",
                    "error": "Set Channel Group to Create In before running this action."}

        try:
            self._check_group_destinations_not_ignored(
                [target_name], settings.get("ignore_groups"))
        except GroupScopeError as exc:
            return self._scope_error_return(exc)

        groups = self._get_all_groups(logger)
        target_ids = [g['id'] for g in groups if g['name'] == target_name]
        if not target_ids:
            return {"status": "error",
                    "error": f"Channel group '{target_name}' does not exist. "
                             f"Create it first, then run this action."}
        if len(target_ids) > 1:
            # Dispatcharr permits duplicate group names, so this is reachable.
            return {"status": "error",
                    "error": f"More than one channel group is named '{target_name}'. "
                             f"Rename one of them so the target is unambiguous."}

        if not self._load_channel_data(settings, logger):
            return {"status": "error", "error": "Could not load the channel databases."}

        streams = self._collect_unattached_streams(source_names, logger)
        if not streams:
            return {"status": "success",
                    "message": "No unattached streams found in those groups."}

        resolve, exclude = self._seed_resolver(settings, logger)
        plan = build_seed_plan(streams, self._existing_channel_names(),
                               resolve, exclude)

        if settings.get("dry_run_mode", False):
            csv_path, csv_filename = self._export_seed_preview(plan, settings, logger)
            return {
                "status": "success",
                "message": (f"Preview only. Would create {len(plan.create)}, "
                            f"skip {len(plan.skip)} already named, "
                            f"skip {len(plan.excluded)} by network, "
                            f"leave {len(plan.unresolved)} unresolved."),
                "file": csv_path,
            }

        # Everything that can refuse has refused by now. The card never shows a
        # background thread's return value, so nothing below this line can tell
        # the operator it went wrong except through Show Status and the log.
        if not self._try_start_thread(
                self._create_channels_from_streams_bg,
                (copy.deepcopy(settings), logger, plan, target_ids[0])):
            return {"status": "error",
                    "error": "An operation is already running. Please wait for it to finish."}
        return {"status": "ok",
                "message": f"Creating {len(plan.create)} channels in background.",
                "background": True}

    def _export_seed_preview(self, plan, settings, logger):
        """Write the plan to a CSV and return (path, filename)."""
        export_dir = PluginConfig.EXPORT_DIR
        os.makedirs(export_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"channel_mapparr_seed_preview_{timestamp}.csv"
        csv_path = os.path.join(export_dir, csv_filename)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8',
                                             dir=export_dir, suffix='.csv',
                                             delete=False) as csvfile:
                tmp_path = csvfile.name
                csvfile.write(self._generate_csv_settings_header(
                    settings,
                    title="Create Channels From Streams preview",
                    summary=[("Channels to create", len(plan.create)),
                             ("Skipped, name already used", len(plan.skip)),
                             ("Skipped, network excluded", len(plan.excluded)),
                             ("Streams left unresolved", len(plan.unresolved))]))
                csvfile.write("#\n# Create Channels From Streams Preview\n#\n")
                writer = csv.DictWriter(csvfile, fieldnames=[
                    'Action', 'Proposed Channel Name', 'Source Stream Names',
                    'Stream Count', 'M3U Accounts'])
                writer.writeheader()
                for label, items in (("create", plan.create),
                                     ("skip, name already used", plan.skip),
                                     ("skip, network excluded", plan.excluded),
                                     ("unresolved", plan.unresolved)):
                    for item in items:
                        writer.writerow({
                            'Action': label,
                            'Proposed Channel Name': item.proposed_name or '',
                            'Source Stream Names': ' | '.join(item.source_names),
                            'Stream Count': len(item.stream_ids),
                            'M3U Accounts': ' '.join(str(a) for a in sorted(
                                {a for a in item.accounts if a is not None})),
                        })
            self._finish_csv_export(tmp_path, csv_path, settings)
            tmp_path = None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        logger.info(f"{PLUGIN_LOG_PREFIX} Seed preview written to {csv_path}")
        return csv_path, csv_filename

    def _create_seed_channels(self, plan, target_group_id, settings, logger):
        """Create one channel per planned item. Attaches no streams."""
        used = {float(n) for n
                in Channel.objects.values_list('channel_number', flat=True)
                if n is not None}
        raw_start = (settings.get("seed_start_number") or "").strip()
        try:
            start = float(raw_start) if raw_start else None
        except ValueError:
            logger.warning(f"{PLUGIN_LOG_PREFIX} First Channel Number to Use is not a "
                           f"number ({raw_start!r}); allocating above the highest in use")
            start = None

        numbers = allocate_channel_numbers(used, len(plan.create), start)
        progress = ProgressTracker(len(plan.create), "seed_channels", logger)

        records = []
        for item, number in zip(plan.create, numbers):
            if self._stop_event.is_set():
                logger.info(f"{PLUGIN_LOG_PREFIX} Channel creation cancelled by user.")
                break
            try:
                with transaction.atomic():
                    channel = Channel.objects.create(
                        name=item.proposed_name,
                        channel_number=number,
                        channel_group_id=target_group_id,
                    )
                records.append({'id': channel.id, 'number': number,
                                'name': item.proposed_name,
                                'source_names': list(item.source_names)})
            except Exception as exc:
                logger.error(f"{PLUGIN_LOG_PREFIX} Could not create channel "
                             f"{item.proposed_name!r}: {exc}")
            progress.update()

        logger.info(f"{PLUGIN_LOG_PREFIX} Created {len(records)} channels in group "
                    f"{target_group_id}")
        return records

    def _create_channels_from_streams_bg(self, settings, logger, plan, target_group_id):
        """Background wrapper: create the channels, then export the result."""
        try:
            records = self._create_seed_channels(plan, target_group_id, settings, logger)
            csv_path, csv_filename = self._export_seed_preview(plan, settings, logger)
            message = (f"Created {len(records)} channels. No streams attached. "
                       f"Details in {csv_filename}")
            self._last_bg_result = {"status": "success", "message": message,
                                    "file": csv_path}
            logger.info(f"{PLUGIN_LOG_PREFIX} SEED COMPLETED: {message}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": self.name, "message": message})
        except Exception as exc:
            self._last_bg_result = {"status": "error", "error": str(exc)}
            logger.exception(f"{PLUGIN_LOG_PREFIX} Channel creation error: {exc}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": self.name,
                "message": f"Channel creation error: {exc}"})

    def plugin_status_action(self, settings, logger):
        """Read the persistent progress file and return a user-facing summary."""
        progress = load_progress(PROGRESS_FILE)
        message = build_status_message(progress)
        return {"status": "success", "message": message}

    @staticmethod
    def _retention_days(value):
        """Read the retention setting as a whole number of days, or None.

        Read through float first, then truncated. int("7.0") raises, so parsing
        with int alone turned a value the operator had explicitly set into a
        silent no-op that behaved exactly like the 0 which means keep
        everything. Nothing in this plugin controls what Dispatcharr stores
        behind a number field, so the parse has to be the tolerant one.

        None means the value could not be read at all. The caller reports that;
        it is not the same as a value of 0.
        """
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _csv_exports_to_delete(entries, retention_days, now, protect=None):
        """Which of this plugin's CSV exports are old enough to remove.

        ``entries`` is a sequence of (filename, modification time) pairs,
        normally the whole export directory. Returns the names to delete,
        sorted. Pure: it touches no filesystem, which is where the tests live.

        Nothing is deleted unless a positive number of days is configured, so an
        installation that never asked for this keeps every file. The file just
        written is never deleted. At least one of this plugin's files always
        survives, so a small number cannot empty the directory.
        """
        days = Plugin._retention_days(retention_days)
        if days is None or days <= 0:
            return []

        mine = []
        for name, mtime in entries:
            # BOTH tests are required. The directory is shared, so selecting on
            # the suffix alone deletes other plugins' exports.
            if not (name.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                    and name.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
                continue
            try:
                stamp = float(mtime)
            except (TypeError, ValueError):
                continue
            # A not-a-number timestamp must be dropped, not kept. Every
            # comparison against it is false, so it would win the "which is
            # newest" test below, become the survivor, and let every real file
            # be deleted instead of one being kept.
            if stamp != stamp:
                continue
            mine.append((name, stamp))
        if not mine:
            return []

        # One of this plugin's files is guaranteed to survive. The file just
        # written is the natural choice when it is present, otherwise the most
        # recent one. The survivor is only ever chosen from our own files, so a
        # newer file belonging to another plugin cannot license deleting all of
        # ours.
        survivor = protect if any(name == protect for name, _ in mine) else None
        if survivor is None:
            survivor = max(mine, key=lambda pair: (pair[1], pair[0]))[0]

        # Strictly older: exactly N days old is not OLDER than N days.
        cutoff = float(now) - days * PluginConfig.SECONDS_PER_DAY
        return sorted(name for name, stamp in mine
                      if stamp < cutoff and name != survivor)

    def _prune_csv_exports(self, retention_days, protect=None):
        """Delete this plugin's CSV exports older than ``retention_days``.

        Returns how many were removed. NEVER raises: this runs immediately after
        a successful export, and a failure to tidy up must not turn a successful
        export into a reported error.
        """
        # Parsed once, here, and the result decides whether there is any work at
        # all. A value that cannot be read behaves exactly like the 0 that means
        # keep everything, so it has to say so; a blank value is the default and
        # is not worth a log line every time an export is written.
        days = self._retention_days(retention_days)
        if days is None:
            if str(retention_days).strip() not in ("", "None"):
                LOGGER.warning(
                    f"{PLUGIN_LOG_PREFIX} Delete CSV Exports Older Than (Days) is set "
                    f"to {retention_days!r}, which is not a number. No exports were "
                    f"deleted. Set it to a whole number of days, or 0 to keep every file."
                )
            return 0
        if days <= 0:
            # Nothing is ever deleted at 0, so the directory is not read at all.
            # That directory is shared with five other plugins and holds well
            # over a hundred files, and two of the four export paths run inside
            # the web request.
            return 0

        directory = PluginConfig.EXPORT_DIR
        try:
            names = os.listdir(directory)
        except OSError:
            return 0

        entries = []
        for name in names:
            # Filtered before asking the filesystem for an age. Only about one
            # file in thirty in that directory is ours, so stat'ing every one of
            # them is work thrown away. The same two tests are applied again
            # inside _csv_exports_to_delete, which is the guard the tests
            # exercise and the one that must not be removed.
            if not (name.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                    and name.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
                continue
            try:
                entries.append((name, os.path.getmtime(os.path.join(directory, name))))
            except OSError:
                # It vanished between listing the directory and asking its age,
                # so there is nothing left to delete.
                continue

        removed = 0
        for name in self._csv_exports_to_delete(entries, retention_days,
                                                time.time(), protect):
            try:
                os.remove(os.path.join(directory, name))
                removed += 1
                LOGGER.info(f"{PLUGIN_LOG_PREFIX} Deleted CSV export older than "
                            f"{retention_days} day(s): {name}")
            except OSError as exc:
                LOGGER.warning(f"{PLUGIN_LOG_PREFIX} Could not delete old CSV "
                               f"export {name}: {exc}")
        return removed

    def _finish_csv_export(self, tmp_path, csv_path, settings):
        """Publish a finished export, then delete this plugin's older ones.

        Every CSV writer here renames its temporary file into place through this
        one function, which is what makes the pruning unavoidable: a new writer
        has to come through here to publish its file. Tidying happens at the
        moment of writing rather than on a schedule, because files only
        accumulate when one is written, so pruning here keeps the directory
        bounded at all times and needs no second timer with its own election and
        duplicate-fire handling.

        The file just written is protected regardless of what the age
        arithmetic says.
        """
        os.replace(tmp_path, csv_path)
        self._prune_csv_exports(settings.get("csv_retention_days"),
                                protect=os.path.basename(csv_path))

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
                # Same two tests the age-based deletion uses, from the same
                # constants, so the two paths cannot drift apart on what counts
                # as one of this plugin's files. This action deliberately
                # ignores the age setting: someone pressing it expects
                # everything of ours cleared.
                if (filename.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                        and filename.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
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
