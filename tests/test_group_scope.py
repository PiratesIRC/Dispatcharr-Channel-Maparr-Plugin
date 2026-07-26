"""Pure scope resolution. Zero mocks - this module never imports Django.

The behaviour table this pins is spec section 4 of
docs/superpowers/specs/2026-07-26-ignore-groups-design.md
"""
import importlib.util
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


@pytest.fixture(scope="module")
def gs():
    """Load group_scope.py directly. It imports wildcard_match as a sibling, so
    the plugin dir goes on sys.path rather than loading it as a package."""
    import sys
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "group_scope_under_test", PLUGIN_DIR / "group_scope.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(PLUGIN_DIR))


GROUP_ROWS = [
    {"id": 10, "name": "Sports"},
    {"id": 20, "name": "News"},
    {"id": 30, "name": "Teamarr"},
    {"id": 40, "name": "Teamarr Live"},
]


@pytest.fixture
def names(gs):
    return gs.build_name_to_ids(GROUP_ROWS)


def resolve(gs, names, include="", ignore=""):
    return gs.resolve_group_scope(
        include, ignore, names, include_label="Channel Groups to Process")


# --- parse_tokens ---------------------------------------------------------

def test_parse_splits_on_commas_and_newlines(gs):
    assert gs.parse_tokens("Sports, News\nTeamarr") == ["Sports", "News", "Teamarr"]


def test_parse_drops_empty_and_whitespace_tokens(gs):
    """A stray comma must not become 'an ignore list that matched nothing',
    which under fail-closed would hard-error every action in the plugin."""
    assert gs.parse_tokens(" , ") == []
    assert gs.parse_tokens(",") == []
    assert gs.parse_tokens("\n") == []
    assert gs.parse_tokens("") == []
    assert gs.parse_tokens(None) == []


# --- build_name_to_ids ---------------------------------------------------

def test_duplicate_group_names_keep_both_ids(gs):
    """A scalar name->id map silently drops one of two same-named groups, which
    would leave one unprotected by an exclusion naming it."""
    mapping = gs.build_name_to_ids(
        [{"id": 98, "name": "Teamarr"}, {"id": 99, "name": "Teamarr"}])
    assert mapping["Teamarr"] == {98, 99}


def test_rows_missing_name_or_id_are_skipped(gs):
    mapping = gs.build_name_to_ids(
        [{"id": 1}, {"name": "Orphan"}, {"id": 2, "name": "Good"}])
    assert mapping == {"Good": {2}}


# --- include only --------------------------------------------------------

def test_blank_include_selects_every_group_and_ungrouped(gs, names):
    scope = resolve(gs, names)
    assert scope.group_ids == frozenset({10, 20, 30, 40})
    assert scope.include_ungrouped is True


def test_include_is_exact_and_case_sensitive(gs, names):
    scope = resolve(gs, names, include="Sports")
    assert scope.group_ids == frozenset({10})
    assert scope.include_ungrouped is False
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, include="sports")


# --- the reporter's case -------------------------------------------------

def test_ignore_only_processes_everything_else(gs, names):
    scope = resolve(gs, names, ignore="Teamarr")
    assert scope.group_ids == frozenset({10, 20, 40})
    assert scope.ignored_names == ("Teamarr",)
    assert scope.include_ungrouped is True


def test_ignore_is_case_insensitive(gs, names):
    assert resolve(gs, names, ignore="teamarr").group_ids == frozenset({10, 20, 40})


def test_ignore_wildcard_catches_the_family(gs, names):
    scope = resolve(gs, names, ignore="Teamarr*")
    assert scope.group_ids == frozenset({10, 20})
    assert scope.ignored_names == ("Teamarr", "Teamarr Live")


def test_include_and_ignore_compose(gs, names):
    scope = resolve(gs, names, include="Sports, News", ignore="News")
    assert scope.group_ids == frozenset({10})
    assert scope.include_ungrouped is False


# --- the refusal table (spec section 4) ----------------------------------

def test_ignore_token_matching_nothing_refuses(gs, names):
    """A typo must not degrade to 'process everything' - that is exactly the
    damage the setting exists to prevent, and it would be silent."""
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, ignore="Teamar")
    assert "Teamar" in str(exc.value)


def test_partial_match_still_refuses(gs, names):
    """One good token does not license a bad one: a group renamed out from under
    a stale entry would leave it silently unprotected."""
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, ignore="Teamarr, Teamar")
    assert "Teamar" in str(exc.value)
    assert "Teamarr," not in str(exc.value)      # only the unmatched one is named


def test_ignoring_a_group_outside_the_include_scope_is_a_no_op(gs, names):
    """Teamarr is real but not selected. Not a typo, so not an error."""
    scope = resolve(gs, names, include="Sports", ignore="Teamarr")
    assert scope.group_ids == frozenset({10})
    assert scope.out_of_scope_names == ("Teamarr",)


def test_exclusion_emptying_the_target_refuses(gs, names):
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, include="Sports", ignore="Sport*")
    msg = str(exc.value)
    assert "excluded every group" in msg
    # Must be distinguishable from "your include list matched nothing" - they
    # call for different user actions.
    assert "could not be found" not in msg


