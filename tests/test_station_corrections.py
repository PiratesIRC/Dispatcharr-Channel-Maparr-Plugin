"""Corrections applied to the FCC station table when it is rebuilt.

The FCC affiliation and virtual channel fields are not maintained to a
standard, so some licensed stations carry an empty or wrong value. The
supplemental file cannot help: it loads after the main table with setdefault,
so a main table record always wins, which means it can ADD a station but never
CORRECT one.

scripts/networks_corrections.json fills that gap. It is a build input, applied
by scripts/build_networks_json.py while the table is rebuilt, so the shipped
networks.json carries the corrected value and the runtime loader stays as it
is. Every corrected record states in the file itself that it was corrected and
why.

A correction names the value it believes it is replacing. When that value no
longer matches, because the FCC has fixed its own data or changed it to
something else, the correction is skipped and reported rather than applied
blindly. A stale correction that silently overwrites newly correct data would
be worse than no correction at all.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_networks_json as build  # noqa: E402

CORRECTIONS_FILE = REPO_ROOT / "scripts" / "networks_corrections.json"
STATION_TABLE = REPO_ROOT / "Channel-Maparr" / "networks.json"


@pytest.fixture(scope="module")
def corrections():
    return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stations():
    return json.loads(STATION_TABLE.read_text(encoding="utf-8"))


# --- The file itself --------------------------------------------------------

def test_the_corrections_file_is_a_list(corrections):
    """An empty corrections file stays valid rather than being deleted.

    The build reads it unconditionally, so removing it when the last
    correction is retired would break the next rebuild.
    """
    assert isinstance(corrections, list)


def test_every_correction_states_a_callsign_a_change_and_a_reason(corrections):
    for entry in corrections:
        assert entry.get("callsign"), "a correction with no callsign cannot be applied"
        assert entry.get("fields"), "%s changes nothing" % entry.get("callsign")
        assert entry.get("expects") is not None, (
            "%s does not say what it replaces, so a stale correction could not "
            "be detected" % entry["callsign"])
        assert entry.get("reason"), (
            "%s does not say why, and the reason is copied into the shipped "
            "table" % entry["callsign"])


def test_a_correction_never_introduces_a_key_the_records_do_not_have(corrections, stations):
    known = set(stations[0])
    for entry in corrections:
        unknown = (set(entry["fields"]) | set(entry["expects"])) - known
        assert not unknown, "%s names fields no station record has: %s" % (
            entry["callsign"], sorted(unknown))


def test_every_correction_targets_a_callsign_that_is_in_the_shipped_table(
        corrections, stations):
    """A correction is not a way to add a station.

    Adding one is what networks_supplemental.json does. A correction whose
    callsign is absent silently does nothing, so it is caught here.
    """
    present = {s["callsign"] for s in stations}
    missing = sorted(e["callsign"] for e in corrections if e["callsign"] not in present)
    assert missing == [], "corrections naming a callsign absent from networks.json: %s" % missing


def test_every_correction_is_visible_in_the_shipped_table(corrections, stations):
    """The rebuild must have been run after the corrections file changed.

    Without this, a correction can sit in the file for months while the shipped
    table still carries the uncorrected value and everything looks done.
    """
    by_callsign = {s["callsign"]: s for s in stations}
    for entry in corrections:
        station = by_callsign[entry["callsign"]]
        for key, value in entry["fields"].items():
            assert station.get(key) == value, (
                "%s.%s is %r in networks.json but the corrections file asks for "
                "%r; rebuild the table" % (
                    entry["callsign"], key, station.get(key), value))
        assert station.get("corrected"), (
            "%s carries no corrected note, so the shipped table does not say it "
            "was changed" % entry["callsign"])


# --- Applying them ----------------------------------------------------------

RECORDS = {
    "WBMA-LD": {
        "callsign": "WBMA-LD", "community_served_city": "BIRMINGHAM",
        "community_served_state": "AL", "active_ind": "Y",
        "network_affiliation": "", "tv_virtual_channel": "",
        "facility_id": "60214", "station_class": "LPD",
    },
    "WVTM-TV": {
        "callsign": "WVTM-TV", "community_served_city": "BIRMINGHAM",
        "community_served_state": "AL", "active_ind": "Y",
        "network_affiliation": "NBC", "tv_virtual_channel": "13",
        "facility_id": "74173", "station_class": "DTV",
    },
}


def _records():
    return {key: dict(value) for key, value in RECORDS.items()}


def test_a_correction_sets_the_named_fields():
    records = _records()
    applied, skipped, unmatched = build.apply_corrections(records, [{
        "callsign": "WBMA-LD",
        "expects": {"network_affiliation": "", "tv_virtual_channel": ""},
        "fields": {"network_affiliation": "ABC", "tv_virtual_channel": "33"},
        "reason": "The station is the ABC affiliate on channel 33.",
    }])
    assert applied == ["WBMA-LD"]
    assert skipped == [] and unmatched == []
    assert records["WBMA-LD"]["network_affiliation"] == "ABC"
    assert records["WBMA-LD"]["tv_virtual_channel"] == "33"


def test_a_corrected_record_says_so_in_the_table():
    records = _records()
    build.apply_corrections(records, [{
        "callsign": "WBMA-LD",
        "expects": {"network_affiliation": ""},
        "fields": {"network_affiliation": "ABC"},
        "reason": "The station is the ABC affiliate.",
    }])
    note = records["WBMA-LD"].get("corrected")
    assert note, "a corrected record must carry a note explaining the change"
    assert "ABC affiliate" in note
    assert "network_affiliation" in note, "the note must name the field it changed"


def test_a_correction_whose_expected_value_no_longer_matches_is_skipped():
    """The FCC has filled the field in since the correction was written.

    Applying it anyway would overwrite whatever the FCC now says with a value
    that may be older. Skipping and reporting makes the staleness visible.
    """
    records = _records()
    applied, skipped, unmatched = build.apply_corrections(records, [{
        "callsign": "WVTM-TV",
        "expects": {"network_affiliation": ""},
        "fields": {"network_affiliation": "ABC"},
        "reason": "written when the field was empty",
    }])
    assert applied == []
    assert unmatched == []
    assert len(skipped) == 1
    callsign, field, expected, actual = skipped[0]
    assert (callsign, field, expected, actual) == ("WVTM-TV", "network_affiliation", "", "NBC")
    assert records["WVTM-TV"]["network_affiliation"] == "NBC", "the record must be left alone"
    assert "corrected" not in records["WVTM-TV"]


def test_a_correction_for_a_callsign_not_in_the_table_is_reported_not_created():
    records = _records()
    applied, skipped, unmatched = build.apply_corrections(records, [{
        "callsign": "KNOWHERE",
        "expects": {"network_affiliation": ""},
        "fields": {"network_affiliation": "ABC"},
        "reason": "no such station",
    }])
    assert applied == [] and skipped == []
    assert unmatched == ["KNOWHERE"]
    assert "KNOWHERE" not in records, "a correction must never add a station"


def test_all_expected_values_must_match_before_any_field_is_written():
    """A partly applied correction would leave the record in a state that
    neither the FCC data nor the correction describes."""
    records = _records()
    applied, skipped, unmatched = build.apply_corrections(records, [{
        "callsign": "WVTM-TV",
        "expects": {"tv_virtual_channel": "13", "network_affiliation": ""},
        "fields": {"tv_virtual_channel": "99", "network_affiliation": "ABC"},
        "reason": "one expectation holds and the other does not",
    }])
    assert applied == []
    assert records["WVTM-TV"]["tv_virtual_channel"] == "13"
    assert records["WVTM-TV"]["network_affiliation"] == "NBC"
