#!/usr/bin/env python3
"""Rebuild Channel-Maparr/networks.json from an FCC LMS database dump.

The station table is the only source of over the air matches. It is rebuilt
from the FACILITY table of the FCC Licensing and Management System, which the
operator downloads by hand from

    https://enterpriseefiling.fcc.gov/dataentry/public/tv/lmsDatabase.html

as a dated zip such as 08-10-2026_LMS_Dump.zip. Unpack it and point this
script at the facility.dat file inside.

Usage:
    python scripts/build_networks_json.py <path-to-facility.dat> [--dry-run]

WHAT THE FILE LOOKS LIKE, measured on the 2026-08-10 dump: 180831 records,
31 pipe delimited columns, a header row, and a row terminator of "^|" followed
by a newline. It covers every broadcast service, so AM and FM radio are the
bulk of it.

THE SELECTION RULES, and why each exists. Every count below was measured on
the 2026-08-10 dump.

1. Keep only television services and only licensed facilities. A cancelled
   licence is republished with a "D" prepended to its callsign, so DKLMN and
   DDK20DN-D are dead records rather than stations. Filtering on
   facility_status LICEN removes all of them.

2. Keep every licensed television record that names a network. There are 2137.

3. Also keep a licensed television record that names NO network, unless its
   base callsign already belongs to a record that does. Only 2232 of the
   180831 records name a network at all, and among low power digital stations
   it is 4 out of 8533, so a network-only rule silently excludes low power
   stations that providers do carry. A stream that states its own network
   ("US: ABC 57 (WBND) South Bend") still names such a station correctly,
   because the stated network overrides the station record. The collision
   guard matters: 34 base callsigns are shared between an affiliated station
   and an unaffiliated one, for example KAAS-TV carrying FOX against an
   unaffiliated KAAS-LP, and letting the unaffiliated one win would shadow the
   real affiliate.

4. Never drop a station that the previous file had. 65 of the 1915 stations in
   the previous file are present but no longer licensed, and 7 are absent
   altogether. Those are carried over unchanged and marked with a carried_over
   field naming the date and the reason. A rebuild that silently removes
   working stations is a regression, not an upgrade.

5. Apply the corrections in networks_corrections.json, beside this script.
   The FCC affiliation and virtual channel fields are not maintained to a
   standard, and a low power record almost never carries either, so some real
   affiliates are recorded with both fields empty. networks_supplemental.json
   cannot fix that: the loader reads it after the main table and indexes it
   with setdefault, so a main table record always wins, which means it can ADD
   a station but never CORRECT one. A correction names the value it believes
   it is replacing and is skipped and reported when that no longer matches, so
   a correction that the FCC has since made unnecessary cannot silently
   overwrite newer data. Each corrected record carries a corrected field
   saying what was changed and why.

The output is sorted by callsign and written with LF line endings, because
.gitattributes pins the data files to LF and a CRLF rewrite breaks hash pinned
tests on Linux while looking correct on Windows.
"""
import argparse
import json
import pathlib
import re
import sys

# Row terminator used by the LMS dump, not a plain newline.
ROW_TERMINATOR = "^|\n"

# Television service codes. The rest of the dump is AM and FM radio.
TV_SERVICE_CODES = frozenset({"DTV", "DCA", "LPD", "LPT", "LPX", "TV", "TX", "ACA"})

# Only a licensed facility is a station. Every other status is an application,
# a cancellation or a void record.
LICENSED_STATUS = "LICEN"

# Statuses that mean the licence is gone. A record carrying one of these is not
# carried over from the previous file.
CANCELLED_STATUSES = frozenset({"LICAN", "PRCAN"})

# Affiliation values that name no particular network. The FCC field is not
# maintained to a standard: on the 2026-08-10 dump it calls KTVU Independent,
# although that station is a Fox owned and operated station, and calls WANF
# Independent although it carries CBS. Fourteen stations in the previous file
# were affected. A rebuild must not replace a specific network with one of
# these, because the affiliation is what names a channel when the provider
# stream does not state a network itself.
VAGUE_AFFILIATIONS = frozenset({"", "INDEPENDENT", "IND", "N/A", "NONE", "UNKNOWN"})


def is_vague(affiliation):
    """True when an affiliation value names no particular network."""
    value = (affiliation or "").strip()
    if value.upper() in VAGUE_AFFILIATIONS:
        return True
    # One record on the 2026-08-10 dump has a web address in this field.
    return "://" in value


# Suffixes stripped to get a base callsign, matching the loader in
# Channel-Maparr/fuzzy_matcher.py so the two agree on what a base callsign is.
_CLASS_SUFFIX_RE = re.compile(r"-(?:TV|CD|LP|DT|LD)$")

OUTPUT_KEYS = [
    "callsign",
    "community_served_city",
    "community_served_state",
    "active_ind",
    "network_affiliation",
    "tv_virtual_channel",
    "facility_id",
    "station_class",
]


def base_callsign(callsign):
    """Return the callsign without its service class suffix."""
    return _CLASS_SUFFIX_RE.sub("", callsign.upper())


