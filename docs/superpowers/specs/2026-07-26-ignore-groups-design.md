# Ignore Groups — group-exclusion filter for Channel-Maparr

**Date:** 2026-07-26
**Revision:** 2 (rev 1 revised after a four-reviewer pass; see §11 for what changed and why)
**Status:** Approved design, not yet implemented
**Scope:** Channel-Maparr only, channel-group axis only. Ships as **two slices** (§8).

## 1. Motivation

A user reports that every group filter in the plugin is inclusive-only:

> Is there a way we can have negate option for your amazing tools? For example i run
> teamarr and have defined a static channel group of "Teamarr". I would like all other
> groups to process but not the "Teamarr" group. The settings currently seem to be only
> for inclusive of what you want to run and no way to define I want group's x & z
> excluded but everything else ran

Today the only way to express "everything except Teamarr" is to enumerate every other
group by name in `selected_groups` and keep that list in sync by hand as groups come and
go. Teamarr owns its channel group, and Channel-Maparr renaming, re-logoing or relocating
those channels actively fights the other tool.

EPG-Janitor already ships an `ignore_groups` field, so the field itself is a port of an
established in-workspace pattern. What is *not* a port is where it has to be enforced —
see §3.

## 2. Scope

In scope:

- One new setting, `ignore_groups`, on the **channel-group** axis.
- Enforced at the five group-scoped channel-fetch sites, **and** at the two actions that
  mutate from a persisted results file without fetching channels at all (§4.3), **and** in
  the two write directions that can put channels *into* an ignored group (§4.4).
- A hardening slice (§8, slice A) fixing three pre-existing defects the feature would
  otherwise inherit or amplify.

Out of scope, deliberately:

- `m3u_group_filter` and `m3u_category_filter` (provider-side M3U group titles).
- Porting the field to Stream-Mapparr, Lineuparr or Event-Channel-Managarr (§10).
- Scoping the M3U import's stream matching and duplicate detection. Import gets a
  destination guard only (§4.4b); it is not made group-aware.
- Fixing EPG-Janitor's contradictory `ignore_groups` help text (§10).

## 3. Current state

Two include-only filters exist, neither with an exclusion counterpart:

| Setting | Purpose |
| --- | --- |
| `selected_groups` | Limits scan / rename / logo actions to named groups |
| `category_groups` | Limits the Organize-by-Category actions to named groups |

Five sites fetch channels scoped by one of them, and they do **not** all read the same one:

| Line | Action | Scoped by | Errors on unresolvable include? |
| --- | --- | --- | --- |
| 956 | `load_and_process_channels_action` | `selected_groups` | yes (`:948`, names the bad tokens) |
| 1383 | `apply_logos_action` | `selected_groups` | **no — silently empty** |
| 1443 | `apply_tv_logos_action` | `selected_groups` | **no — silently empty** |
| 1557 | `category_groups_dry_run_action` | `category_groups` | yes (`:1552`, different message) |
| 1757 | `organize_by_category_action` | `category_groups` | yes (`:1752`, same as above) |

Each site re-implements the same parse-and-resolve block inline.

**Two findings shaped this design, and neither is visible from the setting list.**

**(a) Organize-by-Category is scoped by `category_groups`, not `selected_groups`, and
treats blank as "all groups"** (`:1753-1754`). It matches each channel to a category and
reparents it via `_bulk_update_channels(updates, ['channel_group_id'], ...)`
(`:1809-1891`). So an exclusion wired into `selected_groups` alone would leave the single
most destructive action free to **move the reporter's Teamarr channels out of Teamarr**.
`ignore_groups` therefore has to be a global guard, not a modifier on one include field.

**(b) Rename Channels and Tag Unknown Channels never fetch channels at all.** Both read
`self.results_file` (`PluginConfig.RESULTS_FILE`, written at `:1137`) and bulk-update from
whatever is in it (`:1249-1262`, `:1287-1301`). Patching fetch sites does not touch them.
See §4.3 — this is the finding that would have shipped the feature broken.

## 4. Behaviour

**Matching.** Comma- or newline-separated names. A token containing `*` or `?` is a glob;
any other token is a literal. Both forms match **case-insensitively**. This is looser than
the include filter (exact, case-sensitive), which is unchanged. The asymmetry is
deliberate: the include filter *selects work*, where a near miss announces itself as
"nothing happened", while the exclusion *withholds work*, where a near miss is silent
damage to channels the user was protecting.

