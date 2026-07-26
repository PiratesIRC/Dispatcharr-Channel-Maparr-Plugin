"""Action-level scope behaviour."""

from unittest.mock import MagicMock

import pytest

GROUPS = [{"id": 10, "name": "Sports"}, {"id": 20, "name": "News"}]


@pytest.fixture
def fake_logo(monkeypatch, plugin_module):
    """Install a fake Logo.objects with one matching entry.

    apply_logos_action looks up settings["default_logo"] against Logo.objects
    BEFORE it resolves the group filter, so without a matching entry the test
    never reaches the guard under test (it hits the pre-existing "Logo ... not
    found" early return instead). Not needed by apply_tv_logos_action (its
    Logo.objects.all() call happens after the group guard), but harmless there.
    """
    logo = MagicMock()
    logo.objects.all.return_value.values.return_value = [{"id": 1, "name": "MyLogo"}]
    monkeypatch.setattr(plugin_module, "Logo", logo)


@pytest.mark.parametrize("action_name", [
    "apply_logos_action",
    "apply_tv_logos_action",
])
def test_logo_actions_refuse_an_unresolvable_include_filter(
        plugin_instance, logger, fake_channel, fake_groups, fake_logo, action_name):
    """A typo in Channel Groups to Process must refuse, not silently no-op.

    Before bug-044 it applied logos to EVERY channel; after the _get_all_channels
    fix it silently did nothing. Neither is acceptable feedback.
    """
    fake_groups(GROUPS)
    action = getattr(plugin_instance, action_name)
    result = action({
        "selected_groups": "Sprots",
        "channel_databases": "US",
        "default_logo": "MyLogo",
    }, logger)
    assert result["status"] == "error"
    assert "Sprots" in result["error"]


GROUPS_WITH_TEAMARR = [
    {"id": 10, "name": "Sports"},
    {"id": 20, "name": "News"},
    {"id": 30, "name": "Teamarr"},
]


def test_resolve_process_scope_subtracts_the_exclusion(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    scope = plugin_instance._resolve_process_scope(
        {"ignore_groups": "Teamarr"}, logger)
    assert scope.group_ids == frozenset({10, 20})
    assert scope.include_ungrouped is True


def test_resolve_process_scope_reads_selected_groups_not_category_groups(
        plugin_instance, logger, fake_groups):
    """Pins WHICH key the wrapper reads: a decoy under category_groups must be
    ignored. Without the decoy, swapping the two wrappers would go unnoticed."""
    fake_groups(GROUPS_WITH_TEAMARR)
    scope = plugin_instance._resolve_process_scope(
        {"selected_groups": "Sports, News",
         "category_groups": "Teamarr",          # decoy - must be ignored here
         "ignore_groups": "Teamarr"}, logger)
    assert scope.group_ids == frozenset({10, 20})
    assert scope.include_ungrouped is False     # an include filter WAS applied


def test_resolve_category_scope_reads_the_category_include_field(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    scope = plugin_instance._resolve_category_scope(
        {"category_groups": "Sports, News", "ignore_groups": "News"}, logger)
    assert scope.group_ids == frozenset({10})


def test_unknown_include_key_is_rejected_loudly(plugin_instance, logger, fake_groups):
    """A stringly-typed key that degrades to settings.get(...) == '' would mean
    'all groups' - the same silent-widening family as bug-044."""
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(ValueError):
        plugin_instance._resolve_group_scope({}, logger, "catagory_groups")


def test_scope_error_return_is_visible(plugin_instance, plugin_module):
    exc = plugin_module.GroupScopeError("nope")
    result = plugin_instance._scope_error_return(exc)
    assert result["status"] == "error"
    assert result["error"] == "nope"
    assert "message" not in result
