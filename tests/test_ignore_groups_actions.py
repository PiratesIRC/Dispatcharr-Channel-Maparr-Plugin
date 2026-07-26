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
    assert "ignored groups" in result["message"]


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


def test_tag_unknown_reports_when_everything_was_ignored(
        plugin_instance, logger, tmp_path, monkeypatch):
    """Clone of the rename-side all-ignored test: this branch in
    rename_unknown_channels_action is otherwise executed by no test."""
    changes = [{"channel_id": 2, "current_name": "b", "status": "Skipped",
                "channel_group": "Teamarr"}]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda *a, **k: pytest.fail("must not write"))

    result = plugin_instance.rename_unknown_channels_action(
        {"unknown_suffix": " [Unk]", "ignore_groups": "Teamarr"}, logger)
    assert result["status"] == "success"
    assert "ignored" in result["message"].lower()


def test_import_refuses_a_custom_group_that_is_ignored(
        plugin_instance, logger, fake_groups):
    """Import is not group-scoped, but it must not write INTO an ignored group."""
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(Exception) as exc:
        plugin_instance._ensure_category_groups_exist(
            ["Sports"],
            {"m3u_custom_group_name": "Teamarr", "ignore_groups": "Teamarr"},
            logger)
    assert "Teamarr" in str(exc.value)


def test_import_refuses_a_category_group_that_is_ignored(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(Exception) as exc:
        plugin_instance._ensure_category_groups_exist(
            ["Teamarr"], {"ignore_groups": "Teamarr*"}, logger)
    assert "Teamarr" in str(exc.value)


def test_import_proceeds_when_nothing_is_ignored(
        plugin_instance, logger, fake_groups, monkeypatch):
    fake_groups(GROUPS_WITH_TEAMARR)
    monkeypatch.setattr(plugin_instance, "_get_or_create_group",
                        lambda name, lg: type("G", (), {"id": 77})())
    mapping = plugin_instance._ensure_category_groups_exist(
        ["Sports"], {"ignore_groups": "Teamarr"}, logger)
    assert mapping == {"Sports": 10}


def test_import_action_refuses_an_ignored_custom_group_before_backgrounding(
        plugin_instance, logger, fake_groups, monkeypatch):
    """The real import runs in a background thread whose result the card never
    shows, so this must be caught in the action body BEFORE _try_start_thread."""
    fake_groups(GROUPS_WITH_TEAMARR)
    monkeypatch.setattr(plugin_instance, "_try_start_thread",
                        lambda *a, **k: pytest.fail("must not start the thread"))

    result = plugin_instance.import_m3u_streams_action(
        {"m3u_custom_group_name": "Teamarr", "ignore_groups": "Teamarr",
         "dry_run_mode": False},
        logger)

    assert result["status"] == "error"
    assert "Teamarr" in result["error"]
    assert "message" not in result


def test_format_capped_name_list_caps_at_five_and_summarizes_the_rest(plugin_module):
    names = [f"Group{i}" for i in range(8)]
    assert (plugin_module._format_capped_name_list(names) ==
            "Group0, Group1, Group2, Group3, Group4 and 3 more")


def test_format_capped_name_list_does_not_summarize_within_the_cap(plugin_module):
    assert plugin_module._format_capped_name_list(["A", "B"]) == "A, B"


def test_import_refusal_message_is_capped_for_many_ignored_categories(
        plugin_instance, logger, fake_groups):
    """Dispatcharr clips action toasts at ~280 chars from the MIDDLE with no
    visual marker - a wildcard ignore token matching many categories (e.g.
    "Sport*") must not dump an unbounded name list into the error."""
    fake_groups([{"id": i, "name": f"Sport{i}"} for i in range(10)])
    categories = [f"Sport{i}" for i in range(10)]
    with pytest.raises(Exception) as exc:
        plugin_instance._ensure_category_groups_exist(
            categories, {"ignore_groups": "Sport*"}, logger)
    msg = str(exc.value)
    assert "Sport0" in msg
    assert "and 5 more" in msg
    assert "Sport9" not in msg


def test_validate_settings_reports_a_resolved_exclusion(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr"}, logger)
    body = result.get("message") or result.get("error")
    assert "Ignore" in body and "Teamarr" in body


def test_validate_settings_flags_an_ignore_typo_as_a_red_error(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "ignore_groups": "Teamar"}, logger)
    assert result["status"] == "error"
    assert result.get("error"), "an ignore typo rendered as a green toast"
    assert "Teamar" in result["error"]
    assert "message" not in result


def test_validate_settings_reports_an_out_of_scope_ignore_as_a_warning(
        plugin_instance, logger, fake_groups):
    """A real group name that the ignore filter matched but the include
    filter had already excluded is a no-op, not a typo - it must render as
    a warning (still a green/success toast), never a red error."""
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "selected_groups": "Sports",
         "ignore_groups": "News"}, logger)
    assert result["status"] == "success"
    assert "News" in result["message"]
    assert "no effect" in result["message"]


