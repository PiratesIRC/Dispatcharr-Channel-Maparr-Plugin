"""Channel Mapparr's storage: where its private state lives and what moves over from /data.

The logic (resolving the data directory, carrying the old /data state over, the tally tail and
binding the state constants) is in plugin_storage.py, a vendored copy of _shared/plugin_storage.py.
This module holds only what belongs to this plugin: the Spec and the table of state paths. Each
public function passes SPEC to the vendored module, so callers see the same names they always did.
"""
from __future__ import annotations

try:
    from . import plugin_storage as _ps
except ImportError:  # loaded as a bare module, as plugin.py allows
    import plugin_storage as _ps

# (constant name, kind, rel under the data dir). Every row is private state of this plugin. The
# reports row (kind "dir", rel None) is the shared Newsflasharr reports directory. One row does
# coordinate workers: the rename tally's tail sync takes a lock (Spec.tally_lock) inside the data
# directory, which serialises the offsets read and write and the append, so two workers of this
# code never copy the same old-code bytes twice. That holds while every worker resolves the same
# data directory: the offsets file and the lock sit in that one directory, and nothing else writes
# the offsets. The slug and key split can break it, since two workers that resolve different
# directories each keep their own tally, lock and offsets. The general rule is that a lock that
# coordinates workers belongs beside the data directory, so every worker shares one; this tally
# lock sits inside it, which is why the guarantee above needs every worker to resolve the same
# directory. The progress file is
# written by whichever worker runs an action and read only by the Show Status readout.
_BIND_TABLE = (
    ("RESULTS_FILE", "file", "channel_mapparr_loaded_channels.json"),
    ("PROGRESS_FILE", "file", "channel_mapparr_progress.json"),
    ("M3U_IMPORT_RESULTS_FILE", "file", "channel_mapparr_m3u_import_results.json"),
    ("RENAME_LEDGER_FILE", "tally", "channel_maparr_rename_counts.jsonl"),
    ("LOGO_BACKUP_DIR", "dir", "logo_backups"),
    ("USAGE_DATA_DIR", "usage_dir", "usage"),
    ("REPORT_DIR", "dir", None),
)

SPEC = _ps.Spec(
    label="Channel Mapparr",
    state_name="_channel_mapparr_storage_state",
    table=_BIND_TABLE,
    beside={},
    reports_subdir="channel-mapparr",
    usage_subdir="channel-mapparr",
    tally_offsets="channel_maparr_rename_counts.offsets.json",
    tally_lock="channel_maparr_rename_counts.lock",
    reports_constant="REPORT_DIR",
)

Layout = _ps.Layout
Item = _ps.Item
MigrateResult = _ps.MigrateResult
atomic_write_bytes = _ps.atomic_write_bytes
atomic_write_json = _ps.atomic_write_json


def resolve(plugin_dir, key, logger):
    """Resolve the data directory for the plugin folder plugin_dir. See plugin_storage.resolve."""
    return _ps.resolve(SPEC, plugin_dir, key, logger)


def migrate(layout, items, logger, version=None):
    """Copy the old state into the data dir once. See plugin_storage.migrate."""
    return _ps.migrate(SPEC, layout, items, logger, version=version)


def sync_tally_tail(layout, tally_path, logger):
    """Append the tally's new old-source bytes to it. See plugin_storage.sync_tally_tail."""
    return _ps.sync_tally_tail(SPEC, layout, tally_path, logger)


def items_for(cfg, layout):
    """The moves bind hands to migrate. See plugin_storage.items_for."""
    return _ps.items_for(SPEC, cfg, layout)


def bind(cfg, plugin_file, logger):
    """Resolve, rebind the state constants and carry the old state over. See plugin_storage.bind."""
    return _ps.bind(SPEC, cfg, plugin_file, logger)


def check_context(layout, context, logger):
    """Warn once when Dispatcharr's data_dir differs from the resolved one. See plugin_storage."""
    return _ps.check_context(SPEC, layout, context, logger)
