"""Action-level scope behaviour."""

import json
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


# ---------------------------------------------------------------------------
# Site-to-wrapper mapping pins.
#
# Each test puts an UNRESOLVABLE name under the key the action SHOULD read and
# a RESOLVABLE decoy under the other scope key. If a site were wired to the
# wrong wrapper it would resolve happily against the decoy and return success
# instead of refusing - the same defect shape already caught for the wrappers
# themselves. `channel_databases: US` is required so `_load_channel_data`
# succeeds and the action reaches the resolver at all (it loads the real local
# US JSON database - no network, no DB).
# ---------------------------------------------------------------------------


def test_load_and_process_reads_selected_groups_not_category_groups(
        plugin_instance, logger, fake_channel, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.load_and_process_channels_action({
        "selected_groups": "Nope",       # unresolvable under the CORRECT key
        "category_groups": "Sports",     # decoy: resolvable, must be ignored here
        "channel_databases": "US",
    }, logger)
    assert result["status"] == "error"
    assert "Nope" in result["error"]


def test_category_dry_run_reads_category_groups_not_selected_groups(
        plugin_instance, logger, fake_channel, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.category_groups_dry_run_action({
        "category_groups": "Nope",       # unresolvable under the CORRECT key
        "selected_groups": "Sports",     # decoy: resolvable, must be ignored here
        "channel_databases": "US",
    }, logger)
    assert result["status"] == "error"
    assert "Nope" in result["error"]


def test_organize_by_category_reads_category_groups_not_selected_groups(
        plugin_instance, logger, fake_channel, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.organize_by_category_action({
        "category_groups": "Nope",       # unresolvable under the CORRECT key
        "selected_groups": "Sports",     # decoy: resolvable, must be ignored here
        "channel_databases": "US",
    }, logger)
    assert result["status"] == "error"
    assert "Nope" in result["error"]


def _results_file(tmp_path, changes):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    return str(path)


def test_rename_skips_rows_in_an_ignored_group(
        plugin_instance, logger, tmp_path, monkeypatch):
    """The stale-file case: the results file predates the ignore setting."""
    changes = [
        {"channel_id": 1, "current_name": "a", "new_name": "A",
         "status": "Renamed", "channel_group": "Sports"},
        {"channel_id": 2, "current_name": "b", "new_name": "B",
         "status": "Renamed", "channel_group": "Teamarr"},
    ]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    captured = []
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)

    result = plugin_instance.rename_channels_action(
        {"dry_run_mode": False, "ignore_groups": "Teamarr"}, logger)

    assert [u["id"] for u in captured] == [1]
    assert result["status"] == "success"
    assert "1" in result["message"]


def test_tag_unknown_skips_rows_in_an_ignored_group(
        plugin_instance, logger, tmp_path, monkeypatch):
    changes = [
        {"channel_id": 1, "current_name": "a", "status": "Skipped",
         "channel_group": "Sports"},
        {"channel_id": 2, "current_name": "b", "status": "Skipped",
         "channel_group": "Teamarr"},
    ]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    captured = []
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)

    plugin_instance.rename_unknown_channels_action(
        {"unknown_suffix": " [Unk]", "ignore_groups": "Teamarr"}, logger)

    assert [u["id"] for u in captured] == [1]


def test_rename_reports_when_everything_was_ignored(
        plugin_instance, logger, tmp_path, monkeypatch):
    changes = [{"channel_id": 2, "current_name": "b", "new_name": "B",
                "status": "Renamed", "channel_group": "Teamarr"}]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda *a, **k: pytest.fail("must not write"))

    result = plugin_instance.rename_channels_action(
        {"dry_run_mode": False, "ignore_groups": "Teamarr"}, logger)
    assert result["status"] == "success"
    assert "ignored" in result["message"].lower()