**Empty tokens are dropped before any emptiness test.** `ignore_groups = " , "` parses to
zero tokens and is treated as **no ignore list at all** — not as an ignore list that
matched nothing. Without this, a stray comma would hard-error every action in the plugin.

**Combining.** `ignore_groups` composes with the include filter: include first, then
subtract. Blank include + `ignore_groups=Teamarr` is the reporter's case;
`selected_groups=Sports, News` + `ignore_groups=Teamarr*` also works.

This diverges from EPG-Janitor, which raises when both are set (`plugin.py:1589`) even
though its own help text (`:164`) says the ignore list is "applied after 'Channel Groups'
filter". The help text describes the better behaviour; the code does not. Channel-Maparr
implements the help text. Recorded as a decision in §10.

**Unmatched tokens.** Resolution runs against **all** group names, not only those inside
the include scope — that distinction is what makes the table below unambiguous:

| Situation | Result |
| --- | --- |
| A token matches no group **anywhere** | **Error** — the action refuses to run |
| A token matches a real group that is outside the current include scope | Info line, no-op, no error |
| Some tokens matched, some matched nothing | Error (row 1 governs; a typo is a typo) |
| Every token matched, exclusion empties the target set | **Error** — the action refuses to run |
| Zero channel groups exist in Dispatcharr | **Error**, with its own message |

Rows 1 and 4 are fail-closed on purpose. A typo (`Teamar`) that degraded to "process
everything" would inflict precisely the damage the setting exists to prevent, and would do
it silently.

This is the *primary operator input* to the operation, so CLAUDE.md's Notifyarr A1 lesson
("never fail-closed on a defense-in-depth **backstop**") does not apply — its own corollary
says fail-closed is right for the primary input. dmonitarr's rule governs instead: *a
not-proven-bad verdict never authorizes an action.* "I could not resolve your exclusion"
must not authorize touching channels.

Note rev 1 had a softer row for partial matches (warn and proceed). It was withdrawn: the
failure case is `ignore_groups = Teamarr, Teamarr Live` where Teamarr was renamed to
"Teamarr TV" — one token matches, one does not, and the plugin renames every channel in the
group the user was protecting behind a transient log warning. Because `status` renders
nowhere and `message` is a transient toast (§4.5), "warn" is not a thing the operator
reliably sees on a mutating action. A stale token for a deleted group now blocks the run;
Validate Settings tells them which one, and deleting one word fixes it.

**A bare `*` is not special-cased.** It matches every group, empties the target set, and is
caught by row 4. No dedicated guard; a test pins it, both with and without an include
filter.

### 4.1 The empty-set footgun (pre-existing — slice A)

`_get_all_channels` guards with plain truthiness (`plugin.py:676`):

```python
def _get_all_channels(self, logger, group_ids=None):
    qs = Channel.objects.all()
    if group_ids:                       # <-- an empty set is FALSY
        qs = qs.filter(channel_group_id__in=group_ids)
```

`group_ids=set()` is therefore indistinguishable from `None`, and both mean *every channel
in the database*. Two sites reach that state today: `apply_logos_action` (`:1380`) and
`apply_tv_logos_action` (`:1441`) build the include set with a filtering comprehension and
no error path, so **a typo in "Channel Groups to Process" applies logos to every channel in
the database** instead of none. Logged as `bug-044`.

Two independent defenses:

1. `_get_all_channels` becomes `if group_ids is not None:`, so an empty set filters to
   nothing rather than to everything, and **logs a warning** on that branch — a
   defense-in-depth layer that degrades loudly, not silently.
2. `_resolve_group_scope` guarantees a non-empty scope or returns an error, so no caller
   reaches the fetch with an empty one.

### 4.2 Ungrouped channels must survive (slice A)

Today a blank include filter passes `group_ids=None`, so `Channel.objects.all()` includes
channels with `channel_group_id IS NULL`, and `load_and_process_channels_action` renders
them as `'No Group'` (`:964`). Once every site passes an explicit set of real group ids,
`filter(channel_group_id__in=ids)` **evicts every ungrouped channel** from Rename, Tag,
Default Logo and tv-logos. The two Organize sites already have this bug today, which is
probably why it has gone unnoticed.

`ignore_groups` can never name a NULL group, so an exclusion must not be able to evict
ungrouped channels. The resolver returns `include_ungrouped`, true exactly when the include
value is blank, and the fetch becomes:

```python
q = Q(channel_group_id__in=group_ids)
if include_ungrouped:
    q |= Q(channel_group_id__isnull=True)
```

Pinned by a test: blank include + `ignore_groups=Teamarr` still processes No-Group
channels.

### 4.3 The two actions that mutate from a file (slice B)

`rename_channels_action` and `rename_unknown_channels_action` read `self.results_file` and
bulk-update names from it, never consulting the DB for scope. The file persists across
invocations and process restarts — it is present in this working tree right now
(`channel_mapparr_loaded_channels.json`). So:

> Run Load & Process → set `ignore_groups=Teamarr` → click Rename Channels → **every
> Teamarr channel is renamed**, and the card shows a green success toast.

Fix: **re-filter the loaded rows against the exclusion at read time.** Each change row
already carries `'channel_group'` as a group **name** (`:1098`, `:1117`), so the rows are
matched directly against the ignore patterns with the same `expand_patterns` call — no id
lookup, and it still works if the group has since been deleted or renamed. Dropped rows are
counted and reported in the action's result.

Filtering at read time also fixes the stale-file case for free, and it is why the exclusion
is matched by name here while the fetch sites match by id.

Residual, documented: the include filter is *not* re-applied at read time. A results file
produced under a wider include filter still renames everything in it. The exclusion is the
safety promise; the include filter is a scoping convenience. `rename_unknown_channels`
gains its first group filter of any kind here.

### 4.4 The two write directions that point *into* an ignored group (slice B)

The design so far filters channels *out of* a scan. Two paths write *in*:

**(a) Organize-by-Category** builds `groups_needed` from channel-database category names
(`:1852`) and creates/reuses those groups, then moves channels in (`:1867-1891`). With
`ignore_groups=Sports*` and a database category `Sports`, the plugin would create or adopt
an ignored group and fill it. Fix: drop any needed group whose name matches an ignore
pattern, skip the moves that target it, and report the count.

**(b) Import M3U Streams** can create channels into an ignored group three ways:
`m3u_custom_group_name` set to it, a database category matching an ignore token, or
`_get_or_create_group` silently adopting the reporter's existing Teamarr group by name.
Import is not being made group-aware (§2), but it gets a **destination guard**: resolve the
import's target group name(s) against the ignore patterns and **refuse the run with a
visible error** rather than writing into a group the user declared untouchable.

Note `_import_matched_streams` also reads `Channel.objects.all()` directly (`:2337`) for
duplicate-name detection. That read is not scoped, so a channel in an ignored group can
still force a `[1]` suffix onto a newly imported one. Read-only with respect to the ignored
group, therefore accepted; stated here so the omission is a decision rather than an
oversight.

### 4.5 Failures must be visible (slice A)

`grep -c '"error":' Channel-Maparr/plugin.py` returns **0**. The plugin sets `error` on none
of its ~30 `{"status": "error", ...}` returns, and `validate_settings_action` returns
`{status, message}` only (`:2836`). Per the workspace finding, Dispatcharr's plugin card
renders `message` (a transient green toast), `error` (red, persistent) and `file` — and
`status` renders **nowhere**. So **every failure in this plugin today is pixel-identical to
success**, including rev 1's acceptance step that asserted a red error.

Slice A therefore audits every failure return to also set `error`, and adds an AST guard
that fails the build on any future return whose `status` is `"error"` without an `error`
key (§6.5). Exactly one of `message` / `error` is set on any given return. This mirrors
what the workspace already did for dmonitarr (24 paths) and metricsarr.

## 5. Design

### 5.1 New module: `Channel-Maparr/wildcard_match.py`

A verbatim copy of `EPG-Janitor/EPG-Janitor/wildcard_match.py`: ~50 lines, stdlib `fnmatch`
only, Django-free.  `expand_patterns(tokens, available_names, ci_plain)` returns
`(matched_names, unmatched_tokens)` — exactly the shape §4's rules need. Channel-Maparr
calls it with `ci_plain=True`; EPG-Janitor uses `False`.

**Why a copy and not `_shared/`.** Rev 1 justified this by claiming `SHARED_FILES` is
common to all four plugins. **That was wrong** — each plugin has its own tracked copy of
`sync_core.py` (`Channel-Maparr/scripts/`, `Stream-Mapparr/scripts/`,
`EPG-Janitor/scripts/`, `Lineuparr/.github/scripts/`) and the parity gate is driven by each
plugin's own `core_manifest.json`, so adding an entry here would have had no effect
elsewhere. The real reasons are: the two call sites already want different `ci_plain`
values, so there is no single shared behaviour to pin; the file is a frozen 50-line
`fnmatch` wrapper with none of the divergence pressure that drove the matcher into a shared
core; and `_shared/` currently holds only files that four plugins genuinely co-evolve.

