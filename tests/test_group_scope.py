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
