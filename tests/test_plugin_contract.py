"""Static contract tests for the plugin's UI surface.

CLAUDE.md: "UI surfaces are declared both in plugin.json AND in the Plugin.fields
property + Plugin.actions class attribute — the Python class is the source of
truth at runtime, so changes to plugin.json alone won't take effect." These tests
catch drift between the two declarations, missing button labels, version skew,
and the silent-action-drop caused by astral-plane (non-BMP) characters.

The Plugin.fields *property* reads the DB (it lists M3U accounts), so we never
execute it here — field parity is checked against plugin.py's source text
instead. It no longer performs a network call; the GitHub update check was
removed on 2026-07-26 (see test_no_update_check_remains below, which keeps it
that way).
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


def test_button_labels_match_between_manifest_and_class(manifest, plugin_module):
    """plugin.json button_label must equal Plugin.actions button_label exactly.

    Guards against silent drift between the manifest and the runtime class — in
    particular the lossy re-encoding signature where a BMP/astral icon symbol
    (e.g. ❖ / ⓘ) gets written to plugin.json as a literal '?'. The BMP-only test
    does not catch this because '?' is itself BMP; only exact parity does.
    """
    class_labels = {a["id"]: a.get("button_label", "") for a in plugin_module.Plugin.actions}
    mismatches = {
        a["id"]: {"plugin.json": a.get("button_label"), "Plugin.actions": class_labels.get(a["id"])}
        for a in manifest["actions"]
        if a.get("button_label") != class_labels.get(a["id"])
    }
    assert not mismatches, f"button_label drift between plugin.json and Plugin.actions: {mismatches}"


def test_no_placeholder_question_mark_in_button_labels(manifest, plugin_module):
    """A literal '?' in a button label is the fingerprint of a corrupted icon symbol."""
    bad_json = [a["id"] for a in manifest["actions"] if "?" in (a.get("button_label") or "")]
    bad_class = [a["id"] for a in plugin_module.Plugin.actions if "?" in (a.get("button_label") or "")]
    assert not bad_json, f"plugin.json button_labels contain a placeholder '?': {bad_json}"
    assert not bad_class, f"Plugin.actions button_labels contain a placeholder '?': {bad_class}"


def test_ignore_groups_field_is_declared_in_both_places(manifest, plugin_source):
    ids = {f["id"] for f in manifest["fields"]}
    assert "ignore_groups" in ids, "field missing from plugin.json"
    assert '"id": "ignore_groups"' in plugin_source, "field missing from Plugin.fields"


def test_every_field_id_in_source_is_also_in_the_manifest(manifest, plugin_source):
    """The existing parity test only checks manifest -> source. A field added to
    the Plugin.fields property alone renders in the UI but is absent from the
    manifest, and that direction passed silently."""
    import re
    source_ids = set(re.findall(r'"id":\s*"([a-z0-9_]+)"', plugin_source))
    known = ({f["id"] for f in manifest["fields"]}
             | {a["id"] for a in manifest["actions"]})
    assert not (source_ids - known), (
        f"ids declared in plugin.py but not in plugin.json: {sorted(source_ids - known)}")


def test_plugin_py_is_bmp_only(plugin_source):
    """Astral-plane characters make Dispatcharr's loader silently drop an action.
    plugin.json is already checked; the class side was not, and the `fields`
    property cannot be executed in tests."""
    offenders = sorted({c for c in plugin_source if ord(c) > 0xFFFF})
    assert not offenders, [hex(ord(c)) for c in offenders]


def test_ignore_groups_is_recorded_in_csv_headers(plugin_source):
    assert "'ignore_groups': 'Channel Groups to Ignore'" in plugin_source


def test_no_update_check_remains(plugin_source):
    """The GitHub update check was removed on 2026-07-26 and must stay removed.

    `Plugin.fields` is on Dispatcharr's per-request hot path. The old code made a
    live api.github.com request (plus a /data cache write) every time the settings
    page was read, so plugin settings could not render without outbound network
    access, and a slow or hung GitHub stalled the request.
    """
    banned = ["urllib", "api.github.com", "_get_latest_version",
              "_should_check_for_updates", "_save_version_check",
              "VERSION_CHECK_FILE", "cached_version_info"]
    found = [t for t in banned if t in plugin_source]
    assert not found, (
        f"update-check machinery is back in plugin.py: {found}. If a network "
        f"call is genuinely needed, it must not live in the `fields` property."
    )


def test_version_status_field_reports_the_installed_version(plugin_source):
    """The field stays (operators need to know what is installed) but is static."""
    assert '"id": "version_status"' in plugin_source
    assert 'f"Installed: v{self.version}"' in plugin_source
