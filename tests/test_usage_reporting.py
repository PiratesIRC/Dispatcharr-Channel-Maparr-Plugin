"""Channel Mapparr reports its Channels Renamed total through the vendored usage client.

Report sites: run() (every return that is not a background start, with the live
settings or None when the call has no settings dict), the two background threads
when they end, and _record_channels_renamed after the ledger line is written
(forced). The checkbox sits in its own section at the end of the form.
"""
import json
import logging
import os

import pytest


class FakeUsage:
    def __init__(self, on_report=None):
        self.calls = []
        self.on_report = on_report

    def report(self, settings=None, logger=None, force=False):
        if self.on_report:
            self.on_report()
        self.calls.append((settings, force))


@pytest.fixture
def usage(plugin_module, monkeypatch):
    fake = FakeUsage()
    monkeypatch.setattr(plugin_module, "USAGE", fake)
    return fake


@pytest.fixture
def plugin(plugin_module, tmp_path):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.results_file = str(tmp_path / "loaded.json")
    p._last_bg_result = None
    return p


@pytest.fixture
def log():
    return logging.getLogger("test-channel-maparr")


def test_the_reporter_is_configured_for_channel_maparr(plugin_module):
    u = plugin_module.USAGE
    assert (u.plugin, u.counter, u.label) == ("channel-mapparr", "channels_renamed", "Channels Renamed")


def test_the_total_sums_the_rename_tally(plugin_module, tmp_path, monkeypatch):
    ledger = tmp_path / "c.jsonl"
    ledger.write_text(json.dumps({"channels": 5}) + "\n" + json.dumps({"channels": 7}) + "\n", encoding="utf-8")
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(ledger))
    assert plugin_module.USAGE.total_fn() == 12


def test_settings_are_read_under_the_key_of_the_installed_folder(plugin_module, monkeypatch):
    seen = []
    monkeypatch.setattr(plugin_module, "load_plugin_settings", lambda key, logger=None: seen.append(key) or {})
    plugin_module.USAGE.settings_fn()
    folder = os.path.dirname(os.path.abspath(plugin_module.__file__))
    assert seen == [os.path.basename(folder).replace(" ", "_").lower()]


@pytest.mark.parametrize("folder, key", [
    ("/data/plugins/channel-mapparr", "channel-mapparr"),  # copied by hand
    ("/data/plugins/channel_maparr", "channel_maparr"),    # release zip imported
    ("/data/plugins/channel_mapparr", "channel_mapparr"),  # installed from the Hub
    ("/data/plugins/Channel Mapparr/", "channel_mapparr"),
])
def test_the_plugin_key_is_the_one_the_loader_gives_the_folder(plugin_module, folder, key):
    assert plugin_module._plugin_key_for_dir(folder) == key


def test_a_written_tally_line_reports_forced_after_the_file_is_closed(plugin, plugin_module, usage, tmp_path, monkeypatch):
    ledger = tmp_path / "c.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(ledger))
    seen = []
    usage.on_report = lambda: seen.append(ledger.read_text(encoding="utf-8").count("\n"))
    assert plugin._record_channels_renamed(3, "rename_channels") is True
    assert usage.calls == [(None, True)]
    assert seen == [1], "the report ran before the line was on disk"


@pytest.mark.parametrize("count", [0, None])
def test_no_tally_line_means_no_report(plugin, plugin_module, usage, tmp_path, monkeypatch, count):
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(tmp_path / "c.jsonl"))
    plugin._record_channels_renamed(count, "rename_channels")
    assert usage.calls == []


def test_a_failed_tally_write_does_not_report(plugin, plugin_module, usage, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(tmp_path / "no-dir" / "c.jsonl"))
    plugin._record_channels_renamed(3, "rename_channels")
    assert usage.calls == []


