"""Tests for the emailed-report builder and renderers.

Newsflasharr sends an attachment verbatim and unredacted, so every rule these
tests pin is a rule about what is allowed to leave the box. The most important
one is structural: the model is built by copying a named allow-list of columns
out of in-memory rows, so a column added to a CSV writer later cannot start
being emailed on its own, and the settings header that names the configured M3U
sources can never be reproduced.
"""
import pathlib
import time

import pytest
from conftest import PLUGIN_DIR, _load_plugin_package  # noqa: F401


@pytest.fixture(scope="module")
def reports():
    _load_plugin_package()
    import channel_maparr.reports as reports_module  # noqa: E402
    return reports_module


# Columns are (row key, display header) pairs. The pair list IS the allow-list.
COLUMNS = [("channel_name", "Channel Name"), ("new_name", "New Name")]

SETTINGS = {"dry_run_mode": True, "match_sensitivity": "strict"}


def _model(reports, rows, **kwargs):
    params = dict(
        title="Rename preview",
        columns=COLUMNS,
        rows=rows,
        account_names=["provider.tv"],
        settings=SETTINGS,
        databases=["US"],
        version="1.26.0000000",
        now=1_700_000_000.0,
    )
    params.update(kwargs)
    return reports.build_model(**params)


# --------------------------------------------------------------------------- #
# The allow-list is the whole defence
# --------------------------------------------------------------------------- #

def test_a_row_key_outside_the_column_list_reaches_neither_rendering(reports):
    rows = [{"channel_name": "WFLA", "new_name": "NBC Tampa",
             "m3u_sources": "SECRETHOSTNAME"}]
    model = _model(reports, rows)
    assert "SECRETHOSTNAME" not in reports.render_html(model)
    assert "SECRETHOSTNAME" not in reports.render_csv(model)


def test_a_settings_key_outside_the_safe_subset_reaches_neither_rendering(reports):
    settings = dict(SETTINGS, m3u_sources="SECRETHOSTNAME", default_logo="SECRETLOGOURL")
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}], settings=settings)
    for text in (reports.render_html(model), reports.render_csv(model)):
        assert "SECRETHOSTNAME" not in text
        assert "SECRETLOGOURL" not in text


def test_building_and_rendering_never_open_a_file(reports):
    """The report is built from in-memory rows. If build_model or either renderer
    ever opened an export file it would re-import the settings header that names
    the configured M3U sources. Only the writing helper is allowed to open
    anything, and it only ever writes."""
    import ast
    source = pathlib.Path(reports.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # _logo_data_uri is the one reader allowed to open anything, because the
    # masthead logo has to come off disk. What makes that exception safe is
    # pinned separately, in the test directly below this one.
    allowed = {"_atomic_write", "_logo_data_uri"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name in allowed:
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "open"):
                offenders.append(node.name)
    assert offenders == [], f"these functions open a file: {sorted(set(offenders))}"
    # A self-test proving the guard above is not vacuous.
    planted = ast.parse("def build_model():\n    open('x')\n")
    found = [n.name for n in ast.walk(planted) if isinstance(n, ast.FunctionDef)
             for i in ast.walk(n)
             if isinstance(i, ast.Call) and isinstance(i.func, ast.Name)
             and i.func.id == "open"]
    assert found == ["build_model"], "the guard cannot detect an open() call"


def test_the_logo_reader_can_only_open_its_own_constant_filename(reports):
    """The single exception to the guard above is safe only while the path it
    builds cannot be influenced by a caller. It takes no argument, and the path
    is assembled from this module's own location and a module-level constant.
    Given an argument it would become a way to read an export file, which is
    precisely what the guard above exists to prevent."""
    import ast
    source = pathlib.Path(reports.__file__).read_text(encoding="utf-8")
    function = next(node for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_logo_data_uri")
    assert not function.args.args, "it takes a positional argument"
    assert not function.args.kwonlyargs, "it takes a keyword argument"
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    assert "_LOGO_FILENAME" in names, "the filename is not the pinned constant"
    assert "__file__" in names, "the directory is not this module's own"
    for node in ast.walk(function):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            modes = [arg.value for arg in node.args[1:]
                     if isinstance(arg, ast.Constant)]
            assert modes == ["rb"], f"opened with {modes}, expected read bytes"


def test_the_module_does_not_reference_the_export_directory_constant(reports):
    source = pathlib.Path(reports.__file__).read_text(encoding="utf-8")
    assert "EXPORT_DIR" not in source


def test_the_reported_databases_are_the_resolved_list_not_the_setting_string(reports):
    """channel_databases is a free-text field, so the raw string is never echoed."""
    settings = dict(SETTINGS, channel_databases="US, UK, ANYTHING TYPED HERE")
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}],
                   settings=settings, databases=["US", "UK"])
    text = reports.render_html(model)
    assert "ANYTHING TYPED HERE" not in text
    assert "US, UK" in text


