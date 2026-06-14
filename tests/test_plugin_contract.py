"""Static contract tests for the plugin's UI surface.

CLAUDE.md: "UI surfaces are declared both in plugin.json AND in the Plugin.fields
property + Plugin.actions class attribute — the Python class is the source of
truth at runtime, so changes to plugin.json alone won't take effect." These tests
catch drift between the two declarations, missing button labels, version skew,
and the silent-action-drop caused by astral-plane (non-BMP) characters.

The Plugin.fields *property* performs a live GitHub version check and an ORM
query, so we never execute it here — field parity is checked against plugin.py's
source text instead.
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
PLUGIN_PY = PLUGIN_DIR / "plugin.py"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plugin_source():
    return PLUGIN_PY.read_text(encoding="utf-8")


def test_manifest_is_valid_json(manifest):
    assert manifest["name"]
    assert manifest["version"]
    assert isinstance(manifest["fields"], list) and manifest["fields"]
    assert isinstance(manifest["actions"], list) and manifest["actions"]


def test_action_ids_match_class(manifest, plugin_module):
    """Every action in plugin.json exists in Plugin.actions and vice versa."""
    manifest_ids = {a["id"] for a in manifest["actions"]}
    class_ids = {a["id"] for a in plugin_module.Plugin.actions}
    assert manifest_ids == class_ids, (
        f"action drift: only in plugin.json={manifest_ids - class_ids}, "
        f"only in Plugin.actions={class_ids - manifest_ids}"
    )


def test_every_class_action_has_button_label(plugin_module):
    """Without button_label, Dispatcharr renders a generic 'Run' button."""
    missing = [a["id"] for a in plugin_module.Plugin.actions if not a.get("button_label")]
    assert not missing, f"actions missing button_label: {missing}"


def test_manifest_field_ids_present_in_source(manifest, plugin_source):
    """Each plugin.json field id must also appear in the Plugin.fields property."""
    missing = [
        f["id"]
        for f in manifest["fields"]
        if f'"id": "{f["id"]}"' not in plugin_source
    ]
    assert not missing, f"fields in plugin.json but not in plugin.py source: {missing}"


def test_manifest_version_matches_class(manifest, plugin_module):
    assert manifest["version"] == plugin_module.Plugin.version, (
        f"version skew: plugin.json={manifest['version']!r} "
        f"Plugin.version={plugin_module.Plugin.version!r}"
    )


# --- Loader guard: astral-plane characters silently drop the whole action ---
# cerebrum.md: any character > U+FFFF (e.g. emoji 🎨 🖼 📊) fails Dispatcharr's
# surrogate-pair validator and drops the action. Only BMP symbols are safe.
def _astral_chars(text):
    return sorted({c for c in text if ord(c) > 0xFFFF})


def test_plugin_json_is_bmp_only():
    text = PLUGIN_JSON.read_text(encoding="utf-8")
    offenders = _astral_chars(text)
    assert not offenders, (
        f"plugin.json contains non-BMP characters that Dispatcharr will reject: "
        f"{[hex(ord(c)) for c in offenders]}"
    )


def test_plugin_action_labels_are_bmp_only(plugin_module):
    offenders = {}
    for a in plugin_module.Plugin.actions:
        bad = _astral_chars(a.get("button_label", "") + a.get("label", ""))
        if bad:
            offenders[a["id"]] = [hex(ord(c)) for c in bad]
    assert not offenders, f"actions with non-BMP characters (will be dropped): {offenders}"
