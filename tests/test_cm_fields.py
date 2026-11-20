"""The Dispatcharr 0.34 settings form built by cm_fields and Plugin.get_fields.

Dispatcharr 0.33 and earlier drop a field type they do not know, so the flat
form in Plugin.fields must hold only the six old types, and the 0.34 form
(sections and pick lists) is built only inside get_fields. Five text settings
are drawn as pick lists under new ids; run() writes them back into the old text
ids, so these tests check both directions and the fallback.
"""
import glob
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

import cm_fields  # noqa: E402

OLD_TYPES = {"string", "number", "boolean", "select", "text", "info"}
CHANNEL_GROUPS = ["Locals", "News", "US: ABC", "Group, With Comma"]
STREAM_GROUPS = ["US| ABC", "US| CBS", "Sports"]


def _flat(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return inst.fields


def _by_id(fields):
    return {f["id"]: f for f in fields}


def _form(plugin_module, settings=None):
    return cm_fields.build_form(_flat(plugin_module), settings or {},
                                CHANNEL_GROUPS, STREAM_GROUPS)


def test_flat_form_uses_only_types_older_dispatcharr_can_draw(plugin_module):
    bad = [(f["id"], f["type"]) for f in _flat(plugin_module) if f["type"] not in OLD_TYPES]
    assert not bad, bad


def test_manifest_fields_use_only_old_types_and_declare_version_1():
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    assert manifest["capabilities"] == ["outbound_network", "filesystem_write"]
    bad = [f["id"] for f in manifest["fields"] if f["type"] not in OLD_TYPES]
    assert not bad, bad


def test_every_flat_setting_is_in_the_new_form_or_mapped_to_a_new_id(plugin_module):
    form = _by_id(_form(plugin_module))
    for field in _flat(plugin_module):
        fid = field["id"]
        if fid in cm_fields.NEW_ID_BY_OLD_ID:
            assert cm_fields.NEW_ID_BY_OLD_ID[fid] in form, fid
            assert fid not in form, fid
        else:
            assert fid in form, fid


def test_unchanged_settings_keep_their_type_and_default(plugin_module):
    form = _by_id(_form(plugin_module))
    for field in _flat(plugin_module):
        fid = field["id"]
        if fid in cm_fields.NEW_ID_BY_OLD_ID or field["type"] == "info":
            continue
        assert form[fid]["type"] == field["type"], fid
        assert form[fid].get("default") == field.get("default"), fid


def test_pick_lists_default_to_the_saved_text_settings(plugin_module):
    saved = {
        "channel_databases": "us, UK",
        "category_groups": "News, Kids",
        "m3u_group_filter": "Sports",
        "m3u_category_filter": "Broadcast, News",
        "seed_source_groups": "US| ABC, US| CBS",
    }
    form = _by_id(_form(plugin_module, saved))
    assert form["channel_database_list"]["default"] == ["US", "UK"]
    assert form["category_group_list"]["default"] == ["News", "Kids"]
    assert form["m3u_group_list"]["default"] == ["Sports"]
    assert form["m3u_category_list"]["default"] == ["Broadcast", "News"]
    assert form["seed_source_group_list"]["default"] == ["US| ABC", "US| CBS"]
    for new_id in cm_fields.LIST_SETTINGS:
        assert form[new_id]["type"] == "multiselect"


def test_an_unsaved_install_gets_the_flat_default(plugin_module):
    form = _by_id(_form(plugin_module))
    flat = _by_id(_flat(plugin_module))
    assert form["channel_database_list"]["default"] == cm_fields.split_list(
        flat["channel_databases"]["default"])


def test_every_default_and_stored_value_is_an_option(plugin_module):
    """Dispatcharr rejects a save holding a value that is not an option."""
    saved = {
        "channel_databases": "US, XX",
        "category_groups": "Gone Group, news",
        "m3u_group_filter": "Deleted Stream Group",
        "m3u_category_filter": "Old Category",
        "seed_source_groups": "Vanished",
        "seed_source_group_list": ["Vanished", "Also Vanished"],
    }
    form = _by_id(_form(plugin_module, saved))
    for new_id in cm_fields.LIST_SETTINGS:
        values = {o["value"] for o in form[new_id]["options"]}
        wanted = set(form[new_id]["default"]) | set(saved.get(new_id, []))
        assert wanted <= values, (new_id, wanted - values)


def test_group_options_keep_both_spellings_that_differ_only_in_case(plugin_module):
    """Group names match case-sensitively, so 'news' is not 'News'."""
    form = _by_id(_form(plugin_module, {"category_groups": "news"}))
    values = [o["value"] for o in form["category_group_list"]["options"]]
    assert "news" in values and "News" in values


def test_a_name_holding_a_comma_is_not_offered(plugin_module):
    form = _by_id(_form(plugin_module))
    values = {o["value"] for o in form["category_group_list"]["options"]}
    assert "Group, With Comma" not in values


def test_no_option_has_an_empty_value(plugin_module):
    """Dispatcharr 0.34.1's field serializer drops a whole field when any option
    value is blank (measured through the container loader, 2026-11-19), which
    is why the seed target group stays a text box."""
    form = _form(plugin_module, {"category_groups": " , News"})
    for field in form:
        for option in field.get("options") or []:
            assert str(option["value"]).strip(), field["id"]
    assert _by_id(form)["seed_target_group"]["type"] == "string"


def test_legacy_settings_writes_lists_back_into_the_text_settings():
    out = cm_fields.legacy_settings({
        "channel_database_list": ["US", "CA"],
        "category_group_list": [],
        "m3u_group_list": ["Sports"],
        "m3u_category_list": ["News", " Kids "],
        "seed_source_group_list": ["US| ABC"],
        "channel_databases": "UK",
        "category_groups": "Old",
    })
    assert out["channel_databases"] == "US, CA"
    assert out["category_groups"] == ""
    assert out["m3u_group_filter"] == "Sports"
    assert out["m3u_category_filter"] == "News, Kids"
    assert out["seed_source_groups"] == "US| ABC"


def test_legacy_settings_ignores_a_new_id_that_is_not_a_list():
    out = cm_fields.legacy_settings({"channel_database_list": "US", "channel_databases": "UK"})
    assert out["channel_databases"] == "UK"


def test_round_trip_keeps_what_the_parsers_read(plugin_module):
    saved = {"m3u_category_filter": "Broadcast, News", "channel_databases": "US, UK"}
    form = _by_id(_form(plugin_module, saved))
    stored = {new_id: form[new_id]["default"] for new_id in cm_fields.LIST_SETTINGS}
    out = cm_fields.legacy_settings(stored)
    assert out["m3u_category_filter"] == "Broadcast, News"
    assert out["channel_databases"] == "US, UK"


def test_every_setting_belongs_to_a_declared_section(plugin_module):
    form = _form(plugin_module)
    sections = {f["id"] for f in form if f["type"] == "section"}
    assert len(sections) == 7
    for field in form:
        if field["type"] != "section":
            assert field.get("section") in sections, field["id"]


def test_rarely_used_sections_start_collapsed(plugin_module):
    form = _by_id(_form(plugin_module))
    for sid in cm_fields.COLLAPSED_SECTIONS:
        assert form[sid]["collapsed"] is True
    assert form["_section_matching"]["collapsed"] is False


def test_section_explanations_are_kept_at_the_top_of_their_section(plugin_module):
    form = _form(plugin_module)
    ids = [f["id"] for f in form]
    for sid in (f["id"] for f in form if f["type"] == "section"):
        assert ids[ids.index(sid) + 1] == sid + cm_fields.ABOUT_SUFFIX


def test_long_help_texts_are_split_into_titled_paragraphs(plugin_module):
    form = _by_id(_form(plugin_module))
    for fid, (short, paragraphs) in cm_fields.HELP_PARAGRAPHS.items():
        key = "description" if form[fid]["type"] == "info" else "help_text"
        assert form[fid][key] == short, fid
        for number, (title, text) in enumerate(paragraphs, start=1):
            info = form[f"{fid}{cm_fields.HELP_SUFFIX}{number}"]
            assert info["label"] == title and info["description"] == text


def test_the_flat_form_keeps_the_full_help_text(plugin_module):
    flat = _by_id(_flat(plugin_module))
    assert flat["ignore_groups"]["help_text"].startswith("Comma-separated. Channels")
    assert "Import M3U Streams does not" in flat["ignore_groups"]["help_text"]


def test_help_text_is_one_plain_paragraph_with_no_em_dash(plugin_module):
    for field in _form(plugin_module):
        for key in ("help_text", "description", "label"):
            text = field.get(key) or ""
            assert "\n" not in text, field["id"]
            assert chr(0x2014) not in text, field["id"]


def test_input_is_not_modified(plugin_module):
    flat = _flat(plugin_module)
    before = json.dumps(flat, sort_keys=True)
    cm_fields.build_form(flat, {}, CHANNEL_GROUPS, STREAM_GROUPS)
    assert json.dumps(flat, sort_keys=True) == before


def test_country_list_matches_the_shipped_database_files():
    codes = {os.path.basename(p)[:2] for p in glob.glob(str(PLUGIN_DIR / "*_channels.json"))}
    assert codes == set(cm_fields.COUNTRY_NAMES)


def test_category_list_matches_the_shipped_database_files():
    found = set()
    for path in glob.glob(str(PLUGIN_DIR / "*_channels.json")):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("channels", data)
        found |= {r.get("category") for r in rows if isinstance(r, dict) and r.get("category")}
    assert found == set(cm_fields.CATEGORIES)


def test_get_fields_builds_the_form_and_sets_the_flag(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    monkeypatch.setattr(plugin_module.Plugin, "_pick_list_names",
                        staticmethod(lambda: (CHANNEL_GROUPS, STREAM_GROUPS)))
    fields = inst.get_fields({"settings": {"channel_databases": "CA"}})
    assert inst._uses_pick_lists is True
    assert _by_id(fields)["channel_database_list"]["default"] == ["CA"]


def test_get_fields_falls_back_to_the_flat_form_when_the_layout_fails(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"

    def boom(*args, **kwargs):
        raise RuntimeError("layout failed")

    monkeypatch.setattr(plugin_module.cm_fields, "build_form", boom)
    fields = inst.get_fields({"settings": {}})
    assert inst._uses_pick_lists is False
    assert [f["id"] for f in fields] == [f["id"] for f in _flat(plugin_module)]


def test_pick_list_names_never_raises(plugin_module, monkeypatch):
    broken = MagicMock()
    broken.objects.values_list.side_effect = RuntimeError("db down")
    monkeypatch.setattr(plugin_module, "ChannelGroup", broken)
    assert plugin_module.Plugin._pick_list_names() == ([], [])


@pytest.mark.parametrize("flag,expected", [(True, "US, CA"), (False, "UK")])
def test_run_translates_pick_lists_only_after_get_fields_built_them(
        plugin_module, monkeypatch, flag, expected):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst._uses_pick_lists = flag
    seen = {}

    def fake_validate(settings, logger):
        seen["channel_databases"] = settings.get("channel_databases")
        return {"status": "ok", "message": "fine"}

    monkeypatch.setattr(inst, "validate_settings_action", fake_validate, raising=False)
    monkeypatch.setattr(plugin_module, "send_websocket_update", MagicMock())
    settings = {"channel_databases": "UK", "channel_database_list": ["US", "CA"]}
    inst._run_body("validate_settings", {}, {"settings": settings, "logger": MagicMock()})
    assert seen["channel_databases"] == expected
    assert settings["channel_databases"] == "UK", "the caller's dict must not change"