def test_bare_star_refuses_via_the_empty_set_rule(gs, names):
    """Not special-cased; caught by the empty-target rule, with or without an
    include filter."""
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, ignore="*")
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, include="Sports, News", ignore="*")


def test_ignore_with_no_groups_at_all_has_its_own_message(gs):
    with pytest.raises(gs.GroupScopeError) as exc:
        gs.resolve_group_scope("", "Teamarr", {}, include_label="Channel Groups to Process")
    assert "no channel groups" in str(exc.value)


def test_whitespace_only_ignore_is_treated_as_blank(gs, names):
    """The stray-comma outage case, end to end."""
    scope = resolve(gs, names, ignore=" , ")
    assert scope.group_ids == frozenset({10, 20, 30, 40})
    assert scope.ignored_names == ()


def test_null_settings_values_do_not_raise(gs, names):
    scope = gs.resolve_group_scope(
        None, None, names, include_label="Channel Groups to Process")
    assert scope.group_ids == frozenset({10, 20, 30, 40})


def test_duplicate_group_names_are_both_excluded(gs):
    names = gs.build_name_to_ids(
        [{"id": 98, "name": "Teamarr"}, {"id": 99, "name": "Teamarr"},
         {"id": 10, "name": "Sports"}])
    scope = gs.resolve_group_scope(
        "", "Teamarr", names, include_label="Channel Groups to Process")
    assert scope.group_ids == frozenset({10})       # BOTH 98 and 99 removed


# --- documented limits (pinned, deliberately not fixed) ------------------

def test_bracket_is_not_treated_as_a_glob(gs):
    """The glob probe is *? only, so [ ] stay literal. Pinned so a future
    'improvement' to the probe has to face this test."""
    names = gs.build_name_to_ids(
        [{"id": 1, "name": "News [US]"}, {"id": 2, "name": "Sports"}])
    scope = gs.resolve_group_scope(
        "", "News [US]", names, include_label="Channel Groups to Process")
    assert scope.ignored_names == ("News [US]",)
    assert scope.group_ids == frozenset({2})      # Sports survives; scope not emptied


def test_lower_not_casefold_is_a_documented_limit(gs):
    """expand_patterns uses .lower(), not .casefold(), so Turkish dotted I and
    German sharp s do not fold. Changing it would break the byte-identical copy
    of EPG-Janitor's helper and its provenance pin, so it stays a known limit."""
    names = gs.build_name_to_ids(
        [{"id": 1, "name": "ISTANBUL TV"}, {"id": 2, "name": "Sports"}])
    with pytest.raises(gs.GroupScopeError) as exc:
        gs.resolve_group_scope(
            "", "İSTANBUL TV", names,      # dotted capital I
            include_label="Channel Groups to Process")
    assert "İSTANBUL TV" in str(exc.value)     # row 1: the token matched nothing
    assert "excluded every group" not in str(exc.value)   # NOT row 4


def test_accented_names_match_case_insensitively(gs):
    """The half that DOES work, so the .lower() limit above is understood as narrow."""
    names = gs.build_name_to_ids(
        [{"id": 1, "name": "TÉLÉ QUÉBEC"}, {"id": 2, "name": "Sports"}])
    scope = gs.resolve_group_scope(
        "", "télé québec", names, include_label="Channel Groups to Process")
    assert scope.ignored_names == ("TÉLÉ QUÉBEC",)
    assert scope.group_ids == frozenset({2})


# --- results-file row filtering ------------------------------------------

ROWS = [
    {"channel_id": 1, "channel_group": "Sports"},
    {"channel_id": 2, "channel_group": "Teamarr"},
    {"channel_id": 3, "channel_group": "Teamarr Live"},
    {"channel_id": 4, "channel_group": "No Group"},
]


def test_split_rows_by_ignore_drops_matching_rows(gs):
    kept, dropped = gs.split_rows_by_ignore(ROWS, "Teamarr*")
    assert [r["channel_id"] for r in kept] == [1, 4]
    assert [r["channel_id"] for r in dropped] == [2, 3]


def test_split_rows_by_ignore_is_case_insensitive(gs):
    kept, _ = gs.split_rows_by_ignore(ROWS, "teamarr")
    assert [r["channel_id"] for r in kept] == [1, 3, 4]


def test_split_rows_with_blank_ignore_keeps_everything(gs):
    kept, dropped = gs.split_rows_by_ignore(ROWS, " , ")
    assert len(kept) == 4 and dropped == []


def test_split_rows_matches_names_not_ids(gs):
    """Matching by NAME means a stale file still filters after the group was
    deleted - an id lookup would silently keep the row."""
    rows = [{"channel_id": 9, "channel_group": "Teamarr"}]
    kept, dropped = gs.split_rows_by_ignore(rows, "Teamarr")
    assert kept == [] and len(dropped) == 1


def test_split_rows_tolerates_a_missing_group_key(gs):
    kept, dropped = gs.split_rows_by_ignore([{"channel_id": 9}], "Teamarr")
    assert len(kept) == 1 and dropped == []
