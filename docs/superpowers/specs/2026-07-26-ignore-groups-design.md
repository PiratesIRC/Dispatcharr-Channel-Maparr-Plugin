# Ignore Groups — group-exclusion filter for Channel-Maparr

**Date:** 2026-07-26
**Status:** Approved design, not yet implemented
**Scope:** Channel-Maparr only, channel-group axis only

## 1. Motivation

A user reports that every group filter in the plugin is inclusive-only:

> Is there a way we can have negate option for your amazing tools? For example i run
> teamarr and have defined a static channel group of "Teamarr". I would like all other
> groups to process but not the "Teamarr" group. The settings currently seem to be only
> for inclusive of what you want to run and no way to define I want group's x & z
> excluded but everything else ran

Today the only way to express "everything except Teamarr" is to enumerate every other
group by name in `selected_groups` and keep that list in sync by hand as groups come and
go. Teamarr owns its channel group and Channel-Maparr renaming, re-logoing or relocating
those channels actively fights the other tool.

EPG-Janitor already solves this with an `ignore_groups` field, so this is a port of an
established in-workspace pattern rather than a new invention.

## 2. Scope

In scope:

- One new setting, `ignore_groups`, on the **channel-group** axis.
- Applied at **all five** channel-fetch sites in `plugin.py`.

Out of scope (deliberate, may be separate slices later):

- `m3u_group_filter` (provider-side M3U group titles) and `m3u_category_filter`.
- Porting the same field to Stream-Mapparr, Lineuparr or Event-Channel-Managarr.
- Fixing EPG-Janitor's stale `ignore_groups` help text (see §7).

## 3. Current state

Two include-only filters exist, and neither has an exclusion counterpart:

| Setting | Purpose |
| --- | --- |
| `selected_groups` | Limits scan / rename / logo actions to named groups |
| `category_groups` | Limits the Organize-by-Category actions to named groups |

There are five sites that fetch channels, and they do **not** all read the same setting:

| Line | Action | Scoped by |
| --- | --- | --- |
| 956 | `load_and_process_channels_action` (feeds Rename / Tag / Preview) | `selected_groups` |
| 1383 | `apply_logos_action` | `selected_groups` |
| 1443 | `apply_tv_logos_action` | `selected_groups` |
| 1557 | `category_groups_dry_run_action` | `category_groups` |
| 1757 | `organize_by_category_action` | `category_groups` |

Each site re-implements the same parse-and-resolve block inline.

**The finding that shaped this design:** because Organize-by-Category scopes by
`category_groups`, and treats a blank value as "all groups", wiring an exclusion into
`selected_groups` alone would leave Organize-by-Category free to **move the reporter's
Teamarr channels into category groups** — the most destructive action in the plugin for
his case. `ignore_groups` therefore has to be a global guard, not a modifier on one
include field.

## 4. Behaviour

**Semantics.** Comma-separated names. A token containing `*` or `?` is a glob; any other
token is a literal. Both forms match **case-insensitively**. This is looser than the
existing include filter (exact, case-sensitive), which is left unchanged — the include
filter selects what to work on, where a near miss is self-announcing, while the exclusion
protects channels from being touched, where a near miss is silent damage.

**Combining.** `ignore_groups` composes with the include filter: include first, then
subtract. Blank include + `ignore_groups=Teamarr` gives the reporter's requested
"everything except Teamarr". `selected_groups=Sports, News` + `ignore_groups=Teamarr*`
also works.

This deliberately diverges from EPG-Janitor, which raises `ValueError` when both fields
are set (`plugin.py:1589`) even though its own help text says the ignore list is "applied
after 'Channel Groups' filter". The help text describes the better behaviour; the code
does not. Channel-Maparr implements the help text.

**Unmatched tokens.**

| Situation | Result |
| --- | --- |
| Ignore list non-empty, **nothing** matched | **Error**, action refuses to run |
| Some tokens matched, some did not | Warn per unmatched token, proceed |
| Ignore list empties the target set entirely | **Error**, action refuses to run |

