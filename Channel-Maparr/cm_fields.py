"""Settings form layout for Dispatcharr 0.34 and later.

Dispatcharr 0.34.0 added collapsible ``section`` fields and ``multiselect``
pick lists. Both need ``manifest_version`` 1 in ``plugin.json``, and Dispatcharr
0.33 and earlier drop a field of any type they do not know, with only a log
warning. So the plugin keeps its flat form in ``Plugin.fields``, which every
version reads, and builds the sectioned copy here for ``Plugin.get_fields``,
which only 0.34 and later call.

The 0.34 form draws five comma-separated text settings as pick lists under NEW
ids (``LIST_SETTINGS``). A multiselect needs a list value, and a stored text
value under the same id would break the form, so the old text setting keeps its
id: 0.33 still edits it, and ``legacy_settings`` writes the list value back into
it at the start of every run, so every parser reads one text form.

This module imports nothing from Dispatcharr or Django; the caller passes the
group names in. It is unit-tested in ``tests/test_cm_fields.py``.
"""

import copy

# new id: old id
LIST_SETTINGS = {
    "channel_database_list": "channel_databases",
    "category_group_list": "category_groups",
    "m3u_group_list": "m3u_group_filter",
    "m3u_category_list": "m3u_category_filter",
    "seed_source_group_list": "seed_source_groups",
}
NEW_ID_BY_OLD_ID = {old: new for new, old in LIST_SETTINGS.items()}

# Country codes with a <CC>_channels.json file, and the name shown for each.
# tests/test_cm_fields.py checks this against the files that ship.
COUNTRY_NAMES = {
    "AU": "Australia",
    "BR": "Brazil",
    "CA": "Canada",
    "DE": "Germany",
    "ES": "Spain",
    "FR": "France",
    "IN": "India",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "UK": "United Kingdom",
    "US": "United States",
}

# Every category named in the shipped channel database files.
# tests/test_cm_fields.py checks this against the files that ship.
CATEGORIES = (
    "Adult", "Algemeen", "Brazil", "Broadcast", "Canada", "Comedy", "Crime",
    "Culture", "Documentaire", "Documentary", "Entertainment", "Films", "France",
    "Germany", "Internationaal Nieuws", "Kids", "Kinderen", "Lifestyle", "Mexico",
    "Movies", "Music", "Muziek", "News", "Other", "Public Broadcasting",
    "Regionaal", "Regional", "Religious", "Sci-Fi", "Shopping", "Spain",
    "Spanish", "Sports", "United Kingdom", "United States", "Weather",
)

_RELOAD_NOTE = ("The list is read when the plugin loads: after adding a group, "
                "press the refresh control on the Plugins page to see it here.")

NEW_FIELDS = {
    "channel_database_list": {
        "label": "Channel Databases",
        "help_text": "The countries whose channel databases are loaded. The same countries choose which tv-logos directories the per-channel logo action searches, so a country left out here is left out of both. Pick at least one: with none picked, the logo actions and Validate Settings refuse to run.",
    },
    "category_group_list": {
        "label": "Category Organization Groups",
        "help_text": "The groups Organize by Category is allowed to move channels into or create. The list holds every channel group plus every category named in the channel database files. Leave empty to use the categories named in the channel database files. A group listed in Channel Groups to Ignore is skipped and the run continues. " + _RELOAD_NOTE,
    },
    "m3u_group_list": {
        "label": "M3U Group Filter",
        "help_text": "The stream groups to import from. This filter is applied BEFORE matching, so a stream in a group not picked here is never looked at. Leave empty to read every group. The list holds every group that contains at least one stream. " + _RELOAD_NOTE,
    },
    "m3u_category_list": {
        "label": "Category Filter",
        "help_text": "Categories from the channel database. This filter is applied AFTER matching, so a stream is matched first and then kept only if its category is picked. Leave empty to keep every category.",
    },
    "seed_source_group_list": {
        "label": "Stream Groups to Seed From",
        "help_text": "Used by Create Channels From Streams. The stream groups to scan. Only streams in these groups that are not attached to any channel are considered. Leave empty to do nothing. The list holds every group that contains at least one stream. " + _RELOAD_NOTE,
    },
}

