"""
Channel Mapparr Plugin
Standardizes US broadcast (OTA) and premium/cable channel names.
"""

import logging
import csv
import os
import re
import requests
from datetime import datetime
from difflib import SequenceMatcher

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

class Plugin:
    """Channel Mapparr Plugin"""
    
    name = "Channel Mapparr"
    version = "0.1"
    description = "Standardizes US broadcast (OTA) and premium/cable channel names using network data and channel lists."
    
    # Settings rendered by UI
    fields = [
        {
            "id": "dispatcharr_url",
            "label": "Dispatcharr URL",
            "type": "string",
            "default": "",
            "placeholder": "http://192.168.1.10:9191",
            "help_text": "URL of your Dispatcharr instance (from your browser's address bar). Example: http://127.0.0.1:9191",
        },
        {
            "id": "dispatcharr_username",
            "label": "Dispatcharr Admin Username",
            "type": "string",
            "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "dispatcharr_password",
            "label": "Dispatcharr Admin Password",
            "type": "string",
            "input_type": "password",
            "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "selected_groups",
            "label": "Channel Groups (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "Locals, News, Entertainment",
            "help_text": "Apply actions only to specific channel groups. Leave empty to apply to all groups.",
        },
        {
            "id": "ota_format",
            "label": "OTA Channel Name Format",
            "type": "string",
            "default": "{NETWORK} - {STATE} {CITY} ({CALLSIGN})",
            "placeholder": "{NETWORK} - {STATE} {CITY} ({CALLSIGN})",
            "help_text": "Format for OTA channel names. Available tags: {NETWORK}, {STATE}, {CITY}, {CALLSIGN}. Channels missing required fields will be skipped.",
        },
        {
            "id": "unknown_suffix",
            "label": "Suffix for Unknown Channels",
            "type": "string",
            "default": " [Unk]",
            "placeholder": " [Unk]",
            "help_text": "Suffix to append to channels that cannot be matched (OTA and premium/cable). Leave empty for no suffix.",
        },
        {
            "id": "default_logo",
            "label": "Default Logo",
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
    ]
    
    def __init__(self):
        self.loaded_channels = []
        self.processing_status = {"current": 0, "total": 0, "status": "idle", "start_time": None}
        self.results_file = "/data/channel_mapparr_loaded_channels.json"
        self.networks_file = os.path.join(os.path.dirname(__file__), "networks.json")
        self.channels_file = os.path.join(os.path.dirname(__file__), "channels.txt")
        self.network_lookup = {}
        self.premium_channels = []
        self.group_name_map = {}
        LOGGER.info(f"{self.name} Plugin v{self.version} initialized")

    def _get_api_token(self, settings, logger):
        """Get an API access token using username and password."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        username = settings.get("dispatcharr_username", "")
        password = settings.get("dispatcharr_password", "")

        if not all([dispatcharr_url, username, password]):
            return None, "Dispatcharr URL, Username, and Password must be configured in the plugin settings."

        try:
            url = f"{dispatcharr_url}/api/accounts/token/"
            payload = {"username": username, "password": password}
            
            logger.info(f"Attempting to authenticate with Dispatcharr at: {url}")
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access")

            if not access_token:
                logger.error("No access token returned from API")
                return None, "Login successful, but no access token was returned by the API."
            
            logger.info("Successfully obtained API access token")
            return access_token, None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error during authentication: {e}")
            return None, f"Network error occurred while authenticating: {e}"
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return None, f"Unexpected error during authentication: {e}"

    def _get_api_data(self, endpoint, token, settings, logger, paginated=False):
        """Helper to perform GET requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        
        try:
            all_results = []
            
            while url:
                response = requests.get(url, headers=headers, timeout=30)
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
            logger.error(f"API request failed for {endpoint}: {e}")
            raise Exception(f"API request failed: {e}")

    def _patch_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform PATCH requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        try:
            response = requests.patch(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API PATCH request failed for {endpoint}: {e}")
            raise Exception(f"API PATCH request failed: {e}")

    def _post_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform POST requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API POST request failed for {endpoint}: {e}")
            raise Exception(f"API POST request failed: {e}")

    def _trigger_m3u_refresh(self, token, settings, logger):
        """Triggers a global M3U refresh to update the GUI via WebSockets."""
        logger.info("Triggering M3U refresh to update the GUI...")
        try:
            self._post_api_data("/api/m3u/refresh/", token, {}, settings, logger)
            logger.info("M3U refresh triggered successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to trigger M3U refresh: {e}")
            return False

    def _load_network_data(self, logger):
        """Load network/station data from bundled networks.json file."""
        if os.path.exists(self.networks_file):
            import json
            try:
                with open(self.networks_file, 'r') as f:
                    stations_list = json.load(f)
                
                # Create a lookup dictionary by callsign
                self.network_lookup = {}
                for station in stations_list:
                    callsign = station.get('callsign', '').strip()
                    if callsign:
                        # Store with original callsign as key
                        self.network_lookup[callsign] = station
                        
                        # Also store without suffix (-TV, -CD, -LP, -DT, -LD) for easier matching
                        base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                        if base_callsign != callsign:
                            self.network_lookup[base_callsign] = station
                
                logger.info(f"Loaded {len(stations_list)} stations from networks.json")
                return True
            except Exception as e:
                logger.error(f"Error loading networks.json: {e}")
                return False
        else:
            logger.warning(f"networks.json not found at {self.networks_file}")
            return False

    def _load_premium_channels(self, logger):
        """Load premium/cable channel names from channels.txt."""
        if os.path.exists(self.channels_file):
            try:
                with open(self.channels_file, 'r', encoding='utf-8') as f:
                    self.premium_channels = [line.strip() for line in f if line.strip()]
                
                logger.info(f"Loaded {len(self.premium_channels)} premium/cable channels from channels.txt")
                return True
            except Exception as e:
                logger.error(f"Error loading channels.txt: {e}")
                return False
        else:
            logger.warning(f"channels.txt not found at {self.channels_file}")
            return False

    def _extract_callsign(self, channel_name):
        """
        Extract US TV callsign from channel name with priority order.
        Returns None if EAST/WEST appears alone (not a valid callsign).
        """
        # Remove D<number>- prefix if present
        channel_name = re.sub(r'^D\d+-', '', channel_name)
        
        # Priority 1: Callsigns in parentheses
        paren_match = re.search(r'\(([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\)', channel_name, re.IGNORECASE)
        if paren_match:
            return paren_match.group(1).upper()
        
        # Priority 2: Callsigns at the end (possibly with suffix or file extension)
        end_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\s*(?:\.[a-z]+)?\s*$', channel_name, re.IGNORECASE)
        if end_match:
            callsign = end_match.group(1).upper()
            # Reject if it's just "WEST" or "EAST"
            if callsign not in ['WEST', 'EAST']:
                return callsign
        
        # Priority 3: Any word that matches callsign pattern (but not WEST/EAST alone)
        word_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\b', channel_name, re.IGNORECASE)
        if word_match:
            callsign = word_match.group(1).upper()
            if callsign not in ['WEST', 'EAST']:
                return callsign
        
        return None
    
    def _normalize_callsign(self, callsign):
        """Remove suffixes like -TV, -CD, -LP from callsign for display purposes."""
        if callsign:
            callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
        return callsign
    
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

    def _similarity_ratio(self, str1, str2):
        """Calculate similarity ratio between two strings."""
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()

    def _normalize_channel_name_for_matching(self, name):
        """
        Normalize channel name for matching.
        Removes: quality tags, common suffixes
        Preserves: core channel name and subchannel identifiers
        """
        # Remove quality indicators
        name = re.sub(r'\[(HD|FHD|SD|4K|Slow)\]', '', name, flags=re.IGNORECASE)
        
        # Remove common country suffixes ONLY if not "USA Network"
        if not re.search(r'\bUSA\s+Network\b', name, re.IGNORECASE):
            name = re.sub(r'\bUSA\b', '', name, flags=re.IGNORECASE)
        
        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()
        
        return name

    def _extract_regional_and_quality_tags(self, name):
        """Extract regional indicators, extra tags, and quality tags to preserve them."""
        regional = None
        extra_tags = []
        quality_tags = []
        
        # Extract regional indicator ONLY if it appears in specific contexts
        regional_pattern = r'\b(East|West)\b(?!\s*\[)|[\(\s](East|West)[\)\s]'
        regional_match = re.search(regional_pattern, name, re.IGNORECASE)
        if regional_match:
            regional_text = regional_match.group(1) or regional_match.group(2)
            # Only treat as regional if not part of a callsign pattern
            if not re.search(r'\b[A-Z]{4}\s+(East|West)\b', name):
                regional = regional_text.capitalize()
        
        # Extract other tags in parentheses (like CX, etc.) - but not East/West
        other_tags = re.findall(r'\(([A-Z0-9]+)\)', name)
        for tag in other_tags:
            if tag.upper() not in ['EAST', 'WEST']:
                extra_tags.append(f"({tag})")
        
        # Extract ALL quality/bracketed tags (preserve case and collect all)
        bracketed_tags = re.findall(r'\[([^\]]+)\]', name)
        for tag in bracketed_tags:
            quality_tags.append(f"[{tag}]")
        
        return regional, extra_tags, quality_tags

    def _fuzzy_match_premium_channel(self, channel_name):
        """
        Match premium/cable channel name against channels.txt.
        Returns (matched_name, regional, extra_tags, quality_tags, match_type) or (None, None, None, None, None)
        """
        # Extract tags to preserve
        regional, extra_tags, quality_tags = self._extract_regional_and_quality_tags(channel_name)
        
        # Normalize for matching
        normalized = self._normalize_channel_name_for_matching(channel_name)
        
        if not normalized:
            return None, None, None, None, None
        
        # Remove regional indicators for matching
        normalized_for_match = re.sub(r'\b(East|West)\b', '', normalized, flags=re.IGNORECASE)
        # Remove extra tags for matching
        normalized_for_match = re.sub(r'\([A-Z0-9]+\)', '', normalized_for_match)
        normalized_for_match = re.sub(r'\s+', ' ', normalized_for_match).strip()
        
        best_match = None
        best_ratio = 0
        match_type = None
        
        # Create versions with and without spaces for comparison
        normalized_lower = normalized_for_match.lower()
        normalized_nospace = re.sub(r'[\s&\-]+', '', normalized_lower)
        
        # Stage 1: Exact/near-exact match
        for premium_channel in self.premium_channels:
            premium_normalized = self._normalize_channel_name_for_matching(premium_channel)
            premium_normalized = re.sub(r'\b(East|West)\b', '', premium_normalized, flags=re.IGNORECASE)
            premium_normalized = re.sub(r'\s+', ' ', premium_normalized).strip()
            premium_lower = premium_normalized.lower()
            premium_nospace = re.sub(r'[\s&\-]+', '', premium_lower)
            
            # Exact match (with or without spaces/separators)
            if normalized_nospace == premium_nospace:
                return premium_channel, regional, extra_tags, quality_tags, "exact"
            
            # Very high similarity (97%+) - prevents partial matches
            ratio = self._similarity_ratio(normalized_lower, premium_lower)
            if ratio >= 0.97 and ratio > best_ratio:
                best_match = premium_channel
                best_ratio = ratio
                match_type = "exact"
        
        if best_match:
            return best_match, regional, extra_tags, quality_tags, match_type
        
        # Stage 2: Check for number variations (HBO 2, HBO2, HBO 2 HD -> HBO2)
        number_pattern = re.match(r'^(.+?)\s*(\d+)\s*(.*)$', normalized_for_match)
        if number_pattern:
            base_channel = number_pattern.group(1).strip()
            number = number_pattern.group(2)
            suffix = number_pattern.group(3).strip()
            
            # Try matching "BaseChannel" + "Number" (e.g., HBO2)
            combined = f"{base_channel}{number}".lower()
            combined_nospace = re.sub(r'[\s&\-]+', '', combined)
            
            for premium_channel in self.premium_channels:
                premium_normalized = self._normalize_channel_name_for_matching(premium_channel)
                premium_lower = premium_normalized.lower()
                premium_nospace = re.sub(r'[\s&\-]+', '', premium_lower)
                
                if combined_nospace == premium_nospace:
                    return premium_channel, regional, extra_tags, quality_tags, "exact"
        
        return None, None, None, None, None

    def _build_final_channel_name(self, base_name, regional, extra_tags, quality_tags):
        """
        Build final channel name with regional indicator, extra tags, and quality tags.
        Format: "Channel Name (Extra) (Regional) [Quality1] [Quality2] ..."
        """
        parts = [base_name]
        
        # Add extra tags first
        if extra_tags:
            parts.extend(extra_tags)
        
        # Add regional indicator
        if regional:
            parts.append(f"({regional})")
        
        # Add ALL quality tags (preserve original case and count)
        if quality_tags:
            parts.extend(quality_tags)
        
        return " ".join(parts)

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
        display_callsign = self._normalize_callsign(callsign)
        
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
            }
            
            if action not in action_map:
                return {"status": "error", "message": f"Unknown action: {action}"}
            
            return action_map[action](settings, logger)
                
        except Exception as e:
            self.processing_status['status'] = 'idle'
            LOGGER.error(f"Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def load_and_process_channels_action(self, settings, logger):
        """Load channels from API and process them with network data and premium channel list."""
        try:
            import json
            
            # Load both data sources
            networks_loaded = self._load_network_data(logger)
            premium_loaded = self._load_premium_channels(logger)
            
            if not networks_loaded and not premium_loaded:
                return {"status": "error", "message": "Neither networks.json nor channels.txt could be loaded. Please ensure at least one file is in the same directory as plugin.py"}
            
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}

            logger.info("Loading channels from Dispatcharr API...")
            
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
                
                logger.info(f"Target group IDs: {target_group_ids}")
            else:
                target_group_ids = set(group_name_to_id.values())
                valid_names = set(group_name_to_id.keys())
            
            # Fetch all channels and filter by group ID
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            channels_to_process = [
                ch for ch in all_channels 
                if ch.get('channel_group_id') in target_group_ids
            ]
            logger.info(f"Filtered to {len(channels_to_process)} channels in groups: {selected_groups_str if selected_groups_str else 'all groups'}")
            
            # Store channels with proper group names
            for channel in channels_to_process:
                group_id = channel.get('channel_group_id')
                channel['_group_name'] = group_id_to_name.get(group_id, 'No Group')
            
            self.loaded_channels = channels_to_process
            
            # Process channels
            logger.info(f"Processing {len(self.loaded_channels)} channels...")
            self.processing_status = {
                "current": 0,
                "total": len(self.loaded_channels),
                "status": "running",
                "start_time": datetime.now().isoformat()
            }
            
            renamed_channels = []
            skipped_channels = []
            ota_format = settings.get("ota_format", "{NETWORK} - {STATE} {CITY} ({CALLSIGN})")
            
            for i, channel in enumerate(self.loaded_channels):
                self.processing_status["current"] = i + 1
                
                current_name = channel.get('name', '').strip()
                channel_id = channel.get('id')
                channel_number = channel.get('channel_number', '')
                group_id = channel.get('channel_group_id')
                group_name = channel.get('_group_name', 'No Group')
                
                new_name = None
                matcher_used = None
                skip_reason = None
                
                # Try OTA matching first (networks.json)
                if networks_loaded:
                    callsign = self._extract_callsign(current_name)
                    
                    if callsign:
                        station = self.network_lookup.get(callsign)
                        
                        if station:
                            new_name = self._format_ota_name(station, ota_format, callsign)
                            if new_name:
                                matcher_used = "networks.json"
                            else:
                                skip_reason = "Missing required fields for OTA format"
                        else:
                            skip_reason = f"Callsign {callsign} not in networks.json"
                
                # If OTA match failed, try premium/cable matching (channels.txt)
                if not new_name and premium_loaded:
                    matched_premium, regional, extra_tags, quality_tags, match_type = self._fuzzy_match_premium_channel(current_name)
                    
                    if matched_premium:
                        new_name = self._build_final_channel_name(matched_premium, regional, extra_tags, quality_tags)
                        matcher_used = "channels.txt"
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
                        'reason': ''
                    })
                else:
                    if new_name == current_name:
                        skip_reason = "Already in correct format"
                    elif not skip_reason:
                        skip_reason = "No match found in networks.json or channels.txt"
                    
                    skipped_channels.append({
                        'channel_id': channel_id,
                        'channel_number': channel_number,
                        'channel_group': group_name,
                        'current_name': current_name,
                        'new_name': current_name,
                        'status': 'Skipped',
                        'matcher': 'none',
                        'reason': skip_reason
                    })
            
            self.processing_status['status'] = 'complete'
            
            # Combine results
            all_results = renamed_channels + skipped_channels
            
            # Save processed results
            with open(self.results_file, 'w') as f:
                json.dump({
                    "processed_at": datetime.now().isoformat(),
                    "total_channels_loaded": len(self.loaded_channels),
                    "channels_to_rename": len(renamed_channels),
                    "channels_skipped": len(skipped_channels),
                    "group_map": group_id_to_name,
                    "channels": self.loaded_channels,
                    "changes": all_results
                }, f, indent=2)
            
            # Count matches by source
            ota_matches = sum(1 for c in renamed_channels if c.get('matcher') == 'networks.json')
            premium_matches = sum(1 for c in renamed_channels if c.get('matcher') == 'channels.txt')
            
            message_parts = [
                f"✓ Processing complete",
                f"\n**Summary:**",
                f"• Total channels loaded: {len(self.loaded_channels)}",
                f"• Channels to be renamed: {len(renamed_channels)}",
                f"  - OTA matches (networks.json): {ota_matches}",
                f"  - Premium/cable matches (channels.txt): {premium_matches}",
                f"• Channels skipped: {len(skipped_channels)}"
            ]
            
            if renamed_channels:
                message_parts.append(f"\n**Sample Changes:**")
                for change in renamed_channels[:5]:
                    message_parts.append(f"• '{change['current_name']}' → '{change['new_name']}' ({change['matcher']})")
                
                if len(renamed_channels) > 5:
                    message_parts.append(f"...and {len(renamed_channels) - 5} more.")
            
            message_parts.append("\nUse 'Preview Changes' to export full list or 'Rename Channels' to apply changes.")
            
            return {"status": "success", "message": "\n".join(message_parts)}
            
        except Exception as e:
            self.processing_status['status'] = 'idle'
            logger.error(f"Error in load and process: {e}")
            return {"status": "error", "message": f"Error in load and process: {e}"}

    def preview_changes_action(self, settings, logger):
        """Export CSV showing which channels would be renamed."""
        try:
            import json
            
            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}
            
            with open(self.results_file, 'r') as f:
                data = json.load(f)
            
            all_changes = data.get('changes', [])
            
            if not all_changes:
                return {"status": "success", "message": "No changes to preview."}
            
            filename = f"channel_mapparr_preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = os.path.join("/data/exports", filename)
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['channel_id', 'channel_number', 'channel_group', 'current_name', 'new_name', 'status', 'dbase', 'reason']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                # Remap 'matcher' to 'dbase' for CSV output
                for change in all_changes:
                    csv_row = {
                        'channel_id': change.get('channel_id'),
                        'channel_number': change.get('channel_number'),
                        'channel_group': change.get('channel_group'),
                        'current_name': change.get('current_name'),
                        'new_name': change.get('new_name'),
                        'status': change.get('status'),
                        'dbase': change.get('matcher'),  # Remap matcher -> dbase
                        'reason': change.get('reason')
                    }
                    writer.writerow(csv_row)
            
            renamed_count = sum(1 for c in all_changes if c.get('status') == 'Renamed')
            skipped_count = sum(1 for c in all_changes if c.get('status') == 'Skipped')
            ota_count = sum(1 for c in all_changes if c.get('matcher') == 'networks.json')
            premium_count = sum(1 for c in all_changes if c.get('matcher') == 'channels.txt')
            
            message = f"✓ Preview exported to {filepath}\n\n**Summary:**\n• {renamed_count} channels will be renamed\n  - OTA: {ota_count}\n  - Premium/cable: {premium_count}\n• {skipped_count} channels will be skipped"
            return {"status": "success", "message": message}
            
        except Exception as e:
            logger.error(f"Error generating preview: {str(e)}")
            return {"status": "error", "message": f"Error generating preview: {str(e)}"}

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
            renamed_channels = [c for c in all_changes if c.get('status') == 'Renamed']
            
            if not renamed_channels:
                return {"status": "success", "message": "No channels to rename."}
            
            payload = [{'id': ch['channel_id'], 'name': ch['new_name']} for ch in renamed_channels]
            
            logger.info(f"Renaming {len(payload)} channels...")
            self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            self._trigger_m3u_refresh(token, settings, logger)
            
            # Create CSV file of renamed channels
            filename = f"channel_mapparr_renamed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            filepath = os.path.join("/data/exports", filename)
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['channel_id', 'channel_number', 'channel_group', 'current_name', 'new_name', 'status', 'dbase', 'reason']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                # Remap 'matcher' to 'dbase' for CSV output
                for change in renamed_channels:
                    csv_row = {
                        'channel_id': change.get('channel_id'),
                        'channel_number': change.get('channel_number'),
                        'channel_group': change.get('channel_group'),
                        'current_name': change.get('current_name'),
                        'new_name': change.get('new_name'),
                        'status': change.get('status'),
                        'dbase': change.get('matcher'),
                        'reason': change.get('reason')
                    }
                    writer.writerow(csv_row)
            
            ota_count = sum(1 for c in renamed_channels if c.get('matcher') == 'networks.json')
            premium_count = sum(1 for c in renamed_channels if c.get('matcher') == 'channels.txt')
            
            message_parts = [
                f"✓ Successfully renamed {len(payload)} channels.",
                f"  - OTA: {ota_count}",
                f"  - Premium/cable: {premium_count}",
                f"\n✓ Rename report exported to: {filepath}"
            ]
            
            if renamed_channels:
                message_parts.append("\n**Sample Changes:**")
                for change in renamed_channels[:5]:
                    message_parts.append(f"• '{change['current_name']}' → '{change['new_name']}'")
                if len(renamed_channels) > 5:
                    message_parts.append(f"...and {len(renamed_channels) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"Error renaming channels: {e}")
            return {"status": "error", "message": f"Error renaming channels: {e}"}

    def rename_unknown_channels_action(self, settings, logger):
        """Append suffix to channels that could not be matched (OTA and premium/cable)."""
        try:
            import json
            
            if not os.path.exists(self.results_file):
                return {"status": "error", "message": "No processed channels found. Please run 'Load/Process Channels' first."}
            
            # Get suffix with default fallback matching the field default
            suffix = settings.get("unknown_suffix", " [Unk]")
            
            # Log what we received
            logger.info(f"Suffix setting value: '{suffix}' (length: {len(suffix)})")
            
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
            
            logger.info(f"Adding suffix '{suffix}' to {len(payload)} unknown channels...")
            self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            self._trigger_m3u_refresh(token, settings, logger)
            
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
            logger.error(f"Error renaming unknown channels: {e}")
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
            logger.info("Fetching all logos from API (with pagination)...")
            all_logos = self._get_api_data("/api/channels/logos/", token, settings, logger, paginated=True)
            
            logger.info(f"Fetched {len(all_logos)} total logos from API")
            
            # Find the logo entry matching the display name
            logo_id = None
            for logo in all_logos:
                logo_name = logo.get('name', '')
                
                # Case-insensitive exact match
                if logo_name.lower() == default_logo.lower():
                    logo_id = logo.get('id')
                    logger.info(f"Found logo: '{logo_name}' (ID: {logo_id})")
                    break
            
            if not logo_id:
                logger.error(f"Could not find logo '{default_logo}' in logo manager")
                logger.info(f"Searched through {len(all_logos)} logos")
                logger.info("Available logo names (first 30):")
                for logo in all_logos[:30]:
                    logger.info(f"  - '{logo.get('name', '')}'")
                
                return {
                    "status": "error", 
                    "message": f"Logo '{default_logo}' not found in logo manager.\n\nSearched through {len(all_logos)} logos. Check the Dispatcharr logs to see available logo names."
                }
            
            # Fetch FRESH channel data from API (not from cache)
            logger.info("Fetching current channel data from API...")
            
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
            
            logger.info(f"Found {len(channels_without_logos)} channels without logos (or with Default logo)")
            
            if not channels_without_logos:
                return {"status": "success", "message": "All channels already have logos assigned."}
            
            # Create payload with logo_id field (not logo)
            payload = [{'id': ch['id'], 'logo_id': int(logo_id)} for ch in channels_without_logos]
            
            logger.info(f"Applying logo ID {logo_id} to {len(payload)} channels...")
            logger.info(f"Sample payload: {payload[0] if payload else 'N/A'}")
            
            result = self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
            logger.info(f"Bulk update response: {result}")
            
            self._trigger_m3u_refresh(token, settings, logger)
            
            message_parts = [f"✓ Successfully applied logo '{default_logo}' (ID: {logo_id}) to {len(payload)} channels."]
            
            if channels_without_logos:
                message_parts.append("\n**Sample Channels:**")
                for ch in channels_without_logos[:5]:
                    message_parts.append(f"• {ch.get('name', 'Unknown')}")
                if len(channels_without_logos) > 5:
                    message_parts.append(f"...and {len(channels_without_logos) - 5} more.")
            
            return {"status": "success", "message": "\n".join(message_parts)}

        except Exception as e:
            logger.error(f"Error applying logos: {e}")
            return {"status": "error", "message": f"Error applying logos: {e}"}

# Export fields and actions for Dispatcharr plugin system
fields = Plugin.fields
actions = Plugin.actions

# Additional exports for Dispatcharr plugin system compatibility
plugin = Plugin()
plugin_instance = Plugin()

# Alternative export names in case Dispatcharr looks for these
channel_mapparr = Plugin()
CHANNEL_MAPPARR = Plugin()