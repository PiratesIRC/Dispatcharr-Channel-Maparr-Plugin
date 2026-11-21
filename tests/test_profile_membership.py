"""bug-203: every channel the plugin creates must be added to the channel profiles.

Dispatcharr outputs a channel only through a ChannelProfileMembership row, and it
adds those rows when a profile is created, not when a channel is. Its own
channel-create endpoint therefore adds each new channel to every profile with
enabled=True. Both creation sites in this plugin (Import M3U Streams and Create
Channels From Streams) skipped that step, so 271 channels created on 2026-08-10
were in no profile and were never output.
"""

import ast
import pathlib
import sys
from unittest.mock import MagicMock

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

from channel_seeder import SeedItem, SeedPlan  # noqa: E402

PLUGIN_SOURCE = PLUGIN_DIR / "plugin.py"


class _FakeChannelManager:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return type("FakeChannel", (), {"id": len(self.created)})()

    def values_list(self, *fields, **kwargs):
        return []


class _FakeMembershipManager:
    def __init__(self):
        self.rows = []

    def bulk_create(self, rows):
        self.rows.extend(rows)
        return rows


def _install_fakes(plugin_module, monkeypatch, profiles):
    channels = _FakeChannelManager()
    memberships = _FakeMembershipManager()
    monkeypatch.setattr(plugin_module, "Channel", MagicMock(objects=channels))
    monkeypatch.setattr(
        plugin_module, "ChannelProfile",
        MagicMock(objects=MagicMock(all=MagicMock(return_value=list(profiles)))))
    membership_model = MagicMock(
        side_effect=lambda **kwargs: dict(kwargs), objects=memberships)
    monkeypatch.setattr(plugin_module, "ChannelProfileMembership", membership_model)
    return channels, memberships


def _plan(*names):
    return SeedPlan(
        create=[SeedItem(name, ["source of " + name], [1], [6]) for name in names],
        skip=[], unresolved=[])


def test_a_seeded_channel_is_added_to_every_profile_enabled(
        plugin_module, monkeypatch, logger):
    profile_a, profile_b = object(), object()
    channels, memberships = _install_fakes(
        plugin_module, monkeypatch, [profile_a, profile_b])

    records = plugin_module.Plugin()._create_seed_channels(
        _plan("ABC - NY New York (WABC)", "CBS - NY New York (WCBS)"),
        target_group_id=11, settings={}, logger=logger)

    assert len(records) == 2
    assert len(memberships.rows) == 4, memberships.rows
    for row in memberships.rows:
        assert row["enabled"] is True, row
    pairs = {(id(r["channel_profile"]), r["channel"].id) for r in memberships.rows}
    assert pairs == {(id(p), c) for p in (profile_a, profile_b) for c in (1, 2)}


def test_profiles_are_read_once_per_run_not_once_per_channel(
        plugin_module, monkeypatch, logger):
    _install_fakes(plugin_module, monkeypatch, [object()])
    plugin_module.Plugin()._create_seed_channels(
        _plan("A", "B", "C"), target_group_id=11, settings={}, logger=logger)
    assert plugin_module.ChannelProfile.objects.all.call_count == 1


def test_with_no_profiles_the_channel_is_still_created(
        plugin_module, monkeypatch, logger):
    channels, memberships = _install_fakes(plugin_module, monkeypatch, [])
    records = plugin_module.Plugin()._create_seed_channels(
        _plan("A"), target_group_id=11, settings={}, logger=logger)
    assert len(records) == 1
    assert memberships.rows == []


# --- Source guard -----------------------------------------------------------

def _is_call_to(node, owner, method):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "objects"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == owner)


def _is_profile_add(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_add_channel_to_profiles")


def test_every_channel_creation_adds_profiles_in_the_same_transaction():
    """A syntax tree guard so a new creation site cannot skip the membership.

    Every `with` block that calls Channel.objects.create must also call
    _add_channel_to_profiles, so the channel and its memberships commit or roll
    back together.
    """
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    creates = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and _is_call_to(n, "Channel", "create")]
    assert len(creates) >= 2, "expected the two known creation sites"

    for with_node in (n for n in ast.walk(tree) if isinstance(n, ast.With)):
        inner = list(ast.walk(with_node))
        if any(_is_call_to(n, "Channel", "create") for n in inner):
            assert any(_is_profile_add(n) for n in inner), (
                "Channel.objects.create at line %d has no _add_channel_to_profiles "
                "call in the same with block" % with_node.lineno)

    covered = {id(n) for w in ast.walk(tree) if isinstance(w, ast.With)
               for n in ast.walk(w) if _is_call_to(n, "Channel", "create")}
    for node in creates:
        assert id(node) in covered, (
            "Channel.objects.create at line %d is outside a transaction block"
            % getattr(node, "lineno", -1))