def test_validate_settings_labels_an_include_filter_typo_correctly(
        plugin_instance, logger, fake_groups):
    """A typo in selected_groups (blank ignore_groups) must be attributed to
    'Channel Groups to Process', not mislabelled as an Ignore problem - both
    settings raise the SAME GroupScopeError, so the generic 'Ignore:' prefix
    the ignore branch uses would misdirect the operator to the wrong field."""
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "selected_groups": "Sprots"}, logger)
    assert result["status"] == "error"
    assert "Channel Groups to Process" in result["error"]
    assert "Ignore" not in result["error"]


def test_validate_settings_caps_many_out_of_scope_names(
        plugin_instance, logger, fake_groups):
    """Many out-of-scope names must collapse to one capped line, not one
    line per name - otherwise a broad wildcard blows the ~280 char toast
    budget Dispatcharr clips from the middle with no visual marker."""
    groups = [{"id": i, "name": f"Sport{i}"} for i in range(8)]
    groups.append({"id": 100, "name": "Keep"})
    fake_groups(groups)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "selected_groups": "Keep",
         "ignore_groups": "Sport*"}, logger)
    assert result["status"] == "success"
    assert "and 3 more" in result["message"]
    assert "Sport7" not in result["message"]


def test_csv_header_records_the_exclusion(plugin_instance):
    header = plugin_instance._generate_csv_settings_header(
        {"ignore_groups": "Teamarr", "channel_databases": "US"})
    assert "Channel Groups to Ignore: Teamarr" in header


def test_load_and_process_status_summary_reports_the_scope(
        plugin_instance, logger, fake_channel, fake_groups, monkeypatch, plugin_module):
    """Show Status (progress.finish's persisted summary) must say what scope
    the completed run applied, not just how many channels were touched."""
    fake_groups(GROUPS_WITH_TEAMARR)
    captured = {}
    monkeypatch.setattr(
        plugin_module.ProgressTracker, "finish",
        lambda self, summary=None: captured.__setitem__("summary", summary))

    result = plugin_instance.load_and_process_channels_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr"}, logger)

    assert result["status"] == "success"
    assert "Teamarr" in captured["summary"]


def test_import_dry_run_refuses_an_ignored_custom_group(
        plugin_instance, logger, fake_groups, monkeypatch):
    """The preview must refuse rather than silently show a plan that the real
    (non-dry-run) import would reject."""
    fake_groups(GROUPS_WITH_TEAMARR)
    monkeypatch.setattr(
        plugin_instance, "_fetch_streams_from_m3u_sources",
        lambda *a, **k: [{"id": 1, "name": "ESPN", "m3u_account": 1, "channel_group": None}])
    monkeypatch.setattr(
        plugin_instance, "_match_streams_to_categories",
        lambda *a, **k: ({"Sports": [{"stream": {"id": 1, "name": "ESPN"}}]}, []))

    result = plugin_instance.import_m3u_streams_dry_run_action(
        {"m3u_custom_group_name": "Teamarr", "ignore_groups": "Teamarr"}, logger)

    assert result["status"] == "error"
    assert "Teamarr" in result["error"]


