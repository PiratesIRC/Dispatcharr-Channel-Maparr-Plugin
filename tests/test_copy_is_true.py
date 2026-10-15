"""Claims the settings form makes about the plugin, checked against the plugin.

Wording is only worth improving once it is true, and copy written today was not.
Both of these were found by running the check rather than by reading the text.

DRY RUN MODE NAMED FOUR ACTIONS AND SEVEN HONOUR IT. The help text written on
2026-09-05 said the setting applies to Rename Channels, Organize by Category,
Import M3U Streams and Create Channels From Streams. Reading every
settings.get("dry_run_mode") call out of plugin.py finds seven acting on it, the
three missing ones being Tag Unknown Channels, Apply Default Logo and Apply
Per-Channel Logos. An operator turning Dry Run on to be safe, and reading that
list, would believe those three were about to write to the database. The
manifest's older wording named only three and was worse, which is how the
shorter list survived being reviewed.

THE IGNORE RULE WAS STATED WITHOUT ITS EXCEPTION. The Matching and Scope section
said a name in Channel Groups to Ignore matching no group stops the run. It stops
the actions that resolve the group scope. The three that replay a saved results
file filter on the group name stored in that file and do not refuse, and Import
M3U Streams refuses only when its own target group is ignored. The setting's own
help text had this right already, saying "most actions".

These tests read the behaviour out of plugin.py rather than repeating a list, so
an action that starts or stops honouring either rule fails them.
"""
import ast
import functools
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Channel-Maparr", "plugin.py")


@functools.cache
def _tree():
    """Parsed once for the whole module.

    plugin.py is about 4,500 lines, and the helpers below are called from
    parametrize decorators at collection time as well as from test bodies, so
    a fixture cannot cover them. Without this the file was read and parsed
    about a dozen times per run.
    """
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _functions_reading_setting(setting_id):
    """Functions containing a settings.get("<setting_id>", ...) call."""
    found = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                    and inner.args[0].value == setting_id):
                found.add(node.name)
    return found


def _action_label(plugin_module, action_id):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return next(a["label"] for a in inst.actions if a["id"] == action_id)


def _help_text(plugin_module, field_id):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return next(f.get("help_text") or "" for f in inst.fields
                if f.get("id") == field_id)


def _section_body(plugin_module, section_id):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return next(f.get("description") or "" for f in inst.fields
                if f.get("id") == section_id)


# --------------------------------------------------------------------------- #
# Dry Run Mode
# --------------------------------------------------------------------------- #
# validate_settings reads the setting to report it back, and writes nothing
# either way, so it is not one of the actions the help text is about.
DRY_RUN_REPORTERS = {"validate_settings_action"}


def _actions_honouring_dry_run():
    return _functions_reading_setting("dry_run_mode") - DRY_RUN_REPORTERS


def test_more_than_one_action_honours_dry_run_mode():
    """Guards the test below: an empty set would make it pass saying nothing."""
    honouring = _actions_honouring_dry_run()
    assert len(honouring) >= 5, honouring


@pytest.mark.parametrize("function_name", sorted(_actions_honouring_dry_run()))
def test_the_dry_run_help_text_names_every_action_that_honours_it(
        plugin_module, function_name):
    """Naming some of them is worse than naming none: the rest read as excluded."""
    action_id = function_name[:-len("_action")]
    label = _action_label(plugin_module, action_id)
    help_text = _help_text(plugin_module, "dry_run_mode")
    assert label in help_text, \
        f"{label} honours Dry Run Mode and the help text does not say so"


