"""The Create Channels From Streams action.

It finds streams in chosen provider groups that are not attached to any
channel, resolves each to a broadcast station, and creates ONE channel per
station in a chosen existing channel group. It attaches no streams: a channel
created here is a target for a stream matcher to fill in afterwards.

The existing Import M3U Streams action cannot do this job. It creates one
channel per stream and disambiguates with a suffix, so a provider that carries
each station once per M3U account yields four channels for one station.

The declaration tests read the source rather than the fields property, because
that property performs database work on every access.
"""
import ast
import json
import pathlib
import sys

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

from channel_seeder import SeedItem, SeedPlan  # noqa: E402


def _source():
    return (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")


def _manifest():
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


# --- Declaration ------------------------------------------------------------

def test_the_action_is_declared_with_a_button_label_and_colour():
    """An unrecognised colour or variant makes the serializer drop the whole
    action, and a dropped action looks exactly like one never added."""
    action = next(a for a in _manifest()["actions"]
                  if a["id"] == "create_channels_from_streams")
    assert action["button_label"]
    assert action["button_color"] in {"blue", "green", "red", "orange", "violet", "gray"}
    assert "—" not in json.dumps(action, ensure_ascii=False), "em dash in plugin-facing copy"


def test_the_action_label_is_inside_the_basic_multilingual_plane():
    """Dispatcharr's loader silently drops an action containing a character
    above U+FFFF."""
    action = next(a for a in _manifest()["actions"]
                  if a["id"] == "create_channels_from_streams")
    for key in ("label", "button_label", "description"):
        for character in action.get(key, ""):
            assert ord(character) <= 0xFFFF, (
                "%s carries %r, which is above U+FFFF" % (key, character))


def test_the_action_is_routed_in_the_action_map():
    assert ('"create_channels_from_streams": self.create_channels_from_streams_action'
            in _source())


def test_the_action_is_declared_in_both_the_class_and_the_manifest():
    """The Python class is the source of truth at runtime, so editing the
    manifest alone changes nothing."""
    assert '"id": "create_channels_from_streams"' in _source()
    assert "create_channels_from_streams" in {a["id"] for a in _manifest()["actions"]}


SEED_SETTINGS = ("seed_source_groups", "seed_target_group", "seed_start_number")


@pytest.mark.parametrize("field_id", SEED_SETTINGS)
def test_each_setting_is_declared_in_both_places(field_id):
    assert '"id": "%s"' % field_id in _source(), field_id
    assert field_id in {f["id"] for f in _manifest()["fields"]}, field_id


@pytest.mark.parametrize("field_id", SEED_SETTINGS)
def test_no_em_dash_in_the_new_help_text(field_id):
    chunk = _source().split('"id": "%s"' % field_id)[1][:900]
    assert "—" not in chunk, field_id


def test_the_action_declares_that_it_runs_in_the_background():
    action = next(a for a in _manifest()["actions"]
                  if a["id"] == "create_channels_from_streams")
    assert action.get("background") is True


# --- Dry run ----------------------------------------------------------------

@pytest.fixture
def seeded_plugin(plugin_module, monkeypatch):
    """A Plugin whose database access is replaced by fixed rows."""
    plugin = plugin_module.Plugin()

    streams = [
        {"id": 1, "name": "US: ABC (WABC)", "m3u_account_id": 6},
        {"id": 2, "name": "US: ABC (WABC)", "m3u_account_id": 7},
        {"id": 3, "name": "US: ABC NEWS LIVE HD", "m3u_account_id": 6},
    ]
    monkeypatch.setattr(plugin, "_collect_unattached_streams",
                        lambda names, logger: list(streams))
    monkeypatch.setattr(plugin, "_get_all_groups",
                        lambda logger: [{"id": 11, "name": "US: ABC"}])
    monkeypatch.setattr(plugin, "_existing_channel_names", lambda: [])
    return plugin


BASE_SETTINGS = {
    "dry_run_mode": True,
    "seed_source_groups": "US| ABC",
    "seed_target_group": "US: ABC",
    "channel_databases": "US",
    "ota_format": "{NETWORK} - {STATE} {CITY} ({CALLSIGN})",
}


def _settings(**overrides):
    merged = dict(BASE_SETTINGS)
    merged.update(overrides)
    return merged


def test_dry_run_writes_a_file_and_returns_its_path(seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(_settings(), logger)
    assert result["status"] == "success", result
    assert result.get("file", "").endswith(".csv"), result
    assert "error" not in result


def test_dry_run_creates_no_channels(seeded_plugin, logger, monkeypatch):
    created = []
    monkeypatch.setattr(seeded_plugin, "_create_seed_channels",
                        lambda *a, **k: created.append(a) or [])
    seeded_plugin.create_channels_from_streams_action(_settings(), logger)
    assert created == []


def test_the_preview_file_names_the_station_once_not_once_per_stream(
        seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(_settings(), logger)
    body = pathlib.Path(result["file"]).read_text(encoding="utf-8")
    assert body.count("US: ABC (WABC)") == 1, (
        "the two account copies of one station must be one row")


def test_a_missing_target_group_is_a_visible_error(seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(
        _settings(seed_target_group="No Such Group"), logger)
    assert result.get("error"), "a missing target group must reach the red area"


def test_empty_source_groups_refuses_rather_than_scanning_everything(
        seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(
        _settings(seed_source_groups=""), logger)
    assert result.get("error")


def test_an_empty_target_group_refuses(seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(
        _settings(seed_target_group=""), logger)
    assert result.get("error")


def test_a_target_group_in_the_ignore_list_is_refused(seeded_plugin, logger):
    result = seeded_plugin.create_channels_from_streams_action(
        _settings(ignore_groups="US: ABC"), logger)
    assert result.get("error")
    assert "ignore" in result["error"].lower()


def test_a_duplicated_target_group_name_refuses_rather_than_guessing(
        seeded_plugin, logger, monkeypatch):
    monkeypatch.setattr(seeded_plugin, "_get_all_groups",
                        lambda logger: [{"id": 11, "name": "US: ABC"},
                                        {"id": 12, "name": "US: ABC"}])
    result = seeded_plugin.create_channels_from_streams_action(_settings(), logger)
    assert result.get("error")


def test_a_name_already_in_use_is_skipped_not_created_again(
        seeded_plugin, logger, monkeypatch):
    """This is what makes a second run a no-op rather than a duplicator."""
    monkeypatch.setattr(
        seeded_plugin, "_existing_channel_names",
        lambda: ["ABC - NY New York (WABC)"])
    result = seeded_plugin.create_channels_from_streams_action(_settings(), logger)
    body = pathlib.Path(result["file"]).read_text(encoding="utf-8")
    assert "skip, name already used" in body


# --- Creation ---------------------------------------------------------------

class _FakeChannelManager:
    def __init__(self, numbers_in_use=()):
        self.created = []
        self._numbers = list(numbers_in_use)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return type("FakeChannel", (), {"id": len(self.created)})()

    def all(self):
        return self

    def values(self, *fields):
        return []

    def values_list(self, *fields, **kwargs):
        return list(self._numbers)


def _plan(*names):
    return SeedPlan(
        create=[SeedItem(name, ["source of " + name], [1], [6]) for name in names],
        skip=[], unresolved=[])


def test_creation_attaches_no_streams(plugin_module, monkeypatch, logger):
    """Attaching is deliberately another tool's job.

    A ChannelStream write here would change what the operator gets without
    saying so, which is why this asserts on the call rather than trusting a
    comment.
    """
    links = []
    manager = _FakeChannelManager()
    monkeypatch.setattr(plugin_module.Channel, "objects", manager)
    monkeypatch.setattr(
        plugin_module.ChannelStream, "objects",
        type("M", (), {"create": lambda self, **k: links.append(k)})())

    plugin = plugin_module.Plugin()
    records = plugin._create_seed_channels(
        _plan("ABC - NY New York (WABC)"),
        target_group_id=11, settings={}, logger=logger)

    assert len(records) == 1
    assert manager.created[0]["name"] == "ABC - NY New York (WABC)"
    assert manager.created[0]["channel_group_id"] == 11
    assert links == [], "no stream may be attached by this action"


def test_creation_uses_the_configured_start_number(plugin_module, monkeypatch, logger):
    manager = _FakeChannelManager(numbers_in_use=[4000.0])
    monkeypatch.setattr(plugin_module.Channel, "objects", manager)
    plugin = plugin_module.Plugin()
    plugin._create_seed_channels(_plan("A", "B"), 11,
                                 {"seed_start_number": "5110"}, logger)
    assert [c["channel_number"] for c in manager.created] == [5110.0, 5111.0]


def test_creation_skips_a_number_already_in_use(plugin_module, monkeypatch, logger):
    manager = _FakeChannelManager(numbers_in_use=[5110.0])
    monkeypatch.setattr(plugin_module.Channel, "objects", manager)
    plugin = plugin_module.Plugin()
    plugin._create_seed_channels(_plan("A", "B"), 11,
                                 {"seed_start_number": "5110"}, logger)
    assert [c["channel_number"] for c in manager.created] == [5111.0, 5112.0]


def test_a_start_number_that_is_not_a_number_falls_back_rather_than_raising(
        plugin_module, monkeypatch, logger):
    manager = _FakeChannelManager(numbers_in_use=[4000.0])
    monkeypatch.setattr(plugin_module.Channel, "objects", manager)
    plugin = plugin_module.Plugin()
    plugin._create_seed_channels(_plan("A"), 11,
                                 {"seed_start_number": "not a number"}, logger)
    assert [c["channel_number"] for c in manager.created] == [4001.0]


# --- Source guard -----------------------------------------------------------

def test_no_function_in_the_creation_path_writes_a_channelstream_row():
    """A syntax tree guard, so a stream attachment cannot be added quietly.

    The one place the plugin does create ChannelStream rows is the M3U import
    action, which is a different feature. This pins that the seeding functions
    do not.
    """
    tree = ast.parse(_source())
    seeding = {"_create_seed_channels", "_create_channels_from_streams_bg",
               "create_channels_from_streams_action"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in seeding:
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Attribute) and inner.attr == "create"
                    and isinstance(inner.value, ast.Attribute)
                    and inner.value.attr == "objects"
                    and isinstance(inner.value.value, ast.Name)
                    and inner.value.value.id == "ChannelStream"):
                raise AssertionError(
                    "%s creates a ChannelStream row at line %d; this action must "
                    "attach no streams" % (node.name, inner.lineno))