def _seed_matcher_for_teamarr_category(plugin_instance, monkeypatch):
    """Seed the matcher (the real producer of channel -> category, since
    there is no _build_category_mapping helper) so the channel named
    "Orphan" (id 3, CHANNEL_ROWS via fake_channel) maps to category
    "Teamarr" through an EXACT premium-name match. "Orphan" is used
    (rather than the shorter "A"/"B" rows) because _get_cached_norm/
    normalize_name discard anything that normalizes to under 2 chars, which
    a single letter does - "Orphan" survives normalization unchanged and its
    lowercase form is exactly the category_map_premium key built from
    premium_channels_full, so the category branch is reached deterministically
    rather than via fuzzy scoring.

    _load_channel_data is stubbed to a no-op: both organize_by_category_action
    and category_groups_dry_run_action call it FIRST, and it calls
    matcher.reload_databases(...), which loads the real US JSON and overwrites
    whatever is seeded here if allowed to run (proven live: without this stub
    the seeded premium_channels_full is silently replaced and the category
    branch is never reached).
    """
    monkeypatch.setattr(plugin_instance, "_load_channel_data", lambda *a, **k: True)
    monkeypatch.setattr(plugin_instance.matcher, "broadcast_channels", [])
    monkeypatch.setattr(
        plugin_instance.matcher, "premium_channels_full",
        [{"channel_name": "Orphan", "category": "Teamarr"}])
    monkeypatch.setattr(plugin_instance.matcher, "premium_channels", ["Orphan"])


def test_organize_reaches_the_seeded_category_without_the_guard(
        plugin_instance, logger, fake_groups, fake_channel, monkeypatch):
    """Sanity check for the guard test below: proves the seeded matcher really
    does route the "Orphan" channel (id 3) to the Teamarr group (id 30) when
    nothing is ignored, so the guard test is known to exercise the real
    category-write path rather than passing because nothing ever matched."""
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr"}])
    _seed_matcher_for_teamarr_category(plugin_instance, monkeypatch)
    captured_updates = []
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured_updates.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh", lambda *a, **k: None)

    result = plugin_instance.organize_by_category_action(
        {"channel_databases": "US", "dry_run_mode": False}, logger)

    assert any(u["id"] == 3 and u["channel_group_id"] == 30 for u in captured_updates)
    assert result["status"] == "success"


def test_organize_never_creates_or_fills_an_ignored_group(
        plugin_instance, logger, fake_groups, fake_channel, monkeypatch):
    """ignore_groups must block the write direction too: a channel-database
    category whose name is ignored must not be created or filled - proven
    against the same seeded scenario the sanity check above confirms reaches
    the category-write path.

    NOTE: because "Teamarr" already exists as a group here (id 30),
    `new_group_name not in group_name_to_id` is False and _get_or_create_group
    is never called for it either WITH or WITHOUT the guard - so
    `"Teamarr" not in created` alone is vacuous for the "would CREATE it" claim.
    This test only covers the ADOPT-an-existing-group direction (the
    `all(u["id"] != 3 ...)` assertion); the create-a-brand-new-group direction
    is covered separately below by
    test_organize_never_creates_an_ignored_group_that_does_not_exist_yet,
    which uses a category name that does NOT pre-exist as a group."""
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr"}])
    _seed_matcher_for_teamarr_category(plugin_instance, monkeypatch)

    created = []
    captured_updates = []
    monkeypatch.setattr(
        plugin_instance, "_get_or_create_group",
        lambda name, lg: created.append(name) or type("G", (), {"id": 99})())
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured_updates.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh", lambda *a, **k: None)

    result = plugin_instance.organize_by_category_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr",
         "dry_run_mode": False},
        logger)

    assert "Teamarr" not in created
    assert all(u["id"] != 3 for u in captured_updates)
    assert result["status"] == "success"
    assert "Skipped" in result["message"]
    assert "Teamarr" in result["message"]


