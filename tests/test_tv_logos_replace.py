"""Replace Logos (tv-logos) and the guarantee Apply Per-Channel Logos keeps.

Both actions share _assign_tv_logos. These tests drive the real body with a
fake channel table in which most channels already carry a logo, which is the
situation that prompted Replace Logos: an operator's UK group where 174 of 181
channels had EPG artwork, so Apply Per-Channel Logos touched only seven.
"""
import json
import os
from unittest.mock import MagicMock

import pytest
from conftest import FakeQuerySet

GROUPS = [{"id": 10, "name": "UK"}]

# id 1: has EPG artwork, matches a tv-logos file -> Replace
# id 2: has no logo, matches -> Assign
# id 3: has EPG artwork, no tv-logos match -> keeps its logo
# id 4: already carries the exact tv-logos URL -> unchanged
# id 5: has no logo, no tv-logos match, its EPG entry has an icon -> EPG logo
# id 6: has no logo, no tv-logos match, no EPG icon -> stays without a logo
ROWS = [
    {"id": 1, "name": "ITVBe", "channel_number": 1.0, "channel_group_id": 10, "logo_id": 50},
    {"id": 2, "name": "Disney Jr.", "channel_number": 2.0, "channel_group_id": 10, "logo_id": None},
    {"id": 3, "name": "WildEarth", "channel_number": 3.0, "channel_group_id": 10, "logo_id": 51},
    {"id": 4, "name": "That's 60s", "channel_number": 4.0, "channel_group_id": 10, "logo_id": 52},
    {"id": 5, "name": "BBC Radio 4", "channel_number": 5.0, "channel_group_id": 10, "logo_id": None},
    {"id": 6, "name": "Nosey", "channel_number": 6.0, "channel_group_id": 10, "logo_id": "0"},
]

# EPG icons by channel id. 1 and 3 already have a logo and 2 has a tv-logos
# match, so only 5 may take its EPG icon.
EPG_ICONS = {
    1: "https://epg.example/other-itvbe.png",
    2: "https://epg.example/disney-epg.png",
    3: "https://epg.example/other-wildearth.png",
    5: "https://epg.example/radio4.png",
}

EPG_URL_1 = "https://epg.example/itvbe.png"
EPG_URL_3 = "https://epg.example/wildearth.png"
TV_LOGOS = {
    "ITVBe": "itv-be-uk.png",
    "Disney Jr.": "disney-jr-uk.png",
    "That's 60s": "thats-60s-uk.png",
}


def _url(filename):
    return f"https://tvlogos.example/{filename}"


def _logo(logo_id, url):
    obj = MagicMock()
    obj.id = logo_id
    obj.url = url
    return obj


@pytest.fixture
def world(monkeypatch, plugin_module, plugin_instance, fake_groups):
    """Install channels, the Logo catalog and a stub tv-logos repo.

    Returns a dict that collects what the action wrote.
    """
    fake_groups(GROUPS)
    calls = []
    channel = MagicMock()
    channel.objects.all = lambda: FakeQuerySet([dict(r) for r in ROWS], calls)
    epg_reads = []

    def _filter(id__in):
        epg_reads.append(sorted(id__in))
        result = MagicMock()
        result.values.return_value = [
            {"id": i, "epg_data__icon_url": EPG_ICONS.get(i)} for i in id__in]
        return result

    channel.objects.filter = _filter
    monkeypatch.setattr(plugin_module, "Channel", channel)

    catalog = [_logo(50, EPG_URL_1), _logo(51, EPG_URL_3),
               _logo(52, _url("thats-60s-uk.png"))]
    created = []

    def _create(name, url):
        obj = _logo(900 + len(created), url)
        created.append(obj)
        return obj

    logo = MagicMock()
    logo.objects.all.return_value = catalog
    logo.objects.create.side_effect = _create
    monkeypatch.setattr(plugin_module, "Logo", logo)

    import channel_maparr.logo_matcher as logo_matcher_module
    monkeypatch.setattr(logo_matcher_module, "fetch_tv_logos_filelist",
                        lambda repo, branch, country_dir: list(TV_LOGOS.values()))
    thresholds = []

    def _match(name, files, suffix, threshold=None):
        thresholds.append(threshold)
        return TV_LOGOS.get(name)

    monkeypatch.setattr(logo_matcher_module, "match_channel_to_logo", _match)
    monkeypatch.setattr(logo_matcher_module, "build_logo_url",
                        lambda repo, branch, country_dir, filename: _url(filename))

    out = {"updates": None, "created": created, "thresholds": thresholds,
           "epg_reads": epg_reads}

    def _bulk(updates, fields, logger):
        out["updates"] = {u["id"]: u["logo_id"] for u in updates}
        return len(updates)

    monkeypatch.setattr(plugin_instance, "_bulk_update_channels", _bulk)
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)
    return out


