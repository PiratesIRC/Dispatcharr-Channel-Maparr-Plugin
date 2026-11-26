"""Every /data path literal in the shipped modules is one the storage design accounts for.

plugin.py holds its /data literals only in the PluginConfig class body: the legacy paths that
storage.bind moves, and the user-facing paths that stay (the shared exports folder). The other
modules name a path only where the design keeps it: the vendored usage client and notify client
keep their own defaults (the plugin overrides the usage directory by passing data_dir), the report
writer and the notify bridge keep the legacy default that storage.bind overwrites, and the report
counter keeps Newsflasharr's counter folder. A literal anywhere else is a path the data-dir move
does not cover.
"""
import ast
import pathlib

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "Channel-Maparr"

# storage.py names the legacy paths in its docstrings; plugin_storage.py guards real data.
SKIPPED = {"storage.py", "plugin_storage.py"}

# Every other module, and the exact literals it may hold. Each one is a deliberate default:
# reports.py and notify_bridge.py default to the shared Newsflasharr folder, which storage.bind
# leaves as it is; plugin.py sets the value the data directory resolved.
ALLOWED = {
    "plugin.py": None,  # checked separately: only inside PluginConfig
    "reports.py": {"/data/plugins/_shared/newsflasharr/channel-mapparr", "/data/exports"},
    "notify_bridge.py": {"/data/plugins/_shared/newsflasharr/channel-mapparr"},
    "usage_client.py": {"/data/plugin_stats"},
    "notify_client.py": {"/data/newsflasharr"},
    "report_counter.py": {"/data/"},
}

CONFIG_LITERALS = {
    "/data/channel_mapparr_loaded_channels.json",
    "/data/channel_mapparr_progress.json",
    "/data/channel_mapparr_m3u_import_results.json",
    "/data/channel_maparr_rename_counts.jsonl",
    "/data/plugin_stats",
    "/data/channel_mapparr_logo_backups",
    "/data/channel_mapparr_reports",
    "/data/exports",
}


def _data_literals(tree):
    """(value, node) for each string constant starting with /data and holding no whitespace."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("/data") and not any(ch.isspace() for ch in value):
                yield value, node


def _plugin_config_span(tree):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginConfig")
    return cls.lineno, cls.end_lineno


def test_plugin_py_keeps_data_literals_only_in_the_config_class():
    path = PLUGIN_DIR / "plugin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    first, last = _plugin_config_span(tree)
    found = list(_data_literals(tree))
    outside = sorted({v for v, n in found if not (first <= n.lineno <= last)})
    assert not outside, "a /data literal outside PluginConfig: %s" % outside
    inside = {v for v, n in found if first <= n.lineno <= last}
    assert inside == CONFIG_LITERALS


def test_no_other_module_holds_a_data_literal_outside_the_allowed_set():
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name in SKIPPED or path.name == "plugin.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = {v for v, _n in _data_literals(tree)}
        allowed = ALLOWED.get(path.name, set())
        strays = found - allowed
        assert not strays, "%s holds unaccounted /data literals: %s" % (path.name, sorted(strays))
