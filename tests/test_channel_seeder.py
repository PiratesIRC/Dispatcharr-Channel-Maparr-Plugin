"""Planning which channels to create from provider streams that have none.

One channel per station, not one per stream. This provider carries the same
station once per M3U account, so four stream rows share one name, and the
layout on this installation is one channel holding all four. The existing
Import M3U Streams action cannot do this: it creates one channel per stream.
"""
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

from channel_seeder import (  # noqa: E402
    allocate_channel_numbers,
    build_seed_plan,
)


def _stream(sid, name, account):
    return {"id": sid, "name": name, "m3u_account_id": account}


STREAMS = [
    _stream(1, "US: ABC (WABC)", 6),
    _stream(2, "US: ABC (WABC)", 7),
    _stream(3, "US: ABC 7 NEW YORK", 6),
    _stream(4, "US: ABC (KGO) SAN FRANCISCO", 6),
    _stream(5, "US: ABC NEWS LIVE HD", 6),
]

RESOLVED = {
    "US: ABC (WABC)": "ABC - NY New York (WABC)",
    "US: ABC 7 NEW YORK": "ABC - NY New York (WABC)",
    "US: ABC (KGO) SAN FRANCISCO": "ABC - CA San Francisco (KGO)",
}


def resolve(name):
    return RESOLVED.get(name)


# --- Planning ---------------------------------------------------------------

def test_one_item_per_station_not_per_stream():
    plan = build_seed_plan(STREAMS, existing_names=[], resolve=resolve)
    names = [item.proposed_name for item in plan.create]
    assert names == ["ABC - CA San Francisco (KGO)", "ABC - NY New York (WABC)"]


def test_every_source_stream_is_recorded_on_its_item():
    plan = build_seed_plan(STREAMS, existing_names=[], resolve=resolve)
    item = next(i for i in plan.create if i.proposed_name.endswith("(WABC)"))
    assert sorted(item.source_names) == ["US: ABC (WABC)", "US: ABC 7 NEW YORK"]
    assert sorted(item.stream_ids) == [1, 2, 3]
    assert sorted(item.accounts) == [6, 6, 7]


def test_an_existing_name_is_skipped_not_created():
    plan = build_seed_plan(
        STREAMS,
        existing_names=["abc - ny new york (wabc)"],
        resolve=resolve,
    )
    assert [i.proposed_name for i in plan.create] == ["ABC - CA San Francisco (KGO)"]
    assert [i.proposed_name for i in plan.skip] == ["ABC - NY New York (WABC)"]


def test_name_comparison_ignores_case_and_surrounding_space():
    plan = build_seed_plan(STREAMS,
                           existing_names=["  ABC - CA SAN FRANCISCO (KGO) "],
                           resolve=resolve)
    assert "ABC - CA San Francisco (KGO)" in [i.proposed_name for i in plan.skip]


def test_an_unresolved_stream_is_reported_rather_than_dropped():
    plan = build_seed_plan(STREAMS, existing_names=[], resolve=resolve)
    assert [i.source_names for i in plan.unresolved] == [["US: ABC NEWS LIVE HD"]]
    assert plan.unresolved[0].proposed_name is None


def test_the_plan_does_not_depend_on_the_order_the_rows_arrive_in():
    first = build_seed_plan(STREAMS, existing_names=[], resolve=resolve)
    second = build_seed_plan(list(reversed(STREAMS)), existing_names=[], resolve=resolve)
    assert ([i.proposed_name for i in first.create]
            == [i.proposed_name for i in second.create])


def test_empty_input_produces_an_empty_plan():
    plan = build_seed_plan([], existing_names=[], resolve=resolve)
    assert plan.create == [] and plan.skip == [] and plan.unresolved == []


def test_a_stream_with_a_blank_name_is_ignored():
    plan = build_seed_plan([_stream(9, "   ", 6)], existing_names=[], resolve=resolve)
    assert plan.create == [] and plan.skip == [] and plan.unresolved == []


# --- Channel number allocation ----------------------------------------------

def test_allocation_starts_above_the_highest_number_in_use():
    assert allocate_channel_numbers(used=[1.0, 5109.0], count=3) == [5110.0, 5111.0, 5112.0]


def test_allocation_honours_an_explicit_start():
    assert allocate_channel_numbers(used=[1.0], count=2, start=3858.0) == [3858.0, 3859.0]


def test_allocation_skips_numbers_already_in_use():
    got = allocate_channel_numbers(used=[100.0, 101.0, 103.0], count=3, start=100.0)
    assert got == [102.0, 104.0, 105.0]


def test_allocation_of_zero_returns_nothing():
    assert allocate_channel_numbers(used=[1.0], count=0) == []


def test_allocation_with_no_numbers_in_use_starts_at_one():
    assert allocate_channel_numbers(used=[], count=2) == [1.0, 2.0]


def test_allocation_never_repeats_a_number():
    got = allocate_channel_numbers(used=[10.0, 12.0], count=50, start=10.0)
    assert len(set(got)) == 50
    assert not set(got) & {10.0, 12.0}


def test_allocation_ignores_a_null_in_the_used_list():
    """A channel row may carry no number, and that is not a number in use."""
    assert allocate_channel_numbers(used=[None, 4.0], count=1) == [5.0]