Because a copy with no gate is the pre-2026-06-28 divergence state the shared core was
created to end, the copy gets a **provenance pin**: a test asserting the sha256 of its
LF-normalized bytes, with a comment saying that editing it deliberately means updating the
hash *and* recording the divergence in the CHANGELOG. If a second plugin ever needs it,
promote it then.

### 5.2 New setting

Placed immediately after `selected_groups` in **both** `plugin.json` and the `Plugin.fields`
property, BMP-only:

```
id:          ignore_groups
label:       "Channel Groups to Ignore"
type:        string
default:     ""
placeholder: "Teamarr, PPV*"
help_text:   "Comma-separated. Channels in these groups are excluded from renaming,
              tagging, logos and Organize by Category, regardless of 'Channel Groups
              to Process' or 'Category Organization Groups'. Supports * and ?
              wildcards; matching is case-insensitive. Does not apply to Import
              M3U Streams, which refuses to run if its target group is ignored."
```

Rev 1's text said "never touched by any action" and "Applied after 'Channel Groups to
Process'". Both were false: the promise over-reached (§4.4b), and the "applied after"
clause named the wrong setting for two of the five sites. A clause is also added to **both**
include fields' help text pointing at the exclusion, since a user configuring the category
actions currently gets no signal the field exists.

Null-safety: read as `(settings.get("ignore_groups") or "").strip()`.
`settings.get(k, "").strip()` crashes on a stored `None`, and `dict.get` cannot distinguish
absent from present-but-null.

Lifecycle: adding a field is safe on upgrade — `_merge_settings_with_defaults` only ever
ADDS, so existing installs get `""`, a no-op. Because Dispatcharr never prunes a removed
setting, the key name is permanent; a future slice must not reuse `ignore_groups` for
different semantics.

### 5.3 Resolver: a pure core plus a thin Django wrapper

Mirroring the established workspace shape (`epg_watchdog.py` pure + glue in `plugin.py`;
`wildcard_match.py` Django-free), the logic splits so the interesting part needs no mocks:

```python
# group_scope.py  (Django-free, unit-tested with zero mocks)
def resolve_group_scope(include_value, ignore_value, group_name_to_ids, *, include_label):
    """-> GroupScope(group_ids, include_ungrouped, ignored_names, info) or raises GroupScopeError."""
```

```python
# plugin.py (thin)
def _resolve_group_scope(self, settings, logger, include_key):
    """Fetch groups, delegate, format the error/info returns."""
```

`include_key` is validated against `frozenset({"selected_groups", "category_groups"})` —
a stringly-typed key that silently degrades to `""` (= "all groups") is exactly the
silent-empty family §4.1 exists to kill. Two named wrappers, `_resolve_process_scope` and
`_resolve_category_scope`, keep the key out of the call sites.

Steps:

1. Fetch groups once and build `name -> set(ids)`. **A set, not a scalar:** the existing
   `{g['name']: g['id']}` comprehension (`:935`) silently collapses two groups sharing a
   name, which would leave one of them unprotected by an exclusion naming it.
2. Apply the include filter. **Matching** semantics preserved verbatim (exact,
   case-sensitive). **Error** semantics unified: today the five sites behave three
   different ways (§3 table); all five now error when a non-empty include list resolves to
   nothing. That is a deliberate behaviour change at the two logo sites, and it is the
   `bug-044` fix rather than a regression. It ships in slice A, separately from the feature.
3. Resolve `ignore_groups` against **all** group names via
   `expand_patterns(..., ci_plain=True)`; subtract the matched ids. Apply §4's table.
4. Return a `GroupScope` carrying an always-explicit, always-non-empty `group_ids`, the
   `include_ungrouped` flag (§4.2), the resolved ignored names, and an info string for
   logs, the CSV header and the progress file.

Errors surface as `GroupScopeError`, caught explicitly at each call site ahead of the
generic handler, returning `{"status": "error", "error": msg}` (§4.5).

### 5.4 Surfacing

