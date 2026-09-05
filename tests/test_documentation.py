"""The user guide describes the settings and actions the plugin actually has.

Measured 2026-09-05: the settings reference table in docs/USER-GUIDE.md named
three settings that the form does not. It called Category Organization Groups
"Channel Groups for Category Organization", OTA Name Format "OTA Channel Name
Format", and Unknown Channel Suffix "Suffix for Unknown Channels". Those are the
labels the settings carried before release 1.26.1001200 relabelled them on
2026-04-10, and the guide was never updated. A reader looking for any of the
three on the settings page would not find it.

The same three names were also printed at the top of every CSV export, which is
covered by tests/test_csv_header.py. Both places had copied the old labels, and
both were wrong in the same way for the same reason.

README.md is deliberately an introduction rather than a reference, so it is not
required to mention every setting. Only the guide is checked for coverage.
"""
import functools
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = os.path.join(ROOT, "docs", "USER-GUIDE.md")

# The wording each of these settings carried before 2026-04-10. None of them may
# come back, in the guide or anywhere else a reader is told to look for them.
RETIRED_LABELS = [
    "Channel Groups for Category Organization",
    "OTA Channel Name Format",
    "Suffix for Unknown Channels",
    "Channel Database Category Filter",
    "Imported Channel Group Name",
]


@pytest.fixture(scope="module")
def guide():
    with open(GUIDE, encoding="utf-8") as handle:
        return handle.read()


def _settings(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return [f for f in inst.fields if f.get("type") != "info"]


def _sections(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return [f for f in inst.fields if f.get("type") == "info"]


def _actions(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin).actions


def test_the_guide_names_every_setting_as_the_form_names_it(plugin_module, guide):
    missing = [f["id"] for f in _settings(plugin_module) if f["label"] not in guide]
    assert missing == [], f"settings the guide does not name: {missing}"


def test_the_guide_names_every_action_as_the_page_names_it(plugin_module, guide):
    missing = [a["id"] for a in _actions(plugin_module) if a["label"] not in guide]
    assert missing == [], f"actions the guide does not name: {missing}"


@pytest.mark.parametrize("retired", RETIRED_LABELS)
def test_the_guide_does_not_use_a_label_the_form_retired(guide, retired):
    assert retired not in guide, \
        f"the guide still calls a setting {retired!r}, which the form does not"


def test_the_guide_lists_the_sections_the_form_is_divided_into(plugin_module, guide):
    """A reader matching the guide against the page needs the same headings."""
    missing = [f["id"] for f in _sections(plugin_module) if f["label"] not in guide]
    assert missing == [], f"section headings the guide does not name: {missing}"


def test_the_guide_explains_every_button_colour_the_page_uses(plugin_module, guide):
    """The colours mean something specific, and nothing else says what.

    An operator looking at five colours on the Plugins page has no way to learn
    that red means an existing value is replaced. If a colour is ever added to
    an action, this fails until the guide explains that colour too.
    """
    used = sorted({a.get("button_color") for a in _actions(plugin_module)})
    legend = guide.split("Button colours")[-1]
    missing = [c for c in used if c and f"**{c.capitalize()}**" not in legend]
    assert missing == [], f"colours the guide does not explain: {missing}"


def test_the_guide_does_not_explain_a_colour_the_page_never_uses(
        plugin_module, guide):
    """A legend entry for a colour nothing carries sends a reader looking."""
    used = {a.get("button_color") for a in _actions(plugin_module)}
    legend = guide.split("Button colours")[-1].split("\n\n")[0]
    for colour in ("red", "orange", "green", "cyan", "blue", "yellow", "violet"):
        if f"**{colour.capitalize()}**" in legend:
            assert colour in used, f"the guide explains {colour}, which no action uses"


@functools.cache
def _station_table_rows():
    """Counted once. The station table is about 1.6 MB of JSON and four
    tests want only its length."""
    import json
    with open(os.path.join(ROOT, "Channel-Maparr", "networks.json"),
              encoding="utf-8") as handle:
        return len(json.load(handle))


# Documents that describe the station table to a reader. docs/CHANGELOG.md and
# docs/TODO.md are excluded on purpose: their entries record what was true at
# the time, and 1,915 was true until the 2026-08-10 rebuild.
STATION_TABLE_DOCUMENTS = ["README.md", "docs/USER-GUIDE.md", "docs/DEVELOPMENT.md"]


def test_the_station_table_is_not_empty():
    """Guards the count below: zero rows would make its assertions meaningless."""
    assert _station_table_rows() > 1000


@pytest.mark.parametrize("relative_path", STATION_TABLE_DOCUMENTS)
def test_no_document_quotes_the_station_table_size_it_had_before_the_rebuild(
        relative_path):
    """Three documents said 1,915, the size before the 2026-08-10 rebuild.

    The table was rebuilt from an FCC Licensing and Management System dump and
    holds 6,839 stations. Only docs/DEVELOPMENT.md was corrected at first, so
    README.md and the user guide were left asserting a number this repository
    can disprove by counting its own file.
    """
    rows = _station_table_rows()
    with open(os.path.join(ROOT, relative_path), encoding="utf-8") as handle:
        text = handle.read()
    assert "1,915" not in text, f"{relative_path} still quotes the pre-rebuild size"
    assert f"{rows:,}" in text, \
        f"{relative_path} does not say the table holds {rows:,} stations"


def test_the_development_notes_use_no_em_dash():
    with open(os.path.join(ROOT, "docs", "DEVELOPMENT.md"),
                 encoding="utf-8") as handle:
        development = handle.read()
    assert chr(0x2014) not in development
    assert chr(0x2013) not in development


def test_the_guide_uses_no_em_dash(guide):
    assert chr(0x2014) not in guide
    assert chr(0x2013) not in guide


def test_the_guide_is_plain_ascii(guide):
    """It is read on GitHub, in an editor and in a terminal, so keep it simple."""
    bad = sorted({c for c in guide if ord(c) > 127})
    assert not bad, [hex(ord(c)) for c in bad]


# The writing gate reports "[Your first run]" on line 10 as an unfilled
# placeholder. It is not one: it is the link text of a table of contents entry
# pointing at the walkthrough of that name. No test asserts it away, because
# removing it would break the contents list to satisfy a false positive.
