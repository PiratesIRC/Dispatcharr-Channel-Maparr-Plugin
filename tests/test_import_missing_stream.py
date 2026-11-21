"""Import M3U Streams must not save a channel for a stream that has gone.

_import_matched_streams prefetches the matched Stream rows once. A stream
deleted after matching is absent from that prefetch. The loop used to create
the channel and its profile memberships first and only then notice the stream
was missing; its `continue` left the transaction block normally, so the block
committed a channel with no stream attached. Found 2026-11-21 while fixing
bug-203.
"""
from unittest.mock import MagicMock

import pytest


class _Recorder:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return type("Row", (), {"id": 1000 + len(self.created), **kwargs})()


@pytest.fixture
def import_loop(plugin_module, monkeypatch):
    channels = _Recorder()
    links = _Recorder()
    channel_manager = MagicMock()
    channel_manager.create = channels.create
    channel_manager.all.return_value.values.return_value = []
    monkeypatch.setattr(plugin_module, "Channel", MagicMock(objects=channel_manager))
    monkeypatch.setattr(plugin_module, "ChannelStream", MagicMock(objects=links))
    surviving = type("Stream", (), {"id": 2})()
    stream_manager = MagicMock()
    stream_manager.filter.return_value = [surviving]   # stream 1 was deleted
    monkeypatch.setattr(plugin_module, "Stream", MagicMock(objects=stream_manager))
    profiles = MagicMock()
    profiles.objects.all.return_value = ["profile-a"]
    monkeypatch.setattr(plugin_module, "ChannelProfile", profiles)
    memberships = MagicMock(side_effect=lambda **k: dict(k))
    memberships.objects.bulk_create = MagicMock()
    monkeypatch.setattr(plugin_module, "ChannelProfileMembership", memberships)

    plugin = plugin_module.Plugin()
    monkeypatch.setattr(plugin, "_get_next_channel_number", lambda logger: 100.0)
    monkeypatch.setattr(plugin, "_get_all_groups", lambda logger: [{"id": 10, "name": "Sports"}])
    return plugin, channels, links, memberships, surviving


def _matched():
    return {"Sports": [
        {"stream": {"id": 1, "name": "ESPN", "priority": 0, "m3u_account": 7}},
        {"stream": {"id": 2, "name": "FOX", "priority": 0, "m3u_account": 7}},
    ]}


def test_a_deleted_stream_creates_no_channel(import_loop, logger):
    plugin, channels, links, memberships, surviving = import_loop
    results = plugin._import_matched_streams(_matched(), {"Sports": 10}, {}, logger)

    assert [c["name"] for c in channels.created] == ["FOX"], channels.created
    assert len(links.created) == 1 and links.created[0]["stream"] is surviving
    assert results["total_imported"] == 1
    assert [r["stream_id"] for r in results["imports"] if r.get("status") == "success"] == [2]


def test_a_deleted_stream_does_not_use_up_a_channel_number(import_loop, logger):
    plugin, channels, _, _, _ = import_loop
    plugin._import_matched_streams(_matched(), {"Sports": 10}, {}, logger)
    assert channels.created[0]["channel_number"] == 100.0


def test_a_deleted_stream_adds_no_profile_membership(import_loop, logger):
    plugin, _, _, memberships, _ = import_loop
    plugin._import_matched_streams(_matched(), {"Sports": 10}, {}, logger)
    assert memberships.objects.bulk_create.call_count == 1, "one membership batch, for FOX only"


def test_a_deleted_stream_is_logged(import_loop, logger):
    plugin, _, _, _, _ = import_loop
    plugin._import_matched_streams(_matched(), {"Sports": 10}, {}, logger)
    warnings = " ".join(str(c) for c in logger.warning.call_args_list)
    assert "Stream 1 not found" in warnings, warnings