- **Validate Settings** gains an Ignore line feeding the existing counters:
  `✅ Ignore: 2 group(s) — Teamarr, Teamarr Live` / `❌ Ignore: 'Teamar' matched nothing`.
  Its return must carry `error` when the count is non-zero, or the ❌ is a green toast.
  This is also the first group validation in that action — it validates neither include
  field today.
- **`_generate_csv_settings_header`** gains `'ignore_groups': 'Channel Groups to Ignore'`,
  plus an **excluded-count line** (`# Excluded by ignore: 40 channel(s) in 1 group(s)`).
  Absence of rows alone proves nothing — a narrowed include filter and a typo produce the
  same empty CSV.
- **The progress file** (`ProgressTracker` / `build_status_message`) records the scope info,
  so Show Status can report whether the completed run honoured the exclusion. The plugin
  keeps a persistent status file precisely so the user need not read container logs.
- **Action messages** report the excluded count on success.

## 6. Testing

No Django locally; tests run against `tests/conftest.py`'s mocks. `Plugin()` is safe to
construct (verified: `__init__` does no I/O), but the `Plugin.fields` **property** must
never be executed in tests — it performs a live GitHub version check and an ORM query.

New fixtures in `conftest.py`: `plugin_instance`, `logger`, `fake_groups` (installs a fake
`ChannelGroup.objects` so the **real** `_get_all_groups` and the real map-building run), and
`fake_channel` (below). `plugin_module` is session-scoped, so all installs go through
`monkeypatch`, never bare `setattr`.

### 6.1 The empty-set test must observe the producer

Rev 1 specified `assert _get_all_channels(logger, group_ids=set()) == []` and claimed it
fails against today's code. **It does not.** `Channel.objects` is a MagicMock, so
`list(qs.values(...))` is `[]` for every input — the assertion is a tautology that passes
with the guard fixed, broken, or deleted.

The test needs a fake queryset that implements real filter semantics **and records the
call**, so the assertion is on whether `.filter()` was applied:

```python
class FakeQS:
    def __init__(self, rows, calls): self.rows, self.calls = rows, calls
    def filter(self, **kw):
        self.calls.append(kw)                        # <-- the observable
        ids = kw["channel_group_id__in"]
        return FakeQS([r for r in self.rows if r["channel_group_id"] in ids], self.calls)
    def values(self, *f): return [{k: r[k] for k in f} for r in self.rows]

def test_empty_group_ids_filters_to_nothing(plugin_instance, logger, fake_channel):
    assert plugin_instance._get_all_channels(logger, group_ids=set()) == []
    assert fake_channel == [{"channel_group_id__in": set()}]   # load-bearing
```

Verified by a reviewer against the unmodified repo: this **fails** on today's `:676` and
passes once hardened, with the `None` and subset cases as surviving controls. Per the
workspace's mutation-testing rule, judge by pytest's return code and keep a control that
must survive.

### 6.2 Pure resolver tests (`tests/test_group_scope.py`)

Zero mocks against `resolve_group_scope`: include-only; **ignore-only (the reporter's
case)**; both combined; a token matching nothing → error; a token matching a real group
outside the include scope → no-op, no error; exclusion emptying the set → error; exclusion
a superset of the inclusion → error with a message distinguishing it from "your include
list matched nothing"; blank + blank → all groups; `ignore_groups="*"` → error, with and
without an include filter; no groups exist at all → its own error.

Parsing and matching edge cases, each pinning real behaviour: `" , "` / `","` / `"\n"` →
treated as no ignore list, **no error**; duplicate tokens → one warning each; a group name
containing a comma → unreachable by a literal, error message says so and `Movies*` does
match it; a name containing `[` `]` → treated as a literal by the `*?`-only glob probe; a
group literally named `Sports?` → only reachable by an over-broad glob (documented);
duplicate group names → **both** ids subtracted; a group row missing `name` or `id`.

Case folding: `expand_patterns` uses `.lower()`, not `.casefold()`, so `İSTANBUL`/`istanbul`
and `STRAßE`/`Strasse` do **not** match, and NFD-typed `Théâtre` does not match an
NFC-stored name. Tests pin these as **documented limits** rather than fixing them —
changing to `casefold()` would break the byte-identical copy and its provenance pin (§5.1).
Accented ASCII-foldable names (`Ä`/`ä`) do work.

### 6.3 The two file-driven actions (§4.3)

