"""The settings form is divided into sections, and every setting sits under the right one.

Measured 2026-09-05 against the live plugin instance: the form served 24 settings
and NO information panels at all, so it was one undivided list running from the
channel databases through to the notification format. The sibling plugins
Stream-Mapparr and IPTV-Checker both divide their forms with an "info" field
acting as a heading, and this plugin now does the same.

The failure this guards against is a heading left open. One heading in the middle
with nothing closing it makes every setting after it read as part of that one
feature, which is worse than no headings at all. The boundary tests below lock
which setting OPENS each section, so a setting added later cannot silently land
under the wrong heading, and a heading cannot be left to swallow the rest of the
form.

Locking the boundary rather than full membership means adding a setting inside a
section needs no test change, while moving a boundary does.

Section labels here are plain ASCII. This plugin's own setting labels carry no
emoji, unlike Stream-Mapparr's, so a decorated heading would be the only such
text on the page.
"""
import json
import os

import pytest

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Channel-Maparr", "plugin.json")

# The M3U source list is built from the Dispatcharr database when the settings
# page loads, so the manifest cannot carry it and these two keys are compared
# nowhere. Everything else about every field must match.
DYNAMIC = {("m3u_sources", "options")}

# Each section heading and the setting that must come directly after it.
SECTION_BOUNDARIES = [
    ("_section_matching", "channel_databases"),
    ("_section_m3u_import", "m3u_sources"),
    ("_section_create_channels", "seed_source_groups"),
    ("_section_naming", "ota_format"),
    ("_section_run_behaviour", "dry_run_mode"),
    ("_section_notifications", "notify_enabled"),
    ("_section_usage", "share_usage_counts"),
]


def _fields(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return inst.fields


def _ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)]


def _section_ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)
            if str(f.get("id", "")).startswith("_section_")]


def _fields_under(plugin_module, section_id):
    """The field ids between ``section_id`` and the next section heading."""
    ids = _ids(plugin_module)
    start = ids.index(section_id) + 1
    out = []
    for fid in ids[start:]:
        if str(fid).startswith("_section_"):
            break
        out.append(fid)
    return out


# --------------------------------------------------------------------------- #
# The sections exist, in order, and nothing escapes them
# --------------------------------------------------------------------------- #
def test_every_expected_section_heading_is_served(plugin_module):
    served = _section_ids(plugin_module)
    expected = [name for name, _first in SECTION_BOUNDARIES]
    missing = [s for s in expected if s not in served]
    assert not missing, f"missing section heading(s): {missing}"


def test_the_sections_appear_in_the_expected_order(plugin_module):
    served = _section_ids(plugin_module)
    expected = [name for name, _first in SECTION_BOUNDARIES]
    assert served == expected


@pytest.mark.parametrize("section_id,first_field", SECTION_BOUNDARIES)
def test_each_section_is_followed_by_the_setting_that_opens_it(
        plugin_module, section_id, first_field):
    """Locks where one section ends and the next begins."""
    ids = _ids(plugin_module)
    assert section_id in ids, f"{section_id} is not served at all"
    actual = ids[ids.index(section_id) + 1]
    assert actual == first_field, f"{section_id} is followed by {actual}"


def test_no_setting_sits_above_the_first_section_heading(plugin_module):
    """A setting above every heading belongs to no section and reads as stray."""
    ids = _ids(plugin_module)
    first_section = next((i for i, f in enumerate(ids)
                          if str(f).startswith("_section_")), None)
    assert first_section is not None, "the form has no section headings at all"
    assert first_section == 0, f"{ids[:first_section]} appear before any heading"


def test_every_setting_falls_under_some_section(plugin_module):
    """Counts the settings inside sections against the settings served."""
    covered = []
    for section_id, _first in SECTION_BOUNDARIES:
        covered.extend(_fields_under(plugin_module, section_id))
    served = [f for f in _ids(plugin_module) if not str(f).startswith("_section_")]
    assert sorted(covered) == sorted(served)


# --------------------------------------------------------------------------- #
# Placement that was wrong, and must not come back
# --------------------------------------------------------------------------- #
def test_the_export_retention_setting_sits_with_the_run_behaviour_settings(
        plugin_module):
    """It was added between Channel Group to Create In and First Channel Number.

    That put a housekeeping setting in the middle of the four settings belonging
    to Create Channels From Streams, where it read as one of them. It governs the
    CSV files every action writes, so it belongs with the other settings that
    apply to any run. Its id is unchanged, so no saved value is affected.
    """
    assert "csv_retention_days" in _fields_under(plugin_module, "_section_run_behaviour")


