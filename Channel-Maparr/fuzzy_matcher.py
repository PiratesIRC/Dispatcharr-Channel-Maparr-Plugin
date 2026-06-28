"""
Fuzzy Matcher Module for Dispatcharr Plugins
Handles fuzzy matching, normalization, and channel database loading.
Reusable across multiple plugins (Stream-Mapparr, Channel Mapparr, etc.)
"""

import os
import re
import json
import logging
from glob import glob

try:
    from .aliases import CHANNEL_ALIASES as _BUILTIN_ALIASES
except (ImportError, ValueError):
    try:
        from aliases import CHANNEL_ALIASES as _BUILTIN_ALIASES
    except ImportError:
        _BUILTIN_ALIASES = {}

# Version: YY.DDD.HHMM (Julian date format: Year.DayOfYear.Time)
__version__ = "26.100.1200"

# FCC station table (callsign -> network_affiliation / community_served_city /
# community_served_state). Loaded into the OTA broadcast lookup when the US
# database is selected. US-only; absent for non-US deployments.
BROADCAST_STATIONS_FILE = "networks.json"

# Setup logging
LOGGER = logging.getLogger("plugins.fuzzy_matcher")

# The shared matching primitives (calculate_similarity with its rapidfuzz fast path +
# pure-Python fallback, process_string_for_matching, the length/trailing-number helpers,
# the callsign denylist + extract/normalize) live in the vendored core. The decorative
# helpers are re-exported so the conftest/unit tests that reference them keep working.
# Channel-Maparr keeps its own normalize_name, single-digit token-overlap guard, and the
# callsign ladder (channel_lookup rescue, parenthesized-only), which diverge from the core.
try:
    from .matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,  # noqa: F401  re-exported for the decoration unit tests
        _normalize_emoji,  # noqa: F401
        _strip_stylized_tokens,  # noqa: F401
    )
except ImportError:  # script/test context without the package parent on sys.path
    from matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,  # noqa: F401
        _normalize_emoji,  # noqa: F401
        _strip_stylized_tokens,  # noqa: F401
    )

# Categorized regex patterns for granular control during fuzzy matching
# Note: All patterns are applied with re.IGNORECASE flag in normalize_name()

# Quality-related patterns: [4K], HD, (SD), etc.
QUALITY_PATTERNS = [
    # Quality tags in any format: brackets, parentheses, or standalone
    # These patterns match quality tags at the beginning, middle, or end of names
    # Matches: [4K], (4K), 4K, [FHD], (FHD), FHD, etc.
    # Quality keywords list: 4K, 8K, UHD, FHD, HD, SD, FD, Unknown, Unk, Slow, Dead, Backup
    
    # Bracketed quality tags: [4K], [UHD], [FHD], [HD], [SD], etc.
    r'\s*\[(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead|Backup)\]\s*',
    
    # Parenthesized quality tags: (4K), (UHD), (FHD), (HD), (SD), etc.
    r'\s*\((4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead|Backup)\)\s*',
    
    # Standalone quality tags at START of string (with word boundary)
    r'^\s*(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s*',
    
    # Standalone quality tags at END of string (with word boundary)
    r'\s*\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)$',
    
    # Standalone quality tags in MIDDLE (with word boundaries on both sides)
    r'\s+\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Numeric resolution markers the keyword QUALITY_PATTERNS miss: 720p, 1080p/i, 2160p,
# 3840P, 480p, etc. — a 3-4 digit run glued directly to p/i. The 3-digit lower bound
# excludes 2-digit noise; the 4-digit upper bound excludes 5-digit numbers (10800p won't
# match). The p/i must be GLUED to the digits (no space): real markers are always written
# "720P"/"3840P", and requiring the glue avoids stripping a spaced standalone P/I such as a
# roman numeral ("Volume 100 I"). The p/i \b anchor keeps bare numbers (1080, "Channel 4")
# intact. Applied with re.IGNORECASE in the ignore_quality block, like QUALITY_PATTERNS.
RESOLUTION_PATTERNS = [
    r'\b\d{3,4}[pi]\b',
]

# Regional indicator patterns: Pacific, Central, Mountain, Atlantic
# NOTE: East/West are intentionally NOT stripped — they distinguish separate channel feeds
# (e.g., "HBO East" and "HBO West" are different channels)
REGIONAL_PATTERNS = [
    # bug-066: bare " Pacific"/" Central"/" Mountain"/" Atlantic" removed — as bare
    # words they are brand tokens far more often than feed markers ("Comedy Central",
    # "The Atlantic") and collapsed distinct channels onto one grouping key. The
    # parenthesized timezone tags below are kept (an explicit "(Central)" is a feed).
    # Regional: (Pacific) or (PACIFIC) (parenthesized format)
    r'\s*\([Pp][Aa][Cc][Ii][Ff][Ii][Cc]\)\s*',
    # Regional: (Central) or (CENTRAL) (parenthesized format)
    r'\s*\([Cc][Ee][Nn][Tt][Rr][Aa][Ll]\)\s*',
    # Regional: (Mountain) or (MOUNTAIN) (parenthesized format)
    r'\s*\([Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]\)\s*',
    # Regional: (Atlantic) or (ATLANTIC) (parenthesized format)
    r'\s*\([Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]\)\s*',
]

# Geographic prefix patterns: US:, USA:, etc.
# Strip a leading box-bar bouquet/source tag with arbitrary inner text
# ("┃CANAL+┃ NPO 1" -> "NPO 1"); box bars never occur in real names, so this
# is always safe and also covers leading "┃XX┃" country/source tags.
_LEADING_BAR_TAG_RE = re.compile(r'^\s*[┃│]\s*[^┃│]*[┃│]\s*')


GEOGRAPHIC_PATTERNS = [
    # Country codes in various formats
    # Matches patterns like: US, USA, FR, UK, CA, DE, etc.
    # With separators: US:, USA:, |FR|, US -, FR -, etc.
    
    # Format: XX: or XXX: (e.g., US:, USA:, FR:, UK:). The optional
    # second 2-3 letter group catches provider sub-tags like "CA FR:",
    # "US ES:", "UK FHD:" so both pieces are stripped, not just the
    # piece adjacent to the colon (which would otherwise leave "CA"
    # stranded as a token). Box bars (┃│) accepted as colon-equivalents.
    r'\b[A-Z]{2,3}(?:\s+[A-Z]{2,4})?[:┃│]\s*',
    
    # Format: XX - or XXX - (e.g., US - , USA - , FR - )
    # Safe because the dash clearly indicates a separator
    r'\b[A-Z]{2,3}\s*-\s*',
    
    # Format: |XX| or |XXX| and box-bar pairs ┃XX┃ / │XX│ (matched pair only,
    # so a stray "|US┃" is left alone). Pipes/bars clearly indicate a tag.
    r'(?:\|[A-Z]{2,3}\||┃[A-Z]{2,3}┃|│[A-Z]{2,3}│)\s*',
    
    # Format: [XX] or [XXX] (e.g., [US], [FR], [UK])
    # Safe because brackets clearly indicate a tag
    r'\[[A-Z]{2,3}\]\s*',
]

# Enhanced provider prefix patterns for IPTV-specific naming
PROVIDER_PREFIX_PATTERNS = [
    r'^(?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\s*[:\-\|┃│]\s*',
    r'^\s*\((?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\)\s*',
    r'\s*[\|┃│]\s*(?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\s*$',
]

# Miscellaneous patterns: (CX), (Backup), single-letter tags, etc.
MISC_PATTERNS = [
    # Remove ALL content within parentheses (e.g., (CX), (B), (PRIME), (Backup), etc.)
    r'\s*\([^)]*\)\s*',
]

# Spelled-out numbers normalized to digits inside normalize_name() so brands
# like "BBC Three" share tokens with "BBC 3".
NUM_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}
NUM_WORDS_RE = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b',
    re.IGNORECASE,
)