A test writing a results file with rows in both an ignored and a kept group, monkeypatching
`_bulk_update_channels` to capture, and asserting only the kept row's id is updated. Same
for `rename_unknown_channels_action`. Without this, §4.3's fix is untested and the
reporter's exact scenario stays broken.

### 6.4 The wiring guard (`tests/test_group_scope_wiring.py`)

An AST test asserting, for all five `_get_all_channels` call sites: `group_ids` is passed by
keyword; it is not a literal `None`; it is **not an `ast.IfExp`** (the
`ids if selected_groups_str else None` shape at `:956` today); it traces to a name bound
from `_resolve_group_scope` in the same function; and the resolver call precedes the fetch.
The site count is pinned so a new fetch site fails the build loudly.

Following `metricsarr/tests/test_no_mutations.py` — the workspace's mature AST precedent —
it carries **synthetic self-tests** feeding the detector inline source it must reject
(literal `None`, the `IfExp` shape, an unwired set comprehension). An AST guard with no
positive fixture is the `boxborderw` failure mode: inert for months, exit 0 throughout.

A companion guard bans `Channel.objects` outside an allowlist of helper methods — that is
what would have caught the direct read at `:2337`.

### 6.5 Contract gaps to close

`test_manifest_field_ids_present_in_source` is **one-directional** (plugin.json →
plugin.py), so rev 1's claim that the contract test catches a field landing in one place
only was wrong in the direction that matters: a field added to `Plugin.fields` alone passes
silently. Add the reverse assertion, plus a BMP-only check over `plugin.py`'s field text
(today only `plugin.json` and `Plugin.actions` labels are checked, and the `fields`
property can't be executed), plus assertions that `ignore_groups` reaches
`_generate_csv_settings_header`'s `field_labels` and `validate_settings_action` — otherwise
§5.4's surfacing is entirely untested.

And the `error`-key guard from §4.5: an AST test over every `Return` of a dict literal
whose `status` is `"error"`, asserting an `error` key is present.

## 7. Non-goals restated

The exclusion protects channels from being *processed* and from being *written into*. It
does not make the plugin blind to them: `_detect_duplicate_channels` still reads their names
for collision suffixes, and Import still reads all channel names for duplicate detection.
Both are read-only with respect to the ignored group.

## 8. Release — two slices

**Slice A — hardening (no new setting, ships first).** Independently revertible, and it
fixes real defects whether or not the feature lands.

1. `_get_all_channels`: `if group_ids is not None:` + a warning on the empty branch (§4.1).
2. Ungrouped-channel handling (§4.2).
3. Unified include-error semantics at the five sites (§5.3 step 2).
4. `error` set on every failure return, plus the AST guard (§4.5).
5. `bug-044` already logged; add a `docs/CHANGELOG.md` entry.

**Slice B — the feature.** Purely additive to existing configs: `wildcard_match.py`,
`group_scope.py`, the setting, the resolver, the five fetch sites, the two file-driven
actions (§4.3), the two write-direction guards (§4.4), and the surfacing (§5.4).

Gates for each slice, in order:

- `python -m pytest -q` (482 tests today) — judge by the **return code**.
- `ruff check .`
- `python scripts/check_version_sync.py`
- The core parity + golden gates (the matcher core is untouched, but CI runs them; note the
  working tree already carries an uncommitted bug-126 re-vendor — do not sweep it in).
- `python scripts/bump_version.py` (calver `Major.YY.DDDHHMM`; current released version is
  `1.26.1930617`).
- Build with `scripts/package_plugin.py` or `git archive`, never `Compress-Archive`
  (bug-087). Gate with `python scripts/validate_zip.py Channel-Maparr.zip`, **and confirm
  `wildcard_match.py` and `group_scope.py` appear in the zip listing** — a missing new
  module is an import-time death on install, the failure that already hit `matching_core.py`.
- CRLF: this repo has **not** been renormalized (bug-118), and `validate_zip.py` does not
  check line endings. Pin the new files LF in `.gitattributes` and byte-check the zip.
- Deploy: `docker cp` `plugin.py`, the new modules, and `plugin.json` **last** (hot-reload
  fires on `plugin.json` mtime) into `/data/plugins/channel-mapparr/`, then
  `chown dispatch:dispatch`. Never exec plugin code as root against `/data` (E3 trap).

Docs per this repo's OpenWolf rules: `docs/CHANGELOG.md`, the settings table in the README /
`Channel-Maparr.txt`, where the new field sits in the documented 8-step run order,
`.wolf/anatomy.md` entries for the new modules, `.wolf/cerebrum.md` learnings and the §10
divergence decision, and `.wolf/buglog.json` (bug-044 logged; add entries for anything found
during implementation).

## 9. Acceptance

On the live container, by the user. **Take a Dispatcharr backup first** — steps 6-7 mutate
(`apps.backups.tasks.create_backup_task.apply()`), per the workspace hard rule.

Read-only and dry-run:

1. `ignore_groups=Teamarr`, `selected_groups` blank. Validate Settings reports
   `✅ Ignore: 1 group(s) — Teamarr`.
2. Dry-run Load & Process: zero Teamarr rows; the header records the exclusion and a
   non-zero excluded count.
3. Category Groups Dry Run: zero Teamarr rows. (Exercises the `category_groups` path, which
   rev 1's acceptance never touched.)
4. `ignore_groups=Teamar` (typo): both actions refuse with a **red persistent error**, not a
   green toast.
5. `ignore_groups=" , "`: behaves exactly as blank — no error, everything processes.

Live writes — the only steps that prove what the reporter cares about:

6. Record the Teamarr channels' `name` and `channel_group_id` from the DB. With
   `ignore_groups=Teamarr` and `dry_run_mode=OFF`, run Rename Channels, Tag Unknown
   Channels and Organize by Category. Re-query: **byte-identical**. Assert on the rows, not
   on the toast.
7. The stale-file case: run Load & Process with `ignore_groups` **blank**, then set
   `ignore_groups=Teamarr`, then click Rename Channels. Teamarr channels must be unchanged
   (§4.3). This is the step that fails against rev 1's design.

## 10. Follow-ups, not this slice

- **EPG-Janitor's `ignore_groups` help text contradicts its code.** It promises "Applied
  after 'Channel Groups' filter" while `:1589` raises on that combination. Resolution is
  pre-chosen: make EPG-Janitor *compose* (its ignore path is an `elif`, so the change is
  small); do **not** "fix" the text to match the worse behaviour. Record the divergence in
  `.wolf/cerebrum.md` so §10 is not the only trace of it.
- Porting `ignore_groups` to Stream-Mapparr (`selected_groups`, `selected_stream_groups`)
  and Event-Channel-Managarr (`channel_groups` — load-bearing for its dummy-EPG detach
  scope, needs its own design pass).
- An exclusion counterpart for `m3u_group_filter`.
- A blanket audit of the plugin's other transient-`message` returns for `file` (CSV exports
  currently say "exported to X" in a toast and never render the "Output:" link).
- `_generate_csv_settings_header`'s `field_labels` omits `rate_limiting`, and labels
  `category_groups` differently from its own field label (`:299` vs `:636`).

## 11. Revision history

**Rev 2 (2026-07-26)** — four independent reviewers (correctness, design, completeness, test
strategy) audited rev 1 against the source. Changes:

- **Two rev-1 claims were false.** The `SHARED_FILES` rationale (§5.1) — each plugin has its
  own `sync_core.py` copy, so the stated constraint did not exist; the conclusion is kept on
  new grounds plus a provenance pin. And the contract-test claim (§6.5) — field parity is
  one-directional, so a field added to `Plugin.fields` alone passes silently.
- **Three findings broke the feature as designed.** Rename/Tag mutate from a persisted file
  and never fetch channels (§4.3 — the reporter's exact scenario would still have renamed
  his channels); always passing an explicit set evicts ungrouped channels (§4.2); and the
  plugin sets `error` on zero of ~30 failure returns, making rev 1's "refuses with a red
  error" acceptance step impossible (§4.5).
- **The specified empty-set test was a tautology** under conftest's MagicMock — proven by a
  reviewer to pass whether or not the guard is fixed (§6.1).
- **New:** duplicate group names collapsing in `name -> id` (§5.3 step 1); `" , "` causing a
  total outage under fail-closed (§4); write-direction guards for Organize and Import
  (§4.4); include∩exclude semantics pinned (§4); the partial-match "warn and proceed" row
  withdrawn in favour of fail-closed; null-safe settings reads; the release and deploy gates
  §8 omitted; live-write acceptance with a backup (§9).
- **Sliced into A (hardening) and B (feature)** so a behaviour change to existing configs is
  revertible without pulling the feature.
- **Endorsed unchanged:** the global-guard insight (§3a), fail-closed on the primary input,
  composition over EPG-Janitor's `raise`, and keeping the glob helper unshared.
