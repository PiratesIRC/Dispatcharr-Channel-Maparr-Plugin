"""storage.bind rebinds the plugin's state constants to the data directory, once per import.

These tests drive storage.bind with a fake layout (storage._ps.resolve is replaced) and with
the legacy literals read from the source, so they never touch a real /data path.
"""
import ast
import importlib.util
import os

import pytest

from conftest import PLUGIN_DIR
from storage_helpers import (PLUGIN_SOURCE, STATE_NAMES, Log, make_cfg, make_layout,
                             source_literals, tmp_legacy)


@pytest.fixture
def storage(plugin_impl):
    return plugin_impl.storage


def test_bind_rebinds_every_state_row_and_records_the_legacy_paths(storage, monkeypatch, tmp_path):
    legacy = tmp_legacy(tmp_path)
    cfg = make_cfg(dict(legacy, EXPORT_DIR="/data/exports"))
    layout = make_layout(storage, str(tmp_path))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)

    got = storage.bind(cfg, str(tmp_path / "plugins" / "channel-mapparr" / "plugin.py"), Log())

    assert got is layout
    assert cfg.LEGACY_PATHS == legacy
    assert cfg.RESULTS_FILE == os.path.join(layout.data_dir, "channel_mapparr_loaded_channels.json")
    assert cfg.PROGRESS_FILE == os.path.join(layout.data_dir, "channel_mapparr_progress.json")
    assert cfg.M3U_IMPORT_RESULTS_FILE == os.path.join(
        layout.data_dir, "channel_mapparr_m3u_import_results.json")
    assert cfg.RENAME_LEDGER_FILE == os.path.join(layout.data_dir, "channel_maparr_rename_counts.jsonl")
    assert cfg.LOGO_BACKUP_DIR == os.path.join(layout.data_dir, "logo_backups")
    assert cfg.USAGE_DATA_DIR == os.path.join(layout.data_dir, "usage")
    assert cfg.REPORT_DIR == layout.shared_reports_dir("channel-mapparr")
    # User-facing paths are not table rows, so bind leaves them alone.
    assert cfg.EXPORT_DIR == "/data/exports"
    assert cfg.STORAGE_MIGRATION is not None


def test_bind_names_the_legacy_paths_the_way_the_source_does(storage):
    """The table row names must be PluginConfig attributes the source defines."""
    literals = source_literals()
    for name in STATE_NAMES:
        assert name in literals, f"{name} is not a PluginConfig literal"
        assert literals[name].startswith("/data/")


def test_legacy_mode_keeps_the_old_paths_exactly(storage, monkeypatch, tmp_path):
    literals = source_literals()
    cfg = make_cfg(dict(literals))
    monkeypatch.setattr(
        storage._ps, "resolve",
        lambda spec, plugin_dir, key, logger: storage.Layout(True, None, None, key, "test"))

    layout = storage.bind(cfg, str(tmp_path / "plugin.py"), Log())

    assert layout.legacy is True
    for name in STATE_NAMES:
        assert getattr(cfg, name) == literals[name], name
    assert cfg.STORAGE_MIGRATION is None


def test_a_failed_carry_over_falls_back_to_the_legacy_paths(storage, monkeypatch, tmp_path):
    literals = source_literals()
    cfg = make_cfg(dict(literals))
    layout = make_layout(storage, str(tmp_path))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(storage._ps, "migrate", boom)
    log = Log()
    result = storage.bind(cfg, str(tmp_path / "plugin.py"), log)

    assert result.legacy is True
    for name in STATE_NAMES:
        assert getattr(cfg, name) == literals[name], name
    assert log.errors, "a failed carry-over must be logged"


def test_the_session_plugin_runs_in_legacy_mode(plugin_impl):
    """No Dispatcharr sandbox in the test process, so the real module never resolves a directory."""
    assert plugin_impl.PluginConfig.STORAGE_LAYOUT.legacy is True


def test_the_usage_reporter_is_built_after_bind_on_the_rebound_directory(plugin_impl):
    """The usage reporter is built at import, so it needs the rebound directory and must come
    after storage.bind in the source (a path captured before bind would stay legacy)."""
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    bind_line = usage_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "bind" \
                and getattr(node.func.value, "id", "") == "storage":
            bind_line = node.lineno
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "USAGE" for t in node.targets):
            usage_line = node.lineno
            keywords = {k.arg: k.value for k in node.value.keywords}
            assert ast.unparse(keywords["data_dir"]) == "PluginConfig.USAGE_DATA_DIR"
    assert bind_line is not None and usage_line is not None
    assert bind_line < usage_line


def test_the_bound_reports_folder_reaches_the_report_writer_and_the_notify_bridge(plugin_impl):
    """Both modules must hold the value bind resolved. The import sets them from PluginConfig;
    the conftest redirects all three together, so the equality holds in every test."""
    cfg = plugin_impl.PluginConfig
    assert plugin_impl._reports_module.REPORT_DIR == cfg.REPORT_DIR
    assert plugin_impl._notify_bridge_module.REPORT_LOCATION == cfg.REPORT_DIR


def test_the_plugin_reads_its_paths_from_the_rebound_constants(plugin_impl, monkeypatch, tmp_path):
    """The scan, the progress writer and the import results take their paths from PluginConfig."""
    cfg = plugin_impl.PluginConfig
    results = str(tmp_path / "rebound_results.json")
    progress = str(tmp_path / "rebound_progress.json")
    monkeypatch.setattr(cfg, "RESULTS_FILE", results)
    monkeypatch.setattr(cfg, "PROGRESS_FILE", progress)
    plugin = plugin_impl.Plugin()
    assert plugin.results_file == results


def test_the_module_defaults_name_the_shared_reports_folder(plugin_impl):
    """A module imported without plugin.py must not default to the legacy reports folder.

    Each module is loaded fresh from its file, so the conftest redirect of the bound values
    does not apply, and the literal the module ships with is what is checked.
    """
    shared ="/data/plugins/_shared/newsflasharr/channel-mapparr"
    for filename, attribute in (("reports.py", "REPORT_DIR"), ("notify_bridge.py", "REPORT_LOCATION")):
        spec = importlib.util.spec_from_file_location("fresh_" + filename[:-3], PLUGIN_DIR / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert getattr(module, attribute) == shared, f"{filename} {attribute} defaults to the legacy folder"


def test_every_legacy_path_is_redirected_away_from_the_container(plugin_impl):
    """The autouse fixture points every legacy path the bind table names at tmp_path."""
    cfg = plugin_impl.PluginConfig
    assert cfg.LEGACY_PATHS, "bind must have recorded the legacy paths"
    still_real = {name: getattr(cfg, name) for name in cfg.LEGACY_PATHS
                  if str(getattr(cfg, name)).startswith("/data")}
    assert still_real == {}, f"tests would write these container paths: {still_real}"