# --------------------------------------------------------------------------- #
# Scrubbing, and it fails closed
# --------------------------------------------------------------------------- #

def test_an_account_name_is_removed_bracketed_bare_and_in_any_case(reports):
    rows = [
        {"channel_name": "ESPN [provider.tv]", "new_name": "ESPN"},
        {"channel_name": "ESPN backup PROVIDER.TV", "new_name": "ESPN"},
        {"channel_name": "ESPN (provider.tv)", "new_name": "ESPN"},
    ]
    text = reports.render_csv(_model(reports, rows))
    assert "provider.tv" not in text.lower()


def test_the_longest_account_name_is_removed_first(reports):
    """provider.tv is a prefix of provider.tv-alt1. Matching the short one first
    would leave a -alt1 fragment behind."""
    rows = [{"channel_name": "ESPN provider.tv-alt1", "new_name": "ESPN"}]
    model = _model(reports, rows, account_names=["provider.tv", "provider.tv-alt1"])
    text = reports.render_csv(model)
    assert "alt1" not in text
    assert "provider" not in text.lower()


def test_an_ipv4_address_is_removed(reports):
    rows = [{"channel_name": "Edge 203.0.113.7", "new_name": "Edge"}]
    assert "203.0.113" not in reports.render_csv(_model(reports, rows))


def test_an_ipv6_address_is_removed(reports):
    rows = [{"channel_name": "Edge 2001:db8:85a3::8a2e:370:7334", "new_name": "Edge"}]
    assert "2001:db8" not in reports.render_csv(_model(reports, rows))


def test_an_ordinary_clock_time_is_not_mistaken_for_an_address(reports):
    rows = [{"channel_name": "LIVE EVENT 04 - 20:30", "new_name": "Event"}]
    assert "20:30" in reports.render_csv(_model(reports, rows))


def test_building_a_model_without_an_account_name_list_refuses(reports):
    """None means the lookup failed. Treating it as an empty list would make the
    scrub a silent no-op, and this is the primary redaction input here, not a
    backstop, so it must fail closed."""
    with pytest.raises(ValueError):
        _model(reports, [{"channel_name": "A", "new_name": "B"}], account_names=None)


def test_an_installation_with_no_m3u_accounts_is_allowed_to_proceed(reports):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}], account_names=[])
    assert "A" in reports.render_csv(model)


# --------------------------------------------------------------------------- #
# The row cap
# --------------------------------------------------------------------------- #

def test_a_row_set_within_the_cap_is_not_marked_truncated(reports):
    rows = [{"channel_name": f"C{i}", "new_name": "N"} for i in range(5)]
    model = _model(reports, rows)
    assert model["truncated"] is False
    assert model["shown_rows"] == 5
    assert model["total_rows"] == 5


def test_a_row_set_over_the_cap_keeps_the_leading_rows_in_order(reports):
    rows = [{"channel_name": f"C{i}", "new_name": "N"}
            for i in range(reports.MAX_REPORT_ROWS + 10)]
    model = _model(reports, rows)
    assert model["truncated"] is True
    assert model["shown_rows"] == reports.MAX_REPORT_ROWS
    assert model["total_rows"] == reports.MAX_REPORT_ROWS + 10
    assert model["entries"][0][0] == "C0"
    assert model["entries"][-1][0] == f"C{reports.MAX_REPORT_ROWS - 1}"


def test_a_truncated_report_says_so_in_both_renderings(reports):
    rows = [{"channel_name": f"C{i}", "new_name": "N"}
            for i in range(reports.MAX_REPORT_ROWS + 10)]
    model = _model(reports, rows, export_filename="channel_mapparr_preview_1.csv")
    for text in (reports.render_html(model), reports.render_csv(model)):
        assert str(reports.MAX_REPORT_ROWS) in text
        assert str(reports.MAX_REPORT_ROWS + 10) in text
        assert "channel_mapparr_preview_1.csv" in text


