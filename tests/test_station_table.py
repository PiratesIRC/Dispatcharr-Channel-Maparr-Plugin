"""Integrity of networks.json, the FCC station table.

This file is the ONLY source of OTA matches. tests/test_data_integrity.py
covers the per-country premium databases; nothing covered them both, which is
why this file exists separately.
"""
import json
from pathlib import Path

import pytest
from conftest import _load_plugin_package  # noqa: F401

_load_plugin_package()
from channel_maparr.station_affiliation import station_networks  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
REQUIRED_KEYS = {
    "callsign", "community_served_city", "community_served_state",
    "active_ind", "network_affiliation", "tv_virtual_channel", "facility_id",
}


@pytest.fixture(scope="module")
def stations():
    return json.loads((PLUGIN_DIR / "networks.json").read_text(encoding="utf-8"))


def test_table_is_a_non_empty_list(stations):
    assert isinstance(stations, list)
    assert len(stations) > 1500


def test_every_record_has_the_required_keys(stations):
    for station in stations:
        missing = REQUIRED_KEYS - set(station)
        assert not missing, "%s is missing %s" % (station.get("callsign"), missing)


def test_callsigns_are_uppercase_and_non_empty(stations):
    for station in stations:
        callsign = station["callsign"]
        assert callsign and callsign == callsign.upper(), callsign


def test_no_duplicate_callsigns(stations):
    seen = {}
    for station in stations:
        callsign = station["callsign"]
        assert callsign not in seen, "duplicate record for %s" % callsign
        seen[callsign] = station


def test_active_indicator_has_one_of_two_values(stations):
    assert {s["active_ind"] for s in stations} <= {"Y", "N"}


def test_virtual_channel_is_digits_or_empty(stations):
    for station in stations:
        value = station["tv_virtual_channel"]
        assert value == "" or value.isdigit(), "%s has %r" % (station["callsign"], value)


def test_state_is_two_letters_or_empty(stations):
    for station in stations:
        state = station["community_served_state"]
        assert state == "" or (len(state) == 2 and state.isalpha()), station["callsign"]


def test_every_affiliation_parses_to_at_least_one_network(stations):
    """A record whose affiliation parses to nothing can never be matched.

    Measured against the shipped table: every non-empty affiliation string
    parses to at least one network, so this test carries no exclusion list.
    A future record that fails to parse should fail this test rather than
    be silently absorbed into an exclusion list.
    """
    unparsed = [s["callsign"] for s in stations
                if s["network_affiliation"] and not station_networks(s["network_affiliation"])]
    assert unparsed == [], unparsed


def test_no_record_carries_a_category_field(stations):
    """The OTA import path reads a category that no record has.

    If this test starts failing, a category field has been added and
    plugin.py should read it rather than falling through to its default. See
    the constant OTA_IMPORT_CATEGORY.
    """
    assert not any("category" in s for s in stations)


# Stations a live provider feed carries that must resolve. Each was verified
# against its FCC record. Extend this list whenever the table is rebuilt.
MUST_BE_PRESENT = [
    "WVEC", "WSYR-TV", "KCPQ", "KTVU", "KVVU-TV", "WSB-TV", "KSTP-TV",
]


@pytest.mark.parametrize("callsign", MUST_BE_PRESENT)
def test_known_stations_are_present(stations, callsign):
    assert any(s["callsign"] == callsign for s in stations), callsign


def test_the_ota_category_is_a_named_constant():
    """Reading a key that no record has hides the real behaviour.

    Every OTA match is categorised Broadcast. Saying so in a constant means the
    next reader does not have to measure the data file to find out.
    """
    source = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    assert 'OTA_IMPORT_CATEGORY = "Broadcast"' in source
    assert "ota_station.get('category'" not in source


SUPPLEMENTAL = PLUGIN_DIR / "networks_supplemental.json"


@pytest.fixture(scope="module")
def supplemental():
    return json.loads(SUPPLEMENTAL.read_text(encoding="utf-8"))


def test_supplemental_records_have_the_same_shape(supplemental, stations):
    for station in supplemental:
        missing = REQUIRED_KEYS - set(station)
        assert not missing, "%s is missing %s" % (station.get("callsign"), missing)
        assert station.get("source"), "every supplemental record must say where it came from"


def test_supplemental_does_not_shadow_the_main_table(supplemental, stations):
    main = {s["callsign"] for s in stations}
    clashes = [s["callsign"] for s in supplemental if s["callsign"] in main]
    assert clashes == [], clashes


def test_supplemental_covers_the_stations_known_to_be_missing(supplemental):
    present = {s["callsign"].split("-")[0] for s in supplemental}
    assert {"WBND", "WBMA"} <= present


def test_matcher_loads_supplemental_stations(matcher):
    callsign, station = matcher.match_broadcast_channel("US: ABC 57 (WBND) South Bend")
    assert station is not None
    assert station["community_served_city"].upper().startswith("SOUTH BEND")
