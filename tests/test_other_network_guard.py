"""A stream never resolves to a station whose primary network is another one.

The FCC table names every network a station carries, subchannels included. A
CBS station's FOX subchannel is therefore not a FOX station, and a FOX stream
must not resolve to it. Real names from the provider feed:

- "US FOX 44 (WEVV DT2)": WEVV-TV is CBS on channel 44, FOX on DT2.
- "FOX - ID TWIN FALLS (KMVT)": KMVT is "CBS. FOX, CW", so CBS is primary.
- "FOX - CA SAN FRANCISCO (KGO)" style names: KGO-TV is ABC.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

from market_index import (  # noqa: E402
    MarketReference,
    build_market_index,
    parse_market_reference,
    resolve_station,
)
from station_affiliation import (  # noqa: E402
    canonical_network,
    is_primary_network,
    names_other_network,
)

WEVV = {"callsign": "WEVV-TV", "community_served_city": "EVANSVILLE",
        "community_served_state": "IN", "active_ind": "Y",
        "network_affiliation": "CBS", "tv_virtual_channel": "44"}
KGO = {"callsign": "KGO-TV", "community_served_city": "SAN FRANCISCO",
       "community_served_state": "CA", "active_ind": "Y",
       "network_affiliation": "ABC", "tv_virtual_channel": "7"}
KMVT = {"callsign": "KMVT", "community_served_city": "TWIN FALLS",
        "community_served_state": "ID", "active_ind": "Y",
        "network_affiliation": "CBS. FOX, CW", "tv_virtual_channel": "11"}
KUAN = {"callsign": "KUAN-LP", "community_served_city": "SAN ANTONIO",
        "community_served_state": "TX", "active_ind": "Y",
        "network_affiliation": "", "tv_virtual_channel": "62"}
THE_CW = {"callsign": "KCWX", "community_served_city": "SAMPLE",
          "community_served_state": "TX", "active_ind": "Y",
          "network_affiliation": "THE CW", "tv_virtual_channel": "9"}


# --------------------------------------------------------------------------- #
# The conflict test
# --------------------------------------------------------------------------- #
def test_a_fox_stream_does_not_take_the_cbs_station_of_wevv():
    assert names_other_network(WEVV, "FOX") is True


def test_a_cbs_stream_takes_wevv():
    assert names_other_network(WEVV, "CBS") is False


def test_a_kmvt_fox_stream_is_refused_because_cbs_is_primary():
    assert names_other_network(KMVT, "FOX") is True
    assert names_other_network(KMVT, "CBS") is False


def test_an_abc_stream_is_refused_on_a_cbs_station():
    assert names_other_network(KGO, "CBS") is True
    assert names_other_network(KGO, "ABC") is False


def test_a_station_naming_no_network_never_conflicts():
    """A low power record often names none, so the stream name decides."""
    assert names_other_network(KUAN, "TELEMUNDO") is False


def test_a_stream_naming_no_network_never_conflicts():
    assert names_other_network(WEVV, None) is False
    assert names_other_network(None, "FOX") is False


# --------------------------------------------------------------------------- #
# Spellings of one network compare equal
# --------------------------------------------------------------------------- #
def test_the_cw_and_cw_are_one_network():
    assert canonical_network("THE CW") == canonical_network("CW") == "CW"
    assert names_other_network(THE_CW, "CW") is False
    assert is_primary_network(THE_CW, "CW") is True


@pytest.mark.parametrize("spelling", ["MYTV", "MY NETWORK TV", "MYNET", "MYNETWORK", "MYNETWORKTV"])
def test_the_mynetworktv_spellings_are_one_network(spelling):
    station = {"network_affiliation": "MY NETWORK TV"}
    assert names_other_network(station, spelling) is False


def test_a_real_other_network_still_conflicts_after_aliasing():
    assert names_other_network({"network_affiliation": "MY NETWORK TV"}, "CW") is True


# --------------------------------------------------------------------------- #
# is_primary_network is the stage-1 test for the market index
# --------------------------------------------------------------------------- #
def test_is_primary_network_refuses_a_subchannel_network():
    assert is_primary_network(WEVV, "CBS") is True
    assert is_primary_network(WEVV, "FOX") is False


def test_a_substring_of_the_field_is_not_a_primary_network():
    """"FOX" is inside "CBS. FOX, CW" but is not that station's primary."""
    assert is_primary_network(KMVT, "FOX") is False
    assert is_primary_network(KMVT, "CBS") is True


# --------------------------------------------------------------------------- #
# The market stage: a FOX market reference does not take a CBS station
# --------------------------------------------------------------------------- #
def test_market_stage_one_refuses_a_subchannel_network():
    index = build_market_index([KMVT])
    reference = MarketReference(number="11", city="TWIN FALLS", state="ID")
    assert resolve_station(reference, "FOX", index) is None
    assert resolve_station(reference, "CBS", index)["callsign"] == "KMVT"


# --------------------------------------------------------------------------- #
# A channel number glued to the network label ("ABC7 [CITY]")
# --------------------------------------------------------------------------- #
def test_a_glued_channel_number_is_read():
    reference = parse_market_reference("ABC7 [CITY]", "ABC")
    assert reference.number == "7"
    assert reference.city == "CITY"


def test_a_spaced_channel_number_is_still_read():
    assert parse_market_reference("US: ABC 9 HD [SYRACUSE]", "ABC").number == "9"


def test_a_glued_network_word_is_not_a_channel_number():
    """A letter after the network label is part of a word, not a number."""
    reference = parse_market_reference("ABCDE [SYRACUSE]", "ABC")
    assert reference is None or reference.number is None


# --------------------------------------------------------------------------- #
# The seed resolver refuses the conflicting station
# --------------------------------------------------------------------------- #
@pytest.fixture
def seed_plugin(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    inst.matcher = MagicMock()
    inst.matcher.normalize_callsign.side_effect = lambda callsign: callsign
    return inst


def test_seed_resolve_refuses_a_fox_stream_on_the_cbs_station(seed_plugin):
    seed_plugin.matcher.match_broadcast_channel.return_value = ("WEVV", WEVV)
    resolve, exclude = seed_plugin._seed_resolver({}, MagicMock())

    assert resolve("US FOX 44 (WEVV DT2)") is None
    # The matcher did return the CBS station, so the refusal is the guard's.
    seed_plugin.matcher.match_broadcast_channel.assert_called()


def test_seed_resolve_keeps_the_station_for_its_own_network(seed_plugin):
    seed_plugin.matcher.match_broadcast_channel.return_value = ("WEVV", WEVV)
    resolve, _ = seed_plugin._seed_resolver({}, MagicMock())

    assert resolve("US CBS 44 (WEVV)") is not None
