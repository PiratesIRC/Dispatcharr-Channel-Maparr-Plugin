"""Action button colour, and the two action lists agreeing with each other.

Measured 2026-09-05 against the live plugin instance and the manifest.

COLOUR RAN OPPOSITE TO CONSEQUENCE. Clear CSV Exports was the only red button on
the page, and it deletes this plugin's own CSV files and no channel data at all.
Meanwhile Rename Channels, which rewrites the name of every matched channel, was
green, the same colour as Load and Process Channels, which writes no channel data
at all. Create Channels From Streams was blue, the colour this plugin uses for
Validate Settings and Show Status, while it creates channels in the database.

The scheme adopted here, following the sibling plugin Stream-Mapparr:

  red     replaces a value the operator can see on an existing channel, and the
          plugin cannot put the old value back
  orange  writes data or clears state, but replaces nothing
  green   runs a normal operation that writes no channel data
  cyan    sends something outward, to an inbox
  blue    reads and reports, changing nothing

Red is worth stating precisely for this plugin, because measured against every
database write in plugin.py, THIS PLUGIN NEVER DELETES A CHANNEL OR A STREAM. The
only writes are five bulk_update calls, which set name, logo_id or
channel_group_id on channels that already exist, and two create calls, which add
channels and add stream links. So red marks replacement rather than deletion, and
only two actions qualify:

  Rename Channels replaces the whole name, and the old name is then nowhere on
  the channel.
  Organize by Category replaces channel_group_id, and the group the channel came
  from is then recorded only in the preview CSV.

The three that look like they belong with them do not, which was measured by
reading the code rather than the button:

  Apply Default Logo and Apply Per-Channel Logos both select only channels whose
  logo_id is None, 0 or "0", so neither can overwrite a logo somebody chose.
  Tag Unknown Channels appends the configured suffix to the existing name, so the
  old name is still there and removing the suffix gives it back.

The two create actions are orange for the same reason: a channel that was not
there before cannot lose anything, however many of them a run makes.

THE TWO DECLARATIONS HAD DRIFTED. Dispatcharr serves the list in plugin.py for an
enabled plugin; plugin.json is the manifest the Plugin Hub and anyone reading the
repository sees. Show Status carried button_color blue in plugin.py and no colour
at all in plugin.json, and Organize by Category described itself differently in
each. Reading either file alone gives a false picture of the interface.
"""
import json
import os

import pytest

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Channel-Maparr", "plugin.json")

EXPECTED_COLOURS = {
    # red: replaces a value on channels that already exist
    "rename_channels": "red",
    "organize_by_category": "red",
    "replace_tv_logos": "red",
    # orange: writes or clears, replaces nothing
    "rename_unknown_channels": "orange",
    "apply_logos": "orange",
    "apply_tv_logos": "orange",
    "import_m3u_streams": "orange",
    "create_channels_from_streams": "orange",
    "clear_csv_exports": "orange",
    # green: an operation that writes no channel data
    "load_and_process_channels": "green",
    # cyan: sends something outward
    "email_report_now": "cyan",
    # blue: reads and reports
    "validate_settings": "blue",
    "plugin_status": "blue",
}

# The colours this plugin and its siblings use. This is a deliberate
# restriction, NOT a technical limit: measured in the running container on
# 2026-09-05, PluginActionSerializer.button_color is a plain CharField and
# _normalize_actions passed "violet" and even "not-a-colour" straight through
# without dropping the action. An earlier note in this workspace said an
# unrecognised colour made the serializer drop the whole action silently, and
# that is not what the code does.
#
# What has NOT been measured here is how the frontend renders a name outside its
# own palette, which is the actual reason to stay inside a known set: a button
# that falls back to a default style carries none of the meaning the colour is
# supposed to carry.
KNOWN_COLOURS = {"red", "orange", "yellow", "green", "cyan", "blue", "gray"}


