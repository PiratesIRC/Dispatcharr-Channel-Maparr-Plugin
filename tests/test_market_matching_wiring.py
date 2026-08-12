"""The market fallback is opt-in and does not disturb callsign matching.

The fallback infers a station from a market city and a channel number rather
than reading a callsign printed in the name. Turning it on by default would
silently change the names of channels that match today, so every caller opts in
explicitly and the default is off.

The names here were measured on the live provider feed on 2026-08-12 and every
expected callsign was checked against the shipped station table.
"""
import ast
import pathlib

import pytest


def _plugin_source():
    root = pathlib.Path(__file__).resolve().parents[1] / "Channel-Maparr" / "plugin.py"
    return root.read_text(encoding="utf-8")


# --- The matcher keeps its current behaviour with the fallback off ----------

def test_callsign_matching_is_unchanged_with_the_fallback_off(matcher):
    callsign, station = matcher.match_broadcast_channel("US: ABC 7 (KGO) SAN FRANCISCO HD")
    assert callsign == "KGO"
    assert station is not None
    assert station["community_served_city"].upper() == "SAN FRANCISCO"


def test_a_market_name_does_not_resolve_with_the_fallback_off(matcher):
    callsign, station = matcher.match_broadcast_channel("US: ABC 9 HD [SYRACUSE]")
    assert station is None


def test_the_market_index_is_built_when_the_station_table_loads(matcher):
    assert matcher.market_index is not None
    assert "SYRACUSE" in matcher.market_index.by_city


# --- The fallback, when a caller opts in ------------------------------------

def test_a_market_name_resolves_with_the_fallback_on(matcher):
    callsign, station = matcher.match_broadcast_channel(
        "US: ABC 9 HD [SYRACUSE]", network="ABC", allow_market_fallback=True)
    assert station is not None
    assert station["callsign"].startswith("WSYR")
    assert callsign.startswith("WSYR")


def test_the_fallback_needs_the_network_the_name_states(matcher):
    callsign, station = matcher.match_broadcast_channel(
        "US: ABC 9 HD [SYRACUSE]", network=None, allow_market_fallback=True)
    assert station is None


def test_the_fallback_does_not_override_a_callsign_that_is_printed(matcher):
    """A printed callsign is read, not inferred, so it wins.

    The bracketed market here is deliberately wrong for KGO.
    """
    callsign, station = matcher.match_broadcast_channel(
        "US: ABC 7 (KGO) HD [SYRACUSE]", network="ABC", allow_market_fallback=True)
    assert callsign == "KGO"
    assert station["community_served_city"].upper() == "SAN FRANCISCO"


# Names measured on the live feed with the station each must resolve to. These
# are the cases the callsign matcher misses. The first block resolves because
# the market city is the community the station is licensed to; the second
# because the station is licensed to a neighbouring community and the channel
# number picks it out of the state.
MARKET_CASES = [
    ("US: ABC 9 HD [SYRACUSE]", "ABC", "WSYR"),
    ("US: ABC 2 HD [ATLANTA]", "ABC", "WSB"),
    ("US: ABC 5 HD [ST. PAUL]", "ABC", "KSTP"),
    ("US: ABC 7 NEW YORK", "ABC", "WABC"),
    ("US: ABC 13 Asheville", "ABC", "WLOS"),
    ("US: ABC 22 BURLINGTON HD", "ABC", "WVNY"),
    ("US: FOX 7 HD [AUSTIN]", "FOX", "KTBC"),
    ("US: FOX NET [ABILENE TX]", "FOX", "KXVA"),
    ("US: FOX NET [MEDFORD OR]", "FOX", "KMVU"),
    ("US: FOX 21 HD [GREENVILLE SC]", "FOX", "WHNS"),
    # Licensed to a neighbouring community.
    ("US: FOX 13 HD [SEATTLE]", "FOX", "KCPQ"),
    ("US: FOX 2 HD [SAN FRANCISCO]", "FOX", "KTVU"),
    ("US: FOX 5 HD [LAS VEGAS]", "FOX", "KVVU"),
    ("US: FOX 46 HD [CHARLOTTE]", "FOX", "WJZY"),
    ("US: ABC 11 HD [RALEIGH]", "ABC", "WTVD"),
    ("US: FOX 8 HD [GREENSBORO]", "FOX", "WGHP"),
    # Reached only through the alias table.
    ("US: ABC 13 HD [HAMPTON ROADS]", "ABC", "WVEC"),
    ("US: FOX NET [CHARLES LA]", "FOX", "KVHP"),
    ("US: ABC 7 HD [FORT MYERS]", "ABC", "WZVN"),
]


@pytest.mark.parametrize("name,network,expected", MARKET_CASES)
def test_a_known_market_name_resolves_to_its_station(matcher, name, network, expected):
    callsign, station = matcher.match_broadcast_channel(
        name, network=network, allow_market_fallback=True)
    assert station is not None, "no station resolved for %r" % name
    assert station["callsign"].split("-")[0] == expected


# Names that must NOT resolve. National and cable feeds carry a network name
# and no market, and are the names most likely to be renamed wrongly. The last
# two are markets that genuinely cannot be decided.
MUST_NOT_RESOLVE = [
    ("US: ABC NEWS LIVE HD", "ABC"),
    ("US: FOX SPORTS SOUTHWEST HD", "FOX"),
    ("US: FOX NEWS HD", "FOX"),
    ("US: ABC (EAST)", "ABC"),
    ("US: CBS SPORTS NETWORK HD", "CBS"),
    ("##### ABC HD #####", "ABC"),
    # Harrisburg exists in three states and more than one has a FOX station on
    # channel 43.
    ("US: FOX 43 HD [HARRISBURG]", "FOX"),
    # Charleston has an ABC station in West Virginia and another in South
    # Carolina, and the correct one carries no channel number in the FCC data.
    ("US: ABC 4 HD [CHARLESTON]", "ABC"),
    # A secondary service names a sister station, not the market's main one.
    ("US: FOX 9 PLUS HD [MINNEAPOLIS]", "FOX"),
    ("US: FOX 10 XTRA HD [PHOENIX]", "FOX"),
]


@pytest.mark.parametrize("name,network", MUST_NOT_RESOLVE)
def test_a_name_with_no_decidable_market_resolves_to_nothing(matcher, name, network):
    callsign, station = matcher.match_broadcast_channel(
        name, network=network, allow_market_fallback=True)
    assert station is None, "%r wrongly resolved to %r" % (
        name, station.get("callsign") if station else None)


# --- Every call site must opt in explicitly ---------------------------------

def test_every_ota_call_site_passes_the_fallback_flag():
    """A call site that omits the keyword silently opts out of the feature.

    This walks the syntax tree rather than searching the text, so a call split
    across several lines is still checked.
    """
    tree = ast.parse(_plugin_source())
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "match_broadcast_channel"]
    assert calls, "no call sites found; this test is reading the wrong file"
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "allow_market_fallback" in keywords, (
            "the call at line %d does not pass allow_market_fallback" % call.lineno)
        assert "network" in keywords, (
            "the call at line %d does not pass network" % call.lineno)


def test_the_setting_is_declared_with_a_default_of_off():
    """Reads the source rather than the fields property.

    The fields property does database work on every access, so a unit test must
    not touch it. Reading the source is the established pattern here; see
    tests/test_group_scope_wiring.py.
    """
    source = _plugin_source()
    assert '"id": "ota_market_fallback"' in source
    chunk = source.split('"id": "ota_market_fallback"')[1][:900]
    assert '"default": False' in chunk
    assert "—" not in chunk, "em dash in plugin-facing copy"
