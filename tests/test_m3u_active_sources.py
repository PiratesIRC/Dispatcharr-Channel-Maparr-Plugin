"""All sources (no filter) skips M3U accounts that are switched off.

A user suggested it (2026-10-18): the M3U Source option "All sources" read every
stream in the database, so an import pulled streams from an M3U account the
operator had disabled in Dispatcharr (M3UAccount.is_active False). A disabled
account is one the operator has said not to use, so the all-sources read now
leaves it out. A stream with no M3U account (a custom stream) has no account to
be disabled and is still read. Picking a disabled account by name still reads
it, because that choice is explicit; the dropdown marks it as disabled.
"""
from types import SimpleNamespace

import pytest

ACCOUNTS = {1: True, 2: False}

STREAM_ROWS = [
    SimpleNamespace(id=10, name="ESPN", m3u_account_id=1, channel_group_id=None),
    SimpleNamespace(id=20, name="CNN", m3u_account_id=2, channel_group_id=None),
    SimpleNamespace(id=30, name="Custom", m3u_account_id=None, channel_group_id=None),
]


class FakeStreamRows:
    def __init__(self, rows):
        self.rows = rows

    def only(self, *fields):
        return self

    def all(self):
        return self

    def exclude(self, **kwargs):
        assert kwargs == {"m3u_account__is_active": False}, kwargs
        return FakeStreamRows([r for r in self.rows
                                if r.m3u_account_id is None or ACCOUNTS[r.m3u_account_id]])

    def filter(self, **kwargs):
        names = {"Active": 1, "Disabled": 2}
        return FakeStreamRows([r for r in self.rows
                                if r.m3u_account_id == names[kwargs["m3u_account__name"]]])

    def __iter__(self):
        return iter(self.rows)


@pytest.fixture
def fake_streams(plugin_module, plugin_instance, monkeypatch):
    monkeypatch.setattr(plugin_module.Stream, "objects", FakeStreamRows(STREAM_ROWS),
                        raising=False)
    monkeypatch.setattr(plugin_instance, "_get_all_groups", lambda logger: [])
    return plugin_instance


@pytest.mark.parametrize("stored", ["_all", ""])
def test_all_sources_leaves_out_a_disabled_account(fake_streams, logger, stored):
    streams = fake_streams._fetch_streams_from_m3u_sources({"m3u_sources": stored}, logger)
    ids = sorted(s["id"] for s in streams)
    assert ids == [10, 30], ids


def test_a_disabled_account_picked_by_name_is_still_read(fake_streams, logger):
    streams = fake_streams._fetch_streams_from_m3u_sources({"m3u_sources": "Disabled"}, logger)
    assert [s["id"] for s in streams] == [20]


def test_the_dropdown_marks_a_disabled_account(plugin_module, plugin_instance, monkeypatch):
    rows = [{"id": 1, "name": "Active", "is_active": True},
            {"id": 2, "name": "Disabled", "is_active": False}]
    values = SimpleNamespace(order_by=lambda *a: rows)
    monkeypatch.setattr(plugin_module.M3UAccount, "objects",
                        SimpleNamespace(all=lambda: SimpleNamespace(values=lambda *a: values)),
                        raising=False)
    field = next(f for f in plugin_instance.fields if f.get("id") == "m3u_sources")
    options = {o["value"]: o["label"] for o in field["options"]}
    assert options["Active"] == "Active"
    assert options["Disabled"] == "Disabled (disabled in Dispatcharr)"
    assert "disabled" in options["_all"].lower(), options["_all"]