def read_facility_dat(path):
    """Parse the LMS FACILITY table into a list of dicts.

    Rows are separated by "^|" and a newline rather than by newlines alone,
    because a field may contain a newline. Splitting on newlines produces
    plausible looking garbage rather than an error, so it is done properly
    here.
    """
    raw = open(path, encoding="utf-8", errors="replace").read()
    chunks = raw.split(ROW_TERMINATOR)
    header = [name for name in chunks[0].split("|") if name]
    records = []
    malformed = 0
    for chunk in chunks[1:]:
        if not chunk.strip():
            continue
        parts = chunk.split("|")
        if len(parts) < len(header):
            malformed += 1
            continue
        records.append(dict(zip(header, parts, strict=False)))
    return header, records, malformed


def field(record, name):
    return (record.get(name) or "").strip()


# Corrections applied while the table is rebuilt. See apply_corrections.
CORRECTIONS_FILE = "networks_corrections.json"


def apply_corrections(records, corrections):
    """Overwrite named fields on stations the FCC data records wrongly.

    ``records`` maps a callsign to a station dict and is changed in place.
    ``corrections`` is the parsed contents of networks_corrections.json.

    This exists because networks_supplemental.json cannot do it: the loader
    reads that file after the main table and indexes it with setdefault, so a
    main table record always wins. The supplemental file can therefore ADD a
    station but never CORRECT one.

    Each correction names the value it believes it is replacing, in
    ``expects``. Every one of those must still match before anything is
    written. When the FCC fills a field in or changes it, the correction is
    skipped and reported rather than applied, because a stale correction that
    silently overwrites newly correct data is worse than no correction. The
    check is all or nothing, so a record is never left in a state that neither
    the FCC data nor the correction describes.

    Returns ``(applied, skipped, unmatched)``, where ``applied`` is a list of
    callsigns, ``skipped`` a list of
    ``(callsign, field, expected, actual)`` tuples, and ``unmatched`` a list of
    callsigns the table does not contain.
    """
    applied = []
    skipped = []
    unmatched = []
    for entry in corrections:
        callsign = (entry.get("callsign") or "").strip().upper()
        station = records.get(callsign)
        if station is None:
            # A correction is not a way to add a station. That is what
            # networks_supplemental.json is for.
            unmatched.append(callsign)
            continue
        mismatched = [
            (callsign, key, expected, station.get(key, ""))
            for key, expected in entry.get("expects", {}).items()
            if station.get(key, "") != expected
        ]
        if mismatched:
            skipped.extend(mismatched)
            continue
        changed = sorted(entry.get("fields", {}))
        station.update(entry["fields"])
        station["corrected"] = "%s corrected (%s). %s" % (
            callsign, ", ".join(changed), entry.get("reason", ""))
        applied.append(callsign)
    return applied, skipped, unmatched