class FuzzyMatcher(FuzzyMatcherCore):
    """Handles fuzzy matching for channel and stream names with normalization and database loading."""

    # Common words excluded from token indexing (too generic to be discriminating)
    _COMMON_TOKENS = frozenset({"the", "and", "of", "in", "on", "at", "to", "for", "a", "an", "tv", "channel", "network"})

    def __init__(self, plugin_dir=None, match_threshold=80, logger=None):
        """
        Initialize the fuzzy matcher.

        Args:
            plugin_dir: Directory where the plugin and channel JSON files are located (optional)
            match_threshold: Minimum similarity score (0-100) for a match to be accepted
            logger: Logger instance (optional)
        """
        # The core seeds match_threshold, logger, the four normalization/callsign caches,
        # and the _known_callsigns rescue slot. Channel-Maparr's callsign ladder rescues via
        # channel_lookup instead, so _known_callsigns stays unused. Channel databases are
        # still NOT loaded here (see the NOTE below) - the constructor must stay cheap.
        super().__init__(match_threshold=match_threshold, logger=logger or LOGGER)
        self.plugin_dir = plugin_dir or os.path.dirname(__file__)

        # Channel data storage
        self.broadcast_channels = []  # Channels with callsigns
        self.premium_channels = []  # Channel names only (for fuzzy matching)
        self.premium_channels_full = []  # Full channel objects with category
        self.channel_lookup = {}  # Callsign -> channel data mapping
        self.country_codes = None  # Track which country databases are currently loaded

        # Alias map: channel_name -> [stream-name variants]. Builtins ship in
        # aliases.py; users can extend at runtime via set_user_aliases().
        # _reverse_alias_index maps normalized-variant -> canonical channel,
        # rebuilt by _rebuild_reverse_alias_index() whenever alias_map changes.
        # It enables O(1) alias lookup when the QUERY is a stream name and
        # the CANDIDATES are channel names (Channel-Maparr's pipeline).
        self.alias_map = dict(_BUILTIN_ALIASES)
        self._reverse_alias_index = {}
        self._rebuild_reverse_alias_index()

        # Inverted token index for candidate pre-filtering (built by build_token_index)
        self._token_index = {}      # token -> set of original candidate names
        self._indexed_names = set() # all names in the token index

        # NOTE: channel databases are intentionally NOT loaded here. Loading all
        # ~42k records is expensive, and Dispatcharr re-instantiates every Plugin
        # on each plugin discovery (cascading across all uWSGI/Celery workers via
        # .reload_token). Eager-loading in the constructor pinned the gevent
        # workers and wedged streaming (ops incident 2026-06-27). The run path
        # calls reload_databases() with the user-selected countries before
        # matching (plugin.py), which loads on demand — so the eager load was
        # always discarded anyway. Construct cheap; load when actually matching.

    def precompute_normalizations(self, names, user_ignored_tags=None):
        """
        Pre-normalize a list of names and cache the results.
        Call this once before matching many queries against the same candidate list.

        Not thread-safe — do not call concurrently from multiple threads.
        """
        self._norm_cache.clear()
        self._norm_nospace_cache.clear()
        self._processed_cache.clear()
        self._callsign_cache.clear()

        for name in names:
            norm = self.normalize_name(name, user_ignored_tags)
            if norm and len(norm) >= 2:
                norm_lower = norm.lower()
                self._norm_cache[name] = norm_lower
                self._norm_nospace_cache[name] = re.sub(r'[\s&\-]+', '', norm_lower)
                self._processed_cache[name] = self.process_string_for_matching(norm)

        self.logger.info(f"Pre-normalized {len(self._norm_cache)} names (from {len(names)} total)")

    def build_token_index(self, names, user_ignored_tags=None, min_token_len=2):
        """
        Build an inverted index mapping normalized tokens to candidate names.
        Call once before using get_candidates() to pre-filter fuzzy match candidates.

        Args:
            names: List of candidate names to index (e.g., premium_channels)
            user_ignored_tags: Tags to strip during normalization
            min_token_len: Minimum token length to include in index
        """
        self._token_index.clear()
        self._indexed_names = set(names)

        for name in names:
            norm_lower, _ = self._get_cached_norm(name, user_ignored_tags)
            if not norm_lower:
                norm = self.normalize_name(name, user_ignored_tags)
                if not norm or len(norm) < 2:
                    continue
                norm_lower = norm.lower()

            # Extract tokens, skip common words and very short tokens
            tokens = set()
            for token in re.split(r'[\s&\-]+', norm_lower):
                if len(token) >= min_token_len and token not in self._COMMON_TOKENS:
                    tokens.add(token)

            for token in tokens:
                if token not in self._token_index:
                    self._token_index[token] = set()
                self._token_index[token].add(name)

        self.logger.info(f"Token index built: {len(self._token_index)} unique tokens across {len(names)} names")

    def get_candidates(self, query_name, user_ignored_tags=None, max_candidates=2000):
        """
        Use the token index to find candidate names likely to match the query.
        Returns a subset of indexed names that share at least one significant token.

        Args:
            query_name: Name to find candidates for
            user_ignored_tags: Tags to strip during normalization
            max_candidates: Safety cap on returned candidates

        Returns:
            List of candidate names from the index, or None if no index built
        """
        if not self._token_index:
            return None  # No index — caller should fall back to full list

        # Normalize the query
        norm_lower, _ = self._get_cached_norm(query_name, user_ignored_tags)
        if not norm_lower:
            norm = self.normalize_name(query_name, user_ignored_tags)
            if not norm or len(norm) < 2:
                return []
            norm_lower = norm.lower()

        # Extract query tokens
        query_tokens = set()
        for token in re.split(r'[\s&\-]+', norm_lower):
            if len(token) >= 2 and token not in self._COMMON_TOKENS:
                query_tokens.add(token)

        if not query_tokens:
            return []

        # Collect candidates that share at least one token
        candidates = set()
        for token in query_tokens:
            if token in self._token_index:
                candidates.update(self._token_index[token])

        if len(candidates) > max_candidates:
            # Rank by token overlap count so most likely matches survive the cap
            ranked = sorted(candidates, key=lambda c: sum(
                1 for t in query_tokens if t in self._token_index and c in self._token_index[t]
            ), reverse=True)
            self.logger.debug(f"Token pre-filter: {len(candidates)} candidates truncated to {max_candidates} for '{query_name}'")
            return ranked[:max_candidates]

        return list(candidates)

    def _get_cached_norm(self, name, user_ignored_tags=None):
        """Get cached normalization or compute on the fly."""
        if name in self._norm_cache:
            return self._norm_cache[name], self._norm_nospace_cache[name]
        norm = self.normalize_name(name, user_ignored_tags)
        if not norm or len(norm) < 2:
            return None, None
        norm_lower = norm.lower()
        return norm_lower, re.sub(r'[\s&\-]+', '', norm_lower)

    def _get_cached_processed(self, name, user_ignored_tags=None):
        """Get cached processed string or compute on the fly."""
        if name in self._processed_cache:
            return self._processed_cache[name]
        norm = self.normalize_name(name, user_ignored_tags)
        if not norm or len(norm) < 2:
            return None
        return self.process_string_for_matching(norm)

    def _load_channel_databases(self):
        """Load all *_channels.json files from the plugin directory."""
        pattern = os.path.join(self.plugin_dir, "*_channels.json")
        channel_files = glob(pattern)
        
        if not channel_files:
            self.logger.warning(f"No *_channels.json files found in {self.plugin_dir}")
            return False
        
        self.logger.info(f"Found {len(channel_files)} channel database file(s): {[os.path.basename(f) for f in channel_files]}")
        
        total_broadcast = 0
        total_premium = 0
        
        for channel_file in channel_files:
            try:
                with open(channel_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract the channels array from the JSON structure
                    channels_list = data.get('channels', []) if isinstance(data, dict) else data

                file_broadcast = 0
                file_premium = 0

                for channel in channels_list:
                    channel_type = channel.get('type', '').lower()
                    
                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign
                        self.broadcast_channels.append(channel)
                        file_broadcast += 1
                        
                        # Create lookup by callsign
                        callsign = channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = channel
                            
                            # Also store base callsign without suffix for easier matching
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = channel
                    else:
                        # Premium/cable/national channel
                        channel_name = channel.get('channel_name', '').strip()
                        if channel_name:
                            self.premium_channels.append(channel_name)
                            self.premium_channels_full.append(channel)
                            file_premium += 1
                
                total_broadcast += file_broadcast
                total_premium += file_premium
                
                self.logger.info(f"Loaded from {os.path.basename(channel_file)}: {file_broadcast} broadcast, {file_premium} premium channels")
                
            except Exception as e:
                self.logger.error(f"Error loading {channel_file}: {e}")

        # The *_channels.json files carry no broadcast entries; OTA/callsign
        # matching comes entirely from the US FCC station table.
        total_broadcast += self._load_broadcast_stations()

        self.logger.info(f"Total channels loaded: {total_broadcast} broadcast, {total_premium} premium")
        return True

    def _load_broadcast_stations(self):
        """Load the FCC station table (``networks.json``) into the OTA lookup.

        The per-country ``*_channels.json`` databases carry only premium
        (National/Regional) entries — no ``broadcast`` type, no ``callsign``
        field — so OTA/callsign matching relies entirely on this US station
        table: callsign -> {network_affiliation, community_served_city,
        community_served_state, ...}. Each station is appended to
        ``broadcast_channels`` and indexed in ``channel_lookup`` by both its full
        callsign (``WEWS-TV``) and its base callsign (``WEWS``) so a stream that
        cites either form resolves. A missing file is non-fatal (non-US
        deployments simply have no OTA table).

        Returns the number of stations loaded.
        """
        stations_path = os.path.join(self.plugin_dir, BROADCAST_STATIONS_FILE)
        if not os.path.exists(stations_path):
            self.logger.info(f"No {BROADCAST_STATIONS_FILE} present — OTA matching disabled")
            return 0

        try:
            with open(stations_path, 'r', encoding='utf-8') as f:
                stations = json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading {BROADCAST_STATIONS_FILE}: {e}")
            return 0

        loaded = 0
        for station in stations:
            callsign = (station.get('callsign') or '').strip().upper()
            if not callsign:
                continue
            self.broadcast_channels.append(station)
            # setdefault: keep the first (primary) station for a given key so a
            # later subchannel entry can't clobber the main affiliate.
            self.channel_lookup.setdefault(callsign, station)
            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
            if base_callsign != callsign:
                self.channel_lookup.setdefault(base_callsign, station)
            loaded += 1

        self.logger.info(f"Loaded {loaded} OTA broadcast stations from {BROADCAST_STATIONS_FILE}")
        return loaded

    def reload_databases(self, country_codes=None):
        """
        Reload channel databases with specific country codes.

        Args:
            country_codes: List of country codes to load (e.g., ['US', 'UK', 'CA'])
                          If None, loads all available databases.

        Returns:
            bool: True if databases were loaded successfully, False otherwise
        """
        # Clear existing channel data
        self.broadcast_channels = []
        self.premium_channels = []
        self.premium_channels_full = []
        self.channel_lookup = {}

        # Update country_codes tracking
        self.country_codes = country_codes

        # Determine which files to load
        if country_codes:
            # Load only specified country databases
            channel_files = []
            for code in country_codes:
                file_path = os.path.join(self.plugin_dir, f"{code}_channels.json")
                if os.path.exists(file_path):
                    channel_files.append(file_path)
                else:
                    self.logger.warning(f"Channel database not found: {code}_channels.json")
        else:
            # Load all available databases
            pattern = os.path.join(self.plugin_dir, "*_channels.json")
            channel_files = glob(pattern)

        if not channel_files:
            self.logger.warning(f"No channel database files found to load")
            return False

        self.logger.info(f"Loading {len(channel_files)} channel database file(s): {[os.path.basename(f) for f in channel_files]}")

        total_broadcast = 0
        total_premium = 0

        for channel_file in channel_files:
            try:
                with open(channel_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract the channels array from the JSON structure
                    channels_list = data.get('channels', []) if isinstance(data, dict) else data

                file_broadcast = 0
                file_premium = 0

                for channel in channels_list:
                    channel_type = channel.get('type', '').lower()

                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign
                        self.broadcast_channels.append(channel)
                        file_broadcast += 1

                        # Create lookup by callsign
                        callsign = channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = channel

                            # Also store base callsign without suffix for easier matching
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = channel
                    else:
                        # Premium/cable/national channel
                        channel_name = channel.get('channel_name', '').strip()
                        if channel_name:
                            self.premium_channels.append(channel_name)
                            self.premium_channels_full.append(channel)
                            file_premium += 1

                total_broadcast += file_broadcast
                total_premium += file_premium

                self.logger.info(f"Loaded from {os.path.basename(channel_file)}: {file_broadcast} broadcast, {file_premium} premium channels")

            except Exception as e:
                self.logger.error(f"Error loading {channel_file}: {e}")

        # OTA/callsign matching is driven by the US FCC station table, not the
        # *_channels.json premium databases. Load it whenever US is in scope
        # (country_codes is None means "load everything").
        if country_codes is None or any(str(c).strip().upper() == 'US' for c in country_codes):
            total_broadcast += self._load_broadcast_stations()

        self.logger.info(f"Total channels loaded: {total_broadcast} broadcast, {total_premium} premium")
        return True


    def _compute_callsign_with_confidence(self, channel_name):
        """
        Extract US TV callsign with a confidence flag.

        Returns (callsign, is_high_confidence). High confidence =
        Priorities 1-3 (parenthesized / suffixed-paren / end-of-name).
        Priority 4 (any loose word) is low confidence — useful as a hint
        but should not floor or hard-reject a match on its own.
        """
        channel_name = re.sub(r'^D\d+-', '', channel_name)
        channel_name = re.sub(r'^USA?\s*[^a-zA-Z0-9]*\s*', '', channel_name, flags=re.IGNORECASE)

        # Priority 1: Callsigns in parentheses
        paren_match = re.search(r'\(([KW][A-Z]{3})(?:-[A-Z\s]+)?\)', channel_name, re.IGNORECASE)
        if paren_match:
            callsign = paren_match.group(1).upper()
            # Parentheses are an explicit "this is a callsign" signal, so accept a
            # denylisted English word here IF it's a real loaded station — KING /
            # WOOD / WAVE are NBC callsigns. Unparenthesized matches (Priority 3/4)
            # keep the strict denylist so prose like "King of the Hill" isn't
            # mis-read, and a non-station word like "(WEST)" stays rejected.
            if callsign not in self._CALLSIGN_DENYLIST or callsign in self.channel_lookup:
                return callsign, True

        # Priority 1b: grandfathered 3-letter callsigns in parentheses without a suffix
        # (WWL/WJZ/KYW/WRC); channel_lookup rescues denylisted-but-real stations (WHO).
        # Suffixed forms fall through to Priority 2. bug-062.
        paren3_match = re.search(r'\(([KW][A-Z]{2})\)', channel_name, re.IGNORECASE)
        if paren3_match:
            callsign = paren3_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST or callsign in self.channel_lookup:
                return callsign, True

        # Priority 2: Callsigns with broadcast suffix in parentheses
        paren_suffix_match = re.search(r'\(([KW][A-Z]{2,4}-(?:TV|CD|LP|DT|LD))\)', channel_name, re.IGNORECASE)
        if paren_suffix_match:
            return paren_suffix_match.group(1).upper(), True

        # Priority 3: Callsigns at end of name
        end_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\s*(?:\.[a-z]+)?\s*$', channel_name, re.IGNORECASE)
        if end_match:
            callsign = end_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST:
                return callsign, True

        # Priority 4: Any loose callsign-shaped word (low confidence)
        word_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\b', channel_name, re.IGNORECASE)
        if word_match:
            callsign = word_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST:
                return callsign, False

        return None, False

    def _extract_callsign_with_confidence(self, channel_name):
        """Cached wrapper. Cache cleared in precompute_normalizations.
        Size cap protects long-lived workers from unbounded growth when
        extract_callsign is called on raw channel names that never appear
        in the precomputed stream-candidate list."""
        cached = self._callsign_cache.get(channel_name)
        if cached is not None:
            return cached
        if len(self._callsign_cache) >= 50000:
            self._callsign_cache.clear()
        result = self._compute_callsign_with_confidence(channel_name)
        self._callsign_cache[channel_name] = result
        return result


    def set_user_aliases(self, user_aliases):
        """Merge user-supplied aliases on top of the builtin set. Pass a dict
        of channel_name -> [variants]; values are appended (deduped) to any
        existing builtin entry. Pass None or {} to reset to builtins only."""
        self.alias_map = dict(_BUILTIN_ALIASES)
        if user_aliases:
            for canonical, variants in user_aliases.items():
                existing = self.alias_map.get(canonical, [])
                merged = list(dict.fromkeys(existing + list(variants)))
                self.alias_map[canonical] = merged
        self._rebuild_reverse_alias_index()

    def _rebuild_reverse_alias_index(self):
        """Build {normalized_variant: canonical_channel_name} for fast lookup.
        Skips variants whose normalized form collides with the canonical of a
        DIFFERENT channel (keeps the first-seen mapping; a future collision
        is logged but not raised — alias_map is curated by humans, conflicts
        are an authoring error not a runtime error)."""
        self._reverse_alias_index = {}
        for canonical, variants in self.alias_map.items():
            for variant in [canonical] + list(variants):
                norm = self.normalize_name(variant)
                if not norm:
                    continue
                key_spaced = norm.lower()
                key_nospace = re.sub(r'[\s&\-]+', '', key_spaced)
                for key in (key_spaced, key_nospace):
                    existing = self._reverse_alias_index.get(key)
                    if existing and existing != canonical:
                        self.logger.debug(
                            f"Alias collision: '{variant}' maps to both "
                            f"'{existing}' and '{canonical}'; keeping first."
                        )
                        continue
                    self._reverse_alias_index[key] = canonical

    def alias_match(self, query_name, candidate_names, user_ignored_tags=None):
        """
        Stage 0 of matching. Normalizes the query (typically a stream name)
        and looks it up in the reverse alias index. If the resulting canonical
        channel name is present in `candidate_names`, returns it with score
        100. Returns (None, 0, None) on miss.
        """
        if not self._reverse_alias_index:
            return None, 0, None

        norm = self.normalize_name(query_name, user_ignored_tags)
        if not norm:
            return None, 0, None
        key_spaced = norm.lower()
        canonical = (self._reverse_alias_index.get(key_spaced)
                     or self._reverse_alias_index.get(re.sub(r'[\s&\-]+', '', key_spaced)))
        if not canonical:
            return None, 0, None

        if canonical in candidate_names:
            return canonical, 100, "alias"
        # Case-insensitive fallback — candidate lists may have inconsistent casing.
        cl = canonical.lower()
        for cand in candidate_names:
            if cand.lower() == cl:
                return cand, 100, "alias"
        return None, 0, None
    
    
    def normalize_name(self, name, user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                       ignore_geographic=True, ignore_misc=True, remove_cinemax=False, remove_country_prefix=False):
        """
        Normalize channel or stream name for matching by removing tags, prefixes, and other noise.

        Args:
            name: Name to normalize
            user_ignored_tags: Additional user-configured tags to ignore (list of strings)
            ignore_quality: If True, remove ALL quality indicators in any format (e.g., 4K, [4K], (4K), FHD, [FHD], (FHD), HD, SD, UHD, 8K)
            ignore_regional: If True, remove regional indicator patterns (e.g., East)
            ignore_geographic: If True, remove ALL country code patterns (e.g., US, USA, US:, |FR|, FR -, [UK])
            ignore_misc: If True, remove ALL content within parentheses (e.g., (CX), (B), (PRIME), (Backup))
            remove_cinemax: If True, remove "Cinemax" prefix (useful when channel name contains "max")
            remove_country_prefix: If True, remove country code prefixes (e.g., CA:, UK , DE: ) from start of name

        Returns:
            Normalized name
        """
        if user_ignored_tags is None:
            user_ignored_tags = []

        # Store original for logging
        original_name = name

        name = _LEADING_BAR_TAG_RE.sub('', name)  # leading "┃CANAL+┃" bouquet tag

        # Map emoji-as-letters (⚽ = 'o' in "SP⚽RTS") and strip emoji decoration, before
        # the stylized-Unicode strip and ASCII regexes below — so "beIN SP⚽RTS" -> "beIN sports".
        name = _normalize_emoji(name)

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
        # bullets) up front so the ASCII tag regexes below see plain text. Runs
        # unconditionally: a token written in superscript/small-caps is decoration
        # regardless of tag_handling, and it would otherwise block matches
        # (e.g. a superscript-RAW suffix never matches channel "WeatherNation").
        name = _strip_stylized_tokens(name)

        # CRITICAL FIX (v25.019.0100): Apply quality patterns FIRST, before space normalization
        # This prevents space normalization from breaking quality tags like "4K" -> "4 K"
        # which would then fail to match quality patterns looking for "4K"
        # Bug: Streams with "4K" suffix were not matching because "4K" was split to "4 K"
        # by the space normalization step, then quality patterns couldn't find "4K" at end
        if ignore_quality:
            # Strip numeric resolution markers (3840P/2160P/1080P/720P/...) before the
            # digit/letter spacer below would split "3840P" into "3840 P".
            # Must run before QUALITY_PATTERNS so that removing " 4K " does not glue
            # "SPoRTS" to "3840P" and break the word-boundary anchor.
            for pattern in RESOLUTION_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Normalize spacing around numbers (AFTER quality patterns are removed)
        # This ensures "ITV1" and "ITV 1" are treated identically during matching
        # Pattern: Insert space before number if preceded by letter, and after number if followed by letter
        # Examples: "ITV1" -> "ITV 1", "BBC2" -> "BBC 2", "E4" -> "E 4"
        name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', name)  # Letter followed by digit
        name = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)  # Digit followed by letter

        # Normalize hyphens to spaces for better token matching
        # This ensures "UK-ITV" becomes "UK ITV" and matches properly
        # Common patterns: "UK-ITV 1", "US-CNN", etc.
        name = re.sub(r'-', ' ', name)

        # Replace dots between word chars with spaces (e.g. "JusticeCentral.TV"
        # → "JusticeCentral TV"). Keeps the dot-suffix variant equivalent to
        # the spaced form. Trailing-dot URLs like ".com" are unaffected.
        name = re.sub(r'(?<=\w)\.(?=\w)', ' ', name)

        # Number-word → digit so "BBC Three" matches "BBC 3" and
        # "Three Angels Broadcasting" matches "3 Angels Broadcasting Network".
        # Word boundaries protect brand names with embedded letters ("Onesimus").
        name = NUM_WORDS_RE.sub(lambda m: NUM_WORDS[m.group(0).lower()], name)

        # Split CamelCase: "JusticeCentral" → "Justice Central",
        # "DangerTV" → "Danger TV". The 4-char floor on the acronym rule
        # protects short brand names like "MeTV" / "truTV" whose existing
        # matches depend on the un-split form.
        name = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', name)
        name = re.sub(r'([a-z]{4,})([A-Z]{2,})\b', r'\1 \2', name)

        # Preserve parenthesized East/West (and the (E)/(W) abbreviations) as
        # bare words so they survive the leading-parenthetical strip below
        # AND the generic MISC_PATTERNS strip. Without this, a zoned lineup
        # entry like "Cartoon Network (W)" loses its zone and can't match
        # "US: Cartoon Network West". Only E/W are promoted — other single
        # letters (A/S/H/F/X/D) are stream-source/quality tags.
        name = re.sub(r'\(\s*(?:east|e)\s*\)', ' East ', name, flags=re.IGNORECASE)
        name = re.sub(r'\(\s*(?:west|w)\s*\)', ' West ', name, flags=re.IGNORECASE)

        # Remove ALL leading parenthetical prefixes like (US) (PRIME2), (SP2), (D1), etc.
        # Loop until no more leading parentheses are found
        while name.lstrip().startswith('('):
            new_name = re.sub(r'^\s*\([^\)]+\)\s*', '', name)
            if new_name == name:  # No change, break to avoid infinite loop
                break
            name = new_name

        # Remove IPTV provider prefixes (common in M3U streams)
        for pattern in PROVIDER_PREFIX_PATTERNS:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Remove country code prefix if requested (e.g., "CA:", "UK ", "USA: ")
        # This handles multi-country databases where streams may be prefixed with country codes
        if remove_country_prefix:
            # Known quality tags that should NOT be removed (to avoid false positives)
            quality_tags = {'HD', 'SD', 'FD', 'UHD', 'FHD'}

            # Check for 2-3 letter prefix with colon or space at start
            # Fixed regex: [:\s] instead of [:|\\s] (pipe and backslash were incorrect)
            prefix_match = re.match(r'^([A-Z]{2,3})[:\s]\s*', name)
            if prefix_match:
                prefix = prefix_match.group(1).upper()
                # Only remove if it's NOT a quality tag
                if prefix not in quality_tags:
                    name = name[len(prefix_match.group(0)):]

        # Remove "Cinemax" prefix if requested (for channels containing "max")
        if remove_cinemax:
            name = re.sub(r'\bCinemax\b\s*', '', name, flags=re.IGNORECASE)

        # Build list of patterns to apply based on category flags
        # NOTE: Quality patterns are now applied earlier (before space normalization)
        # to prevent "4K" from being split to "4 K" before removal
        patterns_to_apply = []

        if ignore_regional:
            patterns_to_apply.extend(REGIONAL_PATTERNS)

        if ignore_geographic:
            patterns_to_apply.extend(GEOGRAPHIC_PATTERNS)

        if ignore_misc:
            # CRITICAL FIX: Only apply MISC_PATTERNS (which removes ALL parentheses) if we're also
            # ignoring regional tags. Otherwise, MISC_PATTERNS would strip regional indicators like
            # "(WEST)" even when the user has set ignore_regional=False.
            # This ensures that "BBC America" won't match "BBC AMERICA (WEST)" when ignore_regional=False
            if ignore_regional:
                # Safe to remove ALL parentheses since regional indicators are already being ignored
                patterns_to_apply.extend(MISC_PATTERNS)
            else:
                # User wants to preserve regional indicators - skip MISC_PATTERNS to avoid
                # removing parenthetical content that might be regional indicators
                # Note: This means some misc tags like (CX), (B), (PRIME) won't be removed
                # when ignore_regional=False, but this is the correct behavior to preserve
                # regional tags like (WEST), (EAST), etc.
                pass

        # Apply selected hardcoded patterns
        for pattern in patterns_to_apply:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Apply user-configured ignored tags with improved handling
        for tag in user_ignored_tags:
            escaped_tag = re.escape(tag)

            # Check if tag contains brackets or parentheses - if so, match literally
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                # Literal match for bracketed/parenthesized tags, remove with trailing whitespace
                name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)
            else:
                # CRITICAL FIX: Word boundaries (\b) only work with alphanumeric characters
                # Tags with Unicode/special characters (like ┃NLZIET┃) fail with word boundaries
                # Check if tag contains only word characters (alphanumeric + underscore)
                if re.match(r'^\w+$', tag):
                    # Safe to use word boundaries for pure word tags
                    # This prevents "East" from matching the "east" in "Feast"
                    name = re.sub(r'\b' + escaped_tag + r'\b', '', name, flags=re.IGNORECASE)
                else:
                    # Tag contains special/Unicode characters - can't use word boundaries
                    # Match the tag followed by optional whitespace
                    name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)
        
        # Remove callsigns in parentheses
        # CRITICAL FIX: Don't remove regional indicators like (WEST), (EAST), etc. when ignore_regional=False
        # The callsign pattern \([KW][A-Z]{3}...\) accidentally matches (WEST), (WETA), (KOMO), etc.
        # We need to exclude known regional indicators even when matching callsigns
        if ignore_regional:
            # Safe to remove callsigns without checking for regional indicators
            name = re.sub(r'\([KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)
        else:
            # Only remove callsigns that are NOT regional indicators
            # Use negative lookahead to exclude WEST, EAST, etc.
            # Pattern matches (K or W) + 3 letters, but NOT if those 3 letters form a regional word
            name = re.sub(r'\([KW](?!EST\)|ACIFIC\)|ENTRAL\)|OUNTAIN\)|TLANTIC\))[A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)

        # Remove other tags in parentheses (but only if we're also ignoring regional tags)
        # Otherwise this would remove regional indicators like (WEST), (EAST), etc.
        if ignore_regional:
            name = re.sub(r'\([A-Z0-9]+\)', '', name)
        
        # Remove common pattern fixes
        name = re.sub(r'^The\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Network\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Channel\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+TV\s*$', '', name, flags=re.IGNORECASE)
        
        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()

        # Log debug message if normalization resulted in empty string (indicates overly aggressive stripping)
        if not name:
            self.logger.debug(f"normalize_name returned empty string for input: '{original_name}' (original input was stripped too aggressively)")

        return name
    
    def extract_tags(self, name, user_ignored_tags=None):
        """
        Extract regional indicators, extra tags, and quality tags to preserve them.
        
        Returns:
            Tuple of (regional, extra_tags, quality_tags)
        """
        if user_ignored_tags is None:
            user_ignored_tags = []
        
        regional = None
        extra_tags = []
        quality_tags = []
        
        # Extract regional indicator
        regional_pattern_paren = r'\((East|West|Pacific|Central|Mountain|Atlantic)\)'
        regional_match = re.search(regional_pattern_paren, name, re.IGNORECASE)
        if regional_match:
            regional = regional_match.group(1).capitalize()
        else:
            regional_pattern_word = r'\b(East|West|Pacific|Central|Mountain|Atlantic)\b(?!.*\b(East|West|Pacific|Central|Mountain|Atlantic)\b)'
            regional_match = re.search(regional_pattern_word, name, re.IGNORECASE)
            if regional_match:
                regional = regional_match.group(1).capitalize()
        
        # Extract ALL tags in parentheses
        paren_tags = re.findall(r'\(([^\)]+)\)', name)
        first_paren_is_prefix = name.strip().startswith('(') if paren_tags else False
        
        for idx, tag in enumerate(paren_tags):
            # Skip first tag if it is a prefix
            if idx == 0 and first_paren_is_prefix:
                continue
            
            # Check if tag should be ignored
            if f"({tag})" in user_ignored_tags or f"[{tag}]" in user_ignored_tags:
                continue
            
            tag_upper = tag.upper()
            
            # Skip regional indicators
            if tag_upper in ['EAST', 'WEST', 'PACIFIC', 'CENTRAL', 'MOUNTAIN', 'ATLANTIC']:
                continue
            
            # Skip callsigns
            if re.match(r'^[KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?$', tag_upper):
                continue
            
            extra_tags.append(f"({tag})")
        
        # Extract ALL quality/bracketed tags
        bracketed_tags = re.findall(r'\[([^\]]+)\]', name)
        for tag in bracketed_tags:
            # Check if tag should be ignored
            if f"[{tag}]" in user_ignored_tags or f"({tag})" in user_ignored_tags:
                continue
            quality_tags.append(f"[{tag}]")
        
        return regional, extra_tags, quality_tags
    
    

    @staticmethod
    def _has_token_overlap(str_a, str_b, min_token_len=4, require_majority=False):
        """Check that distinctive tokens are shared between two strings.

        Basic mode: at least one token (>= min_token_len) must be shared.
        Majority mode: uses all tokens (>= 2 chars), requires that more than
        half of the smaller set overlaps, and applies subset/divergent/numeric
        guards to reject sibling-channel false positives even when a fuzzy
        score is high. Sourced from Lineuparr; catches:
          - "ABC News" vs "BBC News"  (no shared distinctive token)
          - "Sky Cinema Disney" vs "Sky Cinema Decades"  (divergent unique tokens)
          - "In Country Television" vs "Country Music Television"  (subset)
          - "BBC One" vs "BBC Two"  (numeric divergence)
        "network"/"channel"/"television" are demoted to common — they're brand
        suffixes, not distinguishing tokens.
        """
        common_words = {
            "the", "and", "of", "in", "on", "at", "to", "for", "a", "an",
            "network", "channel", "television",
        }

        if require_majority:
            # Single-digit tokens (1,2,3,...) are channel-distinguishing
            # (BBC 1 vs BBC 2, ESPN 1 vs ESPN 2) even though only 1 char,
            # so keep them as meaningful.
            def _meaningful(t):
                if t in common_words:
                    return False
                return len(t) >= 2 or t.isdigit()
            tokens_a = {t for t in str_a.split() if _meaningful(t)}
            tokens_b = {t for t in str_b.split() if _meaningful(t)}
            if not tokens_a or not tokens_b:
                return True
            shared = tokens_a & tokens_b
            if not shared:
                return False
            smaller = min(len(tokens_a), len(tokens_b))
            if not len(shared) > smaller / 2:
                return False

            unique_a = tokens_a - tokens_b
            unique_b = tokens_b - tokens_a

            # Subset guard: when one side is a strict subset of the other AND
            # the larger side has a distinctive (>=5 char) token the smaller
            # lacks, the candidate is a more specific channel than the query.
            # Catches "In Country Television" {country} vs "Country Music
            # Television" {country, music} — "music" distinguishes them.
            # Short extras like "live"/"two" do not trigger this, preserving
            # legitimate matches like "ABC News" → "ABC News Live".
            if not unique_a:
                if any(len(t) >= 5 for t in unique_b):
                    return False
            elif not unique_b:
                if any(len(t) >= 5 for t in unique_a):
                    return False

            # Divergent guard: BOTH sides have unique tokens AND at least one
            # of those unique tokens is a distinctive (>=4 char) word — they
            # describe different brands. Catches "Sky Cinema Disney" vs
            # "Sky Cinema Decades" (decades = 7 chars).
            if unique_a and unique_b:
                if any(len(t) >= 4 for t in unique_a | unique_b):
                    return False

            # Numeric/ordinal divergent guard: BOTH sides have a unique
            # numeric/ordinal token — sibling channels distinguished by number
            # (BBC One vs BBC Two; ESPN 1 vs ESPN 2). Short tokens like
            # "one"/"two" wouldn't trip the >=4 guard so they need this check.
            _NUMERIC = {
                "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine", "ten", "eleven", "twelve",
                "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
                "first", "second", "third", "fourth", "fifth",
            }
            if (unique_a & _NUMERIC) and (unique_b & _NUMERIC):
                return False

            return True

        tokens_a = {t for t in str_a.split() if t not in common_words and len(t) >= min_token_len}
        tokens_b = {t for t in str_b.split() if t not in common_words and len(t) >= min_token_len}
        if not tokens_a or not tokens_b:
            return True
        return bool(tokens_a & tokens_b)


    
    def find_best_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False,
                        ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True):
        """
        Find the best fuzzy match for a name among a list of candidate names.

        Args:
            query_name: Name to match
            candidate_names: List of candidate names to match against
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names
            ignore_quality: If True, remove ALL quality indicators during normalization
            ignore_regional: If True, remove regional indicator patterns during normalization
            ignore_geographic: If True, remove ALL country code patterns during normalization
            ignore_misc: If True, remove ALL content within parentheses during normalization

        Returns:
            Tuple of (matched_name, score) or (None, 0) if no match found
        """
        if not candidate_names:
            return None, 0

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Normalize the query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                                ignore_quality=ignore_quality,
                                                ignore_regional=ignore_regional,
                                                ignore_geographic=ignore_geographic,
                                                ignore_misc=ignore_misc)
        
        if not normalized_query:
            return None, 0
        
        # Process query for token-sort matching
        processed_query = self.process_string_for_matching(normalized_query)
        normalized_query_lower = normalized_query.lower()
        query_trailing_num = self._trailing_number(normalized_query_lower)

        best_score = -1.0
        best_match = None

        # Guards inside the loop: a high-scoring guard-rejected candidate must
        # not suppress a lower-scoring valid one.
        for candidate in candidate_names:
            candidate_lower, _ = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue
            if query_trailing_num is not None:
                cand_trailing_num = self._trailing_number(candidate_lower)
                if cand_trailing_num is not None and cand_trailing_num != query_trailing_num:
                    continue
            processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
            if not processed_candidate:
                continue

            score = self.calculate_similarity(processed_query, processed_candidate,
                                                min_ratio=max(self.match_threshold / 100.0, best_score))
            if score <= best_score:
                continue
            pct = int(score * 100)
            shorter_len = min(len(processed_query), len(processed_candidate))
            effective_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
            if pct < effective_threshold:
                continue
            if not self._has_token_overlap(processed_query, processed_candidate, require_majority=True):
                continue
            best_score = score
            best_match = candidate

        if best_match is not None:
            return best_match, int(best_score * 100)
        return None, 0
    
    def fuzzy_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False,
                    ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True):
        """
        Generic fuzzy matching function that can match any name against a list of candidates.
        This is the main entry point for fuzzy matching.

        Args:
            query_name: Name to match (channel name)
            candidate_names: List of candidate names to match against (stream names)
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names (for channels with "max")
            ignore_quality: If True, remove ALL quality indicators during normalization
            ignore_regional: If True, remove regional indicator patterns during normalization
            ignore_geographic: If True, remove ALL country code patterns during normalization
            ignore_misc: If True, remove ALL content within parentheses during normalization

        Returns:
            Tuple of (matched_name, score, match_type) or (None, 0, None) if no match found
        """
        if not candidate_names:
            return None, 0, None

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Stage 0: Alias hit — O(1) lookup against curated channel-name variants.
        # Skips fuzzy entirely when a known alias matches.
        alias_hit, alias_score, alias_type = self.alias_match(query_name, candidate_names, user_ignored_tags)
        if alias_hit:
            return alias_hit, alias_score, alias_type

        # Normalize query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                                ignore_quality=ignore_quality,
                                                ignore_regional=ignore_regional,
                                                ignore_geographic=ignore_geographic,
                                                ignore_misc=ignore_misc)

        if not normalized_query:
            return None, 0, None

        best_match = None
        best_ratio = 0
        match_type = None

        # Stage 1: Exact match (after normalization)
        normalized_query_lower = normalized_query.lower()
        normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)
        query_trailing_num = self._trailing_number(normalized_query_lower)

        for candidate in candidate_names:
            candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            # Exact match
            if normalized_query_nospace == candidate_nospace:
                return candidate, 100, "exact"

            if query_trailing_num is not None:
                cand_trailing_num = self._trailing_number(candidate_lower)
                if cand_trailing_num is not None and cand_trailing_num != query_trailing_num:
                    continue

            # Very high similarity (97%+) — require majority token overlap so
            # near-identical but distinct brands ("ABC News" vs "BBC News") don't slip through.
            ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=0.97)
            if ratio >= 0.97 and ratio > best_ratio and self._has_token_overlap(
                    normalized_query_lower, candidate_lower, require_majority=True):
                best_match = candidate
                best_ratio = ratio
                match_type = "exact"

        if best_match:
            return best_match, int(best_ratio * 100), match_type

        # Stage 2: Substring matching. Guards live INSIDE the loop so that a
        # higher-scoring but guard-rejected candidate (e.g. "Cartoon Network UK"
        # for query "Cartoon Network West") doesn't suppress a lower-scoring
        # but valid one ("Cartoon Network").
        for candidate in candidate_names:
            candidate_lower, _ = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            if query_trailing_num is not None:
                cand_trailing_num = self._trailing_number(candidate_lower)
                if cand_trailing_num is not None and cand_trailing_num != query_trailing_num:
                    continue

            if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                length_ratio = min(len(normalized_query_lower), len(candidate_lower)) / max(len(normalized_query_lower), len(candidate_lower))
                if length_ratio >= 0.75:
                    ratio = self.calculate_similarity(normalized_query_lower, candidate_lower,
                                                        min_ratio=self.match_threshold / 100.0)
                    sub_score = int(ratio * 100)
                    shorter_len = min(len(normalized_query_lower), len(candidate_lower))
                    effective_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                    if (ratio > best_ratio and sub_score >= effective_threshold
                            and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True)):
                        best_match = candidate
                        best_ratio = ratio
                        match_type = "substring"

        if best_match and int(best_ratio * 100) >= self.match_threshold:
            return best_match, int(best_ratio * 100), match_type

        # Stage 3: Fuzzy matching with token sorting
        fuzzy_match, score = self.find_best_match(query_name, candidate_names, user_ignored_tags,
                                                   remove_cinemax=remove_cinemax,
                                                   ignore_quality=ignore_quality,
                                                   ignore_regional=ignore_regional,
                                                   ignore_geographic=ignore_geographic,
                                                   ignore_misc=ignore_misc)
        if fuzzy_match:
            return fuzzy_match, score, f"fuzzy ({score})"
        
        return None, 0, None

    def match_all_streams(self, query_name, candidate_names, user_ignored_tags=None,
                          remove_cinemax=False, ignore_quality=True, ignore_regional=True,
                          ignore_geographic=True, ignore_misc=True):
        """
        Full matching pipeline returning ALL matches above threshold, sorted by score desc.
        Useful for CSV preview exports showing top N alternatives per channel.

        Returns:
            List of (stream_name, score, match_type) tuples sorted by score descending.
        """
        if not candidate_names:
            return []

        if user_ignored_tags is None:
            user_ignored_tags = []

        all_matches = {}  # stream_name -> (score, match_type)

        # Stage 0: alias hits go into the result set with score=100/"alias"
        # so the CSV preview surfaces them above any fuzzy alternative.
        aliases = self.alias_map.get(query_name) or []
        if aliases:
            alias_hit, alias_score, alias_type = self.alias_match(query_name, candidate_names, user_ignored_tags)
            if alias_hit:
                all_matches[alias_hit] = (alias_score, alias_type)

        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                               ignore_quality=ignore_quality,
                                               ignore_regional=ignore_regional,
                                               ignore_geographic=ignore_geographic,
                                               ignore_misc=ignore_misc)
        if not normalized_query:
            return []

        normalized_query_lower = normalized_query.lower()
        normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)
        processed_query = self.process_string_for_matching(normalized_query)
        threshold_ratio = self.match_threshold / 100.0
        # A differing space-separated trailing number means a different channel
        # ("HBO 1" vs "HBO 2", "ESPN 1" vs "ESPN 2") -- skip these in the
        # per-candidate loop so they can't slip past as a fuzzy false positive.
        query_trailing_num = self._trailing_number(normalized_query_lower)

        for candidate in candidate_names:
            if candidate in all_matches:
                continue
            candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue
            if query_trailing_num is not None:
                cand_trailing_num = self._trailing_number(candidate_lower)
                if cand_trailing_num is not None and cand_trailing_num != query_trailing_num:
                    continue

            score = 0
            mtype = None

            # Exact
            if normalized_query_nospace == candidate_nospace:
                score = 100
                mtype = "exact"
            else:
                ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=0.97)
                if ratio >= 0.97 and self._has_token_overlap(
                        normalized_query_lower, candidate_lower, require_majority=True):
                    score = int(ratio * 100)
                    mtype = "exact"

            # Substring
            if not mtype:
                if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                    length_ratio = min(len(normalized_query_lower), len(candidate_lower)) / max(len(normalized_query_lower), len(candidate_lower))
                    if length_ratio >= 0.75:
                        ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=threshold_ratio)
                        sub_score = int(ratio * 100)
                        shorter_len = min(len(normalized_query_lower), len(candidate_lower))
                        sub_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                        if sub_score >= sub_threshold and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True):
                            score = sub_score
                            mtype = "substring"

            # Fuzzy token-sort — always require majority overlap (with subset/
            # divergent guards) so sibling channels don't match each other.
            if not mtype:
                processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
                if processed_candidate:
                    ratio = self.calculate_similarity(processed_query, processed_candidate, min_ratio=threshold_ratio)
                    fuzzy_score = int(ratio * 100)
                    shorter_len = min(len(processed_query), len(processed_candidate))
                    fuzzy_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                    if fuzzy_score >= fuzzy_threshold and self._has_token_overlap(processed_query, processed_candidate, require_majority=True):
                        score = fuzzy_score
                        mtype = f"fuzzy ({fuzzy_score})"

            if mtype and score > 0:
                all_matches[candidate] = (score, mtype)

        # Convert to sorted list
        results = [(name, score, mtype) for name, (score, mtype) in all_matches.items()]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def match_broadcast_channel(self, channel_name):
        """
        Match broadcast (OTA) channel by callsign.
        
        Args:
            channel_name: Channel name potentially containing a callsign
        
        Returns:
            Tuple of (callsign, station_data) or (None, None) if no match
        """
        callsign = self.extract_callsign(channel_name)
        
        if not callsign:
            return None, None
        
        # Try exact match first
        station = self.channel_lookup.get(callsign)
        
        if station:
            return callsign, station
        
        # Try base callsign (without suffix)
        base_callsign = self.normalize_callsign(callsign)
        station = self.channel_lookup.get(base_callsign)
        
        if station:
            return callsign, station
        
        return callsign, None
    
    def get_category_for_channel(self, channel_name, user_ignored_tags=None):
        """
        Get the category for a channel by matching it in the database.
        
        Args:
            channel_name: Channel name to look up
            user_ignored_tags: User-configured tags to ignore
        
        Returns:
            Category string or None if not found
        """
        if user_ignored_tags is None:
            user_ignored_tags = []
        
        # Try broadcast channel first
        callsign, station = self.match_broadcast_channel(channel_name)
        if station:
            return station.get('category')
        
        # Try premium channel matching
        if self.premium_channels:
            matched_name, score, match_type = self.fuzzy_match(
                channel_name, 
                self.premium_channels, 
                user_ignored_tags
            )
            
            if matched_name:
                # Find the full channel object
                for channel_obj in self.premium_channels_full:
                    if channel_obj.get('channel_name') == matched_name:
                        return channel_obj.get('category')
        
        return None
    
    def build_final_channel_name(self, base_name, regional, extra_tags, quality_tags):
        """
        Build final channel name with regional indicator, extra tags, and quality tags.
        Format: "Channel Name Regional (Extra) [Quality1] [Quality2] ..."
        """
        parts = [base_name]
        
        # Add regional indicator WITHOUT parentheses
        if regional:
            parts.append(regional)
        
        # Add extra tags (already have parentheses)
        if extra_tags:
            parts.extend(extra_tags)
        
        # Add quality tags (preserve original case and count)
        if quality_tags:
            parts.extend(quality_tags)
        
        return " ".join(parts)