"""An AST guard pinning that the emailed report is actually WIRED IN.

Unit tests on reports.py and notify_bridge.py in isolation prove those modules
work. They do not prove anything calls them. Stream-Mapparr shipped exactly that
mistake: a report module that was fully unit tested and that no real run ever
invoked.

This guard also carries synthetic self-tests. An AST guard with no positive
fixture is inert: it returns exit 0 for months while proving nothing, because a
renamed function or a changed call shape makes it find zero of everything, which
looks identical to finding zero problems.
"""
import ast
import pathlib

import pytest

PLUGIN_PY = pathlib.Path(__file__).resolve().parent.parent / "Channel-Maparr" / "plugin.py"

# Exactly the functions that write a CSV export AND therefore must report.
# organize_by_category_action is deliberately absent: a real run of it writes no
# export at all, only its dry-run branch does.
EXPECTED_EMIT_SITES = {
    "preview_changes_action",
    "category_groups_dry_run_action",
    "_do_import_m3u_streams",
}

HELPER = "_build_and_emit_report"


@pytest.fixture(scope="module")
def tree():
    return ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))


def _functions_calling(tree, method_name):
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == method_name):
                found.add(node.name)
    return found


def test_every_expected_export_site_builds_and_emits_a_report(tree):
    assert _functions_calling(tree, HELPER) >= EXPECTED_EMIT_SITES


def test_no_unexpected_function_emits_a_report(tree):
    """Pinned so a fourth export writer added later cannot quietly skip the
    report, and so an emit cannot be added somewhere unreviewed."""
    allowed = EXPECTED_EMIT_SITES | {"email_report_now_action"}
    assert _functions_calling(tree, HELPER) == allowed


def test_the_report_helper_is_called_with_an_explicit_column_allow_list(tree):
    """The columns argument is the whole redaction boundary. A call that passed
    rows without naming its columns would emit whatever the rows carried."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == HELPER):
            continue
        keywords = {kw.arg for kw in node.keywords}
        assert "columns" in keywords, "a call omits the columns allow list"
        assert "rows" in keywords


def test_the_report_modules_never_read_a_results_or_export_file(tree):
    """The report is built from in-memory rows handed to the helper. If the
    helper ever grew a path argument the settings header could come back."""
    source = PLUGIN_PY.read_text(encoding="utf-8")
    marker = source.index(f"def {HELPER}")
    body = source[marker:marker + 4000]
    assert "EXPORT_DIR" not in body
    assert "results_file" not in body


# --------------------------------------------------------------------------- #
# Synthetic self-tests: prove the guard can actually SEE what it looks for.
# Without these a rename makes every assertion above vacuously true.
# --------------------------------------------------------------------------- #

def test_the_guard_detects_a_call_when_one_is_present():
    planted = ast.parse(
        "class C:\n"
        "    def writer(self):\n"
        f"        self.{HELPER}(columns=[], rows=[])\n")
    assert _functions_calling(planted, HELPER) == {"writer"}


def test_the_guard_detects_the_absence_of_a_call():
    planted = ast.parse(
        "class C:\n"
        "    def writer(self):\n"
        "        self.something_else()\n")
    assert _functions_calling(planted, HELPER) == set()


def test_the_guard_would_fail_if_a_writer_stopped_reporting():
    planted = ast.parse(
        "class C:\n"
        "    def preview_changes_action(self):\n"
        "        pass\n")
    assert not _functions_calling(planted, HELPER) >= EXPECTED_EMIT_SITES
