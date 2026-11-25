"""The carry-over copies old state into the data directory once and never destroys anything.

Proven here: a new file wins over an old one, an old file is never deleted, renamed or edited,
the rename tally and the usage install id arrive byte-identical, a failed install id copy blocks
the usage reports of that process, and migrated.json records the pass. Every path is under tmp_path.
"""
import ast
import json
import os
from types import SimpleNamespace

import pytest

from storage_helpers import PLUGIN_SOURCE, Log, make_cfg, make_layout, tmp_legacy


@pytest.fixture
def storage(plugin_impl):
    return plugin_impl.storage


def build(storage, tmp_path, monkeypatch):
    """Bind a fake non-legacy layout with real old files under tmp_path; return (cfg, layout, legacy)."""
    legacy = tmp_legacy(tmp_path)
    cfg = make_cfg(dict(legacy))
    cfg.LEGACY_PATHS = dict(legacy)
    layout = make_layout(storage, str(tmp_path / "root"))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)
    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    return cfg, layout, legacy


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def items(storage, cfg, layout):
    return storage.items_for(cfg, layout)


def reset_flags(storage):
    """migrate skips a data dir it already settled in this process; clear that per test."""
    state = storage._ps._state(storage.SPEC)
    state.migrated_dirs.clear()


def test_a_new_file_wins_and_the_old_file_is_untouched(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    write(legacy["RESULTS_FILE"], b'{"old": true}\n')
    new_results = os.path.join(layout.data_dir, "channel_mapparr_loaded_channels.json")
    write(new_results, b'{"new": true}\n')
    old_stat = os.stat(legacy["RESULTS_FILE"])

    storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    with open(new_results, "rb") as fh:
        assert fh.read() == b'{"new": true}\n'
    with open(legacy["RESULTS_FILE"], "rb") as fh:
        assert fh.read() == b'{"old": true}\n'
    now = os.stat(legacy["RESULTS_FILE"])
    assert (now.st_size, now.st_mtime_ns) == (old_stat.st_size, old_stat.st_mtime_ns)


def test_an_old_file_is_copied_and_never_deleted_or_edited(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    write(legacy["RESULTS_FILE"], b'{"scan": 1}\n')

    result = storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    new_results = os.path.join(layout.data_dir, "channel_mapparr_loaded_channels.json")
    assert open(new_results, "rb").read() == b'{"scan": 1}\n'
    assert open(legacy["RESULTS_FILE"], "rb").read() == b'{"scan": 1}\n'
    assert new_results in result.copied
    assert result.failed == []


def test_the_rename_tally_and_install_id_arrive_byte_identical(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    tally = b'{"ts": 1, "channels": 3, "mode": "rename"}\n'
    write(legacy["RENAME_LEDGER_FILE"], tally)
    install = b"0123456789abcdef0123456789abcdef"
    write(os.path.join(legacy["USAGE_DATA_DIR"], "channel-mapparr", "install_id"), install)

    result = storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    new_tally = os.path.join(layout.data_dir, "channel_maparr_rename_counts.jsonl")
    assert open(new_tally, "rb").read() == tally
    new_install = os.path.join(layout.data_dir, "usage", "channel-mapparr", "install_id")
    assert open(new_install, "rb").read() == install
    assert open(os.path.join(legacy["USAGE_DATA_DIR"], "channel-mapparr", "install_id"), "rb").read() == install
    assert not result.install_id_blocked
    # The tally's offset is recorded, so the tail sync starts after the copied bytes.
    offsets = storage._ps._load_offsets(os.path.join(layout.data_dir, "channel_maparr_rename_counts.offsets.json"))
    assert offsets == {legacy["RENAME_LEDGER_FILE"]: len(tally)}


def test_a_failed_install_id_copy_blocks_the_usage_reports(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    install = b"0123456789abcdef0123456789abcdef"
    old_install = os.path.join(legacy["USAGE_DATA_DIR"], "channel-mapparr", "install_id")
    write(old_install, install)
    real = storage._ps._read_bytes

    def flaky(path):
        if path == old_install:
            raise OSError("unreadable")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", flaky)
    result = storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    assert result.install_id_blocked is True
    assert "install_id" in result.failed
    assert not os.path.exists(os.path.join(layout.data_dir, "usage", "channel-mapparr", "install_id"))


def test_the_module_guard_turns_reports_off_when_the_install_id_is_blocked(plugin_impl):
    """The guard is the module-level if block in plugin.py. Run that block alone, against a
    blocked migration, and check the reporter it names is replaced by a no-op."""
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    guard = next(n for n in tree.body
                 if isinstance(n, ast.If) and "STORAGE_MIGRATION" in ast.unparse(n.test))
    calls = []

    class FakeUsage:
        def report(self, *args, **kwargs):
            calls.append(args)

    usage = FakeUsage()
    cfg = SimpleNamespace(STORAGE_MIGRATION=SimpleNamespace(install_id_blocked=True))
    code = compile(ast.Module(body=[guard], type_ignores=[]), str(PLUGIN_SOURCE), "exec")
    exec(code, {"PluginConfig": cfg, "USAGE": usage})

    usage.report({"share_usage_counts": True})
    assert calls == [], "a blocked install id must leave the reporter silent"


def test_the_module_guard_leaves_the_reporter_alone_when_the_install_id_is_kept(plugin_impl):
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    guard = next(n for n in tree.body
                 if isinstance(n, ast.If) and "STORAGE_MIGRATION" in ast.unparse(n.test))
    calls = []

    class FakeUsage:
        def report(self, *args, **kwargs):
            calls.append(args)

    usage = FakeUsage()
    cfg = SimpleNamespace(STORAGE_MIGRATION=SimpleNamespace(install_id_blocked=False))
    code = compile(ast.Module(body=[guard], type_ignores=[]), str(PLUGIN_SOURCE), "exec")
    exec(code, {"PluginConfig": cfg, "USAGE": usage})

    usage.report("settings")
    assert calls == [("settings",)]


def test_the_progress_file_is_carried_into_the_data_dir(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    write(legacy["PROGRESS_FILE"], b'{"status": "done"}')

    storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    assert open(os.path.join(layout.data_dir, "channel_mapparr_progress.json"), "rb").read() == b'{"status": "done"}'
    assert os.path.exists(legacy["PROGRESS_FILE"])


def test_the_logo_backups_are_copied_and_the_originals_kept(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    backup = b'{"channel_id": 7, "logo_id": 3}\n'
    write(os.path.join(legacy["LOGO_BACKUP_DIR"], "run_1.json"), backup)

    storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    assert open(os.path.join(layout.data_dir, "logo_backups", "run_1.json"), "rb").read() == backup
    assert open(os.path.join(legacy["LOGO_BACKUP_DIR"], "run_1.json"), "rb").read() == backup


def test_migrated_json_and_markers_are_written_on_a_clean_pass(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    write(legacy["RESULTS_FILE"], b"{}\n")

    storage.migrate(layout, items(storage, cfg, layout), Log(), version="1.2.3")

    record = json.loads(open(os.path.join(layout.data_dir, "migrated.json"), encoding="utf-8").read())
    assert record["version"] == "1.2.3"
    assert record["failed"] == []
    assert record["copied"] and record["copied"][0]["source"] == legacy["RESULTS_FILE"]
    assert os.path.exists(layout.beside(".migrated"))
    assert os.path.exists(layout.beside(".active"))


def test_a_failed_copy_keeps_the_markers_unwritten(storage, monkeypatch, tmp_path):
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    write(legacy["RESULTS_FILE"], b"{}\n")
    real = storage._ps._read_bytes

    def flaky(path):
        if path == legacy["RESULTS_FILE"]:
            raise OSError("unreadable")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", flaky)
    result = storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    assert result.failed == ["channel_mapparr_loaded_channels.json"]
    assert not os.path.exists(layout.beside(".migrated"))


def test_the_tally_copy_stops_at_its_last_newline(storage, monkeypatch, tmp_path):
    """An old worker may be mid-write: only complete lines are copied, and the offset says so."""
    cfg, layout, legacy = build(storage, tmp_path, monkeypatch)
    reset_flags(storage)
    complete = b'{"ts": 3, "channels": 7, "mode": "rename"}\n'
    partial = b'{"ts": 4, "chan'
    write(legacy["RENAME_LEDGER_FILE"], complete + partial)

    storage.migrate(layout, items(storage, cfg, layout), Log(), version="t")

    new_tally = os.path.join(layout.data_dir, "channel_maparr_rename_counts.jsonl")
    assert open(new_tally, "rb").read() == complete
    offsets = storage._ps._load_offsets(os.path.join(layout.data_dir, "channel_maparr_rename_counts.offsets.json"))
    assert offsets == {legacy["RENAME_LEDGER_FILE"]: len(complete)}
    assert open(legacy["RENAME_LEDGER_FILE"], "rb").read() == complete + partial
