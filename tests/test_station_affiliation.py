"""Parsing the free-text network_affiliation field in the FCC station table.

The field has at least four shapes in the shipped file. Code that asks "is this
station network X" with a substring test gets the wrong answer for a station
that carries X on a subchannel, which is how six ABC, CBS and FOX stations were
wrongly identified as Telemundo during a live sweep in August 2026.
"""
import pytest
from conftest import _load_plugin_package  # noqa: F401

_load_plugin_package()
from channel_maparr.station_affiliation import (  # noqa: E402
    station_networks,
    primary_network,
    carries_network,
    is_primary_network,
)

# (raw field, expected network list) taken verbatim from networks.json
NETWORK_CASES = [
    ("ABC", ["ABC"]),
    ("Telemundo", ["TELEMUNDO"]),
    ("CBS, Telemundo", ["CBS", "TELEMUNDO"]),
    ("FOX, Telemundo, MeTV", ["FOX", "TELEMUNDO", "METV"]),
    ("CBS / Telemundo / MeTV", ["CBS", "TELEMUNDO", "METV"]),
    ("FOX/ME-TV/HEROES & ICONS/TELEMUNDO",
     ["FOX", "ME-TV", "HEROES & ICONS", "TELEMUNDO"]),
    ("ABC (9.1), Telemundo (9.2), GetTV (9.3). Comet (9.4)",
     ["ABC", "TELEMUNDO", "GETTV", "COMET"]),
    ("CBS (7.1), COZITV (7.2), LAFF (7.3), TELEMUNDO (7.4)",
     ["CBS", "COZITV", "LAFF", "TELEMUNDO"]),
    ("CBS Ch 3.1, CW/MTN Ch 3.2", ["CBS", "CW", "MTN"]),
    ("KALB/NBC", ["NBC"]),
    ("CBS & FOX", ["CBS", "FOX"]),
    ("", []),
    (None, []),
    # Verbatim from networks.json line 8206. "&" separates two networks
    # ("This TV" and "Start") here, even though the record also uses "/" as
    # a separator elsewhere in the same string, unlike "Heroes & Icons"
    # above, which is one network's own name.
    ("CBS 5.1/The 365 5.2/This TV & Start 5.3/Quest 5.4/The Outlaw5.5",
     ["CBS", "THE 365", "THIS TV", "START", "QUEST", "THE OUTLAW5.5"]),
    # Verbatim from networks.json line 10654. Same shape as "Heroes & Icons"
    # ("WORD & WORD" inside a "/"-delimited record) but names two networks,
    # "Cozi" and "Court TV", not one.
    ("NBC/ANTENNA/FOX/COZI & COURT TV",
     ["NBC", "ANTENNA", "FOX", "COZI", "COURT TV"]),
]


@pytest.mark.parametrize("raw,expected", NETWORK_CASES)
def test_station_networks(raw, expected):
    assert station_networks(raw) == expected


def test_primary_network_is_the_first_one_named():
    assert primary_network("ABC (9.1), Telemundo (9.2)") == "ABC"
    assert primary_network("") is None


def test_carries_network_finds_a_subchannel():
    station = {"network_affiliation": "ABC (9.1), Telemundo (9.2)"}
    assert carries_network(station, "TELEMUNDO") is True
    assert carries_network(station, "abc") is True
    assert carries_network(station, "NBC") is False


def test_is_primary_network_distinguishes_a_subchannel():
    station = {"network_affiliation": "ABC (9.1), Telemundo (9.2)"}
    assert is_primary_network(station, "ABC") is True
    assert is_primary_network(station, "TELEMUNDO") is False


def test_a_callsign_prefix_is_not_mistaken_for_a_network():
    assert "KALB" not in station_networks("KALB/NBC")