def test_organize_never_creates_an_ignored_group_that_does_not_exist_yet(
        plugin_instance, logger, fake_groups, fake_channel, monkeypatch):
    """The create-path variant of the guard test above, using a category name
    that does NOT already exist as a group, so _get_or_create_group would
    genuinely be called for it absent the guard.

    A WILDCARD is required to reach this scenario at all: _resolve_category_scope
    runs first and refuses the whole action when an ignore token matches no
    existing group, so a bare "Teamarr" token could never reach the loop while
    the category "Teamarr" itself doesn't exist as a group yet. "Teamarr Live"
    is a real, pre-existing group so "Teamarr*" resolves the scope check; the
    channel-database category "Teamarr" (distinct from the group "Teamarr
    Live") does not exist as a group, so absent the guard it would be CREATED.
    """
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr Live"}])
    _seed_matcher_for_teamarr_category(plugin_instance, monkeypatch)
    created = []
    monkeypatch.setattr(
        plugin_instance, "_get_or_create_group",
        lambda name, lg: created.append(name) or type("G", (), {"id": 99})())
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels", lambda *a, **k: None)
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh", lambda *a, **k: None)

    result = plugin_instance.organize_by_category_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr*", "dry_run_mode": False},
        logger)

    assert created == []
    assert "Skipped" in result["message"]


def test_organize_creates_the_category_group_when_nothing_is_ignored(
        plugin_instance, logger, fake_groups, fake_channel, monkeypatch):
    """Control for the test above: the IDENTICAL setup with ignore_groups
    removed must actually create the "Teamarr" group. Without this control,
    `created == []` in the guard test could just as easily mean the seeded
    category was never reached at all (as happened during development, when
    _load_channel_data silently overwrote the seeded matcher and every
    "created == []" assertion passed for the wrong reason)."""
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr Live"}])
    _seed_matcher_for_teamarr_category(plugin_instance, monkeypatch)
    created = []
    monkeypatch.setattr(
        plugin_instance, "_get_or_create_group",
        lambda name, lg: created.append(name) or type("G", (), {"id": 99})())
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels", lambda *a, **k: None)
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh", lambda *a, **k: None)

    result = plugin_instance.organize_by_category_action(
        {"channel_databases": "US", "dry_run_mode": False}, logger)

    assert created == ["Teamarr"]
    assert result["status"] == "success"


class _FakeQuerySet:
    """Local copy of conftest's FakeQuerySet, parameterized by caller-supplied
    rows. conftest's fixed CHANNEL_ROWS names ("A", "B") are too short to
    survive matcher normalization (anything under 2 chars is discarded), so a
    test that needs a SURVIVING (un-ignored) category match alongside an
    ignored one needs its own longer-named rows. conftest is never modified."""

    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def filter(self, **kwargs):
        self.calls.append(kwargs)
        ids = kwargs["channel_group_id__in"]
        kept = [r for r in self.rows if r["channel_group_id"] in ids]
        return _FakeQuerySet(kept, self.calls)

    def values(self, *fields):
        return [{k: r[k] for k in fields} for r in self.rows]


def _install_channel_rows(monkeypatch, plugin_module, rows):
    calls = []
    channel = MagicMock()
    channel.objects.all = lambda: _FakeQuerySet(list(rows), calls)
    monkeypatch.setattr(plugin_module, "Channel", channel)
    return calls


def test_category_dry_run_csv_records_the_resolved_exclusion(
        plugin_instance, logger, fake_groups, monkeypatch, plugin_module, tmp_path):
    """category_groups_dry_run_action already has a resolved `scope` local at
    CSV-write time (no re-parse needed), so its CSV header can record what the
    exclusion actually MATCHED, not just echo the raw setting text."""
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORT_DIR", str(tmp_path))
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr"}])
    monkeypatch.setattr(plugin_instance, "_load_channel_data", lambda *a, **k: True)
    monkeypatch.setattr(plugin_instance.matcher, "broadcast_channels", [])
    monkeypatch.setattr(
        plugin_instance.matcher, "premium_channels_full",
        [{"channel_name": "Orphan", "category": "Sports"}])
    monkeypatch.setattr(plugin_instance.matcher, "premium_channels", ["Orphan"])
    _install_channel_rows(monkeypatch, plugin_module, [
        {"id": 1, "name": "Orphan", "channel_number": 1.0,
         "channel_group_id": None, "logo_id": None},
    ])

    result = plugin_instance.category_groups_dry_run_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr"}, logger)

    assert result["status"] == "success"
    csv_files = list(tmp_path.glob("*.csv"))
    assert csv_files
    text = csv_files[0].read_text(encoding="utf-8")
    assert "Ignore resolved to:" in text
    assert "Teamarr" in text.split("Channel ID,Channel Name")[0]


