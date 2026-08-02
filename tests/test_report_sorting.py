"""Execute the report page's sorting script, rather than inspecting its markup.

The Python tests in tests/test_reports.py prove the page CONTAINS a sorting
script and the attributes it needs. They cannot prove the script works, because
pytest has no JavaScript engine. A test that only checks a script tag is present
is exactly the kind of guard that passes for months while proving nothing.

So this runs the shipped script in Node against a minimal document model built
from the real rendered page, and checks the row order that comes out. It is
skipped when Node is absent, which is the case on the continuous integration
runner, so it is a local safety net rather than a gate. tests/test_reports.py
still covers the markup contract everywhere.

The harness carries its own control: tests/report_sort_harness.js was verified
by running it against a script that does nothing, which fails four of its five
checks.
"""
import pathlib
import shutil
import subprocess

import pytest

from conftest import PLUGIN_DIR, _load_plugin_package  # noqa: F401

HARNESS = pathlib.Path(__file__).resolve().parent / "report_sort_harness.js"

# Channel numbers chosen so that a text sort and a number sort disagree: as text
# the order is 10, 2, 3, and as numbers it is 2, 3, 10. Names chosen so that a
# case-sensitive sort puts the lowercase entry last.
COLUMNS = [("channel_number", "Channel Number"),
           ("channel_name", "Channel Name"),
           ("status", "Status")]
ROWS = [
    {"channel_number": 3, "channel_name": "Charlie", "status": "Renamed"},
    {"channel_number": 10, "channel_name": "alpha", "status": "Skipped"},
    {"channel_number": 2, "channel_name": "Bravo", "status": "Renamed"},
]


@pytest.fixture(scope="module")
def reports():
    _load_plugin_package()
    import channel_maparr.reports as reports_module  # noqa: E402
    return reports_module


def _node():
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="Node is not installed on this machine")
def test_the_shipped_sorting_script_actually_sorts(reports, tmp_path):
    page = tmp_path / "report.html"
    script = tmp_path / "sort.js"

    model = reports.build_model(
        "Sorting check", COLUMNS, ROWS,
        account_names=[], settings={"dry_run_mode": True, "match_sensitivity": "strict"},
        databases=["US"], version="test", now=1_700_000_000.0)
    page.write_text(reports.render_html(model), encoding="utf-8")
    script.write_text(reports._SORT_SCRIPT, encoding="utf-8")

    result = subprocess.run(
        [_node(), str(HARNESS), str(page), str(script)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        "the sorting script did not behave as required:\n" + result.stdout + result.stderr)


@pytest.mark.skipif(_node() is None, reason="Node is not installed on this machine")
def test_the_harness_fails_a_script_that_does_nothing(tmp_path, reports):
    """The control. Without this, a harness that silently parsed zero rows would
    report success forever."""
    page = tmp_path / "report.html"
    script = tmp_path / "noop.js"

    model = reports.build_model(
        "Sorting check", COLUMNS, ROWS,
        account_names=[], settings={}, databases=["US"], version="test",
        now=1_700_000_000.0)
    page.write_text(reports.render_html(model), encoding="utf-8")
    script.write_text("// this script deliberately does nothing\n", encoding="utf-8")

    result = subprocess.run(
        [_node(), str(HARNESS), str(page), str(script)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode != 0, (
        "the harness passed a script that does nothing, so it proves nothing:\n"
        + result.stdout)
