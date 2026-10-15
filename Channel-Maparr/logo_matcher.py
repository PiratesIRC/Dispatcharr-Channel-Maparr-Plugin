"""
Logo matching utilities for matching channel names to tv-logo/tv-logos filenames.

Uses simple fuzzy matching (not the full FuzzyMatcher pipeline) since we're
comparing clean channel names against structured filenames.
"""

import re
import urllib.request
import json
import logging

try:
    from rapidfuzz import fuzz
except ImportError:
    try:
        from thefuzz import fuzz
    except ImportError:
        # Pure-Python fallback: Levenshtein ratio via difflib
        import difflib

        class _FuzzFallback:
            @staticmethod
            def ratio(a, b):
                return difflib.SequenceMatcher(None, a, b).ratio() * 100

        fuzz = _FuzzFallback()

LOGGER = logging.getLogger("plugins.channel_maparr.logo_matcher")

# Threshold for fuzzy matching channel names to logo filenames
LOGO_MATCH_THRESHOLD = 85

# Words a channel name or a tv-logos filename may end with that say nothing
# about which channel it is. Stripped from BOTH sides, repeatedly, so that
# "Food Network" and food-network-uk.png compare as "food" and "food". They
# used to be stripped from the channel name only, which left "food" against
# "food network" and lost the match.
_NOISE_SUFFIX = re.compile(r'\s+(network|channel|tv)$')

# Picture-quality tags. Stripped from channel names only: on a channel name
# they describe the feed, but in a filename they can be part of a different
# channel's name (bbc-uhd-uk.png is not the BBC logo, and stripping it there
# matched "CBBC" to it).
_QUALITY_SUFFIX = re.compile(r'\s+(hd|sd|uhd|4k|fhd)$')

# A name ending in "+1" (a timeshift channel: "ITV2 +1", "E4+1") or in a
# bare "+" ("Sky Sports+"). tv-logos names both with a "-plus" suffix
# (itv-2-plus-uk.png, sky-sports-plus-hz-uk.png). Without this the "+"
# was dropped and the "1" kept, so "ITV2 +1" became "itv2 1" and matched
# itv-1-uk.png, the logo of a different channel.
_TIMESHIFT = re.compile(r'\s*\+\s*1?\s*$')


def _strip_noise_suffixes(name, quality=False):
    while True:
        stripped = _NOISE_SUFFIX.sub('', name)
        if quality:
            stripped = _QUALITY_SUFFIX.sub('', stripped)
        if stripped == name:
            return name
        name = stripped


def normalize_logo_filename(filename, country_suffix):
    """Normalize a tv-logos filename for comparison.

    'cnn-us.png' with country_suffix='us' -> 'cnn'
    'fox-news-us.png' with country_suffix='us' -> 'fox news'
    """
    # Strip extension
    name = re.sub(r'\.(png|svg|jpg|jpeg|gif|webp)$', '', filename, flags=re.IGNORECASE)
    # Strip country suffix (e.g., '-us' at end)
    suffix_pattern = rf'-{re.escape(country_suffix)}$'
    name = re.sub(suffix_pattern, '', name, flags=re.IGNORECASE)
    # Replace hyphens with spaces
    name = name.replace('-', ' ')
    name = re.sub(r'\s+', ' ', name).lower().strip()
    # food-network-plus-uk.png must compare as "food plus", the form
    # match_channel_to_logo builds for "Food Network +1"
    if name.endswith(' plus'):
        return _strip_noise_suffixes(name[:-len(' plus')]) + ' plus'
    return _strip_noise_suffixes(name)


def normalize_channel_name(name):
    """Normalize a channel name for comparison against logo filenames.

    'A&E' -> 'a and e'
    'Fox News' -> 'fox news'
    'CNN HD' -> 'cnn'
    'Discovery Channel' -> 'discovery'
    """
    name = name.lower().strip()
    # Replace & with 'and'
    name = name.replace('&', ' and ')
    # Remove special characters except spaces
    name = re.sub(r'[^\w\s]', '', name)
    # Collapse whitespace, then strip the suffixes normalize_logo_filename
    # strips too, and the picture-quality tags it does not
    name = re.sub(r'\s+', ' ', name).strip()
    return _strip_noise_suffixes(name, quality=True)


def split_timeshift(channel_name):
    """Split "ITV2 +1 HD" into ("ITV2 HD", True); a name with no "+" tail
    comes back unchanged with False. A noise suffix after the "+1" is moved
    in front of it first, so "+1 HD" is recognised as well as "HD +1"."""
    name = channel_name.strip()
    tail = re.search(r'(\s+(hd|sd|uhd|4k|fhd))+$', name, flags=re.IGNORECASE)
    if tail:
        name = name[:tail.start()]
    match = _TIMESHIFT.search(name)
    if not match:
        return channel_name, False
    return name[:match.start()].strip(), True


def _score(normalized_channel, normalized_logo):
    """Similarity of two normalized names, 0 to 100.

    Also compared with every space removed, and the higher score kept:
    tv-logos hyphenates between letters and digits (e-4, film-4, itv-2,
    4-seven) where channel names do not (E4, Film4, ITV2, 4seven), and that
    one hyphen cost enough points to fail the Exact threshold.
    """
    spaced = fuzz.ratio(normalized_channel, normalized_logo)
    if spaced == 100:
        return spaced
    joined = fuzz.ratio(normalized_channel.replace(' ', ''),
                        normalized_logo.replace(' ', ''))
    return max(spaced, joined)