def test_organize_dry_run_never_previews_an_ignored_target(
        plugin_instance, logger, fake_groups, monkeypatch, plugin_module, tmp_path):
    """category_groups_dry_run_action must match what the real run refuses.

    Seeds a SURVIVING row ("Sportsline" -> category "Sports", an un-ignored,
    already-existing group) alongside the ignored one ("Orphan" -> "Teamarr")
    so a CSV is actually written: with only the ignored row present, every
    move is skipped, `moves` stays empty, and category_groups_dry_run_action
    early-returns BEFORE ever writing a CSV - which would leave any assertion
    about CSV contents dead code that always passes vacuously (confirmed by
    running that variant: csv_files was always []).
    """
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORT_DIR", str(tmp_path))
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr"}])
    monkeypatch.setattr(plugin_instance, "_load_channel_data", lambda *a, **k: True)
    monkeypatch.setattr(plugin_instance.matcher, "broadcast_channels", [])
    monkeypatch.setattr(
        plugin_instance.matcher, "premium_channels_full",
        [{"channel_name": "Orphan", "category": "Teamarr"},
         {"channel_name": "Sportsline", "category": "Sports"}])
    monkeypatch.setattr(
        plugin_instance.matcher, "premium_channels", ["Orphan", "Sportsline"])
    _install_channel_rows(monkeypatch, plugin_module, [
        {"id": 1, "name": "Orphan", "channel_number": 1.0,
         "channel_group_id": None, "logo_id": None},
        {"id": 2, "name": "Sportsline", "channel_number": 2.0,
         "channel_group_id": None, "logo_id": None},
    ])

    result = plugin_instance.category_groups_dry_run_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr"}, logger)

    assert result["status"] == "success"
    assert "Skipped" in result["message"]
    csv_files = list(tmp_path.glob("*.csv"))
    assert csv_files, "a preview CSV must be written for the surviving move"
    text = csv_files[0].read_text(encoding="utf-8")
    # The settings header block echoes the setting "Channel Groups to Ignore:
    # Teamarr" verbatim, so check only the DATA rows (after the CSV column
    # header) for the ignored name/category, not the whole file.
    data_rows = text.split("Channel ID,Channel Name")[-1]
    assert "Orphan" not in data_rows
    assert "Teamarr" not in data_rows
    assert "Sportsline" in data_rows


def test_preview_excludes_ignored_rows_from_csv_and_counts(
        plugin_instance, logger, plugin_module, tmp_path, monkeypatch):
    """Dry run must match what the real run does: without the exclusion in
    preview_changes_action, the CSV and toast both include the Teamarr row
    that the real (non-dry-run) rename would have skipped."""
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORT_DIR", str(tmp_path))
    changes = [
        {"channel_id": 1, "current_name": "a", "new_name": "A",
         "status": "Renamed", "channel_group": "Sports"},
        {"channel_id": 2, "current_name": "b", "new_name": "B",
         "status": "Renamed", "channel_group": "Teamarr"},
    ]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))

    result = plugin_instance.preview_changes_action(
        {"ignore_groups": "Teamarr"}, logger)

    assert result["status"] == "success"
    assert "1 channels will be renamed" in result["message"]
    assert "ignored groups were excluded" in result["message"]

    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) == 1
    csv_text = csv_files[0].read_text(encoding="utf-8")
    assert "Sports,a,A,Renamed" in csv_text
    assert "Teamarr,b,B,Renamed" not in csv_text
