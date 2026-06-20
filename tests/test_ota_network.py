"""OTA network resolution: stream-stated network + affiliation parsing.

Two fixes for wrong/malformed network labels seen in the US: CBS group:

1. **Honor the stream's stated network.** A stream names the network it carries
   ("US: CBS 7 (WBBJ-DT3) JACKSON HD"). Callsign extraction strips the subchannel
   suffix to the *main* station (WBBJ = ABC), so the FCC primary affiliation
   disagrees with the stream. ``_extract_stream_network`` reads the leading token
   and ``_format_ota_name`` prefers it, so the rename stays ``CBS - …``.

2. **Harden ``_parse_network_affiliation``** for the messy FCC strings that have
   no stream override to fall back on: subchannel maps ("CBS Ch 3.1, CW/MTN Ch
   3.2"), multi-net joins ("CBS & FOX", "CBS. FOX, CW"), callsign-prefixed
   ("KALB/NBC"), and annotated ("ABC (main) CBS (multicast)").
"""
import pytest


@pytest.fixture(scope="session")
def plugin(plugin_module):
    """A bare Plugin instance — pure helper methods only, no __init__/ORM."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --- Fix 2: network affiliation parsing -----------------------------------
# (raw FCC affiliation string, expected primary network)
PARSE_CASES = [
    ("CBS Ch 3.1, CW/MTN Ch 3.2, Grit CH 3.3, DEFY Ch 3.4, Newsy", "CBS"),
    ("CBS Ch 2.1, CW/MTN Ch 2.2", "CBS"),
    ("CBS & FOX", "CBS"),
    ("CBS FOX", "CBS"),
    ("CBS. FOX, CW", "CBS"),
    ("KALB/NBC", "NBC"),                       # leading callsign dropped
    ("ABC (main) CBS (multicast)", "ABC"),     # parenthetical annotation dropped
    ("ABC,CBS,CW", "ABC"),
    # already-clean strings are unchanged
    ("ABC", "ABC"),
    ("QUEST", "QUEST"),
    ("Independent", "INDEPENDENT"),
    ("Telemundo", "TELEMUNDO"),
]


@pytest.mark.parametrize("raw,expected", PARSE_CASES)
def test_parse_network_affiliation(plugin, raw, expected):
    assert plugin._parse_network_affiliation(raw) == expected


# --- Fix 1: stream-stated network extraction -------------------------------
# (stream name, expected network or None)
STREAM_NET_CASES = [
    ("US: CBS 7 (WBBJ-DT3) JACKSON HD", "CBS"),
    ("US: ABC 7 (KGO) SAN FRANCISCO HD", "ABC"),
    ("US: CBS (WSBK) BOSTON HD", "CBS"),
    ("US: CBS WLNY HD", "CBS"),
    ("CBS: Something", "CBS"),       # network prefix must not be eaten as geo
    ("WABC-TV", None),              # callsign, not a network
    ("Some Random Channel", None),
]


@pytest.mark.parametrize("stream,expected", STREAM_NET_CASES)
def test_extract_stream_network(plugin, stream, expected):
    assert plugin._extract_stream_network(stream) == expected


# --- Parenthesized-callsign override --------------------------------------
# A callsign in parentheses is an explicit signal: accept a denylisted English
# word there IF it's a real loaded station (KING/WOOD/WAVE are NBC callsigns).
# (name, expected_callsign) — parenthesized, denylisted, but a real US station.
PAREN_REAL_CALLSIGN = [
    ("US: NBC 5 (KING) SEATTLE (H)", "KING"),
    ("US: NBC 8 (WOOD) GRAND RAPIDS (H)", "WOOD"),
    ("US: NBC 3 (WAVE) LOUISVILLE (H)", "WAVE"),
]

# Parenthesized denylisted words that are NOT stations must stay rejected.
PAREN_NOT_STATION = ["HBO (WEST)", "Disney (KIDS)"]

# Denylisted words WITHOUT parentheses must still be rejected (no false positive
# from a real-station word appearing in prose).
UNPAREN_DENYLISTED_NONE = ["King of the Hill", "Watch What Happens Live"]


@pytest.mark.parametrize("name,expected", PAREN_REAL_CALLSIGN)
def test_paren_callsign_override(matcher, name, expected):
    assert matcher.extract_callsign(name) == expected


@pytest.mark.parametrize("name", PAREN_NOT_STATION)
def test_paren_non_station_rejected(matcher, name):
    cs = matcher.extract_callsign(name)
    assert cs is None or cs in matcher.channel_lookup, (
        f"{name!r} extracted {cs!r} which is not a real station"
    )


@pytest.mark.parametrize("name", UNPAREN_DENYLISTED_NONE)
def test_unparen_denylisted_still_none(matcher, name):
    assert matcher.extract_callsign(name) is None, (
        f"{name!r} should extract no callsign (denylist guards unparenthesized words)"
    )


# --- bug-062: grandfathered 3-letter callsigns in parentheses --------------
# Real US stations whose callsign predates the 4-letter rule keep a bare
# 3-letter callsign (WWL/WJZ/KYW/WRC). Priority 1 only matches 4-char paren
# callsigns, so these fell through to the low-confidence loose-word path and
# the anchor never fired. Priority 1b matches `([KW][A-Z]{2})` without a suffix
# and returns high confidence. (name, expected_callsign)
PAREN_3LETTER_HIGH_CONF = [
    ("CBS - LA New Orleans (WWL)", "WWL"),
    ("US: CBS 13 (WJZ) BALTIMORE HD", "WJZ"),
    ("US: CBS 3 (KYW) PHILADELPHIA HD", "KYW"),
    ("US: NBC 4 (WRC) WASHINGTON HD", "WRC"),
]


@pytest.mark.parametrize("name,expected", PAREN_3LETTER_HIGH_CONF)
def test_paren_3letter_callsign_high_confidence(matcher, name, expected):
    cs, is_high = matcher._extract_callsign_with_confidence(name)
    assert (cs, is_high) == (expected, True), (
        f"{name!r} extracted ({cs!r}, {is_high}); expected ({expected!r}, True)"
    )


def test_paren_3letter_denylisted_word_rescued_by_allowlist(matcher):
    """A denylisted 3-letter word in parens is only promoted when the allowlist
    (channel_lookup) vouches for it — the bug-014 guard. WHO is in the denylist
    but is a real station, so it rescues to high confidence."""
    assert matcher._extract_callsign_with_confidence("US: NBC 13 (WHO) DES MOINES") == ("WHO", True)


def test_paren_3letter_denylisted_nonstation_still_rejected():
    """A denylisted 3-letter word with no allowlist entry stays rejected at the
    Priority 1b anchor (bug-014 guard), falling through to the loose path."""
    from channel_maparr.fuzzy_matcher import FuzzyMatcher

    m = FuzzyMatcher.__new__(FuzzyMatcher)
    m.channel_lookup = {}
    m._callsign_cache = {}
    # "WHO" is denylisted and not in the allowlist -> not promoted by 1b.
    cs, is_high = m._extract_callsign_with_confidence("Doctor (WHO) marathon")
    assert is_high is False, f"denylisted non-station (WHO) wrongly promoted: {cs!r}/{is_high}"


def test_paren_3letter_allowlist_word_rescued():
    """An allowlisted 3-letter callsign-word (KING) promotes to high confidence
    even though it is denylisted — same rescue path the repo uses for Priority 1."""
    from channel_maparr.fuzzy_matcher import FuzzyMatcher

    m = FuzzyMatcher.__new__(FuzzyMatcher)
    m.channel_lookup = {"KING"}
    m._callsign_cache = {}
    assert m._extract_callsign_with_confidence("US: NBC 5 (KING) SEATTLE") == ("KING", True)


# --- Combined: the WBBJ-DT3 case renders CBS, not ABC ----------------------
def test_subchannel_honors_stream_network(plugin, matcher):
    plugin.matcher = matcher
    stream = "US: CBS 7 (WBBJ-DT3) JACKSON HD"
    cs, station = matcher.match_broadcast_channel(stream)
    assert station is not None and station["network_affiliation"].upper().startswith("ABC")
    override = plugin._extract_stream_network(stream)
    name = plugin._format_ota_name(station, "{NETWORK} - {STATE} {CITY} ({CALLSIGN})",
                                   cs, network_override=override)
    assert name == "CBS - TN Jackson (WBBJ)", name