def test_the_dry_run_help_text_names_no_action_that_ignores_it(plugin_module):
    """The reverse: promising a preview an action does not produce."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    honouring = {f[:-len("_action")] for f in _actions_honouring_dry_run()}
    help_text = _help_text(plugin_module, "dry_run_mode")
    for action in inst.actions:
        if action["id"] in honouring:
            continue
        assert action["label"] not in help_text, \
            f"{action['label']} does not honour Dry Run Mode and is named anyway"


def _dry_run_csv_writers():
    """Actions whose dry run produces a CSV, read from what the branch calls.

    A dry run branch either hands off to an action that writes an export, or
    calls an export helper directly. The other three return a count in the
    toast and write no file at all.
    """
    writers = set()
    exporting = ("preview_changes_action", "category_groups_dry_run_action",
                 "import_m3u_streams_dry_run_action", "_export_seed_preview")
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_action"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and ast.unparse(inner.func).split(".")[-1] in exporting:
                writers.add(node.name)
            # Replace Logos shares _assign_tv_logos with Apply Per-Channel
            # Logos; only the replacing call writes a preview CSV.
            if (isinstance(inner, ast.Call)
                    and ast.unparse(inner.func).split(".")[-1] == "_assign_tv_logos"
                    and any(kw.arg == "replace_existing"
                            and isinstance(kw.value, ast.Constant) and kw.value.value is True
                            for kw in inner.keywords)):
                writers.add(node.name)
    return writers & _actions_honouring_dry_run()


def test_the_dry_run_help_text_does_not_promise_a_csv_from_every_action(
        plugin_module):
    """Five of the eight write a CSV preview. Three report a count and no file.

    Saying "writes a CSV preview" of all eight would send an operator looking in
    /data/exports for a file Apply Default Logo never writes.
    """
    writers = _dry_run_csv_writers()
    others = _actions_honouring_dry_run() - writers
    assert len(writers) == 5, sorted(writers)
    assert len(others) == 3, sorted(others)
    help_text = _help_text(plugin_module, "dry_run_mode").lower()
    assert any(marker in help_text for marker in
               ("report a count", "reports a count", "no file", "without writing a file")), \
        "the help text does not say that three of them write no file"


# --------------------------------------------------------------------------- #
# Which country databases the form says are available
#
# The help text listed eleven country codes and twelve databases ship. The
# missing one is NO, Norway, holding 94 channels. reload_databases builds the
# path straight from the code the operator types, so entering NO does load it;
# the only thing stopping anyone was being told it was not there. README.md, in
# the same set of changes, said "twelve country databases", so the two
# contradicted each other.
# --------------------------------------------------------------------------- #
PACKAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Channel-Maparr")


@functools.cache
def _shipped_country_codes():
    return sorted(name.split("_")[0] for name in os.listdir(PACKAGE)
                  if name.endswith("_channels.json"))


def test_more_than_one_country_database_ships():
    """Guards the two tests below against an empty directory listing."""
    assert len(_shipped_country_codes()) >= 10, _shipped_country_codes()


@pytest.mark.parametrize("code", _shipped_country_codes())
def test_every_shipped_country_database_is_offered_on_the_form(plugin_module, code):
    help_text = _help_text(plugin_module, "channel_databases")
    assert code in help_text, \
        f"{code}_channels.json ships and the form does not offer {code}"


def test_the_form_offers_no_country_database_that_does_not_ship(plugin_module):
    """The reverse: a code that loads nothing and warns into the container log."""
    help_text = _help_text(plugin_module, "channel_databases")
    offered = help_text.split("Available:", 1)[1].split(".", 1)[0]
    codes = [c.strip() for c in offered.split(",") if c.strip()]
    assert codes, help_text
    shipped = set(_shipped_country_codes())
    assert [c for c in codes if c not in shipped] == [], codes


# --------------------------------------------------------------------------- #
# Which actions really run in the background
#
# Organize by Category described itself as running in the background and does
# not: it never calls _try_start_thread and never returns a background flag, so
# it fuzzy-matches every channel in scope inside the uWSGI request. The
# declaration also carries a "background": True key, which reaches nothing.
# Measured in the running container on 2026-09-05: PluginActionSerializer in
# /app/apps/plugins/serializers.py declares id, label, description, confirm,
# button_label, button_variant, button_color and events, and _normalize_actions
# returns only validated_data, so any other key is dropped. The flag this plugin
# actually acts on is the one in an action's RETURN value, which its own run()
# reads to decide whether to send a websocket notification.
# --------------------------------------------------------------------------- #
SERVED_ACTION_KEYS = {"id", "label", "description", "confirm", "button_label",
                      "button_variant", "button_color", "events"}


def _actions_that_start_a_thread():
    started = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef) or not node.name.endswith("_action"):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and ast.unparse(inner.func).endswith("_try_start_thread")):
                started.add(node.name[:-len("_action")])
    return started


def test_some_action_starts_a_thread():
    """Guards the test below: an empty set would let any claim through."""
    assert _actions_that_start_a_thread(), "no action backgrounds any more"


def test_an_action_says_it_runs_in_background_only_if_it_does(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    threading = _actions_that_start_a_thread()
    for action in inst.actions:
        claims = "background" in (action.get("description") or "").lower()
        if claims:
            assert action["id"] in threading, \
                f"{action['id']} says it runs in background and never starts a thread"


def test_no_action_declares_a_key_dispatcharr_throws_away(plugin_module):
    """A key the serializer does not know is dropped, so it misleads a reader."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    for action in inst.actions:
        unknown = sorted(set(action) - SERVED_ACTION_KEYS)
        assert unknown == [], f"{action['id']} declares {unknown}, which is dropped"


# --------------------------------------------------------------------------- #
# Channel Groups to Ignore
# --------------------------------------------------------------------------- #
def test_some_actions_do_not_refuse_on_an_unmatched_ignore_name():
    """The fact the section body has to account for.

    An action that resolves the group scope raises when an ignore name matches
    nothing. An action that replays a saved results file filters on the group
    name stored in that file and cannot refuse, because it never asks the
    database which groups exist.
    """
    def _calling(*names):
        found = set()
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and ast.unparse(inner.func).split(".")[-1] in names):
                    found.add(node.name)
        return found

    # Resolving the scope asks the database which groups exist, and raises
    # GroupScopeError when an ignore name matches none of them. Replaying a
    # saved results file filters on the group name stored in that file and never
    # asks, so it cannot refuse. Reading the ignore_groups SETTING is not the
    # distinction: both kinds read it.
    resolving = _calling("_resolve_process_scope", "_resolve_category_scope")
    replaying = _calling("split_rows_by_ignore")

    assert resolving, "no action resolves the group scope any more"
    assert replaying, "no action replays a results file any more"
    assert replaying - resolving, (
        "every action that replays a results file also resolves the group "
        "scope, so none of them can be the exception this section describes")


def test_the_scope_section_does_not_claim_every_action_refuses(plugin_module):
    """It said the run stops, flatly. It stops for some actions, not all."""
    body = _section_body(plugin_module, "_section_matching")
    assert "most actions" in body or "some actions" in body, \
        "the section states the refusal without the exception the code has"


def test_the_scope_section_still_explains_why_a_typo_is_refused(plugin_module):
    """Softening the claim must not lose the reason, which is the useful half."""
    body = _section_body(plugin_module, "_section_matching")
    assert "typo" in body.lower(), body
