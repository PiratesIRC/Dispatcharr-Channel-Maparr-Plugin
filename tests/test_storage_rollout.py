"""Rollout safety: old-code and new-code workers run side by side for one release.

Channel Mapparr has no cross-worker lock or single-flight record that moves, so the only rollout
hazard is the rename tally: an old-code worker keeps appending to the old file after the carry-over
copied it. A new-code worker brings those bytes in before its next append. The progress file has no
dual-path reader, because nothing gates on it: Show Status reads the new file only.
"""
import json
import os

import pytest

from storage_helpers import Log, make_layout, tmp_legacy


@pytest.fixture
def storage(plugin_impl):
    return plugin_impl.storage


@pytest.fixture
def live_layout(plugin_impl, storage, monkeypatch, tmp_path):
    """A non-legacy layout on PluginConfig, with its legacy copies under tmp_path."""
    cfg = plugin_impl.PluginConfig
    legacy = tmp_legacy(tmp_path)
    monkeypatch.setattr(cfg, "LEGACY_PATHS", dict(legacy), raising=False)
    layout = make_layout(storage, str(tmp_path / "root"))
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", layout, raising=False)
    tally = os.path.join(layout.data_dir, "channel_maparr_rename_counts.jsonl")
    monkeypatch.setattr(cfg, "RENAME_LEDGER_FILE", tally)
    return layout, legacy


def bare(plugin_impl, monkeypatch):
    plugin = plugin_impl.Plugin.__new__(plugin_impl.Plugin)
    plugin._report_usage = lambda *a, **k: None
    return plugin


def test_the_tally_tail_from_an_old_worker_lands_before_the_new_record(
        plugin_impl, storage, live_layout, monkeypatch, tmp_path):
    layout, legacy = live_layout
    head = b'{"ts": 1, "channels": 2, "mode": "rename"}\n'
    tail = b'{"ts": 2, "channels": 5, "mode": "rename"}\n'
    os.makedirs(os.path.dirname(legacy["RENAME_LEDGER_FILE"]), exist_ok=True)
    with open(legacy["RENAME_LEDGER_FILE"], "wb") as fh:
        fh.write(head)
    storage.migrate(layout, storage.items_for(plugin_impl.PluginConfig, layout), Log(), version="t")
    with open(legacy["RENAME_LEDGER_FILE"], "ab") as fh:  # an old-code worker appends after the copy
        fh.write(tail)

    plugin = bare(plugin_impl, monkeypatch)
    assert plugin._record_channels_renamed(4, "rename") is True

    new_tally = os.path.join(layout.data_dir, "channel_maparr_rename_counts.jsonl")
    lines = open(new_tally, "rb").read().splitlines(keepends=True)
    assert lines[0] == head and lines[1] == tail
    assert json.loads(lines[2])["channels"] == 4
    assert len(lines) == 3, "the tail must be appended once, not on every later record"
    plugin._record_channels_renamed(1, "rename")
    assert len(open(new_tally, "rb").read().splitlines()) == 4


def test_a_legacy_process_never_syncs_the_tally(plugin_impl, monkeypatch, tmp_path):
    """In legacy mode the tally is the legacy file itself: there is nothing to sync."""
    cfg = plugin_impl.PluginConfig
    tally = str(tmp_path / "legacy_rename.jsonl")
    monkeypatch.setattr(cfg, "RENAME_LEDGER_FILE", tally)
    calls = []
    monkeypatch.setattr(plugin_impl.storage, "sync_tally_tail", lambda *a, **k: calls.append(a))
    plugin = bare(plugin_impl, monkeypatch)

    plugin._record_channels_renamed(3, "rename")

    assert calls == []
    assert len(open(tally, "rb").read().splitlines()) == 1


def test_the_progress_file_has_no_old_code_reader(plugin_impl):
    """Show Status and the progress writer both use PluginConfig.PROGRESS_FILE and nothing else."""
    import ast
    from storage_helpers import PLUGIN_SOURCE
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and getattr(n.value, "id", "") == "PluginConfig" and n.attr == "PROGRESS_FILE"}
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginConfig")
    outside = [n for n in tree.body if n is not cls]
    bare_names = {t.id for n in outside for t in ast.walk(n)
                  if isinstance(t, ast.Name) and t.id == "PROGRESS_FILE"}
    assert names, "the progress writer must read PluginConfig.PROGRESS_FILE"
    assert not bare_names, "no module-level PROGRESS_FILE may remain"