The first row is fail-closed on purpose. A typo (`Teamar`) that degraded to "process
everything" would inflict exactly the damage the setting exists to prevent, and would do
it silently. The second row keeps a stale entry for a since-deleted group from blocking
every action.

The third row is the subtle one, and it exposes a **pre-existing bug** (see §4.1).

### 4.1 The empty-set footgun (pre-existing)

`_get_all_channels` guards with plain truthiness (`plugin.py:676`):

```python
def _get_all_channels(self, logger, group_ids=None):
    qs = Channel.objects.all()
    if group_ids:                       # <-- an empty set is FALSY
        qs = qs.filter(channel_group_id__in=group_ids)
```

So `group_ids=set()` is indistinguishable from `group_ids=None`, and both mean *every
channel in the database*. Two call sites can already reach that state today:
`apply_logos_action` (`:1380`) and `apply_tv_logos_action` (`:1441`) build the include set
with a filtering comprehension and **no error path**, so a typo in `selected_groups` yields
an empty set and applies logos to every channel, silently. Only
`load_and_process_channels_action` errors on an empty include resolution.

This matters doubly here, because "subtract the ignored groups" is a new way to arrive at
an empty set. Two independent defenses:

1. `_resolve_group_scope` guarantees a **non-empty** set or raises — no caller can reach
   the fetch with an empty scope.
2. `_get_all_channels` is hardened to `if group_ids is not None:`, so an empty set filters
   to nothing rather than to everything. Defense in depth, and it closes the pre-existing
   bug at the two logo sites regardless of the resolver.

## 5. Design

### 5.1 New module: `Channel-Maparr/wildcard_match.py`

A verbatim copy of `EPG-Janitor/EPG-Janitor/wildcard_match.py`: ~50 lines, stdlib
`fnmatch` only, Django-free, and already the proven implementation. Its
`expand_patterns(tokens, available_names, ci_plain)` returns
`(matched_names, unmatched_tokens)`, which is exactly the shape §4's error handling needs.
Channel-Maparr calls it with `ci_plain=True` (EPG-Janitor uses `False`).

**Not** added to the hash-pinned `_shared/` pipeline. `sync_core.py`'s `SHARED_FILES` list
is common to all four plugins, so a second entry would force a re-vendor, manifest bump
and parity-gate change in Stream-Mapparr and Lineuparr, neither of which needs the file. A
frozen 50-line glob helper does not carry the divergence pressure that drove the matcher
into a shared core. If a second plugin later needs it, promote it then.

### 5.2 New setting

Placed immediately after `selected_groups` in **both** `plugin.json` and the
`Plugin.fields` property. The Python class is runtime truth;
`tests/test_plugin_contract.py` fails if the two drift, and the text must stay BMP-only.

```
id:          ignore_groups
label:       "Channel Groups to Ignore"
type:        string
default:     ""
placeholder: "Teamarr, PPV*"
help_text:   "Comma-separated. Channels in these groups are never touched by any
              action (supports * and ? wildcards, case-insensitive). Applied after
              'Channel Groups to Process'."
```

### 5.3 New resolver: `Plugin._resolve_group_scope(settings, logger, include_key)`

One method replaces the five duplicated inline blocks. `include_key` is `selected_groups`
for three sites and `category_groups` for the two Organize sites.

1. Fetch all groups once; build `name -> id` and `id -> name`.
2. Apply the include filter. Its **matching** semantics are preserved verbatim (exact,
   case-sensitive). Its **error** semantics are unified: today the five sites behave three
   different ways — `load_and_process_channels_action` errors when no include name
   resolves, the two Organize sites error with a different message, and the two logo sites
   silently produce an empty set (§4.1). All five now error when a non-empty include list
   resolves to nothing. This is a deliberate, small behaviour change at the two logo
   sites, and it is a fix rather than a regression.
