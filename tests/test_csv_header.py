"""The commented preamble at the top of every CSV this plugin exports.

Reviewed 2026-09-05 by rendering the real header and reading it. Four things were
wrong, and the first is a plain falsehood rather than a matter of wording.

THE PREAMBLE LABELLED SIX SETTINGS WITH NAMES THE INTERFACE DOES NOT USE. The
label map inside _generate_csv_settings_header was written on 2025-11-29 (release
0.5.0) and the settings themselves were relabelled on 2026-04-10 (release
1.26.1001200, commit a071fe1). The map was never updated, so every export written
since then has printed, for example, "Suffix for Unknown Channels" for a setting
the form calls "Unknown Channel Suffix", and "Channel Database Category Filter"
for one the form calls "Category Filter". A reader who went looking for those
settings would not find them. The six: Category Organization Groups, M3U Source,
Category Filter, Custom Import Group Name, OTA Name Format and Unknown Channel
Suffix.

The fix is not to correct the map but to delete it: the labels are now read from
the plugin's own declared fields, so the preamble cannot drift from the form
again.

BOOLEANS PRINTED AS PYTHON VALUES, OR AS NOTHING AT ALL. Every setting was
rendered by "if value:", so Dry Run Mode off printed "(not set)", which reads as
unconfigured rather than as off, and Dry Run Mode on printed "True".

THE PREAMBLE NEVER SAID WHAT THE RUN DID. It recorded configuration in detail and
said nothing about the result, so the reader had to count the rows themselves.

TEN OF THE TWENTY-FOUR SETTINGS WERE MISSING, including Match by Market When No
Callsign, which decides which station a channel matches when no callsign is
printed in its name.

The remaining tests cover plain wording: that the file says what it is before it
says how it was configured, that the sensitivity line says what the threshold
number means, and that nothing outside plain ASCII reaches a file a spreadsheet
may open under a different codepage.
"""
import pytest


def _plugin(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return inst


def _header(plugin_module, settings=None, title="Rename Channels preview",
            summary=(("Channels to rename", 1),)):
    """Build a preamble. title and summary are REQUIRED of the real function,
    so this supplies plausible ones rather than letting a test omit them."""
    return _plugin(plugin_module)._generate_csv_settings_header(
        settings or {}, title, list(summary))


def _line(header, prefix):
    for line in header.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line starting {prefix!r} in:\n{header}")


def _declared_labels(plugin_module):
    return {f["id"]: f.get("label") for f in _plugin(plugin_module).fields
            if f.get("type") != "info"}


# --------------------------------------------------------------------------- #
# The labels that did not match the interface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_id", [
    "category_groups", "m3u_sources", "m3u_category_filter",
    "m3u_custom_group_name", "ota_format", "unknown_suffix",
])
def test_each_setting_is_named_as_the_interface_names_it(plugin_module, field_id):
    """The six whose printed label the form does not use."""
    label = _declared_labels(plugin_module)[field_id]
    header = _header(plugin_module, {field_id: "sample-value"})
    assert f"# {label}:" in header, f"{field_id} is not printed as {label!r}"


def test_no_printed_label_is_absent_from_the_settings_form(plugin_module):
    """Reads every settings line back and checks the name against the form.

    This is the guard that stops the preamble drifting from the interface again:
    a setting relabelled on the form changes this output automatically, and a
    hand-written label that matches nothing fails here.
    """
    declared = set(_declared_labels(plugin_module).values())
    header = _header(plugin_module)
    printed = [line.split(":", 1)[0][2:] for line in header.splitlines()
               if line.startswith("# ") and ":" in line]
    strays = [p for p in printed if p not in declared
              and p not in _PREAMBLE_LINE_NAMES]
    assert strays == [], f"printed names that are not settings labels: {strays}"


# The "name: value" lines in the preamble that are not settings: the three fixed
# ones, plus whatever result label the summary carried. _header always supplies a
# summary now, because the real function requires one.
_PREAMBLE_LINE_NAMES = {"Report", "Generated", "Plugin Version",
                        "Channels to rename"}


# --------------------------------------------------------------------------- #
# Settings that were missing entirely
# --------------------------------------------------------------------------- #
def test_every_setting_that_affects_a_run_is_recorded(plugin_module):
    """Ten were missing, including the market fallback, which changes matching."""
    header = _header(plugin_module)
    missing = []
    for fid, label in _declared_labels(plugin_module).items():
        if fid.startswith("notify_"):
            continue
        if f"# {label}:" not in header:
            missing.append(fid)
    assert missing == [], f"settings absent from the preamble: {missing}"


