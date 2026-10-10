import contextlib
import json
import logging
import types

import pytest


@pytest.fixture
def plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


@pytest.fixture
def ledger(plugin_module, tmp_path, monkeypatch):
    path = tmp_path / "channel_maparr_rename_counts.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(path))
    return path


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_run_that_wrote_names_records_the_count_and_the_mode(plugin, ledger):
    assert plugin._record_channels_renamed(12, "rename_channels") is True
    rows = _lines(ledger)
    assert len(rows) == 1
    assert rows[0]["channels"] == 12
    assert rows[0]["mode"] == "rename_channels"


def test_each_run_appends_rather_than_replacing(plugin, ledger):
    for n in (12, 3, 7):
        plugin._record_channels_renamed(n, "rename_unknown_channels")
    assert [r["channels"] for r in _lines(ledger)] == [12, 3, 7]


def test_one_line_holds_exactly_one_json_object_with_no_names(plugin, ledger):
    plugin._record_channels_renamed(12, "rename_channels")
    raw = ledger.read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1
    assert set(_lines(ledger)[0]) == {"ts", "channels", "mode"}


def test_a_run_that_wrote_nothing_leaves_no_line(plugin, ledger):
    assert plugin._record_channels_renamed(0, "rename_channels") is False
    assert _lines(ledger) == []


@pytest.mark.parametrize("bad", [None, "12", 1.5, True, False])
def test_a_count_that_is_not_a_whole_number_is_refused_and_says_so(plugin, ledger, bad, caplog):
    with caplog.at_level("WARNING"):
        assert plugin._record_channels_renamed(bad, "rename_channels") is False
    assert _lines(ledger) == []
    assert caplog.records, f"{bad!r} was refused without a word"


def test_a_negative_count_is_refused_without_a_line(plugin, ledger):
    assert plugin._record_channels_renamed(-4, "rename_channels") is False
    assert _lines(ledger) == []


def test_a_tally_that_cannot_be_written_does_not_raise(plugin, plugin_module, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE",
                        str(tmp_path / "no-such-directory" / "counts.jsonl"))
    assert plugin._record_channels_renamed(12, "rename_channels") is False


def _results_file(tmp_path, rows):
    path = tmp_path / "loaded.json"
    path.write_text(json.dumps({"changes": rows}), encoding="utf-8")
    return str(path)


def _rename_row(i):
    return {"channel_id": i, "status": "Renamed", "current_name": f"old {i}", "new_name": f"new {i}"}


def _unknown_row(i):
    return {"channel_id": i, "status": "Skipped", "current_name": f"plain {i}"}


def test_rename_channels_records_only_the_names_bulk_update_reports_written(plugin, plugin_module, ledger, tmp_path, monkeypatch):
    plugin.results_file = _results_file(tmp_path, [_rename_row(1), _rename_row(2), _rename_row(3)])
    monkeypatch.setattr(plugin, "_ignore_tokens_error", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_bulk_update_channels", lambda updates, fields, logger: 2)
    result = plugin.rename_channels_action({"dry_run_mode": False}, plugin_module.LOGGER)
    assert result["status"] == "success"
    assert [(r["channels"], r["mode"]) for r in _lines(ledger)] == [(2, "rename_channels")]


def test_rename_channels_dry_run_writes_no_line(plugin, plugin_module, ledger, tmp_path, monkeypatch):
    plugin.results_file = _results_file(tmp_path, [_rename_row(1)])
    monkeypatch.setattr(plugin, "preview_changes_action", lambda s, l: {"status": "success", "message": "preview"})
    plugin.rename_channels_action({"dry_run_mode": True}, plugin_module.LOGGER)
    assert _lines(ledger) == []


def test_rename_unknown_records_the_suffixed_names_under_its_own_mode(plugin, plugin_module, ledger, tmp_path, monkeypatch):
    plugin.results_file = _results_file(tmp_path, [_unknown_row(1), _unknown_row(2)])
    monkeypatch.setattr(plugin, "_ignore_tokens_error", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_bulk_update_channels", lambda updates, fields, logger: 2)
    plugin.rename_unknown_channels_action({"dry_run_mode": False, "unknown_suffix": " [Unk]"}, plugin_module.LOGGER)
    assert [(r["channels"], r["mode"]) for r in _lines(ledger)] == [(2, "rename_unknown_channels")]


def test_rename_unknown_dry_run_writes_no_line(plugin, plugin_module, ledger, tmp_path, monkeypatch):
    plugin.results_file = _results_file(tmp_path, [_unknown_row(1)])
    monkeypatch.setattr(plugin, "_ignore_tokens_error", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_bulk_update_channels", lambda *a, **k: pytest.fail("a dry run wrote"))
    plugin.rename_unknown_channels_action({"dry_run_mode": True, "unknown_suffix": " [Unk]"}, plugin_module.LOGGER)
    assert _lines(ledger) == []


def test_bulk_update_returns_the_number_it_wrote_and_zero_for_nothing(plugin, plugin_module, monkeypatch):
    rows = {1: types.SimpleNamespace(id=1, name="old"), 2: types.SimpleNamespace(id=2, name="old")}
    monkeypatch.setattr(plugin_module.Channel.objects, "filter", lambda **k: list(rows.values()))
    monkeypatch.setattr(plugin_module.Channel.objects, "bulk_update", lambda *a, **k: None)
    monkeypatch.setattr(plugin_module, "transaction", types.SimpleNamespace(atomic=lambda: contextlib.nullcontext()))
    log = logging.getLogger("test-channel-maparr")
    assert plugin._bulk_update_channels([], ["name"], log) == 0
    updates = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 9, "name": "c"}]
    assert plugin._bulk_update_channels(updates, ["name"], log) == 2


def test_a_second_identical_run_counts_nothing(plugin, plugin_module, monkeypatch):
    rows = {1: types.SimpleNamespace(id=1, name="old"), 2: types.SimpleNamespace(id=2, name="old")}
    monkeypatch.setattr(plugin_module.Channel.objects, "filter", lambda **k: list(rows.values()))
    monkeypatch.setattr(plugin_module.Channel.objects, "bulk_update", lambda *a, **k: None)
    monkeypatch.setattr(plugin_module, "transaction", types.SimpleNamespace(atomic=lambda: contextlib.nullcontext()))
    log = logging.getLogger("test-channel-maparr")
    updates = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert plugin._bulk_update_channels(updates, ["name"], log) == 2
    assert plugin._bulk_update_channels(updates, ["name"], log) == 0