3. Parse `ignore_groups` on `[,\n]+`, resolve via `expand_patterns(..., ci_plain=True)`,
   subtract the matched ids.
4. Apply §4's error and warning rules.
5. Return `(target_group_ids, group_id_to_name, scope_info)` where `target_group_ids` is
   always an explicit set, plus a description string for logs and CSV headers.

Errors surface as the plugin's existing `{"status": "error", "message": ...}` shape. Per
the workspace-wide finding that `status` renders nowhere in Dispatcharr's plugin card,
every failure return **must also set `error`**, or it is pixel-identical to success.

### 5.4 Surfacing

- **Validate Settings** gains an Ignore line, feeding the existing counters:
  `✅ Ignore: 2 group(s) — Teamarr, Teamarr Live` /
  `⚠ Ignore: 'Teamar' matched nothing` /
  `❌ Ignore: no groups matched`.
- **`_generate_csv_settings_header`** gains `'ignore_groups': 'Channel Groups to Ignore'`
  in `field_labels`, so every dry-run CSV records the exclusion that produced it.

## 6. Testing

No Django is available locally, so all three test files run against the existing
`tests/conftest.py` mocks.

- `tests/test_wildcard_match.py` — the pure module: glob vs literal, case-insensitivity
  under `ci_plain=True`, result ordering, unmatched-token reporting.
- `tests/test_group_scope.py` — the resolver over a fake `name -> id` map: include-only,
  **ignore-only (the reporter's case)**, both combined, typo produces an error, partial
  match warns and proceeds, exclusion emptying the set produces an error, blank-and-blank
  yields all groups.
- A test that `_get_all_channels(logger, group_ids=set())` returns **no** channels, pinning
  the §4.1 hardening. This test fails against today's code.
- **A wiring test** asserting all five `_get_all_channels(` call sites take their
  `group_ids` from `_resolve_group_scope`, and that no site can pass `None` while an
  ignore list is active. The workspace lesson is explicit: two real gaps hid behind tests
  that exercised a helper while the producer went untested.

`tests/test_plugin_contract.py` needs no change — it already enforces
`plugin.json` ↔ class field parity and BMP-only text, so it fails if the new field lands
in only one place. Note its standing constraint: do not execute the `Plugin.fields`
property in tests (it performs a live GitHub version check and an ORM query); assert
against `plugin.py` source text.

## 7. Follow-ups, not this slice

- **EPG-Janitor's `ignore_groups` help text is wrong.** It promises "Applied after
  'Channel Groups' filter" while the code raises on that combination. Either the code or
  the text should change; separate repo, separate slice.
- Porting `ignore_groups` to Stream-Mapparr (`selected_groups`,
  `selected_stream_groups`) and Event-Channel-Managarr (`channel_groups`). ECM's group
  filter is load-bearing for its dummy-EPG detach scope and needs its own design pass.
- An exclusion counterpart for `m3u_group_filter`.

## 8. Release

- Bump the version with `scripts/bump_version.py` (calver `Major.YY.DDDHHMM`, keeps
  `plugin.py` and `plugin.json` in sync). Current released version is `1.26.1930617`.
- Add a `docs/CHANGELOG.md` entry.
- Build the zip with `git archive` or `scripts/package_plugin.py`, never
  `Compress-Archive` (bug-087 backslash separators), and gate it with
  `python scripts/validate_zip.py Channel-Maparr.zip`.

## 9. Acceptance

Verified on the live container by the user, not locally:

1. Set `ignore_groups=Teamarr`, leave `selected_groups` blank.
2. Validate Settings reports `✅ Ignore: 1 group(s) — Teamarr`.
3. Dry-run Load & Process: CSV contains zero Teamarr rows and the settings header records
   the exclusion.
4. Category Groups Dry Run: CSV contains zero Teamarr rows.
5. Set `ignore_groups=Teamar` (typo): both actions refuse to run with a red error, not a
   green toast.
