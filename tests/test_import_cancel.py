"""Cancelling an M3U import returns the cancel result instead of raising.

_import_matched_streams returns its results dict, or, when the stop event is
set, a different dict ({"status": "ok", "message": "Import cancelled. ..."})
that has no "imports" key. _do_import_m3u_streams passed either one on to
_save_m3u_import_results and then counted import_results["imports"], so a
cancelled import raised KeyError instead of reporting how many channels were
created before the cancel. Found by basedpyright (reportArgumentType on the
"status" lookup), 2026-10-17.
"""
import pytest

CANCELLED = {"status": "ok", "message": "Import cancelled. 3 channels created before cancellation."}


@pytest.fixture
def import_steps_stubbed(plugin_instance, monkeypatch):
    monkeypatch.setattr(plugin_instance, "_fetch_streams_from_m3u_sources",
                        lambda settings, logger: [{"id": 1, "name": "ESPN"}])
    monkeypatch.setattr(plugin_instance, "_match_streams_to_categories",
                        lambda streams, settings, logger: ({"Sports": [{"id": 1}]}, []))
    monkeypatch.setattr(plugin_instance, "_ensure_category_groups_exist",
                        lambda categories, settings, logger: {"Sports": 10})
    monkeypatch.setattr(plugin_instance, "_save_m3u_import_results",
                        lambda *a, **k: pytest.fail("a cancelled import must not be saved as results"))
    return plugin_instance


def test_a_cancelled_import_returns_the_cancel_result(import_steps_stubbed, logger, monkeypatch):
    plugin = import_steps_stubbed
    monkeypatch.setattr(plugin, "_import_matched_streams", lambda *a, **k: dict(CANCELLED))

    result = plugin._do_import_m3u_streams({}, logger)

    assert result == CANCELLED