# Dispatcharr draws help text as one plain paragraph. In the 0.34 form a long
# help text is cut to a short form and the rest follows as titled info fields,
# one topic each, right below the field. Keyed by the field id in the 0.34 form;
# a section header's text is keyed by "<section id>_about". Each value is
# (short help, [(title, paragraph), ...]). The flat form keeps the full text.
HELP_PARAGRAPHS = {
    "_section_matching_about": (
        "Which channel databases a run matches against, how close a name has to be before it counts as a match, and which channel groups are in scope.",
        [
            ("The two group settings", "They are not opposites: Channel Groups to Process narrows a run to the groups you name, while Channel Groups to Ignore is a refusal that every action obeys, including the ones that only replay a saved results file."),
            ("A name that matches no group", "A name in Channel Groups to Ignore that matches no group refuses most actions rather than being skipped, because a typo there would otherwise quietly widen the run to everything. The actions that replay a saved results file cannot refuse, because they never ask the database which groups exist; they filter on the group name stored in that file."),
        ],
    ),
    "ignore_groups": (
        "Comma-separated. Channels in these groups are excluded from renaming, tagging, logos and Organize by Category, regardless of Channel Groups to Process or Category Organization Groups. Supports * and ? wildcards; matching is case-insensitive.",
        [
            ("An entry that matches no group", "An entry that matches no channel group refuses most actions. Organize by Category skips an ignored target group and continues. Import M3U Streams does not check entries against every group; it only refuses if its own target group is ignored."),
        ],
    ),
    "dry_run_mode": (
        "With this on, no action writes to the database. Worth turning on the first time you run any action with new settings.",
        [
            ("What a dry run produces", "Rename Channels, Organize by Category, Replace Logos (tv-logos), Import M3U Streams and Create Channels From Streams write a CSV preview to /data/exports. Tag Unknown Channels, Apply Default Logo and Apply Per-Channel Logos (tv-logos) report a count of what they would have done and write no file."),
        ],
    ),
    "csv_retention_days": (
        "After each export, this plugin's own CSV exports in /data/exports older than this many days are deleted. 0 keeps every file, which is the default.",
        [
            ("What is never deleted", "The file just written is never deleted, and at least one file always survives. Only files named channel_mapparr_*.csv are touched, because that directory is shared with other plugins. Clear CSV Exports still deletes all of them regardless of this setting."),
        ],
    ),
}
HELP_SUFFIX = "_help_"

# The info fields that head each group of settings in the flat form.
SECTION_PREFIX = "_section_"

# Groups a typical user rarely opens start closed.
COLLAPSED_SECTIONS = frozenset((
    "_section_m3u_import",
    "_section_create_channels",
    "_section_notifications",
    "_section_usage",
))

# Id suffix of the info field that keeps a section header's explanation.
ABOUT_SUFFIX = "_about"


def is_section_header(field):
    """True for an info field whose id marks the start of a settings group."""
    return (isinstance(field, dict) and field.get("type") == "info"
            and str(field.get("id", "")).startswith(SECTION_PREFIX))


def split_list(text):
    """A comma-separated setting as a list of trimmed, non-empty entries."""
    if isinstance(text, (list, tuple)):
        return [str(v).strip() for v in text if str(v).strip()]
    if text is None:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


def legacy_settings(settings):
    """Return a copy of ``settings`` with every pick-list value written back into
    its old text setting, so the rest of the plugin reads one form.

    Only a new id holding a list counts; anything else leaves the old setting
    alone. Dispatcharr 0.34 fills a new id that was never saved with its field
    default, which get_fields builds from the old setting, so the two agree.
    """
    out = dict(settings or {})
    for new_id, old_id in LIST_SETTINGS.items():
        value = out.get(new_id)
        if isinstance(value, list):
            out[old_id] = ", ".join(split_list(value))
    return out


def _options(names, extra=(), forbidden=",", sort=True):
    """Pick-list options from ``names`` plus every ``extra`` value.

    Names are deduplicated by exact spelling, never case-insensitively: group
    names are matched case-sensitively, so "news" and "News" are different
    values and both must stay selectable. A name containing a character in
    ``forbidden`` is left out, because the comma-separated text it is written
    back into could not hold it. An ``extra`` value is never left out:
    Dispatcharr rejects a save holding a value that is not an option.
    """
    seen = []
    known = set()
    for name in names or ():
        name = str(name or "").strip()
        if name and name not in known and not any(c in name for c in forbidden):
            known.add(name)
            seen.append(name)
    for name in extra or ():
        name = str(name or "").strip()
        if name and name not in known:
            known.add(name)
            seen.append(name)
    if sort:
        seen.sort(key=lambda n: (n.casefold(), n))
    return [{"label": n, "value": n} for n in seen]