def match_channel_to_logo(channel_name, logo_filenames, country_suffix, threshold=LOGO_MATCH_THRESHOLD):
    """Match a channel name against a list of tv-logos filenames.

    Returns the matching filename or None if no match meets the threshold.

    A timeshift channel ("ITV2 +1") is matched first as "<name> plus", which
    is how tv-logos names those files, and falls back to the plain channel's
    logo when the repo has no "-plus" file for it.
    """
    base_name, is_timeshift = split_timeshift(channel_name)
    if is_timeshift:
        base = normalize_channel_name(base_name)
        if not base:
            return None
        found = _best_logo(f"{base} plus", logo_filenames, country_suffix, threshold)
        if found:
            return found
        return _best_logo(base, logo_filenames, country_suffix, threshold)
    return _best_logo(normalize_channel_name(channel_name), logo_filenames,
                      country_suffix, threshold)


def _best_logo(normalized_channel, logo_filenames, country_suffix, threshold):
    """The filename scoring highest against normalized_channel, or None when
    the best score is below threshold."""
    if not normalized_channel:
        return None

    best_score = 0
    best_file = None

    for filename in logo_filenames:
        normalized_logo = normalize_logo_filename(filename, country_suffix)
        if not normalized_logo:
            continue

        score = _score(normalized_channel, normalized_logo)
        if score > best_score:
            best_score = score
            best_file = filename
            if best_score == 100:
                break

    if best_score >= threshold:
        return best_file
    return None


# A broadcast station name in this plugin's OTA format, "{NETWORK} - {STATE}
# {CITY} ({CALLSIGN})": "CBS - AL Birmingham (WIAT)". A callsign may carry a
# suffix ("WBMA-LD"); only the four or three letters are kept.
_STATION_NAME = re.compile(
    r'^\s*([A-Za-z0-9]+)\s+-\s+.*\(([KW][A-Z]{2,3})(?:-[A-Z0-9]+)?\)\s*$')


def parse_station_name(channel_name):
    """("cbs", "wiat") for "CBS - AL Birmingham (WIAT)"; None for any name
    not in the broadcast station format."""
    match = _STATION_NAME.match(channel_name or '')
    if not match:
        return None
    return match.group(1).lower(), match.group(2).lower()


def match_station_logo(station, local_files):
    """The tv-logos us-local file for a (network, callsign) pair, or None.

    us-local files are named "<network>-<channel number>-<callsign>-us.png"
    (cbs-42-wiat-us.png), with variations ("abc-fox-kfbb-us.png",
    "cbs-2-kalb-dt2-us.png"). A file qualifies when one of its hyphen-separated
    words is the callsign AND one is the channel's network: a station that
    changed affiliation keeps its callsign, and a file showing its old
    network is the wrong logo (KALB has cbs-2-kalb-dt2 for its CBS
    subchannel and nbc-5-kalb for NBC). Among qualifying files, one without a
    "dt" subchannel word, then one without a style word (white, hz, light, icon),
    then the shortest name wins, so the choice does not depend on the order
    GitHub lists files in.
    """
    if not station:
        return None
    network, callsign = station
    candidates = []
    for filename in local_files:
        words = re.sub(r'\.[a-z]+$', '', filename.lower()).split('-')
        if callsign in words and network in words:
            candidates.append((filename, words))
    if not candidates:
        return None

    def rank(item):
        filename, words = item
        subchannel = any(re.fullmatch(r'dt\d*', w) for w in words)
        styled = any(w in ('white', 'hz', 'light', 'dark', 'icon') for w in words)
        return (subchannel, styled, len(filename), filename)

    return min(candidates, key=rank)[0]


def network_logo(network, logo_filenames, country_suffix):
    """The tv-logos file for a network name ("cbs"), or None. Tries
    "<network>-<suffix>.png" and then the white variant, which is the only
    CBS network logo the repo has (cbs-logo-white-us.png)."""
    available = set(logo_filenames)
    for candidate in (f"{network}-{country_suffix}.png",
                      f"{network}-logo-white-{country_suffix}.png"):
        if candidate in available:
            return candidate
    return None


_IMAGE_EXTS = ('.png', '.svg', '.jpg', '.jpeg', '.gif', '.webp')


def fetch_tv_logos_filelist(repo, branch, country_dir):
    """Fetch logo filenames from the tv-logos GitHub repo.

    Uses the Git Trees API with recursive=1 so directories with more than
    1000 files (united-states, for example) return complete results — the
    Contents API silently caps at 1000.
    """
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            LOGGER.warning(
                f"GitHub rate limit hit fetching tv-logos for {country_dir}. "
                f"Anonymous API is 60 req/hr/IP — try again later."
            )
        else:
            LOGGER.warning(f"GitHub HTTP {e.code} fetching tv-logos for {country_dir}: {e.reason}")
        return []
    except Exception as e:
        LOGGER.warning(f"Failed to fetch tv-logos file list for {country_dir}: {e}")
        return []

    if data.get("truncated"):
        LOGGER.warning(f"GitHub tree response was truncated for {repo}@{branch}; some logos missing.")

    prefix = f"countries/{country_dir}/"
    files = []
    for entry in data.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):]
        if "/" in name:  # skip nested subdirectories
            continue
        if name.lower().endswith(_IMAGE_EXTS):
            files.append(name)
    return files


def build_logo_url(repo, branch, country_dir, filename):
    """Build a raw GitHub URL for a logo file."""
    return f"https://raw.githubusercontent.com/{repo}/{branch}/countries/{country_dir}/{filename}"