def test_the_notification_settings_are_not_recorded(plugin_module):
    """They decide whether an email is sent, not what is in this file."""
    header = _header(plugin_module)
    for fid, label in _declared_labels(plugin_module).items():
        if fid.startswith("notify_"):
            assert f"# {label}:" not in header, fid


# --------------------------------------------------------------------------- #
# Booleans
# --------------------------------------------------------------------------- #
def test_a_boolean_that_is_off_reads_as_no_rather_than_as_unset(plugin_module):
    """Dry Run Mode off printed "(not set)", which reads as never configured."""
    line = _line(_header(plugin_module, {"dry_run_mode": False}), "# Dry Run Mode:")
    assert "No" in line and "not set" not in line, line


def test_a_boolean_that_is_on_reads_as_yes_rather_than_as_true(plugin_module):
    line = _line(_header(plugin_module, {"dry_run_mode": True}), "# Dry Run Mode:")
    assert "Yes" in line, line


@pytest.mark.parametrize("stored,expected", [
    ("true", "Yes"), ("True", "Yes"), ("false", "No"), ("False", "No"),
    (True, "Yes"), (False, "No"),
])
def test_a_boolean_stored_as_a_string_still_reads_as_yes_or_no(
        plugin_module, stored, expected):
    """Dispatcharr stores some booleans as the strings true and false."""
    line = _line(_header(plugin_module, {"ota_market_fallback": stored}),
                 "# Match by Market When No Callsign:")
    assert expected in line, line


def test_no_python_boolean_reaches_the_reader(plugin_module):
    header = _header(plugin_module, {"dry_run_mode": True,
                                     "ota_market_fallback": False})
    assert "True" not in header and "False" not in header, header


# --------------------------------------------------------------------------- #
# What the run did
# --------------------------------------------------------------------------- #
def test_the_preamble_states_what_the_run_did_before_it_states_the_settings(
        plugin_module):
    header = _header(plugin_module, title="Rename Channels preview",
                     summary=[("Channels to rename", 12), ("Channels skipped", 3)])
    lines = header.splitlines()
    result_at = next(i for i, ln in enumerate(lines)
                     if ln.startswith("# Channels to rename:"))
    settings_at = next(i for i, ln in enumerate(lines)
                       if ln.startswith("# Match Sensitivity:"))
    assert "12" in lines[result_at]
    assert result_at < settings_at, "the result is printed below the settings"


def test_the_preamble_names_the_action_that_produced_the_file(plugin_module):
    header = _header(plugin_module, title="Rename Channels preview")
    assert "Rename Channels preview" in header


def test_a_summary_value_of_zero_is_still_printed(plugin_module):
    """Zero renamed is a result, not a missing value."""
    header = _header(plugin_module, summary=[("Channels to rename", 0)])
    assert "0" in _line(header, "# Channels to rename:")


# --------------------------------------------------------------------------- #
# Plain wording
# --------------------------------------------------------------------------- #
def test_the_file_says_what_it_is_before_it_says_how_it_was_configured(
        plugin_module):
    """Someone opening this in a spreadsheet has no other clue what it is."""
    top = " ".join(_header(plugin_module).splitlines()[:8]).lower()
    assert "channel mapparr" in top
    assert any(word in top for word in ("skip", "ignore", "not data")), \
        "the preamble never says the hash lines should be skipped on import"
    assert "#" in top


def test_the_sensitivity_line_says_what_the_threshold_means(plugin_module):
    """A bare word or a bare number answers neither "how strict" nor "of what"."""
    line = _line(_header(plugin_module, {"match_sensitivity": "normal"}),
                 "# Match Sensitivity:")
    assert "100" in line, line
    assert "strict" in line.lower(), line