def _country_options(extra=()):
    options = [{"label": f"{code} ({name})", "value": code}
               for code, name in sorted(COUNTRY_NAMES.items())]
    known = set(COUNTRY_NAMES)
    for value in extra or ():
        if value not in known:
            known.add(value)
            options.append({"label": f"{value} (no database file)", "value": value})
    return options


def _stored_list(settings, new_id):
    value = (settings or {}).get(new_id)
    return split_list(value) if isinstance(value, list) else []


def pick_list_fields(flat_fields, settings=None, channel_group_names=(),
                     stream_group_names=()):
    """Return the flat form with five text settings replaced by pick lists under
    their new ids. The input is not modified.

    The seed target group stays a text box: a single-choice list would need a
    "not set" option, and Dispatcharr's field serializer drops a select whose
    option value is empty (measured through the 0.34.1 loader, 2026-11-19).

    ``settings`` are the saved settings; a field's default is built from the old
    text setting, so an install that never saved the new field keeps its values.
    ``channel_group_names`` is every channel group; ``stream_group_names`` the
    groups that hold at least one stream.
    """
    settings = settings or {}
    out = []
    for field in flat_fields or []:
        if not isinstance(field, dict):
            out.append(field)
            continue
        fid = field.get("id")
        if fid not in NEW_ID_BY_OLD_ID:
            out.append(copy.deepcopy(field))
            continue
        new_id = NEW_ID_BY_OLD_ID[fid]
        meta = NEW_FIELDS[new_id]
        old_value = settings.get(fid, field.get("default", ""))
        default = split_list(old_value)
        stored = _stored_list(settings, new_id)
        if new_id == "channel_database_list":
            # The parser upper-cases each code, so writing it upper-case here
            # changes nothing about what a run loads.
            default = [code.upper() for code in default]
            options = _country_options(default + stored)
        elif new_id == "category_group_list":
            options = _options(list(channel_group_names) + list(CATEGORIES),
                               default + stored)
        elif new_id == "m3u_category_list":
            options = _options(CATEGORIES, default + stored)
        else:
            options = _options(stream_group_names, default + stored)
        out.append({
            "id": new_id, "label": meta["label"], "type": "multiselect",
            "default": default, "options": options, "help_text": meta["help_text"],
        })
    return out


def sectioned_fields(flat_fields):
    """Return the form rearranged into collapsible sections.

    Each section header becomes a ``section`` marker with the same id, and every
    field after it, up to the next header, gets ``"section": <that id>``. A
    header's explanation moves to an info field at the top of its section,
    because a marker shows only its label. Fields before the first header stay
    ungrouped. The input is not modified.
    """
    out = []
    current = None
    for field in flat_fields or []:
        if not isinstance(field, dict):
            continue
        if is_section_header(field):
            current = field["id"]
            out.append({
                "id": current,
                "label": field.get("label") or current,
                "type": "section",
                "collapsed": current in COLLAPSED_SECTIONS,
            })
            text = field.get("description") or field.get("help_text")
            if text:
                about = {"id": current + ABOUT_SUFFIX, "label": "", "type": "info",
                         "description": text, "section": current}
                out.append(about)
                out.extend(_split_help(about, current))
            continue
        item = copy.deepcopy(field)
        if current is not None:
            item["section"] = current
        out.append(item)
        out.extend(_split_help(item, current))
    return out


def _split_help(item, section):
    """Cut a long help text to its short form (in place) and return the rest as
    titled info fields that follow the field. An info field's text is in
    ``description``; any other field's is in ``help_text``."""
    spec = HELP_PARAGRAPHS.get(item.get("id"))
    if not spec:
        return []
    short, paragraphs = spec
    item["description" if item.get("type") == "info" else "help_text"] = short
    extra = []
    for number, (title, text) in enumerate(paragraphs, start=1):
        info = {"id": f"{item['id']}{HELP_SUFFIX}{number}", "label": title,
                "type": "info", "description": text}
        if section is not None:
            info["section"] = section
        extra.append(info)
    return extra


def build_form(flat_fields, settings=None, channel_group_names=(),
               stream_group_names=()):
    """The whole 0.34 form: pick lists first, then sections."""
    return sectioned_fields(pick_list_fields(
        flat_fields, settings, channel_group_names, stream_group_names))