SETTINGS = {"channel_databases": "UK", "selected_groups": "UK"}


def test_apply_per_channel_logos_still_touches_only_channels_with_no_logo(
        plugin_instance, logger, world):
    result = plugin_instance.apply_tv_logos_action(dict(SETTINGS), logger)

    assert result["status"] == "success", result
    assert set(world["updates"]) == {2, 5}, world["updates"]


def test_replace_sets_matched_logos_and_leaves_unmatched_and_unchanged_alone(
        plugin_instance, logger, world, plugin_module):
    result = plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)

    assert result["status"] == "success", result
    # 3 has no match and keeps its logo; 4 already has the matched URL.
    assert set(world["updates"]) == {1, 2, 5}, world["updates"]
    new_urls = {o.id: o.url for o in world["created"]}
    assert new_urls[world["updates"][1]] == _url("itv-be-uk.png")
    assert new_urls[world["updates"][2]] == _url("disney-jr-uk.png")
    assert "1 of them replacing" in result["message"], result["message"]


def test_replace_saves_previous_logo_ids_before_writing(
        plugin_instance, logger, world, plugin_module):
    result = plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)
    assert result["status"] == "success", result

    backup_dir = plugin_module.PluginConfig.LOGO_BACKUP_DIR
    files = os.listdir(backup_dir)
    assert len(files) == 1 and files[0].startswith("logo_backup_"), files
    with open(os.path.join(backup_dir, files[0]), encoding="utf-8") as handle:
        saved = {c["channel_id"]: c for c in json.load(handle)["channels"]}
    assert set(saved) == {1, 2, 5}
    assert saved[1]["old_logo_id"] == 50
    assert saved[1]["old_logo_url"] == EPG_URL_1
    assert saved[2]["old_logo_id"] is None
    assert files[0] in result["message"], result["message"]


def test_replace_refuses_and_writes_nothing_when_the_backup_fails(
        plugin_instance, logger, world, monkeypatch):
    def _fail(rows):
        raise OSError("disk full")

    monkeypatch.setattr(plugin_instance, "_write_logo_backup", _fail)
    result = plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)

    assert result["status"] == "error", result
    assert "disk full" in result["error"]
    assert world["updates"] is None


def test_replace_dry_run_writes_a_preview_and_nothing_else(
        plugin_instance, logger, world, plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda *a, **k: pytest.fail("dry run must not write channels"))
    settings = dict(SETTINGS, dry_run_mode=True)
    result = plugin_instance.replace_tv_logos_action(settings, logger)

    assert result["status"] == "success", result
    assert "Dry Run" in result["message"]
    assert world["created"] == [], "dry run must not create Logo catalog rows"
    assert not os.path.exists(plugin_module.PluginConfig.LOGO_BACKUP_DIR)

    export_dir = plugin_module.PluginConfig.EXPORT_DIR
    previews = [f for f in os.listdir(export_dir)
                if f.startswith("channel_mapparr_logo_replace_preview_")]
    assert len(previews) == 1, os.listdir(export_dir)
    assert previews[0] in result["message"]
    with open(os.path.join(export_dir, previews[0]), encoding="utf-8") as handle:
        body = handle.read()
    lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    by_name = {ln.split(",")[2]: ln for ln in lines[1:]}
    assert ",Replace," in by_name["ITVBe"]
    assert EPG_URL_1 in by_name["ITVBe"]
    assert ",Assign," in by_name["Disney Jr."]
    assert ",\"No match, logo kept\"," in by_name["WildEarth"] or \
        "No match, logo kept" in by_name["WildEarth"]
    assert "Already this logo" in body


