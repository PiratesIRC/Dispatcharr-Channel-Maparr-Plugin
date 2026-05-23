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
    return name.lower().strip()


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
    # Strip common suffixes that tv-logos filenames don't include
    name = re.sub(r'\s+(hd|sd|uhd|4k|fhd|network|channel|tv)\s*$', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def match_channel_to_logo(channel_name, logo_filenames, country_suffix, threshold=LOGO_MATCH_THRESHOLD):
    """Match a channel name against a list of tv-logos filenames.

    Returns the matching filename or None if no match meets the threshold.
    """
    normalized_channel = normalize_channel_name(channel_name)
    if not normalized_channel:
        return None

    best_score = 0
    best_file = None

    for filename in logo_filenames:
        normalized_logo = normalize_logo_filename(filename, country_suffix)
        if not normalized_logo:
            continue

        score = fuzz.ratio(normalized_channel, normalized_logo)
        if score > best_score:
            best_score = score
            best_file = filename
            if best_score == 100:
                break

    if best_score >= threshold:
        return best_file
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
