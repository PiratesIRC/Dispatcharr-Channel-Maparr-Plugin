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