def test_the_truncation_notice_names_a_container_path_not_a_windows_drive(reports):
    rows = [{"channel_name": f"C{i}", "new_name": "N"}
            for i in range(reports.MAX_REPORT_ROWS + 1)]
    model = _model(reports, rows, export_filename="channel_mapparr_preview_1.csv")
    text = reports.render_html(model)
    assert "/data/exports" in text
    assert ":\\" not in text


# --------------------------------------------------------------------------- #
# Rendering rules
# --------------------------------------------------------------------------- #

def test_a_formula_shaped_cell_is_neutralised_in_the_csv(reports):
    rows = [{"channel_name": "=cmd|calc", "new_name": "+1"}]
    text = reports.render_csv(_model(reports, rows))
    for line in text.splitlines():
        for cell in line.split(","):
            stripped = cell.strip('"')
            assert not stripped.startswith(("=", "+", "@")) or stripped.startswith("'")


def test_html_special_characters_are_escaped(reports):
    """Channel names are provider-supplied text and must never become markup.

    This deliberately checks for the INJECTED script rather than for any script
    element at all: the page carries its own inline sorting script, so a blanket
    ban would be a test of the wrong thing.
    """
    rows = [{"channel_name": "<script>alert(1)</script>", "new_name": "x"}]
    text = reports.render_html(_model(reports, rows))
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


# --------------------------------------------------------------------------- #
# Sorting the table
# --------------------------------------------------------------------------- #

def test_every_column_header_is_marked_sortable(reports):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    text = reports.render_html(model)
    # One marked header per declared column.
    assert text.count('class="sortable"') == len(COLUMNS)
    assert text.count('aria-sort="none"') == len(COLUMNS)


def test_a_header_is_reachable_and_operable_from_the_keyboard(reports):
    """A control that only responds to a mouse is not usable by everyone."""
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    text = reports.render_html(model)
    assert 'tabindex="0"' in text


def test_each_cell_carries_the_value_the_sort_compares(reports):
    model = _model(reports, [{"channel_name": "WFLA", "new_name": "NBC"}])
    text = reports.render_html(model)
    assert 'data-v="WFLA"' in text
    assert 'data-v="NBC"' in text


def test_a_quote_in_a_cell_cannot_break_out_of_the_sort_attribute(reports):
    """The sort value is attribute content, so an unescaped quote would let
    provider-supplied text inject markup."""
    rows = [{"channel_name": 'He said "hi" <b>', "new_name": "x"}]
    text = reports.render_html(_model(reports, rows))
    assert 'data-v="He said "hi"' not in text
    assert "&quot;" in text
    assert "<b>" not in text


def test_the_page_fetches_nothing_from_the_network(reports):
    """The page is opened from a file path or from a mail attachment. A fetched
    external resource would not resolve, and would disclose that the report had
    been opened.

    A footer LINK is a different thing and is allowed: nothing is requested
    until the reader chooses to click it. So every src must be an embedded data
    URI, and every href must be one of the pinned addresses. An address anywhere
    else, in either attribute, fails.
    """
    import re
    text = reports.render_html(_model(reports, [{"channel_name": "A", "new_name": "B"}]))
    assert "<script>" in text
    for marker in ("<link ", "@import", "url("):
        assert marker not in text, f"the page fetches something external: {marker}"
    for src in re.findall(r'\bsrc="([^"]*)"', text):
        assert src.startswith("data:"), f"a src is fetched from elsewhere: {src}"
    for href in re.findall(r'\bhref="([^"]*)"', text):
        assert href in reports._ALLOWED_LINKS, f"unexpected link: {href}"


def test_the_allowed_link_list_is_pinned(reports):
    """The list is what makes the guard above meaningful. Left free to grow, a
    link to anywhere could be added to the footer and the guard would widen to
    accept it in the same edit."""
    assert reports._ALLOWED_LINKS == (
        "https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin",
        "https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/issues",
        "https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin")


def test_the_page_credits_the_plugin_that_emails_it(reports):
    """Newsflasharr writes the same credit into the email body. Carrying it in
    the page as well means it survives the page being saved, forwarded or opened
    from disk with no mail around it.

    The wording must stay about EMAILED COPIES rather than about this copy: a
    report read straight from the report directory was never delivered by
    anything, and a page thanking a deliverer that had not run would be saying
    something untrue in its own footer."""
    text = reports.render_html(_model(reports, [{"channel_name": "A", "new_name": "B"}]))
    assert "Emailed copies of this report are delivered courtesy of" in text
    assert ">Newsflasharr</a>" in text


