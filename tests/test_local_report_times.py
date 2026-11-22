"""File names and the CSV preamble use Dispatcharr's timezone, not the UTC clock.

A Celery worker's datetime.now() is UTC, so a report named at 10:00 Central
was stamped 15:00. local_now() formats in the operator's zone. Stored machine
timestamps (processed_at, a backup's "written") stay as they are.
"""
import ast
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


@pytest.fixture
def progress_status(plugin_module):
    import importlib
    return importlib.import_module(plugin_module.__name__.rsplit(".", 1)[0] + ".progress_status")


def test_local_now_uses_the_operator_zone(progress_status, monkeypatch):
    monkeypatch.setattr(progress_status, "_display_tz", lambda: ZoneInfo("America/Chicago"))
    assert progress_status.local_now("%Z") in {"CST", "CDT"}


def test_local_now_falls_back_to_the_process_clock(progress_status, monkeypatch):
    monkeypatch.setattr(progress_status, "_display_tz", lambda: None)
    stamp = progress_status.local_now("%Y%m%d_%H%M%S")
    assert len(stamp) == 15 and stamp[8] == "_"


def test_plugin_names_files_with_the_local_clock_only():
    """The naive clock is used only for the stored "written" stamp of a logo backup."""
    text = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "datetime.now()" in line]
    assert len(lines) == 3, lines
    stored = [line for line in lines if "processed_at" in line or '"written"' in line]
    assert len(stored) == 3, lines


def test_every_file_name_timestamp_goes_through_local_now():
    tree = ast.parse((PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8"))
    naive = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "strftime"):
            base = node.func.value
            if (isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute)
                    and base.func.attr == "now"):
                naive.append(node.lineno)
    # Only the stored logo-backup "written" stamp may format the naive clock.
    assert len(naive) == 1, naive