def test_the_create_channels_settings_are_contiguous(plugin_module):
    """All four Create Channels From Streams settings, and nothing else."""
    under = _fields_under(plugin_module, "_section_create_channels")
    assert under == ["seed_source_groups", "seed_target_group",
                     "seed_start_number", "seed_exclude_networks"]


def test_the_m3u_import_section_holds_only_the_import_settings(plugin_module):
    under = _fields_under(plugin_module, "_section_m3u_import")
    strays = [f for f in under if not str(f).startswith("m3u_")]
    assert not strays, f"non-import settings under the M3U heading: {strays}"


def test_the_notification_section_holds_only_notification_settings(plugin_module):
    under = _fields_under(plugin_module, "_section_notifications")
    strays = [f for f in under if not str(f).startswith("notify_")]
    assert not strays, f"non-notification settings under that heading: {strays}"


# --------------------------------------------------------------------------- #
# Rules for text this plugin shows the operator
# --------------------------------------------------------------------------- #
def test_every_section_heading_has_a_body(plugin_module):
    """A heading with no body says only what its own words say."""
    for f in _fields(plugin_module):
        if str(f.get("id", "")).startswith("_section_"):
            body = (f.get("description") or "").strip()
            assert len(body) > 80, f"{f['id']} has no real body: {body!r}"


def test_no_section_body_contains_a_line_break(plugin_module):
    """An info panel body is one flowing paragraph; line breaks are not safe there."""
    offenders = [f.get("id") for f in _fields(plugin_module)
                 if str(f.get("id", "")).startswith("_section_")
                 and "\n" in (f.get("description") or "")]
    assert not offenders, offenders


def test_no_section_heading_uses_an_em_dash(plugin_module):
    """Standing instruction: no em dashes in copy the plugin shows the operator."""
    em_dash = chr(0x2014)
    offenders = []
    for f in _fields(plugin_module):
        fid = str(f.get("id", ""))
        if not fid.startswith("_section_"):
            continue
        if em_dash in (f.get("label") or "") or em_dash in (f.get("description") or ""):
            offenders.append(fid)
    assert not offenders, offenders


def test_section_headings_are_information_panels_that_store_nothing(plugin_module):
    """A heading must never become a stored setting: Dispatcharr never prunes one."""
    for f in _fields(plugin_module):
        if str(f.get("id", "")).startswith("_section_"):
            assert f.get("type") == "info", f["id"]
            assert "default" not in f, f["id"]


# --------------------------------------------------------------------------- #
# The manifest says the same as the form
#
# Measured 2026-09-05: the two files carried two different sets of settings help
# text, and the manifest's was the fuller of the two for eleven settings. Since
# Dispatcharr renders plugin.py for an enabled plugin, the operator was reading
# the shorter wording while the better wording sat in the file only the Plugin
# Hub and a reader of the repository ever see.
# --------------------------------------------------------------------------- #
def _manifest_fields():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)["fields"]


def test_the_manifest_lists_the_same_settings_in_the_same_order(plugin_module):
    assert [f["id"] for f in _manifest_fields()] == _ids(plugin_module)


@pytest.mark.parametrize("key", ["label", "type", "help_text", "description",
                                 "placeholder", "options", "default"])
def test_the_manifest_and_the_form_agree_on_every_setting(plugin_module, key):
    served = {f["id"]: f.get(key) for f in _fields(plugin_module)}
    manifest = {f["id"]: f.get(key) for f in _manifest_fields()}
    disagree = {fid: (served.get(fid), manifest.get(fid))
                for fid in served
                if served.get(fid) != manifest.get(fid) and (fid, key) not in DYNAMIC}
    assert disagree == {}, f"form vs manifest disagree on {key}: {sorted(disagree)}"


def test_no_setting_label_or_option_uses_an_em_dash(plugin_module):
    """Measured: the Match Sensitivity and Rate Limiting options each used one.

    They read "Relaxed - more matches, more false positives" and so on, with an
    em dash rather than a hyphen, in copy the operator reads on the form.
    """
    em_dash = chr(0x2014)
    offenders = []
    for f in _fields(plugin_module):
        for key in ("label", "help_text", "description", "placeholder"):
            if em_dash in str(f.get(key) or ""):
                offenders.append((f.get("id"), key))
        for opt in f.get("options") or []:
            if em_dash in str(opt.get("label") or ""):
                offenders.append((f.get("id"), opt.get("value")))
    assert not offenders, offenders