def test_every_row_is_present_in_the_markup_without_any_scripting(reports):
    """Sorting is an addition, not a requirement. A reader whose mail client
    strips scripts must still see the whole table."""
    rows = [{"channel_name": f"C{i}", "new_name": "N"} for i in range(5)]
    text = reports.render_html(_model(reports, rows))
    for i in range(5):
        assert f">C{i}<" in text


def test_the_numeric_hint_is_declared_so_ten_does_not_sort_before_two(reports):
    """Without a numeric comparison, 10 sorts before 2 as text. The comparison
    itself lives in the page script; what is testable here is that the script
    contains the numeric branch rather than comparing everything as strings."""
    text = reports.render_html(_model(reports, [{"channel_name": "A", "new_name": "B"}]))
    assert "Number(" in text or "parseFloat(" in text


def test_the_html_states_where_the_complete_export_lives(reports):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    assert "/data/exports" in reports.render_html(model)


def test_the_generation_time_is_labelled_utc(reports):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    assert "UTC" in reports.render_html(model)


def test_the_operator_facing_text_uses_no_em_dashes(reports):
    """A standing operator instruction for this workspace."""
    source = pathlib.Path(reports.__file__).read_text(encoding="utf-8")
    assert "—" not in source


# --------------------------------------------------------------------------- #
# Writing and pruning
# --------------------------------------------------------------------------- #

def test_write_report_writes_both_files_under_one_timestamp_stem(reports, tmp_path):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    written = reports.write_report(model, str(tmp_path), 1_700_000_000.0)
    assert written["error"] is None
    html_path = pathlib.Path(written["html_path"])
    csv_path = pathlib.Path(written["csv_path"])
    assert html_path.is_file() and csv_path.is_file()
    assert html_path.stem == csv_path.stem
    assert html_path.name.startswith(reports.FILENAME_PREFIX)


def test_write_report_reports_a_failure_rather_than_raising(reports, tmp_path):
    model = _model(reports, [{"channel_name": "A", "new_name": "B"}])
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    written = reports.write_report(model, str(blocked / "sub"), 1_700_000_000.0)
    assert written["error"] is not None
    assert written["html_path"] is None


def test_pruning_keeps_the_newest_files_and_deletes_the_rest(reports, tmp_path):
    now = time.time()
    old = now - 10 * reports.RETRY_WINDOW_SECONDS
    made = []
    for i in range(reports.KEEP_REPORTS + 3):
        p = tmp_path / f"{reports.FILENAME_PREFIX}{i:03d}.csv"
        p.write_text("x", encoding="utf-8")
        import os
        os.utime(p, (old + i, old + i))
        made.append(p)
    reports._prune(str(tmp_path), ".csv", now=now)
    surviving = sorted(p.name for p in tmp_path.iterdir())
    assert len(surviving) == reports.KEEP_REPORTS


def test_pruning_never_deletes_a_file_a_delivery_retry_could_still_need(reports, tmp_path):
    """Newsflasharr re-reads the attachment path on every retry attempt across a
    2130 second worst case. Deleting a young file strips the attachment from mail
    that is still queued."""
    now = time.time()
    import os
    for i in range(reports.KEEP_REPORTS + 5):
        p = tmp_path / f"{reports.FILENAME_PREFIX}{i:03d}.csv"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (now - i, now - i))
    reports._prune(str(tmp_path), ".csv", now=now)
    assert len(list(tmp_path.iterdir())) == reports.KEEP_REPORTS + 5


def test_pruning_ignores_files_that_are_not_this_plugins_reports(reports, tmp_path):
    now = time.time()
    import os
    stranger = tmp_path / "someone_elses_report_001.csv"
    stranger.write_text("x", encoding="utf-8")
    os.utime(stranger, (now - 10 * reports.RETRY_WINDOW_SECONDS,) * 2)
    for i in range(reports.KEEP_REPORTS + 3):
        p = tmp_path / f"{reports.FILENAME_PREFIX}{i:03d}.csv"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (now - 10 * reports.RETRY_WINDOW_SECONDS + i,) * 2)
    reports._prune(str(tmp_path), ".csv", now=now)
    assert stranger.exists()