@pytest.mark.parametrize("sensitivity, expected", [
    ("relaxed", 70), ("normal", 80), ("strict", 90), ("exact", 95)])
def test_replace_matches_at_the_match_sensitivity_threshold(
        plugin_instance, logger, world, sensitivity, expected):
    settings = dict(SETTINGS, match_sensitivity=sensitivity)
    plugin_instance.replace_tv_logos_action(settings, logger)
    assert world["thresholds"] and set(world["thresholds"]) == {expected}


def test_apply_per_channel_logos_keeps_its_fixed_threshold(
        plugin_instance, logger, world):
    from channel_maparr.logo_matcher import LOGO_MATCH_THRESHOLD
    settings = dict(SETTINGS, match_sensitivity="exact")
    plugin_instance.apply_tv_logos_action(settings, logger)
    assert world["thresholds"] and set(world["thresholds"]) == {LOGO_MATCH_THRESHOLD}


def test_replace_reads_only_the_selected_channel_databases(
        plugin_instance, logger, world, monkeypatch):
    import channel_maparr.logo_matcher as logo_matcher_module
    fetched = []

    def _fetch(repo, branch, country_dir):
        fetched.append(country_dir)
        return list(TV_LOGOS.values())

    monkeypatch.setattr(logo_matcher_module, "fetch_tv_logos_filelist", _fetch)
    plugin_instance._tv_logos_cache = {}
    plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)
    assert fetched == ["united-kingdom"], fetched


def test_epg_logo_is_read_only_for_channels_with_no_logo(
        plugin_instance, logger, world):
    plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)
    # 2, 5 and 6 have no logo ("0" counts as none); 1, 3 and 4 have one
    assert world["epg_reads"] == [[2, 5, 6]], world["epg_reads"]


def test_a_tv_logos_match_wins_over_the_epg_logo(
        plugin_instance, logger, world):
    plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)
    urls = {o.id: o.url for o in world["created"]}
    assert urls[world["updates"][2]] == _url("disney-jr-uk.png")


@pytest.mark.parametrize("action_name", [
    "apply_tv_logos_action", "replace_tv_logos_action"])
def test_an_unmatched_channel_with_no_logo_takes_its_epg_logo(
        plugin_instance, logger, world, action_name):
    result = getattr(plugin_instance, action_name)(dict(SETTINGS), logger)
    assert result["status"] == "success", result
    urls = {o.id: o.url for o in world["created"]}
    assert urls[world["updates"][5]] == "https://epg.example/radio4.png"
    # 6 has no EPG icon and 3 already has a logo: neither is touched
    assert 6 not in world["updates"] and 3 not in world["updates"]
    assert "1 of them took the EPG logo" in result["message"], result["message"]


def test_a_failed_epg_read_degrades_to_tv_logos_only(
        plugin_instance, logger, world, plugin_module):
    def _broken(**kwargs):
        raise RuntimeError("database gone")

    plugin_module.Channel.objects.filter = _broken
    result = plugin_instance.replace_tv_logos_action(dict(SETTINGS), logger)
    assert result["status"] == "success", result
    assert set(world["updates"]) == {1, 2}, world["updates"]
    assert any("Could not read EPG logos" in str(c) for c in logger.warning.call_args_list)


def test_the_dry_run_preview_marks_the_epg_logo(
        plugin_instance, logger, world, plugin_module):
    plugin_instance.replace_tv_logos_action(dict(SETTINGS, dry_run_mode=True), logger)
    export_dir = plugin_module.PluginConfig.EXPORT_DIR
    (name,) = [f for f in os.listdir(export_dir) if "logo_replace_preview" in f]
    with open(os.path.join(export_dir, name), encoding="utf-8") as handle:
        body = handle.read()
    radio = [ln for ln in body.splitlines() if ",BBC Radio 4," in ln]
    assert radio and "Assign EPG logo" in radio[0] and "radio4.png" in radio[0]
    assert world["created"] == []
