"""Resolving a broadcast station from a market city and a channel number.

Providers label a station by its market and its virtual channel number when
they do not print a callsign, as in "US: ABC 9 HD [SYRACUSE]". The station
table is keyed on callsign, so none of those names match.

Every name in this file was measured on the live provider feed on 2026-08-12,
and every expected callsign was checked against the shipped station table.
"""
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

from market_index import (  # noqa: E402
    MARKET_ALIASES,
    build_market_index,
    parse_market_reference,
    resolve_station,
    set_known_states,
)


# --- Parsing ---------------------------------------------------------------

# The parser needs to know which two letter tokens are state codes before it
# can tell "ABILENE TX" from a city whose last word happens to be two letters.
# build_market_index does this from the station table; these tests set it
# directly because they parse without building an index.
set_known_states({"TX", "SC", "NC", "NY", "AL", "VA", "OR", "MS", "FL", "MD"})


PARSE_CASES = [
    # (name, network, expected number, expected city, expected state)
    ("US: ABC 9 HD [SYRACUSE]", "ABC", "9", "SYRACUSE", None),
    ("US: FOX 7 HD [EL PASO]", "FOX", "7", "EL PASO", None),
    ("US: ABC 13 Asheville", "ABC", "13", "ASHEVILLE", None),
    ("US: ABC 22 BURLINGTON HD", "ABC", "22", "BURLINGTON", None),
    ("US: ABC 7 NEW YORK", "ABC", "7", "NEW YORK", None),
    ("US: ABC HD [NEW YORK]", "ABC", None, "NEW YORK", None),
    ("US: FOX NET [ABILENE TX]", "FOX", None, "ABILENE", "TX"),
    ("US: FOX NET [WILMINGTON NC]", "FOX", None, "WILMINGTON", "NC"),
    ("US: FOX 21 HD [GREENVILLE SC]", "FOX", "21", "GREENVILLE", "SC"),
    ("US: FOX 30 HD [MERIDIAN MS]", "FOX", "30", "MERIDIAN", "MS"),
    # A provider that prints two channel numbers states the main one first.
    ("US: ABC 33/40 HD [BIRMINGHAM]", "ABC", "33", "BIRMINGHAM", None),
    # A trailing state in parentheses, with the doubled space the feed carries.
    ("US:  ABC TAMPA (FL)", "ABC", None, "TAMPA", "FL"),
    # The prefix is not always a country code. This provider labels four groups
    # with a word, so the prefix pattern has to accept one.
    ("CITY: ABC 9 HD [SYRACUSE]", "ABC", "9", "SYRACUSE", None),
    ("PRIME: FOX 7 HD [AUSTIN]", "FOX", "7", "AUSTIN", None),
]


@pytest.mark.parametrize("name,network,number,city,state", PARSE_CASES)
def test_parse_market_reference(name, network, number, city, state):
    ref = parse_market_reference(name, network)
    assert ref is not None, "no market reference parsed from %r" % name
    assert ref.number == number
    assert ref.city == city
    assert ref.state == state


NO_REFERENCE_CASES = [
    # A separator row the provider inserts between groups.
    ("##### ABC HD #####", "ABC"),
    # National feeds. There is no market in the name and there must not be one
    # invented, because these are the names most likely to be renamed wrongly.
    ("US: ABC NEWS LIVE HD", "ABC"),
    ("US: ABC", "ABC"),
    ("US: CBS HD", "CBS"),
    ("US: FOX 50 HD", "FOX"),
    # No network stated means there is nothing to read the number relative to.
    ("US: Some Random Channel", None),
    # A secondary service. See the refusal tests below.
    ("US: FOX 9 PLUS HD [MINNEAPOLIS]", "FOX"),
    ("US: FOX 10 XTRA HD [PHOENIX]", "FOX"),
]


@pytest.mark.parametrize("name,network", NO_REFERENCE_CASES)
def test_parse_returns_none_when_there_is_no_market(name, network):
    assert parse_market_reference(name, network) is None


SECONDARY_SERVICE_NAMES = [
    "US: FOX 9 PLUS HD [MINNEAPOLIS]",
    "US: FOX 35 PLUS HD [ORLANDO]",
    "US: FOX 5 PLUS HD [WASHINGTON]",
    "US: FOX 10 XTRA HD [PHOENIX]",
]


@pytest.mark.parametrize("name", SECONDARY_SERVICE_NAMES)
def test_a_secondary_service_is_refused_rather_than_folded_into_the_main_station(name):
    """"PLUS" and "XTRA" name a sister channel, not the market's main station.

    "FOX 9 PLUS" in Minneapolis is WFTC, a different station from KMSP, which
    is "FOX 9". Treating the word as noise resolves both names to KMSP and
    produces two channels claiming to be the same station. Refusing loses a
    match that was never available; accepting invents a wrong one.
    """
    assert parse_market_reference(name, "FOX") is None


# --- Index building and resolution ------------------------------------------