@pytest.mark.parametrize("field_id,stored", [
    ("m3u_sources", "_all"),
    ("rate_limiting", "low"),
    ("match_sensitivity", "relaxed"),
])
def test_a_choice_reads_as_the_form_words_it_not_as_the_stored_value(
        plugin_module, field_id, stored):
    """The M3U source line printed "_all", which appears nowhere on the form.

    The operator picked an option worded "All sources (no filter)". Printing the
    value stored behind it makes the record harder to check against the form than
    it needs to be.
    """
    field = next(f for f in _plugin(plugin_module).fields if f.get("id") == field_id)
    option = next(o for o in field["options"] if o["value"] == stored)
    line = _line(_header(plugin_module, {field_id: stored}),
                 f"# {field['label']}:")
    assert option["label"] in line, line


def test_an_unset_setting_says_so_plainly(plugin_module):
    line = _line(_header(plugin_module, {}), "# Ignored Tags:")
    assert "not set" in line


def test_the_preamble_is_plain_ascii(plugin_module):
    """A CSV may be opened by a spreadsheet under a different codepage."""
    header = _header(plugin_module, title="Rename Channels preview",
                     summary=[("Channels to rename", 1)])
    bad = sorted({c for c in header if ord(c) > 127})
    assert not bad, [hex(ord(c)) for c in bad]


def test_every_preamble_line_is_commented(plugin_module):
    """One uncommented line would be read as data by a spreadsheet import."""
    header = _header(plugin_module, title="X", summary=[("Channels to rename", 1)])
    stray = [line for line in header.splitlines() if line and not line.startswith("#")]
    assert stray == [], stray


def test_the_preamble_uses_no_em_dash(plugin_module):
    assert chr(0x2014) not in _header(plugin_module)


# --------------------------------------------------------------------------- #
# Every writer actually supplies a title and a result
#
# The tests above prove the generator can print those. They cannot see whether
# the four CSV writers pass them, and a generator that supports a summary nobody
# supplies leaves the file exactly as silent as before. Reads the source, because
# the values come from local variables inside each action.
# --------------------------------------------------------------------------- #
import ast  # noqa: E402
import functools  # noqa: E402
import os  # noqa: E402

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Channel-Maparr", "plugin.py")

CSV_WRITERS = [
    "preview_changes_action",
    "category_groups_dry_run_action",
    "_export_m3u_import_preview",
    "_export_seed_preview",
]


@functools.cache
def _plugin_tree():
    """Parsed once rather than once per parametrised writer."""
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _header_call(function_name):
    tree = _plugin_tree()
    func = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == function_name), None)
    assert func is not None, f"{function_name} is not defined"
    for call in ast.walk(func):
        if (isinstance(call, ast.Call)
                and ast.unparse(call.func).endswith("_generate_csv_settings_header")):
            return {k.arg: ast.unparse(k.value) for k in call.keywords}
    raise AssertionError(f"{function_name} does not build a CSV preamble")


# There is no test here that each writer PASSES a title and a summary. It would
# be redundant: _generate_csv_settings_header takes both as required positional
# parameters, so Python refuses a call that omits either, which is a better guard
# than reading this file for the keywords. What still needs checking is the
# CONTENT of what they pass, which is what follows.


def _summary_labels(writer):
    """The literal labels each writer passes in its summary list."""
    passed = _header_call(writer)
    assert "summary" in passed, (
        f"{writer} does not pass summary= by keyword, so its labels cannot be "
        f"read here: {passed}")
    node = ast.parse(passed["summary"], mode="eval").body
    assert isinstance(node, ast.List), passed["summary"]
    labels = []
    for element in node.elts:
        assert isinstance(element, ast.Tuple), ast.unparse(element)
        first = element.elts[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), \
            ast.unparse(element)
        labels.append(first.value)
    return labels


@pytest.mark.parametrize("writer", CSV_WRITERS)
def test_every_result_label_is_plain_ascii_and_carries_no_colon(writer):
    """A colon in the label would split the line in the wrong place.

    Each preamble line reads "# name: value", so a label containing a colon
    makes the name unparseable by anything reading the file back, this suite
    included. Plain ASCII for the same reason the rest of the preamble is.
    """
    for label in _summary_labels(writer):
        assert ":" not in label, f"{writer}: {label!r} contains a colon"
        assert all(ord(c) < 128 for c in label), f"{writer}: {label!r} is not ASCII"
        assert label[:1].isupper(), f"{writer}: {label!r} does not start a sentence"


@pytest.mark.parametrize("writer", CSV_WRITERS)
def test_every_writer_reports_at_least_one_count(writer):
    assert _summary_labels(writer), f"{writer} passes an empty summary"