def build_records(facility_rows, previous, today, corrections=()):
    """Return (records, report) after applying the selection rules.

    ``previous`` is the list of station dicts from the file being replaced, and
    is used only to carry over stations the new data no longer licenses.
    """
    licensed = [
        row for row in facility_rows
        if field(row, "service_code") in TV_SERVICE_CODES
        and field(row, "facility_status") == LICENSED_STATUS
    ]
    affiliated = [row for row in licensed if field(row, "network_affiliation")]
    unaffiliated = [row for row in licensed if not field(row, "network_affiliation")]

    affiliated_bases = {base_callsign(field(row, "callsign")) for row in affiliated}
    kept_unaffiliated = [
        row for row in unaffiliated
        if base_callsign(field(row, "callsign")) not in affiliated_bases
    ]
    shadowed = len(unaffiliated) - len(kept_unaffiliated)

    previous_affiliation = {
        (station.get("callsign") or "").strip().upper():
            (station.get("network_affiliation") or "").strip()
        for station in previous
    }

    records = {}
    kept_affiliation = []
    for row in affiliated + kept_unaffiliated:
        callsign = field(row, "callsign").upper()
        if not callsign:
            continue
        virtual = field(row, "tv_virtual_channel")
        affiliation = field(row, "network_affiliation")
        # Never trade a specific network for a vague one. See VAGUE_AFFILIATIONS.
        prior = previous_affiliation.get(callsign, "")
        if is_vague(affiliation) and prior and not is_vague(prior):
            kept_affiliation.append((callsign, prior, affiliation))
            affiliation = prior
        # First record for a callsign wins, matching the loader, which indexes
        # with setdefault so a later record cannot displace an earlier one.
        records.setdefault(callsign, {
            "callsign": callsign,
            "community_served_city": field(row, "community_served_city").upper(),
            "community_served_state": field(row, "community_served_state").upper(),
            "active_ind": field(row, "active_ind").upper() or "N",
            "network_affiliation": affiliation,
            "tv_virtual_channel": virtual if virtual.isdigit() else "",
            "facility_id": field(row, "facility_id"),
            "station_class": field(row, "service_code"),
        })

    # Status of every callsign the dump knows, used to decide whether a station
    # the new selection missed is worth carrying over.
    status_by_callsign = {}
    for row in facility_rows:
        status_by_callsign.setdefault(field(row, "callsign").upper(),
                                      field(row, "facility_status"))

    carried = []
    dropped = []
    for station in previous:
        callsign = (station.get("callsign") or "").strip().upper()
        if not callsign or callsign in records:
            continue
        if status_by_callsign.get(callsign) in CANCELLED_STATUSES:
            # A cancelled licence is not a station. The FCC republishes one
            # with a "D" prepended to its callsign, and 42 such records were in
            # the previous file, which no provider stream can ever name. They
            # are dropped rather than carried, and listed in the report.
            dropped.append(callsign)
            continue
        carried_record = {key: station.get(key, "") for key in OUTPUT_KEYS}
        carried_record["callsign"] = callsign
        carried_record.setdefault("station_class", "")
        carried_record["carried_over"] = (
            f"Kept from the previous station table on {today}. The FCC LMS dump does "
            "not list this callsign as a licensed television facility carrying "
            "a network. Removing it would drop a station that was matching."
        )
        records[callsign] = carried_record
        carried.append(callsign)

    # Applied last, so a correction reaches a carried over record too.
    corrected, correction_skipped, correction_unmatched = apply_corrections(
        records, corrections)

    report = {
        "corrections_applied": corrected,
        "corrections_skipped": correction_skipped,
        "corrections_unmatched": correction_unmatched,
        "licensed_tv_records": len(licensed),
        "with_affiliation": len(affiliated),
        "without_affiliation": len(unaffiliated),
        "unaffiliated_kept": len(kept_unaffiliated),
        "unaffiliated_shadowed_by_an_affiliate": shadowed,
        "carried_over": carried,
        "dropped_as_cancelled": dropped,
        "kept_affiliation": kept_affiliation,
    }
    return [records[key] for key in sorted(records)], report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("facility", help="path to facility.dat from the LMS dump")
    parser.add_argument("--out", default=None,
                        help="output path (default: Channel-Maparr/networks.json "
                             "beside this script's repository root)")
    parser.add_argument("--date", default=None,
                        help="date recorded on carried over records, as YYYY-MM-DD. "
                             "Required so a rebuild is reproducible.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the counts and write nothing")
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    out_path = pathlib.Path(args.out) if args.out else repo_root / "Channel-Maparr" / "networks.json"

    if not args.date:
        parser.error("--date is required, so the carried over records say when they were kept")

    previous = []
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))

    header, rows, malformed = read_facility_dat(args.facility)
    # Corrections for stations the FCC data records wrongly. The file lives
    # beside this script rather than in the plugin, because it is a build
    # input: the shipped table carries the corrected value, so the runtime
    # loader needs to know nothing about it.
    corrections_path = pathlib.Path(__file__).resolve().parent / CORRECTIONS_FILE
    corrections = []
    if corrections_path.exists():
        corrections = json.loads(corrections_path.read_text(encoding="utf-8"))

    records, report = build_records(rows, previous, args.date, corrections)

    before = {station["callsign"].strip().upper() for station in previous}
    after = {station["callsign"] for station in records}
    added = sorted(after - before)
    removed = sorted(before - after)

    print(f"columns read:                       {len(header):6d}")
    print(f"rows read:                          {len(rows):6d}")
    print(f"rows malformed and skipped:         {malformed:6d}")
    print(f"licensed television records:        {report['licensed_tv_records']:6d}")
    print(f"  naming a network:                 {report['with_affiliation']:6d}")
    print(f"  naming none, kept:                {report['unaffiliated_kept']:6d}")
    print(f"  naming none, skipped as a shadow: {report['unaffiliated_shadowed_by_an_affiliate']:6d}")
    print(f"carried over from the old file:     {len(report['carried_over']):6d}")
    print(f"dropped, licence cancelled:         {len(report['dropped_as_cancelled']):6d}")
    print(f"affiliation kept from the old file: {len(report['kept_affiliation']):6d}")
    print(f"corrections applied:                {len(report['corrections_applied']):6d}")
    print(f"corrections skipped, value moved:   {len(report['corrections_skipped']):6d}")
    print(f"corrections naming no station:      {len(report['corrections_unmatched']):6d}")
    print(f"records written:                    {len(records):6d}")
    print(f"stations added:                     {len(added):6d}")
    print(f"stations removed:                   {len(removed):6d}")
    if removed:
        print("REMOVED: {}".format(", ".join(removed[:40])))
    for callsign, prior, vague in sorted(report["kept_affiliation"]):
        print(f"   KEPT {callsign:<9} {prior[:24]:<24} rather than {vague!r}")
    if report["dropped_as_cancelled"]:
        print("DROPPED AS CANCELLED: {}".format(", ".join(sorted(report["dropped_as_cancelled"])[:50])))
    if report["carried_over"]:
        print("CARRIED OVER: {}".format(", ".join(sorted(report["carried_over"])[:40])))

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    text = json.dumps(records, indent=1, ensure_ascii=False) + "\n"
    out_path.write_text(text, encoding="utf-8", newline="\n")
    written_bytes = len(text.encode("utf-8"))
    print(f"\nwrote {out_path} ({written_bytes} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