def _actions(plugin_module):
    """The list Dispatcharr actually serves for an ENABLED plugin."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin).actions


def _manifest_actions():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)["actions"]


# --------------------------------------------------------------------------- #
# Every button is labelled and coloured
# --------------------------------------------------------------------------- #
def test_every_action_has_a_button_label(plugin_module):
    missing = [a["id"] for a in _actions(plugin_module) if not a.get("button_label")]
    assert missing == [], f"actions with no button_label: {missing}"


def test_every_action_has_a_button_colour(plugin_module):
    missing = [a["id"] for a in _actions(plugin_module) if not a.get("button_color")]
    assert missing == [], f"actions with no button_color: {missing}"


def test_every_colour_is_one_dispatcharr_recognises(plugin_module):
    """Import M3U Streams used violet, which is outside the served vocabulary."""
    unknown = {a["id"]: a.get("button_color") for a in _actions(plugin_module)
               if a.get("button_color") not in KNOWN_COLOURS}
    assert unknown == {}, unknown


def test_this_plugin_has_no_event_driven_actions(plugin_module):
    """Every action here is a button, so every one needs a label and a colour.

    If an action driven by a Dispatcharr event is ever added it must carry
    neither, because it is never pressed, and the two tests above would then
    need an exclusion.
    """
    with_events = [a["id"] for a in _actions(plugin_module) if a.get("events")]
    assert with_events == []


# --------------------------------------------------------------------------- #
# Colour means one thing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action_id,colour", sorted(EXPECTED_COLOURS.items()))
def test_the_action_carries_the_colour_its_consequence_calls_for(
        plugin_module, action_id, colour):
    action = next((a for a in _actions(plugin_module) if a["id"] == action_id), None)
    assert action is not None, f"{action_id} is not served"
    assert action.get("button_color") == colour


def test_every_served_action_has_an_expected_colour(plugin_module):
    """A new action must be given a colour here, with its consequence considered."""
    served = {a["id"] for a in _actions(plugin_module)}
    assert served == set(EXPECTED_COLOURS), served ^ set(EXPECTED_COLOURS)


def test_red_is_reserved_for_the_actions_that_replace_channel_values(plugin_module):
    """If red spreads to merely noisy actions it stops carrying any warning."""
    red = {a["id"] for a in _actions(plugin_module) if a.get("button_color") == "red"}
    assert red == {"rename_channels", "organize_by_category",
                   "replace_tv_logos"}, red


@pytest.mark.parametrize("logo_id,expected", [
    (None, True), (0, True), ("0", True),
    (5, False), ("5", False), ("abc", False),
])
def test_the_predicate_the_logo_actions_select_on(plugin_module, logo_id, expected):
    """Pins the reason those two actions are orange rather than red.

    Both select only channels this returns True for, so neither can replace
    artwork somebody chose. If it were ever widened to every channel in scope,
    the actions would start replacing logos and would belong in red.

    This used to assert two literal substrings of plugin.py, one per spelling
    the predicate had. The two spellings are now one function, so the behaviour
    is tested instead of the text.
    """
    assert plugin_module.Plugin.has_no_logo({"logo_id": logo_id}) is expected


def test_a_row_with_no_logo_field_at_all_counts_as_having_none(plugin_module):
    assert plugin_module.Plugin.has_no_logo({}) is True


def test_both_logo_actions_select_through_that_predicate(plugin_module):
    """The predicate is only load-bearing if both actions actually call it."""
    import ast
    with open(os.path.join(os.path.dirname(MANIFEST), "plugin.py"),
              encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    callers = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(inner, ast.Call)
                and ast.unparse(inner.func).endswith("has_no_logo")
                for inner in ast.walk(node))
    }
    # apply_tv_logos_action delegates to _assign_tv_logos, whose non-replacing
    # branch is the caller. That it stays non-replacing is pinned by behaviour
    # in tests/test_tv_logos_replace.py.
    assert {"apply_logos_action", "_assign_tv_logos"} <= callers, callers


def test_every_red_action_also_asks_for_confirmation(plugin_module):
    """Colour is the glance; the dialog is the guard. A red button needs both."""
    for a in _actions(plugin_module):
        if a.get("button_color") == "red":
            assert a.get("confirm"), f"{a['id']} is red but has no confirm dialog"


def test_the_read_only_actions_do_not_ask_for_confirmation(plugin_module):
    """A dialog in front of an action that changes nothing trains it to be dismissed."""
    for a in _actions(plugin_module):
        if a.get("button_color") == "blue":
            assert not a.get("confirm"), f"{a['id']} changes nothing but asks anyway"


# --------------------------------------------------------------------------- #
# The two declarations agree
# --------------------------------------------------------------------------- #
def test_the_manifest_and_the_served_list_hold_the_same_actions(plugin_module):
    served = sorted(a["id"] for a in _actions(plugin_module))
    manifest = sorted(a["id"] for a in _manifest_actions())
    assert served == manifest


@pytest.mark.parametrize("key", ["button_label", "button_color", "confirm",
                                 "label", "description", "button_variant",
                                 "background"])
def test_the_manifest_and_the_served_list_agree_on_button_metadata(plugin_module, key):
    """They drifted, so reading either one alone misled.

    plugin.json is what the Plugin Hub and an operator browsing the repository
    see; plugin.py is what Dispatcharr renders for an enabled plugin.
    """
    served = {a["id"]: a.get(key) for a in _actions(plugin_module)}
    manifest = {a["id"]: a.get(key) for a in _manifest_actions()}
    disagree = {k: (served.get(k), manifest.get(k))
                for k in served if served.get(k) != manifest.get(k)}
    assert disagree == {}, f"served vs manifest disagree on {key}: {disagree}"
