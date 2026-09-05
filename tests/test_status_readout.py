"""The text the Show Status action puts in front of the operator.

Rendered and read on 2026-09-05, and three things were wrong.

FOUR OF THE FIVE OPERATIONS SHOWED A RAW IDENTIFIER. ProgressTracker writes its
action_id into the progress file, and build_status_message turns that into a
label through ACTION_LABELS. The five names plugin.py actually passes are
process_channels, apply_tv_logos, match_streams, import_streams and
seed_channels. Only the first had an entry, so a run of Apply Per-Channel Logos
reported itself as "apply_tv_logos". Meanwhile seven of the map's eight entries
were for names nothing has ever written, including "preview_changes", which is
not an action this plugin has at all.

THE PROMPT NAMED A BUTTON THAT DOES NOT EXIST. The message shown before anything
has run told the operator to click a button rendered as a bar chart symbol
followed by "Show Status". The button on the page is worded differently and
carries a different symbol.

TWO MESSAGES USED EM DASHES, which are not allowed in copy this plugin shows the
operator.
"""
import ast
import functools
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Channel-Maparr", "plugin.py")

NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def status(plugin_module):
    import channel_maparr.progress_status as module
    return module


def _running(action, **over):
    base = {"status": "running", "current": 412, "total": 1544,
            "action": action, "updated_at": NOW - 5, "start_time": NOW - 90}
    base.update(over)
    return base


@functools.cache
def _tracked_action_names():
    """Every name plugin.py passes to ProgressTracker, read from the source.

    Cached: two parametrize decorators call this at collection time, where a
    fixture cannot reach, and it parses a 4,500-line module.
    """
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ProgressTracker"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


# --------------------------------------------------------------------------- #
# Every operation that can be running has a name a person would recognise
# --------------------------------------------------------------------------- #
def test_the_source_still_reports_progress_for_some_operations():
    """Guards the reader below: an empty set would make both tests vacuous."""
    assert len(_tracked_action_names()) >= 5, _tracked_action_names()


@pytest.mark.parametrize("action", sorted(_tracked_action_names()))
def test_every_operation_that_reports_progress_has_a_readable_label(status, action):
    assert action in status.ACTION_LABELS, \
        f"Show Status would print the raw identifier {action!r}"


def test_no_label_exists_for_an_operation_that_never_runs(status):
    """Seven of the eight entries named operations nothing writes."""
    strays = sorted(set(status.ACTION_LABELS) - _tracked_action_names())
    assert strays == [], f"labels for operations that never report progress: {strays}"


@pytest.mark.parametrize("action", sorted(_tracked_action_names()))
def test_the_label_never_leaks_the_identifier_into_the_message(status, action):
    message = status.build_status_message(_running(action), now=NOW)
    assert action not in message, message
    assert status.ACTION_LABELS[action] in message


# --------------------------------------------------------------------------- #
# The prompt shown before anything has run
# --------------------------------------------------------------------------- #
def test_the_prompt_names_the_button_as_the_page_words_it(status, plugin_module):
    """It told the operator to click a button worded and drawn differently."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    action = next(a for a in inst.actions if a["id"] == "plugin_status")
    message = status.build_status_message({}, now=NOW)
    assert action["label"] in message, message


def test_the_prompt_carries_no_symbol_of_its_own(status):
    """It used a bar chart symbol that appears on no button on the page."""
    message = status.build_status_message({}, now=NOW)
    bad = sorted({c for c in message if ord(c) > 127})
    assert not bad, [hex(ord(c)) for c in bad]


# --------------------------------------------------------------------------- #
# House rules for operator-facing copy
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("progress", [
    {},
    {"status": "running", "current": 1, "total": 10, "action": "process_channels",
     "updated_at": NOW - 5, "start_time": NOW - 90},
    {"status": "running", "current": 1, "total": 10, "action": "process_channels",
     "updated_at": NOW - 4000, "start_time": NOW - 5000},
    {"status": "done", "action": "process_channels", "summary": "412 renamed",
     "finished_at": NOW - 60},
])
def test_no_status_message_uses_an_em_dash(status, progress):
    message = status.build_status_message(progress, now=NOW)
    assert chr(0x2014) not in message, message
    assert chr(0x2013) not in message, message


def test_the_stalled_warning_still_says_what_to_do(status):
    """Rewording it must not lose the instruction, which is the useful half."""
    message = status.build_status_message(
        _running("process_channels", updated_at=NOW - 4000), now=NOW)
    assert "log" in message.lower(), message
    assert "no progress update" in message.lower(), message


def test_a_finished_run_reports_when_it_finished_and_what_it_did(status):
    message = status.build_status_message(
        {"status": "done", "action": "process_channels",
         "summary": "412 channels renamed", "finished_at": NOW - 60}, now=NOW)
    assert "412 channels renamed" in message
    assert status.ACTION_LABELS["process_channels"] in message


def test_the_running_line_reports_progress_and_an_estimate(status):
    message = status.build_status_message(_running("process_channels"), now=NOW)
    assert "412/1544" in message
    assert "27%" in message
    assert "ETA" in message
