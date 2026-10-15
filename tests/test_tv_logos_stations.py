"""Broadcast station logos in both tv-logos actions.

A US installation measured 305 channels without a logo in its US network
groups, almost all named in the OTA station format ("CBS - AL Birmingham
(WIAT)"). tv-logos keeps station logos in countries/united-states/us-local,
which the top-level file list skips, so none of them matched. These tests run
the real matcher against stubbed file lists.
"""
from unittest.mock import MagicMock

import pytest
from conftest import FakeQuerySet

GROUPS = [{"id": 10, "name": "US: CBS"}]

TOP = ["nbc-us.png", "fox-us.png", "cbs-logo-white-us.png", "cnn-us.png"]
LOCAL = ["cbs-42-wiat-us.png", "nbc-5-kalb-us.png"]

# id 1: no logo, station file exists           -> station logo
# id 2: no logo, no station file               -> network logo
# id 3: has a logo, no station file            -> kept, even by Replace
# id 4: has a logo, station file exists        -> station logo under Replace only
# id 5: no logo, not a station name, no match  -> nothing
# id 6: no logo, no station file, EPG icon     -> EPG icon beats the network logo
ROWS = [
    {"id": 1, "name": "CBS - AL Birmingham (WIAT)", "channel_number": 1.0, "channel_group_id": 10, "logo_id": None},
    {"id": 2, "name": "NBC - AR Jonesboro (KAIT)", "channel_number": 2.0, "channel_group_id": 10, "logo_id": None},
    {"id": 3, "name": "NBC - IN Evansville (WFIE)", "channel_number": 3.0, "channel_group_id": 10, "logo_id": 70},
    {"id": 4, "name": "NBC - LA Alexandria (KALB)", "channel_number": 4.0, "channel_group_id": 10, "logo_id": 71},
    {"id": 5, "name": "NFL  | 03 - 1pm Bears at Packers", "channel_number": 5.0, "channel_group_id": 10, "logo_id": None},
    {"id": 6, "name": "FOX - AZ Tucson (KMSB)", "channel_number": 6.0, "channel_group_id": 10, "logo_id": None},
]
EPG_ICONS = {6: "https://epg.example/kmsb.png"}
SETTINGS = {"channel_databases": "US", "selected_groups": "US: CBS"}


def _logo(logo_id, url):
    obj = MagicMock()
    obj.id = logo_id
    obj.url = url
    return obj


@pytest.fixture
def world(monkeypatch, plugin_module, plugin_instance, fake_groups):
    fake_groups(GROUPS)
    channel = MagicMock()
    channel.objects.all = lambda: FakeQuerySet([dict(r) for r in ROWS], [])

    def _filter(id__in):
        result = MagicMock()
        result.values.return_value = [
            {"id": i, "epg_data__icon_url": EPG_ICONS.get(i)} for i in id__in]
        return result

    channel.objects.filter = _filter
    monkeypatch.setattr(plugin_module, "Channel", channel)

    created = []

    def _create(name, url):
        obj = _logo(900 + len(created), url)
        created.append(obj)
        return obj

    logo = MagicMock()
    logo.objects.all.return_value = [_logo(70, "https://epg.example/wfie.png"),
                                     _logo(71, "https://epg.example/kalb.png")]
    logo.objects.create.side_effect = _create
    monkeypatch.setattr(plugin_module, "Logo", logo)

    fetched = []

    def _fetch(repo, branch, country_dir):
        fetched.append(country_dir)
        return {"united-states": TOP, "canada": TOP,
                "united-states/us-local": LOCAL}.get(country_dir, [])

    import channel_maparr.logo_matcher as logo_matcher_module
    monkeypatch.setattr(logo_matcher_module, "fetch_tv_logos_filelist", _fetch)
    monkeypatch.setattr(logo_matcher_module, "build_logo_url",
                        lambda repo, branch, d, f: f"https://tvlogos.example/{d}/{f}")
    plugin_instance._tv_logos_cache = {}

    out = {"updates": {}, "created": created, "fetched": fetched}

    def _bulk(updates, fields, logger):
        out["updates"] = {u["id"]: u["logo_id"] for u in updates}

    monkeypatch.setattr(plugin_instance, "_bulk_update_channels", _bulk)
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh", lambda *a, **k: None)
    return out


def _new_urls(world):
    by_id = {o.id: o.url for o in world["created"]}
    return {cid: by_id[lid] for cid, lid in world["updates"].items()}


def test_apply_fills_empty_channels_with_station_then_epg_then_network(
        plugin_instance, logger, world):
    result = plugin_instance.apply_tv_logos_action(dict(SETTINGS), logger)

    assert result["status"] == "success", result
    assert _new_urls(world) == {
        1: "https://tvlogos.example/united-states/us-local/cbs-42-wiat-us.png",
        2: "https://tvlogos.example/united-states/nbc-us.png",
        6: "https://epg.example/kmsb.png",
    }
    assert "1 of them took their network's logo" in result["message"], result["message"]
    assert world["fetched"] == ["united-states", "united-states/us-local"]


def test_replace_uses_a_station_logo_but_never_a_network_logo_over_an_existing_one(
        plugin_instance, logger, world):
    result = plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)

    assert result["status"] == "success", result
    urls = _new_urls(world)
    assert urls[4] == "https://tvlogos.example/united-states/us-local/nbc-5-kalb-us.png"
    assert 3 not in urls, "a generic network logo must not replace an existing logo"
    assert 5 not in urls
    assert set(urls) == {1, 2, 4, 6}


def test_no_station_folder_is_fetched_for_a_country_without_one(
        plugin_instance, logger, world):
    result = plugin_instance.apply_tv_logos_action(
        dict(SETTINGS, channel_databases="CA"), logger)
    assert result["status"] == "success", result
    assert world["fetched"] == ["canada"]


def test_dry_run_counts_a_shared_new_logo_once(
        plugin_instance, logger, world, monkeypatch, plugin_module):
    """Two stations with no station file share nbc-us.png: one catalog entry."""
    extra = {"id": 7, "name": "NBC - HI Hilo (KSIX)", "channel_number": 7.0,
             "channel_group_id": 10, "logo_id": None}
    plugin_module.Channel.objects.all = lambda: FakeQuerySet(
        [dict(r) for r in ROWS] + [extra], [])
    result = plugin_instance.apply_tv_logos_action(
        dict(SETTINGS, dry_run_mode=True), logger)
    # cbs-42-wiat, nbc-us (ids 2 and 7) and the KMSB EPG icon: three URLs
    assert "would apply logos to 4 channel(s)" in result["message"], result["message"]
    assert "3 would need a new Logo catalog entry" in result["message"], result["message"]