STATIONS = [
    {"callsign": "WSYR-TV", "community_served_city": "SYRACUSE",
     "community_served_state": "NY", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": "9"},
    {"callsign": "WSTM-TV", "community_served_city": "SYRACUSE",
     "community_served_state": "NY", "active_ind": "Y",
     "network_affiliation": "NBC", "tv_virtual_channel": "3"},
    {"callsign": "KCPQ", "community_served_city": "TACOMA",
     "community_served_state": "WA", "active_ind": "Y",
     "network_affiliation": "FOX", "tv_virtual_channel": "13"},
    {"callsign": "KING-TV", "community_served_city": "SEATTLE",
     "community_served_state": "WA", "active_ind": "Y",
     "network_affiliation": "NBC", "tv_virtual_channel": "5"},
    {"callsign": "WVEC", "community_served_city": "HAMPTON",
     "community_served_state": "VA", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": "13"},
    # Two stations named Charleston in different states, both ABC, so a name
    # that states only the city cannot be resolved.
    {"callsign": "WCHS-TV", "community_served_city": "CHARLESTON",
     "community_served_state": "WV", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": "8"},
    {"callsign": "WCIV", "community_served_city": "CHARLESTON",
     "community_served_state": "SC", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": "4"},
    # An inactive record must never be a candidate.
    {"callsign": "WOLD-TV", "community_served_city": "SYRACUSE",
     "community_served_state": "NY", "active_ind": "N",
     "network_affiliation": "ABC", "tv_virtual_channel": "9"},
    # Two stations named Kingsport, both ABC, neither carrying a channel
    # number, plus a third ABC station elsewhere in one of those states that
    # does carry the number. See the fall-through test below.
    {"callsign": "WKPT-TV", "community_served_city": "KINGSPORT",
     "community_served_state": "TN", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": ""},
    {"callsign": "WKPX-TV", "community_served_city": "KINGSPORT",
     "community_served_state": "VA", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": ""},
    {"callsign": "WFAR-TV", "community_served_city": "FARAWAY",
     "community_served_state": "TN", "active_ind": "Y",
     "network_affiliation": "ABC", "tv_virtual_channel": "4"},
]


@pytest.fixture(scope="module")
def index():
    return build_market_index(STATIONS)


def test_index_maps_a_city_to_its_stations(index):
    assert {s["callsign"] for s in index.by_city["SYRACUSE"]} == {"WSYR-TV", "WSTM-TV"}


def test_index_excludes_an_inactive_station(index):
    assert all(s["active_ind"] == "Y" for s in index.by_city["SYRACUSE"])


def test_index_records_every_state_a_city_name_appears_in(index):
    assert index.city_states["CHARLESTON"] == {"WV", "SC"}


def test_resolves_a_city_and_network(index):
    ref = parse_market_reference("US: ABC 9 HD [SYRACUSE]", "ABC")
    assert resolve_station(ref, "ABC", index)["callsign"] == "WSYR-TV"


def test_refuses_when_the_network_does_not_serve_that_city(index):
    ref = parse_market_reference("US: CW 9 HD [SYRACUSE]", "CW")
    assert resolve_station(ref, "CW", index) is None


def test_the_channel_number_separates_two_cities_of_the_same_name(index):
    ref = parse_market_reference("US: ABC 4 HD [CHARLESTON]", "ABC")
    assert resolve_station(ref, "ABC", index)["callsign"] == "WCIV"


def test_refuses_two_cities_of_the_same_name_when_no_number_is_stated(index):
    ref = parse_market_reference("US: ABC HD [CHARLESTON]", "ABC")
    assert resolve_station(ref, "ABC", index) is None


def test_an_undecidable_city_is_refused_rather_than_widened_to_the_state(index):
    """Stage 2 must not run when stage 1 found candidates it could not separate.

    This is a real wrong match, measured on the live station table before it
    was fixed. "US: ABC 4 HD [CHARLESTON]" has an ABC station in Charleston
    West Virginia and another in Charleston South Carolina. The correct answer
    carries no channel number in the FCC data, so the number narrowed nothing
    and two candidates survived. Falling through to a search of both whole
    states then found a single ABC station on channel 4 in a third city and
    returned it, which is a confidently wrong answer rather than no answer.

    When the market city does have stations carrying the network, the answer is
    among them or there is no answer.
    """
    ref = parse_market_reference("US: ABC 4 HD [KINGSPORT]", "ABC")
    assert resolve_station(ref, "ABC", index) is None


def test_resolves_a_market_whose_station_is_licensed_to_a_neighbouring_city(index):
    """KCPQ serves the Seattle market but is licensed to Tacoma.

    The market city gives the state, and the channel number picks the station
    within it. This is measured behaviour, not a guess: on the live station
    table this stage resolves Seattle, San Francisco, Las Vegas, Charlotte,
    Raleigh, Palm Beach, Gainesville and Greensboro, all correctly.
    """
    ref = parse_market_reference("US: FOX 13 HD [SEATTLE]", "FOX")
    assert resolve_station(ref, "FOX", index)["callsign"] == "KCPQ"


def test_the_neighbouring_city_stage_requires_a_channel_number(index):
    """Without a number there is nothing to pick a station within the state."""
    ref = parse_market_reference("US: FOX HD [SEATTLE]", "FOX")
    assert resolve_station(ref, "FOX", index) is None


def test_an_alias_maps_a_market_name_that_is_no_citys_name(index):
    assert MARKET_ALIASES["HAMPTON ROADS"] == ("HAMPTON", "VA")
    ref = parse_market_reference("US: ABC 13 HD [HAMPTON ROADS]", "ABC")
    assert resolve_station(ref, "ABC", index)["callsign"] == "WVEC"


def test_returns_none_for_an_unknown_market(index):
    ref = parse_market_reference("US: ABC 5 HD [NOWHERE CITY]", "ABC")
    assert resolve_station(ref, "ABC", index) is None


def test_returns_none_for_a_reference_that_is_none(index):
    assert resolve_station(None, "ABC", index) is None


def test_every_alias_target_is_a_real_city_in_the_shipped_station_table(matcher):
    """An alias pointing at a city no station serves is silently dead.

    This reads the station table the plugin actually ships rather than the
    fixture above, so an alias that was correct when it was written and is not
    correct now fails here.
    """
    cities = {
        (s.get("community_served_city") or "").upper()
        for s in matcher.broadcast_channels
    }
    dead = sorted(city for city, _ in MARKET_ALIASES.values() if city not in cities)
    assert dead == [], "alias targets absent from networks.json: %s" % dead