def test_a_reporter_that_raises_never_breaks_the_tally(plugin, plugin_module, tmp_path, monkeypatch):
    class Exploding:
        def report(self, settings=None, logger=None, force=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(plugin_module, "USAGE", Exploding())
    ledger = tmp_path / "c.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "RENAME_LEDGER_FILE", str(ledger))
    assert plugin._record_channels_renamed(3, "rename_channels") is True
    assert ledger.read_text(encoding="utf-8").count("\n") == 1


def test_an_immediate_action_reports_once_with_the_live_settings(plugin, usage, log, monkeypatch):
    settings = {"x": 1}
    monkeypatch.setattr(plugin, "plugin_status_action", lambda s, l: {"status": "success", "message": "ok"})
    result = plugin.run("plugin_status", {}, {"settings": settings, "logger": log})
    assert result == {"status": "success", "message": "ok"}
    assert usage.calls == [(settings, False)]


def test_an_action_that_returns_an_error_still_reports(plugin, usage, log, monkeypatch):
    monkeypatch.setattr(plugin, "plugin_status_action", lambda s, l: {"status": "error", "error": "no"})
    result = plugin.run("plugin_status", {}, {"settings": {"x": 1}, "logger": log})
    assert result == {"status": "error", "error": "no"}
    assert len(usage.calls) == 1


def test_an_action_that_raises_still_reports_once(plugin, usage, log, monkeypatch):
    def boom(s, l):
        raise RuntimeError("x")
    monkeypatch.setattr(plugin, "plugin_status_action", boom)
    result = plugin.run("plugin_status", {}, {"settings": {"x": 1}, "logger": log})
    assert result == {"status": "error", "error": "x"}
    assert len(usage.calls) == 1


def test_a_call_with_no_settings_dict_passes_none_not_an_empty_dict(plugin, usage, log, monkeypatch):
    monkeypatch.setattr(plugin, "plugin_status_action", lambda s, l: {"status": "success", "message": "ok"})
    plugin.run("plugin_status", {}, {"logger": log})
    assert usage.calls == [(None, False)]


def test_a_background_start_does_not_report_on_return(plugin, usage, log, monkeypatch):
    monkeypatch.setattr(plugin, "import_m3u_streams_action",
                        lambda s, l: {"status": "ok", "message": "started", "background": True})
    result = plugin.run("import_m3u_streams", {}, {"settings": {}, "logger": log})
    assert result["background"] is True
    assert usage.calls == []


def test_the_import_thread_reports_once_when_it_ends_even_if_it_raises(plugin, usage, log, monkeypatch):
    def boom(settings, logger):
        raise RuntimeError("import broke")
    monkeypatch.setattr(plugin, "_do_import_m3u_streams", boom)
    plugin._do_import_m3u_streams_bg({}, log)
    assert usage.calls == [(None, False)]


def test_the_create_thread_reports_once_when_it_ends(plugin, usage, log, monkeypatch):
    monkeypatch.setattr(plugin, "_create_seed_channels", lambda *a, **k: [])
    monkeypatch.setattr(plugin, "_export_seed_preview", lambda *a, **k: ("/x.csv", "x.csv"))
    plugin._create_channels_from_streams_bg({}, log, None, 1)
    assert usage.calls == [(None, False)]


def test_the_create_thread_reports_once_even_if_it_raises(plugin, usage, log, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("create broke")
    monkeypatch.setattr(plugin, "_create_seed_channels", boom)
    plugin._create_channels_from_streams_bg({}, log, None, 1)
    assert usage.calls == [(None, False)]


def test_the_form_ends_with_the_usage_section_and_checkbox(plugin, plugin_module):
    fields = plugin.fields
    assert fields[-1] == plugin_module.with_usage_field([], plugin_module.USAGE)[0]
    assert fields[-2]["id"] == "_section_usage" and fields[-2]["type"] == "info"
    assert [f.get("id") for f in fields].count("share_usage_counts") == 1


def test_a_real_reporter_with_consent_starts_one_send(plugin_module, tmp_path, monkeypatch):
    started = []
    real = plugin_module.UsageReporter(
        plugin="channel-mapparr", counter="channels_renamed", label="Channels Renamed",
        total_fn=lambda: 3, data_dir=str(tmp_path / "plugin_stats"),
        start=lambda fn: started.append(fn), lock=lambda fd: True)
    monkeypatch.setattr(plugin_module, "USAGE", real)
    real.report({"share_usage_counts": True}, None, force=True)
    assert len(started) == 1


def test_a_real_reporter_without_consent_starts_nothing(plugin_module, tmp_path, monkeypatch):
    started = []
    real = plugin_module.UsageReporter(
        plugin="channel-mapparr", counter="channels_renamed", label="Channels Renamed",
        total_fn=lambda: 3, data_dir=str(tmp_path / "plugin_stats"),
        start=lambda fn: started.append(fn), lock=lambda fd: True)
    monkeypatch.setattr(plugin_module, "USAGE", real)
    real.report({"share_usage_counts": False}, None, force=True)
    assert started == []


def test_the_usage_setting_is_not_printed_in_the_csv_preamble(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    header = inst._generate_csv_settings_header(
        {"share_usage_counts": True}, "Rename Channels preview", [("Channels to rename", 1)])
    assert "Share anonymous usage counts" not in header
