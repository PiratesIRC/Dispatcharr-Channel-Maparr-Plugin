# Ignore Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ignore_groups` setting that excludes named channel groups from every
Channel-Maparr action, so a user can process all groups except the ones another tool owns.

**Architecture:** Two independently shippable slices. **Slice A** (Tasks 1-5) hardens three
pre-existing defects the feature would otherwise inherit: an empty group-id set that means
"all channels", ungrouped channels being evicted by an explicit id filter, and failure
returns that render as green success toasts. **Slice B** (Tasks 6-15) adds a Django-free
`group_scope.py` resolver plus a vendored `wildcard_match.py`, wires it into the five
channel-fetch sites, the two actions that mutate from a persisted results file, and the two
write paths that can create channels *into* an ignored group.

**Tech Stack:** Python 3.11/3.12, stdlib only for the new modules (`fnmatch`, `re`,
`dataclasses`). Django ORM in `plugin.py` (never importable locally — mocked in tests).
pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-26-ignore-groups-design.md` (rev 2). Read §4 before
Task 7 — the behaviour table is the contract.

## Global Constraints

- **The deployable code is the inner folder** `Channel-Maparr/Channel-Maparr/`. Only files
  there ship. Tests live in `tests/` at the repo root.
- **`plugin.py` cannot be imported or run locally.** `from apps.channels.models import ...`
  and `from django.db import ...` resolve only inside Dispatcharr. Never "fix" them. Tests
  reach the module through `tests/conftest.py`, which MagicMock-mocks Django and loads the
  hyphenated folder as the synthetic package `channel_maparr`.
- **Never execute the `Plugin.fields` property in a test.** It performs a live GitHub version
  check and an ORM query. Assert the field contract against `plugin.py` **source text**.
- **`Plugin.fields` and `Plugin.actions` in the Python class are the runtime source of
  truth**; `plugin.json` is the manifest. A new field must land in **both** or the UI and the
  manifest drift.
- **BMP-only text everywhere Dispatcharr parses** (`plugin.json` and the Plugin class). Any
  character above U+FFFF makes the loader silently drop the whole action. Allowed glyphs
  include `✅ ⚠ ❌ ✓ ℹ`; never `🎨 🖼 📊`.
- **Dispatcharr's plugin card renders only `message` (transient green toast), `error` (red,
  persistent) and `file`. `status` renders NOWHERE.** A failure that sets no `error` key is
  pixel-identical to success. Set exactly one of `message` / `error` per return.
- **Read settings null-safely: `(settings.get(key) or "")`.** `settings.get(key, "")` returns
  `None` for a stored null and then `.strip()` raises.
- **`dataclasses`, `re`, `fnmatch` only** in the new pure modules. No Django, no network, no
  filesystem.
- **Do not touch `Channel-Maparr/matching_core.py` or `scripts/core_manifest.json`.** The
  working tree already carries an uncommitted bug-126 re-vendor. Never `git add -A`; stage
  named files only.
- **Version format** is calver `Major.YY.DDDHHMM`, bumped only via
  `python scripts/bump_version.py`. Current released version: `1.26.1930617`.
- Run the suite with `python -m pytest -q` from the repo root and **judge by the return
  code**, never by grepping output.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `Channel-Maparr/wildcard_match.py` | **New.** Verbatim copy of EPG-Janitor's glob helper. `expand_patterns(tokens, available_names, ci_plain)`. Frozen — provenance-pinned by a test. |
| `Channel-Maparr/group_scope.py` | **New.** Django-free scope resolution: token parsing, name→ids mapping, include/exclude rules, the error vocabulary. All of §4's behaviour table lives here and is testable with zero mocks. |
| `Channel-Maparr/plugin.py` | Modified. Thin Django glue: fetch groups, delegate to `group_scope`, format returns. Plus the new field, the five wired fetch sites, the two file-driven actions, the two write-direction guards, and the surfacing. |
| `Channel-Maparr/plugin.json` | Modified. The new field, mirroring the class. |
| `tests/conftest.py` | Modified. New fixtures: `plugin_instance`, `logger`, `fake_groups`, `fake_channel` (a **recording** fake queryset — see Task 1). |
| `tests/test_get_all_channels.py` | **New.** Slice A: the empty-set guard and ungrouped handling, asserted on the recorded `.filter()` calls. |
| `tests/test_error_visibility.py` | **New.** Slice A: AST guard that every `status: "error"` return also sets `error`. |
| `tests/test_wildcard_match.py` | **New.** The vendored helper, plus its provenance hash pin. |
| `tests/test_group_scope.py` | **New.** The pure resolver: §4's table, parsing edges, unicode limits. |
| `tests/test_group_scope_wiring.py` | **New.** AST guard: all five fetch sites take `group_ids` from the resolver; `Channel.objects` stays inside allowed helpers. Carries synthetic self-tests. |
| `tests/test_ignore_groups_actions.py` | **New.** The file-driven rename/tag re-filter and the two write-direction guards. |
| `tests/test_plugin_contract.py` | Modified. Reverse field parity + BMP check over `plugin.py`. |

---

# SLICE A — HARDENING

Ships first, on its own version bump. Fixes `bug-044` and the invisible-failure defect
whether or not Slice B lands.

---

### Task 1: Recording test double + the empty-set guard (bug-044 layer 1)

**Why this is first:** every later test needs these fixtures, and the spec's original test
for this bug was a tautology — under conftest's MagicMock, `list(qs.values(...))` is `[]` for
every input, so the assertion passed whether the guard was fixed, broken, or deleted. The fix
is to assert on **whether `.filter()` was called**, which needs a fake queryset that records.

**Files:**
- Modify: `tests/conftest.py` (append fixtures)
- Create: `tests/test_get_all_channels.py`
- Modify: `Channel-Maparr/plugin.py:673-678` (`_get_all_channels`)

**Interfaces:**
- Produces: fixtures `plugin_instance`, `logger`, `fake_groups`, `fake_channel` used by
  Tasks 2, 9, 12, 13, 14. `fake_channel` returns the list of recorded `.filter(**kwargs)`
  calls.

- [ ] **Step 1: Add the fixtures to `tests/conftest.py`**

Append to the end of the file:

```python
# ---------------------------------------------------------------------------
# Plugin-instance fixtures
#
# Plugin() is safe to construct: __init__ only sets attributes, builds a
# FuzzyMatcher (lazy - see tests/test_lazy_db_load.py) and logs. It does no
# network or DB I/O. The `fields` PROPERTY does both, so never touch it here.
# ---------------------------------------------------------------------------


class FakeQuerySet:
    """Minimal queryset that implements real filter semantics AND records calls.

    A bare MagicMock returns [] for every input, which makes assertions about
    filtering vacuously true. Recording the .filter() kwargs is what lets a
    test prove the filter was actually applied.
    """

    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def filter(self, **kwargs):
        self.calls.append(kwargs)
        ids = kwargs["channel_group_id__in"]
        kept = [r for r in self.rows if r["channel_group_id"] in ids]
        return FakeQuerySet(kept, self.calls)

    def values(self, *fields):
        return [{k: r[k] for k in fields} for r in self.rows]


CHANNEL_ROWS = [
    {"id": 1, "name": "A", "channel_number": 1.0, "channel_group_id": 10, "logo_id": None},
    {"id": 2, "name": "B", "channel_number": 2.0, "channel_group_id": 20, "logo_id": None},
    {"id": 3, "name": "Orphan", "channel_number": 3.0, "channel_group_id": None, "logo_id": None},
]


@pytest.fixture
def plugin_instance(plugin_module):
    return plugin_module.Plugin()


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def fake_channel(monkeypatch, plugin_module):
    """Install a recording fake Channel.objects. Returns the recorded filter calls.

    plugin_module is session-scoped and shared across the whole run, so this MUST
    go through monkeypatch (auto-undone) and never a bare setattr.
    """
    calls = []
    channel = MagicMock()
    channel.objects.all = lambda: FakeQuerySet(list(CHANNEL_ROWS), calls)
    monkeypatch.setattr(plugin_module, "Channel", channel)
    return calls


@pytest.fixture
def fake_groups(monkeypatch, plugin_module):
    """Install a fake ChannelGroup.objects so the REAL _get_all_groups runs.

    Tests drive the real producer (_get_all_groups plus the name-map building)
    rather than injecting a finished dict into the consumer.
    """

    def _install(rows):
        group = MagicMock()
        group.objects.all.return_value.values.return_value = list(rows)
        monkeypatch.setattr(plugin_module, "ChannelGroup", group)
        return rows

    return _install
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_get_all_channels.py`:

```python
"""_get_all_channels scope guards (bug-044).

`if group_ids:` treats an EMPTY set as "no filter", which means EVERY channel.
Two callers can reach that state from a typo, so the empty set must filter to
nothing instead.
"""


def test_empty_group_ids_filters_to_nothing(plugin_instance, logger, fake_channel):
    assert plugin_instance._get_all_channels(logger, group_ids=set()) == []
    # Load-bearing: prove the filter was APPLIED. Without this the assertion
    # above passes against the broken guard too.
    assert fake_channel == [{"channel_group_id__in": set()}]


def test_empty_group_ids_logs_a_warning(plugin_instance, logger, fake_channel):
    """Degrade loudly. A silent empty scope is indistinguishable from 'no channels'."""
    plugin_instance._get_all_channels(logger, group_ids=set())
    assert logger.warning.called


def test_none_group_ids_returns_every_channel(plugin_instance, logger, fake_channel):
    """CONTROL - must keep passing. None means 'no scope', which is all channels."""
    rows = plugin_instance._get_all_channels(logger, group_ids=None)
    assert len(rows) == 3
    assert fake_channel == []


def test_subset_group_ids_filters(plugin_instance, logger, fake_channel):
    """CONTROL - must keep passing."""
    rows = plugin_instance._get_all_channels(logger, group_ids={10})
    assert [r["id"] for r in rows] == [1]
```

- [ ] **Step 3: Run the test to verify the first two fail**

```bash
python -m pytest tests/test_get_all_channels.py -v
```

Expected: `test_empty_group_ids_filters_to_nothing` FAILS (it returns all 3 rows and records
no filter call), `test_empty_group_ids_logs_a_warning` FAILS, and the two CONTROL tests PASS.
If a control fails, the fixtures are wrong — fix them before touching `plugin.py`.

- [ ] **Step 4: Harden `_get_all_channels`**

Replace `Channel-Maparr/plugin.py:673-678` with:

```python
    def _get_all_channels(self, logger, group_ids=None):
        """Fetch channels via Django ORM, optionally filtered by group IDs.

        group_ids=None means "no scope" (every channel). An EMPTY set means "a
        scope that resolved to nothing" and must return nothing - `if group_ids:`
        collapsed those two cases and silently widened the scope to every channel
        in the database (bug-044).
        """
        qs = Channel.objects.all()
        if group_ids is not None:
            if not group_ids:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} Group scope resolved to zero groups - "
                    f"no channels will be processed."
                )
            qs = qs.filter(channel_group_id__in=group_ids)
        return list(qs.values('id', 'name', 'channel_number', 'channel_group_id', 'logo_id'))
```

- [ ] **Step 5: Run the test to verify all four pass**

```bash
python -m pytest tests/test_get_all_channels.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the whole suite**

```bash
python -m pytest -q
```

Expected: exit code 0. If a pre-existing test fails, it is telling you a caller relies on the
old truthiness behaviour — read it, do not "fix" the test.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_get_all_channels.py Channel-Maparr/plugin.py
git commit -m "fix(scope): an empty group-id set must filter to nothing, not everything

bug-044. _get_all_channels guarded with `if group_ids:`, so set() was
indistinguishable from None and both meant every channel in the database. Two
callers reach set() from a typo in Channel Groups to Process, which applied
logos to every channel instead of none.

The test asserts on the RECORDED .filter() call, not just the returned rows -
under conftest's MagicMock a naive row assertion is a tautology that passes
against the broken guard."
```

---

### Task 2: Ungrouped channels must survive an explicit scope

**Context:** today a blank include filter passes `group_ids=None`, so channels with
`channel_group_id IS NULL` are included and rendered as `'No Group'` (`plugin.py:964`). Slice
B will always pass an explicit set of real group ids, which would evict every ungrouped
channel from Rename, Tag, Default Logo and tv-logos. `ignore_groups` can never name a NULL
group, so an exclusion must not be able to drop them.

**Design note:** this is done with a Python-side post-filter rather than `django.db.models.Q`
so no new Django import is needed (`Q` is not currently imported, and `django.db.models` is
not in conftest's mock list). The ORM filter still does the work in the common case. At ~1440
channels the post-filter is free, and the unfiltered fetch is exactly what the `None` path
already does today.

**Files:**
- Modify: `Channel-Maparr/plugin.py:673-690` (`_get_all_channels`, as rewritten in Task 1)
- Modify: `tests/test_get_all_channels.py`

**Interfaces:**
- Produces: `_get_all_channels(logger, group_ids=None, include_ungrouped=False)`. Tasks 10
  and 14 pass `include_ungrouped=scope.include_ungrouped`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_get_all_channels.py`:

```python
def test_include_ungrouped_keeps_null_group_channels(plugin_instance, logger, fake_channel):
    """A blank include filter historically passed None, which included NULL-group
    channels. An explicit id set would evict them, so the flag preserves them."""
    rows = plugin_instance._get_all_channels(
        logger, group_ids={10}, include_ungrouped=True)
    assert sorted(r["id"] for r in rows) == [1, 3]      # kept group 10 + the orphan


def test_include_ungrouped_false_drops_null_group_channels(plugin_instance, logger, fake_channel):
    rows = plugin_instance._get_all_channels(
        logger, group_ids={10}, include_ungrouped=False)
    assert [r["id"] for r in rows] == [1]


def test_include_ungrouped_with_empty_scope_keeps_only_orphans(plugin_instance, logger, fake_channel):
    """ignore_groups='*' with a blank include: every group excluded, but an
    ungrouped channel is not in any group, so it is not excluded by name."""
    rows = plugin_instance._get_all_channels(
        logger, group_ids=set(), include_ungrouped=True)
    assert [r["id"] for r in rows] == [3]
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_get_all_channels.py -v
```

Expected: the three new tests FAIL with `TypeError: _get_all_channels() got an unexpected
keyword argument 'include_ungrouped'`.

- [ ] **Step 3: Implement**

Replace `_get_all_channels` with:

```python
    def _get_all_channels(self, logger, group_ids=None, include_ungrouped=False):
        """Fetch channels via Django ORM, optionally filtered by group IDs.

        group_ids=None means "no scope" (every channel). An EMPTY set means "a
        scope that resolved to nothing" and returns nothing - `if group_ids:`
        collapsed those two cases and silently widened the scope to every channel
        in the database (bug-044).

        include_ungrouped keeps channels whose channel_group_id is NULL. A blank
        include filter used to pass group_ids=None, which included them; once an
        explicit id set is always passed they would silently vanish, and no
        exclusion can name a NULL group anyway.
        """
        qs = Channel.objects.all()
        scoped = group_ids is not None

        if scoped and not include_ungrouped:
            if not group_ids:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} Group scope resolved to zero groups - "
                    f"no channels will be processed."
                )
            qs = qs.filter(channel_group_id__in=group_ids)

        rows = list(qs.values(
            'id', 'name', 'channel_number', 'channel_group_id', 'logo_id'))

        if scoped and include_ungrouped:
            keep = set(group_ids)
            rows = [
                r for r in rows
                if r.get('channel_group_id') in keep
                or r.get('channel_group_id') is None
            ]
            if not group_ids:
                logger.warning(
                    f"{PLUGIN_LOG_PREFIX} Group scope resolved to zero groups - "
                    f"only ungrouped channels will be processed."
                )
        return rows
```

- [ ] **Step 4: Run to verify all seven pass**

```bash
python -m pytest tests/test_get_all_channels.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the whole suite and commit**

```bash
python -m pytest -q
git add tests/test_get_all_channels.py Channel-Maparr/plugin.py
git commit -m "fix(scope): keep ungrouped channels when a scope is explicit

A blank include filter passed group_ids=None, so channels with a NULL
channel_group_id were included and rendered as 'No Group'. Passing an explicit
id set (which the ignore_groups resolver will always do) would evict them.
include_ungrouped preserves them, and no exclusion can name a NULL group."
```

---

### Task 3: Include-filter typos must refuse, not widen (bug-044 layer 2)

**Context:** the five fetch sites behave three different ways on an unresolvable include
value. `load_and_process_channels_action:947` errors and names the bad tokens; the two
Organize sites error with a different, name-less message; **the two logo sites silently
produce an empty set.** With Task 1 landed, that empty set now filters to nothing rather than
everything — but "silently does nothing" is still wrong. Both logo sites get the same guard
as the existing pattern. Slice B replaces all five inline blocks with the resolver.

**Files:**
- Modify: `Channel-Maparr/plugin.py:1374-1383` (`apply_logos_action`)
- Modify: `Channel-Maparr/plugin.py:1435-1443` (`apply_tv_logos_action`)
- Create: `tests/test_ignore_groups_actions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ignore_groups_actions.py`:

```python
"""Action-level scope behaviour."""
import pytest


GROUPS = [{"id": 10, "name": "Sports"}, {"id": 20, "name": "News"}]


@pytest.mark.parametrize("action_name", [
    "apply_logos_action",
    "apply_tv_logos_action",
])
def test_logo_actions_refuse_an_unresolvable_include_filter(
        plugin_instance, logger, fake_channel, fake_groups, action_name):
    """A typo in Channel Groups to Process must refuse, not silently no-op.

    Before bug-044 it applied logos to EVERY channel; after the _get_all_channels
    fix it silently did nothing. Neither is acceptable feedback.
    """
    fake_groups(GROUPS)
    action = getattr(plugin_instance, action_name)
    result = action({"selected_groups": "Sprots", "channel_databases": "US"}, logger)
    assert result["status"] == "error"
    assert "Sprots" in result["message"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest tests/test_ignore_groups_actions.py -v
```

Expected: both parametrized cases FAIL — the actions return success (or an unrelated error),
not an error naming `Sprots`.

- [ ] **Step 3: Implement in `apply_logos_action`**

Replace `plugin.py:1374-1380` (the `selected_groups_str` block) with:

```python
            selected_groups_str = (settings.get("selected_groups") or "").strip()
            target_group_ids = None
            if selected_groups_str:
                all_groups = self._get_all_groups(logger)
                group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
                input_names = {name.strip() for name in selected_groups_str.split(',') if name.strip()}
                valid_names = {n for n in input_names if n in group_name_to_id}
                invalid_names = input_names - valid_names
                target_group_ids = {group_name_to_id[name] for name in valid_names}
                if not target_group_ids:
                    msg = (f"None of the specified groups could be found: "
                           f"{', '.join(sorted(invalid_names))}")
                    return {"status": "error", "error": msg}
```

- [ ] **Step 4: Implement the identical guard in `apply_tv_logos_action`**

Replace `plugin.py:1435-1441` with the same block (the surrounding code differs; only the
`selected_groups_str` resolution block changes).

- [ ] **Step 5: Run to verify both pass, then the whole suite**

```bash
python -m pytest tests/test_ignore_groups_actions.py -v
python -m pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_ignore_groups_actions.py Channel-Maparr/plugin.py
git commit -m "fix(scope): logo actions refuse an unresolvable include filter

The two logo sites built their include set with a filtering comprehension and
no error path, so a typo in Channel Groups to Process produced an empty set.
Before bug-044's fix that meant every channel; after it, a silent no-op. Both
now refuse with a visible error naming the bad tokens, matching what
load_and_process_channels_action already did."
```

---

### Task 4: Failures must be visible (`error` key) + AST guard

**Context:** `grep -c '"error":' Channel-Maparr/plugin.py` returns **0**. Dispatcharr's card
renders `message` as a transient green toast, `error` as persistent red, and `status`
nowhere — so every failure in this plugin is currently pixel-identical to success. Task 3
already set `error` on two returns; this task does the rest and locks it with a guard.

**Files:**
- Create: `tests/test_error_visibility.py`
- Modify: `Channel-Maparr/plugin.py` (every `status: "error"` return; ~30 sites)

- [ ] **Step 1: Write the failing guard**

Create `tests/test_error_visibility.py`:

```python
"""Dispatcharr's plugin card renders `message` (transient green toast), `error`
(persistent red) and `file`. `status` renders NOWHERE. A failure return that
sets no `error` key looks exactly like success.
"""
import ast
from pathlib import Path

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Channel-Maparr" / "plugin.py"


def _error_returns_without_error_key(source):
    """Line numbers of `return {... "status": "error" ...}` with no "error" key."""
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        pairs = {}
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant):
                pairs[key.value] = value
        status = pairs.get("status")
        if (isinstance(status, ast.Constant) and status.value == "error"
                and "error" not in pairs):
            offenders.append(node.lineno)
    return offenders


def test_every_literal_error_return_sets_the_error_key():
    offenders = _error_returns_without_error_key(
        PLUGIN_PY.read_text(encoding="utf-8"))
    assert not offenders, (
        f"status='error' returns with no `error` key (invisible to the user) "
        f"at lines: {offenders}"
    )


# --- the detector must BITE: an AST guard with no positive fixture is inert ---

BAD = '''
def f():
    return {"status": "error", "message": "boom"}
'''

GOOD_ERROR_KEY = '''
def f():
    return {"status": "error", "error": "boom"}
'''

GOOD_NOT_AN_ERROR = '''
def f():
    return {"status": "success", "message": "fine"}
'''


def test_detector_flags_an_error_return_without_the_key():
    assert _error_returns_without_error_key(BAD) == [3]


def test_detector_accepts_a_visible_error():
    assert _error_returns_without_error_key(GOOD_ERROR_KEY) == []


def test_detector_ignores_success_returns():
    assert _error_returns_without_error_key(GOOD_NOT_AN_ERROR) == []
```

- [ ] **Step 2: Run to see the offender list**

```bash
python -m pytest tests/test_error_visibility.py -v
```

Expected: the three detector self-tests PASS; `test_every_literal_error_return_sets_the_error_key`
FAILS with a list of ~28 line numbers. **Record that list** — it is the work queue for step 3.

- [ ] **Step 3: Convert every offender**

For each line in the list, rewrite the return so the operator-facing text moves to `error`
and `message` is dropped. Exactly one of the two is ever set:

```python
# before
return {"status": "error", "message": f"Error renaming channels: {e}"}
# after
return {"status": "error", "error": f"Error renaming channels: {e}"}
```

Work top-to-bottom through the file and re-run the test after every few edits — line numbers
shift as you go, so re-read the failure list rather than trusting the original.

- [ ] **Step 4: Fix `validate_settings_action`'s computed return**

The guard cannot see this one — `status` is a variable (`plugin.py:2836-2840`). Change:

```python
            message = "\n".join(validation_results)

            if status == "error":
                return {"status": status, "error": message}
            return {"status": status, "message": message}
```

Do the same for its `except` handler below.

- [ ] **Step 5: Add a behavioural test for the computed path**

Append to `tests/test_error_visibility.py`:

```python
def test_validate_settings_surfaces_a_red_error(plugin_instance, logger, fake_groups):
    """The computed-status return is invisible to the AST guard, so pin it here."""
    fake_groups([{"id": 10, "name": "Sports"}])
    result = plugin_instance.validate_settings_action({"channel_databases": ""}, logger)
    assert result["status"] == "error"
    assert result.get("error"), "validation failure rendered as a green toast"
    assert "message" not in result
```

- [ ] **Step 6: Run everything**

```bash
python -m pytest tests/test_error_visibility.py -v
python -m pytest -q
```

Expected: all pass, exit 0.

- [ ] **Step 7: Commit**

```bash
git add tests/test_error_visibility.py Channel-Maparr/plugin.py
git commit -m "fix(ui): set \`error\` on every failure return so failures are visible

Dispatcharr's plugin card renders \`message\` as a transient green toast,
\`error\` as persistent red, and \`status\` nowhere at all. This plugin set
\`error\` on zero of ~30 failure returns, so every failure - including a failed
DB write - was pixel-identical to success.

An AST guard now fails the build on any future status='error' return with no
\`error\` key, and it carries synthetic self-tests so it cannot rot into a
no-op. validate_settings_action's computed return is covered behaviourally
because the guard cannot see a variable status."
```

---

### Task 5: Ship Slice A

**Files:**
- Modify: `Channel-Maparr/plugin.py`, `Channel-Maparr/plugin.json` (version, via script)
- Modify: `docs/CHANGELOG.md`
- Modify: `.wolf/buglog.json`, `.wolf/anatomy.md`, `.wolf/memory.md`

- [ ] **Step 1: Run every gate**

```bash
python -m pytest -q ; echo "pytest exit: $?"
ruff check .
python scripts/check_version_sync.py
```

All three must pass. `ruff` and `check_version_sync` are what CI runs.

- [ ] **Step 2: Bump the version**

```bash
python scripts/bump_version.py
```

This keeps `plugin.py` and `plugin.json` in sync. Do not hand-edit either version.

- [ ] **Step 3: Add the CHANGELOG entry**

Add at the top of `docs/CHANGELOG.md`, using the new version:

```markdown
## v<new-version> (July 26, 2026)

**Hardening release. No new settings.** Fixes three defects found while designing the
`ignore_groups` feature (`docs/superpowers/specs/2026-07-26-ignore-groups-design.md`).

- **bug-044 — a typo in "Channel Groups to Process" applied logos to EVERY channel.**
  `_get_all_channels` guarded with `if group_ids:`, so an empty set was indistinguishable
  from `None` and both meant "no filter". The two logo actions built their include set with
  no error path, so an unresolvable value silently widened the scope from the named groups to
  the whole database. Fixed in two layers: an empty set now filters to nothing (loudly), and
  both logo actions refuse an unresolvable include filter with a visible error.
- **Ungrouped channels are no longer at risk of being evicted** when a scope is explicit.
  Channels with no channel group were included only because a blank filter passed `None`.
- **Every failure is now visible.** Dispatcharr's plugin card renders `error` (persistent
  red) and `message` (a transient green toast) but never `status`, and this plugin set
  `error` on none of its ~30 failure returns — so every failure looked like success. An AST
  guard now enforces it.
```

- [ ] **Step 4: Update `bug-044`'s status in `.wolf/buglog.json`**

Set its `fix` field to describe what actually shipped (both layers, plus the test that
asserts on the recorded `.filter()` call) and remove the "NOT YET IMPLEMENTED" prefix.

- [ ] **Step 5: Append the new test files to `.wolf/anatomy.md` and a line to `.wolf/memory.md`**

- [ ] **Step 6: Commit**

```bash
git add Channel-Maparr/plugin.py Channel-Maparr/plugin.json docs/CHANGELOG.md .wolf/
git commit -m "chore(release): v<new-version> - scope hardening (bug-044, error visibility)"
```

- [ ] **Step 7: Hand the deploy to the user**

You cannot deploy or verify this yourself. Ask the user to:

1. Take a backup first (workspace hard rule):
   `docker exec dispatcharr python manage.py shell -c "from apps.backups.tasks import create_backup_task; create_backup_task.apply()"`
2. `docker cp` `plugin.py` **then** `plugin.json` last (hot-reload fires on `plugin.json`
   mtime) into `/data/plugins/channel-mapparr/`, then
   `docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/channel-mapparr`.
3. Confirm: a typo in "Channel Groups to Process" + Apply Default Logo now shows a **red**
   error and changes no logos.

**Do not proceed to Slice B until the user confirms Slice A is live and behaving.**

---

# SLICE B — THE FEATURE

---

### Task 6: Vendor `wildcard_match.py` with a provenance pin

**Files:**
- Create: `Channel-Maparr/wildcard_match.py`
- Create: `tests/test_wildcard_match.py`
- Modify: `.gitattributes`

**Interfaces:**
- Produces: `expand_patterns(tokens, available_names, ci_plain) -> (matched_names, unmatched_tokens)`.
  Used by Task 7.

- [ ] **Step 1: Copy the file verbatim**

```bash
cp ../EPG-Janitor/EPG-Janitor/wildcard_match.py Channel-Maparr/wildcard_match.py
```

Do **not** retype or reformat it. Adjust only the module docstring's first paragraph to name
this plugin, leaving the behaviour description intact. The copy must stay byte-identical apart
from that docstring; Step 4 pins it.

- [ ] **Step 2: Pin it to LF in `.gitattributes`**

This repo has not been renormalized (bug-118), so add:

```
Channel-Maparr/wildcard_match.py text eol=lf
Channel-Maparr/group_scope.py text eol=lf
```

- [ ] **Step 3: Write the tests**

Create `tests/test_wildcard_match.py`:

```python
"""The vendored glob helper. Frozen - see test_provenance_hash."""
import hashlib
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


@pytest.fixture(scope="module")
def expand():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wildcard_match_under_test", PLUGIN_DIR / "wildcard_match.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.expand_patterns


NAMES = ["Teamarr", "Teamarr Live", "Sports", "US: PPV"]


def test_literal_is_case_insensitive_when_ci_plain(expand):
    assert expand(["teamarr"], NAMES, ci_plain=True)[0] == ["Teamarr"]


def test_literal_is_case_sensitive_when_not_ci_plain(expand):
    assert expand(["teamarr"], NAMES, ci_plain=False)[0] == []


def test_glob_matches_case_insensitively(expand):
    matched, unmatched = expand(["teamarr*"], NAMES, ci_plain=True)
    assert matched == ["Teamarr", "Teamarr Live"]
    assert unmatched == []


def test_question_mark_is_a_glob(expand):
    assert expand(["Sport?"], NAMES, ci_plain=True)[0] == ["Sports"]


def test_unmatched_tokens_are_reported_in_input_order(expand):
    matched, unmatched = expand(["Nope", "Sports", "Nada"], NAMES, ci_plain=True)
    assert matched == ["Sports"]
    assert unmatched == ["Nope", "Nada"]


def test_results_are_deduplicated(expand):
    assert expand(["Teamarr", "teamarr"], NAMES, ci_plain=True)[0] == ["Teamarr"]


def test_provenance_hash():
    """This file is a verbatim copy of EPG-Janitor's wildcard_match.py.

    If you edit it deliberately, update this hash AND record the divergence in
    docs/CHANGELOG.md - an ungated copy is the pre-shared-core divergence state.
    """
    raw = (PLUGIN_DIR / "wildcard_match.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest()[:16] == "REPLACE_ON_LANDING"
```

- [ ] **Step 4: Run, then record the real hash**

```bash
python -m pytest tests/test_wildcard_match.py -v
```

The six behaviour tests must PASS immediately (the file is already correct).
`test_provenance_hash` FAILS and prints the actual digest — copy its first 16 hex characters
over `REPLACE_ON_LANDING`, then re-run. `REPLACE_ON_LANDING` must not survive this step.

- [ ] **Step 5: Commit**

```bash
git add Channel-Maparr/wildcard_match.py tests/test_wildcard_match.py .gitattributes
git commit -m "feat(scope): vendor EPG-Janitor's wildcard_match helper

Verbatim copy, pinned by a provenance hash test. Not added to the _shared/
pipeline: the two call sites want different ci_plain values, so there is no
single shared behaviour to pin, and the file is a frozen 50-line fnmatch
wrapper. Pinned LF because this repo is not renormalized (bug-118)."
```

---

### Task 7: `group_scope.py` — parsing and the happy paths

**Files:**
- Create: `Channel-Maparr/group_scope.py`
- Create: `tests/test_group_scope.py`

**Interfaces:**
- Produces, used by Tasks 8-14:
  - `parse_tokens(raw) -> list[str]`
  - `build_name_to_ids(rows) -> dict[str, set[int]]`
  - `GroupScope` dataclass: `group_ids: frozenset`, `include_ungrouped: bool`,
    `ignored_names: tuple[str, ...]`, `out_of_scope_names: tuple[str, ...]`, `info: str`
  - `GroupScopeError(Exception)`
  - `resolve_group_scope(include_value, ignore_value, group_name_to_ids, *, include_label) -> GroupScope`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_group_scope.py`:

```python
"""Pure scope resolution. Zero mocks - this module never imports Django.

The behaviour table this pins is spec section 4 of
docs/superpowers/specs/2026-07-26-ignore-groups-design.md
"""
import importlib.util
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


@pytest.fixture(scope="module")
def gs():
    """Load group_scope.py directly. It imports wildcard_match as a sibling, so
    the plugin dir goes on sys.path rather than loading it as a package."""
    import sys
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "group_scope_under_test", PLUGIN_DIR / "group_scope.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(PLUGIN_DIR))


GROUP_ROWS = [
    {"id": 10, "name": "Sports"},
    {"id": 20, "name": "News"},
    {"id": 30, "name": "Teamarr"},
    {"id": 40, "name": "Teamarr Live"},
]


@pytest.fixture
def names(gs):
    return gs.build_name_to_ids(GROUP_ROWS)


def resolve(gs, names, include="", ignore=""):
    return gs.resolve_group_scope(
        include, ignore, names, include_label="Channel Groups to Process")


# --- parse_tokens ---------------------------------------------------------

def test_parse_splits_on_commas_and_newlines(gs):
    assert gs.parse_tokens("Sports, News\nTeamarr") == ["Sports", "News", "Teamarr"]


def test_parse_drops_empty_and_whitespace_tokens(gs):
    """A stray comma must not become 'an ignore list that matched nothing',
    which under fail-closed would hard-error every action in the plugin."""
    assert gs.parse_tokens(" , ") == []
    assert gs.parse_tokens(",") == []
    assert gs.parse_tokens("\n") == []
    assert gs.parse_tokens("") == []
    assert gs.parse_tokens(None) == []


# --- build_name_to_ids ---------------------------------------------------

def test_duplicate_group_names_keep_both_ids(gs):
    """A scalar name->id map silently drops one of two same-named groups, which
    would leave one unprotected by an exclusion naming it."""
    mapping = gs.build_name_to_ids(
        [{"id": 98, "name": "Teamarr"}, {"id": 99, "name": "Teamarr"}])
    assert mapping["Teamarr"] == {98, 99}


def test_rows_missing_name_or_id_are_skipped(gs):
    mapping = gs.build_name_to_ids(
        [{"id": 1}, {"name": "Orphan"}, {"id": 2, "name": "Good"}])
    assert mapping == {"Good": {2}}


# --- include only --------------------------------------------------------

def test_blank_include_selects_every_group_and_ungrouped(gs, names):
    scope = resolve(gs, names)
    assert scope.group_ids == frozenset({10, 20, 30, 40})
    assert scope.include_ungrouped is True


def test_include_is_exact_and_case_sensitive(gs, names):
    scope = resolve(gs, names, include="Sports")
    assert scope.group_ids == frozenset({10})
    assert scope.include_ungrouped is False
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, include="sports")


# --- the reporter's case -------------------------------------------------

def test_ignore_only_processes_everything_else(gs, names):
    scope = resolve(gs, names, ignore="Teamarr")
    assert scope.group_ids == frozenset({10, 20, 40})
    assert scope.ignored_names == ("Teamarr",)
    assert scope.include_ungrouped is True


def test_ignore_is_case_insensitive(gs, names):
    assert resolve(gs, names, ignore="teamarr").group_ids == frozenset({10, 20, 40})


def test_ignore_wildcard_catches_the_family(gs, names):
    scope = resolve(gs, names, ignore="Teamarr*")
    assert scope.group_ids == frozenset({10, 20})
    assert scope.ignored_names == ("Teamarr", "Teamarr Live")


def test_include_and_ignore_compose(gs, names):
    scope = resolve(gs, names, include="Sports, News", ignore="News")
    assert scope.group_ids == frozenset({10})
    assert scope.include_ungrouped is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_group_scope.py -v
```

Expected: collection error / `FileNotFoundError` — `group_scope.py` does not exist.

- [ ] **Step 3: Write `Channel-Maparr/group_scope.py`**

```python
"""Pure, Django-free channel-group scope resolution.

Lives outside plugin.py so the whole include/exclude behaviour table can be
unit-tested with zero mocks. plugin.py supplies the ORM rows and formats the
returns; every rule lives here.

The contract is section 4 of
docs/superpowers/specs/2026-07-26-ignore-groups-design.md.
"""
import re
from dataclasses import dataclass, field

from wildcard_match import expand_patterns

_SPLIT = re.compile(r'[,\n]+')


class GroupScopeError(Exception):
    """The configured scope cannot be honoured; the action must refuse to run.

    Fail-closed is right here because the scope is the operator's PRIMARY input,
    not a defence-in-depth backstop: "I could not resolve your exclusion" must
    never authorize touching the channels it was meant to protect.
    """


@dataclass(frozen=True)
class GroupScope:
    group_ids: frozenset
    include_ungrouped: bool
    ignored_names: tuple = ()
    out_of_scope_names: tuple = ()
    info: str = ""


def parse_tokens(raw):
    """Split a comma/newline separated setting into non-empty stripped tokens.

    Empty tokens are dropped BEFORE any emptiness test, so a stray comma reads
    as "no list" rather than "a list that matched nothing" - which, under
    fail-closed resolution, would hard-error every action.
    """
    if not raw:
        return []
    return [tok.strip() for tok in _SPLIT.split(raw) if tok.strip()]


def build_name_to_ids(rows):
    """Map group name -> SET of ids.

    A set, not a scalar: Dispatcharr permits two groups with the same name, and
    a scalar map silently drops one of them - leaving it unprotected by an
    exclusion that names it.
    """
    mapping = {}
    for row in rows:
        name, gid = row.get('name'), row.get('id')
        if name is None or gid is None:
            continue
        mapping.setdefault(name, set()).add(gid)
    return mapping


def resolve_group_scope(include_value, ignore_value, group_name_to_ids, *, include_label):
    """Resolve the include filter, then subtract the exclusion.

    Returns a GroupScope whose group_ids is always explicit. Raises
    GroupScopeError for every refusal case in the spec's section 4 table.
    """
    include_tokens = parse_tokens(include_value)
    ignore_tokens = parse_tokens(ignore_value)

    # --- include ---------------------------------------------------------
    if include_tokens:
        # Exact, case-sensitive: unchanged from the pre-existing behaviour.
        missing = [t for t in include_tokens if t not in group_name_to_ids]
        target = set()
        for tok in include_tokens:
            target |= group_name_to_ids.get(tok, set())
        if not target:
            raise GroupScopeError(
                f"None of the groups named in '{include_label}' could be found: "
                f"{', '.join(missing)}"
            )
        include_ungrouped = False
    else:
        target = set()
        for ids in group_name_to_ids.values():
            target |= ids
        include_ungrouped = True

    # --- exclude ---------------------------------------------------------
    ignored_names, out_of_scope = (), ()
    if ignore_tokens:
        if not group_name_to_ids:
            raise GroupScopeError(
                "'Channel Groups to Ignore' is set, but Dispatcharr has no "
                "channel groups to match it against."
            )
        matched, unmatched = expand_patterns(
            ignore_tokens, list(group_name_to_ids), ci_plain=True)
        if unmatched:
            raise GroupScopeError(
                f"These entries in 'Channel Groups to Ignore' match no channel "
                f"group: {', '.join(unmatched)}. Check the spelling, or use a "
                f"wildcard (a group name containing a comma cannot be written "
                f"literally, because the setting splits on commas)."
            )
        ignored_ids = set()
        for name in matched:
            ignored_ids |= group_name_to_ids[name]
        ignored_names = tuple(matched)
        # A real group that the include filter had already excluded: a no-op,
        # NOT a typo. Reported, never fatal.
        out_of_scope = tuple(
            n for n in matched if not (group_name_to_ids[n] & target))
        target -= ignored_ids
        if not target:
            raise GroupScopeError(
                f"'Channel Groups to Ignore' excluded every group that "
                f"'{include_label}' selected, so there is nothing left to "
                f"process. Narrow the exclusion or widen the selection."
            )

    return GroupScope(
        group_ids=frozenset(target),
        include_ungrouped=include_ungrouped,
        ignored_names=ignored_names,
        out_of_scope_names=out_of_scope,
        info=_describe(include_tokens, ignored_names, include_label),
    )


def _describe(include_tokens, ignored_names, include_label):
    parts = []
    if include_tokens:
        parts.append(f"{include_label}: {', '.join(include_tokens)}")
    else:
        parts.append(f"{include_label}: all groups")
    if ignored_names:
        parts.append(f"ignoring {len(ignored_names)} group(s): "
                     f"{', '.join(ignored_names)}")
    return "; ".join(parts)
```

- [ ] **Step 4: Run to verify all pass**

```bash
python -m pytest tests/test_group_scope.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add Channel-Maparr/group_scope.py tests/test_group_scope.py
git commit -m "feat(scope): pure group_scope resolver (parsing + include/exclude)

Django-free so the whole behaviour table is testable with zero mocks. Two
details that are easy to get wrong and are pinned by tests: empty tokens are
dropped BEFORE any emptiness test (else a stray comma hard-errors every action
under fail-closed), and the name map is name -> SET of ids (Dispatcharr allows
duplicate group names, and a scalar map leaves one of them unprotected)."
```

---

### Task 8: `group_scope.py` — the refusal table and the documented limits

**Files:**
- Modify: `tests/test_group_scope.py`
- Modify: `Channel-Maparr/group_scope.py` (only if a test exposes a gap)

- [ ] **Step 1: Write the tests**

Append to `tests/test_group_scope.py`:

```python
# --- the refusal table (spec section 4) ----------------------------------

def test_ignore_token_matching_nothing_refuses(gs, names):
    """A typo must not degrade to 'process everything' - that is exactly the
    damage the setting exists to prevent, and it would be silent."""
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, ignore="Teamar")
    assert "Teamar" in str(exc.value)


def test_partial_match_still_refuses(gs, names):
    """One good token does not license a bad one: a group renamed out from under
    a stale entry would leave it silently unprotected."""
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, ignore="Teamarr, Teamar")
    assert "Teamar" in str(exc.value)
    assert "Teamarr," not in str(exc.value)      # only the unmatched one is named


def test_ignoring_a_group_outside_the_include_scope_is_a_no_op(gs, names):
    """Teamarr is real but not selected. Not a typo, so not an error."""
    scope = resolve(gs, names, include="Sports", ignore="Teamarr")
    assert scope.group_ids == frozenset({10})
    assert scope.out_of_scope_names == ("Teamarr",)


def test_exclusion_emptying_the_target_refuses(gs, names):
    with pytest.raises(gs.GroupScopeError) as exc:
        resolve(gs, names, include="Sports", ignore="Sport*")
    msg = str(exc.value)
    assert "excluded every group" in msg
    # Must be distinguishable from "your include list matched nothing" - they
    # call for different user actions.
    assert "could not be found" not in msg


def test_bare_star_refuses_via_the_empty_set_rule(gs, names):
    """Not special-cased; caught by the empty-target rule, with or without an
    include filter."""
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, ignore="*")
    with pytest.raises(gs.GroupScopeError):
        resolve(gs, names, include="Sports, News", ignore="*")


def test_ignore_with_no_groups_at_all_has_its_own_message(gs):
    with pytest.raises(gs.GroupScopeError) as exc:
        gs.resolve_group_scope("", "Teamarr", {}, include_label="Channel Groups to Process")
    assert "no channel groups" in str(exc.value)


def test_whitespace_only_ignore_is_treated_as_blank(gs, names):
    """The stray-comma outage case, end to end."""
    scope = resolve(gs, names, ignore=" , ")
    assert scope.group_ids == frozenset({10, 20, 30, 40})
    assert scope.ignored_names == ()


def test_null_settings_values_do_not_raise(gs, names):
    scope = gs.resolve_group_scope(
        None, None, names, include_label="Channel Groups to Process")
    assert scope.group_ids == frozenset({10, 20, 30, 40})


def test_duplicate_group_names_are_both_excluded(gs):
    names = gs.build_name_to_ids(
        [{"id": 98, "name": "Teamarr"}, {"id": 99, "name": "Teamarr"},
         {"id": 10, "name": "Sports"}])
    scope = gs.resolve_group_scope(
        "", "Teamarr", names, include_label="Channel Groups to Process")
    assert scope.group_ids == frozenset({10})       # BOTH 98 and 99 removed


# --- documented limits (pinned, deliberately not fixed) ------------------

def test_bracket_is_not_treated_as_a_glob(gs):
    """The glob probe is *? only, so [ ] stay literal. Pinned so a future
    'improvement' to the probe has to face this test."""
    names = gs.build_name_to_ids([{"id": 1, "name": "News [US]"}])
    scope = gs.resolve_group_scope(
        "", "News [US]", names, include_label="Channel Groups to Process")
    assert scope.ignored_names == ("News [US]",)


def test_lower_not_casefold_is_a_documented_limit(gs):
    """expand_patterns uses .lower(), not .casefold(), so Turkish dotted I and
    German sharp s do not fold. Changing it would break the byte-identical copy
    of EPG-Janitor's helper and its provenance pin, so it stays a known limit."""
    names = gs.build_name_to_ids([{"id": 1, "name": "ISTANBUL TV"}])
    with pytest.raises(gs.GroupScopeError):
        gs.resolve_group_scope(
            "", "İSTANBUL TV", names,      # dotted capital I
            include_label="Channel Groups to Process")


def test_accented_names_match_case_insensitively(gs):
    """The half that DOES work, so the limit above is understood as narrow."""
    names = gs.build_name_to_ids([{"id": 1, "name": "TÉLÉ QUÉBEC"}])
    scope = gs.resolve_group_scope(
        "", "télé québec", names,
        include_label="Channel Groups to Process")
    assert scope.ignored_names == ("TÉLÉ QUÉBEC",)
```

- [ ] **Step 2: Run**

```bash
python -m pytest tests/test_group_scope.py -v
```

Expected: all pass against the Task 7 implementation. If any fail, the implementation has a
gap — fix `group_scope.py`, not the test, unless the test's expectation contradicts spec §4.

- [ ] **Step 3: Commit**

```bash
git add tests/test_group_scope.py Channel-Maparr/group_scope.py
git commit -m "test(scope): pin the refusal table and the documented matching limits

Covers every row of spec section 4 plus the edges that bite: a stray comma
reading as blank, duplicate group names both being excluded, a bare * caught by
the empty-target rule rather than a special case, an ignored group outside the
include scope being a no-op rather than a typo, and the .lower()-not-.casefold()
limit pinned as a known narrow gap."
```

---

### Task 9: The Django glue — `_resolve_group_scope` and two named wrappers

**Files:**
- Modify: `Channel-Maparr/plugin.py` (imports + three new methods near `_get_all_groups`)
- Modify: `tests/test_ignore_groups_actions.py`

**Interfaces:**
- Consumes: `group_scope.resolve_group_scope`, `GroupScopeError`, `build_name_to_ids`.
- Produces, used by Tasks 10, 12, 13, 14:
  - `Plugin._resolve_group_scope(settings, logger, include_key) -> GroupScope` (raises `GroupScopeError`)
  - `Plugin._resolve_process_scope(settings, logger) -> GroupScope`
  - `Plugin._resolve_category_scope(settings, logger) -> GroupScope`
  - `Plugin._scope_error_return(exc) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ignore_groups_actions.py`:

```python
GROUPS_WITH_TEAMARR = [
    {"id": 10, "name": "Sports"},
    {"id": 20, "name": "News"},
    {"id": 30, "name": "Teamarr"},
]


def test_resolve_process_scope_subtracts_the_exclusion(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    scope = plugin_instance._resolve_process_scope(
        {"ignore_groups": "Teamarr"}, logger)
    assert scope.group_ids == frozenset({10, 20})
    assert scope.include_ungrouped is True


def test_resolve_category_scope_reads_the_category_include_field(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    scope = plugin_instance._resolve_category_scope(
        {"category_groups": "Sports, News", "ignore_groups": "News"}, logger)
    assert scope.group_ids == frozenset({10})


def test_unknown_include_key_is_rejected_loudly(plugin_instance, logger, fake_groups):
    """A stringly-typed key that degrades to settings.get(...) == '' would mean
    'all groups' - the same silent-widening family as bug-044."""
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(ValueError):
        plugin_instance._resolve_group_scope({}, logger, "catagory_groups")


def test_scope_error_return_is_visible(plugin_instance, plugin_module):
    exc = plugin_module.GroupScopeError("nope")
    result = plugin_instance._scope_error_return(exc)
    assert result["status"] == "error"
    assert result["error"] == "nope"
    assert "message" not in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_ignore_groups_actions.py -v
```

Expected: the four new tests FAIL with `AttributeError`.

- [ ] **Step 3: Add the import to `plugin.py`**

After `from .progress_status import (...)` (around line 21-24), add:

```python
from .group_scope import (
    GroupScope,
    GroupScopeError,
    build_name_to_ids,
    parse_tokens,
    resolve_group_scope,
)
```

Note `group_scope.py` does `from wildcard_match import expand_patterns` — a plain sibling
import, which resolves because Dispatcharr puts the plugin directory on `sys.path`, and in
tests because the `gs` fixture inserts it. Do **not** change it to a relative import; the
provenance-pinned `wildcard_match.py` must stay importable both ways.

- [ ] **Step 4: Add the three methods after `_get_all_groups` (`plugin.py:671`)**

```python
    _INCLUDE_KEYS = frozenset({"selected_groups", "category_groups"})
    _INCLUDE_LABELS = {
        "selected_groups": "Channel Groups to Process",
        "category_groups": "Category Organization Groups",
    }

    def _resolve_group_scope(self, settings, logger, include_key):
        """Resolve the channel-group scope for an action.

        Raises GroupScopeError when the configured scope cannot be honoured; the
        caller turns that into a visible error via _scope_error_return.
        """
        if include_key not in self._INCLUDE_KEYS:
            raise ValueError(f"unknown include_key {include_key!r}")

        name_to_ids = build_name_to_ids(self._get_all_groups(logger))
        scope = resolve_group_scope(
            settings.get(include_key),
            settings.get("ignore_groups"),
            name_to_ids,
            include_label=self._INCLUDE_LABELS[include_key],
        )
        logger.info(f"{PLUGIN_LOG_PREFIX} Scope: {scope.info}")
        for name in scope.out_of_scope_names:
            logger.info(
                f"{PLUGIN_LOG_PREFIX} Ignored group '{name}' was already outside "
                f"the selected scope - no effect."
            )
        return scope

    def _resolve_process_scope(self, settings, logger):
        """Scope for the scan / rename / logo actions."""
        return self._resolve_group_scope(settings, logger, "selected_groups")

    def _resolve_category_scope(self, settings, logger):
        """Scope for the Organize-by-Category actions."""
        return self._resolve_group_scope(settings, logger, "category_groups")

    @staticmethod
    def _scope_error_return(exc):
        """`error`, not `message` - `status` renders nowhere on the plugin card."""
        return {"status": "error", "error": str(exc)}
```

- [ ] **Step 5: Run to verify they pass, then the suite**

```bash
python -m pytest tests/test_ignore_groups_actions.py -v
python -m pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add Channel-Maparr/plugin.py tests/test_ignore_groups_actions.py
git commit -m "feat(scope): Django glue for the group-scope resolver

Thin wrapper: fetch groups, delegate to the pure resolver, format the return.
include_key is validated against a frozenset because a mistyped key would
silently degrade to '' == 'all groups', the same silent-widening family as
bug-044. Two named wrappers keep the key out of the call sites."
```

---

### Task 10: Wire the five fetch sites + the AST wiring guard

**Files:**
- Modify: `Channel-Maparr/plugin.py` (five sites: ~956, ~1383, ~1443, ~1557, ~1757)
- Create: `tests/test_group_scope_wiring.py`

- [ ] **Step 1: Write the wiring guard first**

Create `tests/test_group_scope_wiring.py`:

```python
"""AST guard: every channel fetch takes its scope from the resolver.

Modelled on metricsarr/tests/test_no_mutations.py. The synthetic self-tests at
the bottom are mandatory - an AST guard with no positive fixture is inert and
returns exit 0 for months while proving nothing.
"""
import ast
from pathlib import Path

import pytest

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Channel-Maparr" / "plugin.py"

EXPECTED_FETCH_SITES = 5

# Methods allowed to touch Channel.objects directly. Everything else must go
# through _get_all_channels so the scope cannot be bypassed.
CHANNEL_OBJECTS_ALLOWLIST = {
    "_get_all_channels",
    "_bulk_update_channels",
    "_get_next_channel_number",
    "_import_matched_streams",
    "_detect_duplicate_channels",
    "validate_settings_action",
}


def _methods(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _calls_named(node, attr):
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == attr):
            yield sub


def _resolver_bound_names(fn):
    """Names bound from any self._resolve_*_scope(...) call inside fn."""
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr in {
                    "_resolve_group_scope", "_resolve_process_scope",
                    "_resolve_category_scope"}):
            for target in node.targets:
                elts = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
                for el in elts:
                    if isinstance(el, ast.Name):
                        out.add(el.id)
    return out


def check_wiring(source, expected_sites):
    tree = ast.parse(source)
    sites = [(fn, call) for fn in _methods(tree)
             for call in _calls_named(fn, "_get_all_channels")]
    assert len(sites) == expected_sites, (
        f"expected {expected_sites} _get_all_channels call sites, found "
        f"{len(sites)} - a new fetch site must be wired to the resolver too")

    for fn, call in sites:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        assert "group_ids" in kwargs, f"{fn.name}: group_ids not passed by keyword"
        value = kwargs["group_ids"]
        assert not (isinstance(value, ast.Constant) and value.value is None), \
            f"{fn.name}: passes a literal None (means EVERY channel)"
        assert not isinstance(value, ast.IfExp), (
            f"{fn.name}: a conditional group_ids can still degrade to None - "
            f"this is the shape that shipped bug-044")
        assert isinstance(value, ast.Attribute) or isinstance(value, ast.Name), \
            f"{fn.name}: group_ids should come from the resolved scope"
        bound = _resolver_bound_names(fn)
        root = value.value.id if isinstance(value, ast.Attribute) and isinstance(
            value.value, ast.Name) else getattr(value, "id", None)
        assert root in bound, (
            f"{fn.name}: group_ids does not trace to a _resolve_*_scope() result "
            f"(bound names: {sorted(bound)})")


def check_channel_objects_confined(source):
    tree = ast.parse(source)
    offenders = []
    for fn in _methods(tree):
        if fn.name in CHANNEL_OBJECTS_ALLOWLIST:
            continue
        for sub in ast.walk(fn):
            if (isinstance(sub, ast.Attribute) and sub.attr == "objects"
                    and isinstance(sub.value, ast.Name) and sub.value.id == "Channel"):
                offenders.append((fn.name, sub.lineno))
    assert not offenders, (
        f"Channel.objects used outside the allowlist (bypasses the group "
        f"scope): {offenders}")


def test_all_fetch_sites_are_resolver_wired():
    check_wiring(PLUGIN_PY.read_text(encoding="utf-8"), EXPECTED_FETCH_SITES)


def test_channel_objects_stays_inside_the_helpers():
    check_channel_objects_confined(PLUGIN_PY.read_text(encoding="utf-8"))


# --- the detector must BITE ---------------------------------------------

BAD_LITERAL_NONE = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(l, group_ids=None)
'''

BAD_IFEXP = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(l, group_ids=scope.group_ids if s else None)
'''

BAD_UNWIRED = '''
class P:
    def a(self, s, l):
        ids = {g["id"] for g in self._get_all_groups(l)}
        return self._get_all_channels(l, group_ids=ids)
'''

GOOD = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(
            l, group_ids=scope.group_ids, include_ungrouped=scope.include_ungrouped)
'''


@pytest.mark.parametrize("src", [BAD_LITERAL_NONE, BAD_IFEXP, BAD_UNWIRED])
def test_detector_rejects_unwired_shapes(src):
    with pytest.raises(AssertionError):
        check_wiring(src, expected_sites=1)


def test_detector_accepts_the_wired_shape():
    check_wiring(GOOD, expected_sites=1)


BAD_DIRECT_OBJECTS = '''
class P:
    def some_action(self, s, l):
        return list(Channel.objects.all().values("id"))
'''


def test_detector_flags_direct_channel_objects():
    with pytest.raises(AssertionError):
        check_channel_objects_confined(BAD_DIRECT_OBJECTS)
```

- [ ] **Step 2: Run to see it fail on the real file**

```bash
python -m pytest tests/test_group_scope_wiring.py -v
```

Expected: the four self-tests PASS. `test_all_fetch_sites_are_resolver_wired` FAILS (the
sites still use inline sets and the `IfExp` at `:956`). `test_channel_objects_stays_inside_the_helpers`
should pass — if it names a method, add it to the allowlist **only** if it is genuinely
read-only, and say so in the commit.

- [ ] **Step 3: Rewrite site 1 — `load_and_process_channels_action`**

Replace `plugin.py:939-959` (from `# Filter by selected groups if specified` through the
`logger.info(... Filtered to ...)` line) with:

```python
            # Resolve the group scope (include filter minus ignore_groups)
            try:
                scope = self._resolve_process_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )

            channels_to_process = all_channels
            logger.info(
                f"{PLUGIN_LOG_PREFIX} Filtered to {len(channels_to_process)} "
                f"channels ({scope.info})"
            )
```

`group_name_to_id` / `group_id_to_name` (`:934-937`) stay — `group_id_to_name` is still used
at `:964` for `_group_name`. `valid_names` becomes unused here; delete any later reference or
replace it with `scope.group_ids`.

- [ ] **Step 4: Rewrite sites 2 and 3 — the two logo actions**

In `apply_logos_action`, replace the whole `selected_groups_str` block written in Task 3 plus
its fetch with:

```python
            try:
                scope = self._resolve_process_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )
```

Apply the identical replacement in `apply_tv_logos_action`.

- [ ] **Step 5: Rewrite sites 4 and 5 — the two Organize actions**

In `category_groups_dry_run_action`, replace `:1544-1557` (the `category_groups_str` block
and the fetch) with:

```python
            try:
                scope = self._resolve_category_scope(settings, logger)
            except GroupScopeError as exc:
                return self._scope_error_return(exc)

            all_channels = self._get_all_channels(
                logger,
                group_ids=scope.group_ids,
                include_ungrouped=scope.include_ungrouped,
            )
```

Apply the identical replacement in `organize_by_category_action` (`:1744-1757`). Both still
need `group_name_to_id` and `group_id_to_name` from `:1540-1542` — keep them.

- [ ] **Step 6: Run the wiring guard, then the suite**

```bash
python -m pytest tests/test_group_scope_wiring.py -v
python -m pytest -q
```

Expected: all wiring assertions pass; suite exit 0.

- [ ] **Step 7: Commit**

```bash
git add Channel-Maparr/plugin.py tests/test_group_scope_wiring.py
git commit -m "feat(scope): wire all five channel-fetch sites to the resolver

Replaces five duplicated inline parse-and-resolve blocks, including the
\`ids if selected_groups_str else None\` shape that made bug-044 reachable.

The AST guard pins the site count, forbids a literal None and a conditional
group_ids, requires the value to trace to a _resolve_*_scope() result, and
confines Channel.objects to an allowlist of helpers so the scope cannot be
bypassed. It carries synthetic self-tests because a guard with no positive
fixture is inert."
```

---

### Task 11: The setting itself + contract tests

**Files:**
- Modify: `Channel-Maparr/plugin.py` (the `fields` property, after `selected_groups`)
- Modify: `Channel-Maparr/plugin.json` (after `selected_groups`)
- Modify: `tests/test_plugin_contract.py`

- [ ] **Step 1: Write the failing contract tests**

Append to `tests/test_plugin_contract.py`:

```python
def test_ignore_groups_field_is_declared_in_both_places(manifest, plugin_source):
    ids = {f["id"] for f in manifest["fields"]}
    assert "ignore_groups" in ids, "field missing from plugin.json"
    assert '"id": "ignore_groups"' in plugin_source, "field missing from Plugin.fields"


def test_every_field_id_in_source_is_also_in_the_manifest(manifest, plugin_source):
    """The existing parity test only checks manifest -> source. A field added to
    the Plugin.fields property alone renders in the UI but is absent from the
    manifest, and that direction passed silently."""
    import re
    source_ids = set(re.findall(r'"id":\s*"([a-z0-9_]+)"', plugin_source))
    known = ({f["id"] for f in manifest["fields"]}
             | {a["id"] for a in manifest["actions"]})
    assert not (source_ids - known), (
        f"ids declared in plugin.py but not in plugin.json: {sorted(source_ids - known)}")


def test_plugin_py_is_bmp_only(plugin_source):
    """Astral-plane characters make Dispatcharr's loader silently drop an action.
    plugin.json is already checked; the class side was not, and the `fields`
    property cannot be executed in tests."""
    offenders = sorted({c for c in plugin_source if ord(c) > 0xFFFF})
    assert not offenders, [hex(ord(c)) for c in offenders]


def test_ignore_groups_is_recorded_in_csv_headers(plugin_source):
    assert "'ignore_groups': 'Channel Groups to Ignore'" in plugin_source
```

Check the fixture names first — if `test_plugin_contract.py` calls them something other than
`manifest` / `plugin_source`, use its names.

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_plugin_contract.py -v
```

- [ ] **Step 3: Add the field to `Plugin.fields`**

In `plugin.py`, immediately after the `selected_groups` field dict (ends ~line 296), insert:

```python
            {
                "id": "ignore_groups",
                "label": "Channel Groups to Ignore",
                "type": "string",
                "default": "",
                "placeholder": "Teamarr, PPV*",
                "help_text": (
                    "Comma-separated. Channels in these groups are excluded from "
                    "renaming, tagging, logos and Organize by Category, regardless "
                    "of 'Channel Groups to Process' or 'Category Organization "
                    "Groups'. Supports * and ? wildcards; matching is "
                    "case-insensitive. Does not apply to Import M3U Streams, which "
                    "refuses to run if its target group is ignored."
                ),
            },
```

Then extend the two include fields' help text with a pointer. For `selected_groups`
(`:295`) append: `" Use 'Channel Groups to Ignore' to exclude instead."` Do the same on
`category_groups` (`:299`).

- [ ] **Step 4: Add the same field to `plugin.json`**

After the `selected_groups` entry (`plugin.json:38-43`), insert the matching object with
identical `id`, `label`, `type`, `default`, `placeholder` and `help_text` (single-line
string). BMP-only. Keep the two include fields' `help_text` in sync with the class.

- [ ] **Step 5: Add the CSV header label**

In `_generate_csv_settings_header`'s `field_labels` (`:632-646`), after the
`'selected_groups'` line:

```python
            'ignore_groups': 'Channel Groups to Ignore',
```

- [ ] **Step 6: Run the contract tests and the suite**

```bash
python -m pytest tests/test_plugin_contract.py -v
python -m pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add Channel-Maparr/plugin.py Channel-Maparr/plugin.json tests/test_plugin_contract.py
git commit -m "feat(scope): add the Channel Groups to Ignore setting

Declared in both plugin.json and the Plugin class (the class is runtime truth).
The help text names both include fields, because two of the five enforcement
sites are scoped by Category Organization Groups rather than Channel Groups to
Process, and states the M3U Import carve-out rather than promising 'any action'.

Also closes two contract gaps: field parity was manifest -> source only, so a
field added to the class alone passed silently, and nothing checked plugin.py
for astral-plane characters (which make the loader drop an action)."
```

---

### Task 12: The two actions that mutate from a persisted file

**Context:** `rename_channels_action` and `rename_unknown_channels_action` never fetch
channels — they replay `self.results_file` and bulk-update from it. Without this task the
reporter's exact scenario still renames his channels: Load & Process today, set
`ignore_groups=Teamarr` tomorrow, click Rename. Each change row carries `'channel_group'` as
a **name** (`:1098`, `:1117`), so rows are matched against the ignore patterns directly — no
id lookup, and it still works if the group was since renamed or deleted.

**Files:**
- Modify: `Channel-Maparr/group_scope.py` (add `split_rows_by_ignore`)
- Modify: `Channel-Maparr/plugin.py:1237-1329` (both actions)
- Modify: `tests/test_group_scope.py`, `tests/test_ignore_groups_actions.py`

**Interfaces:**
- Produces: `group_scope.split_rows_by_ignore(rows, ignore_value, *, group_key='channel_group') -> (kept, dropped)`

- [ ] **Step 1: Write the failing pure test**

Append to `tests/test_group_scope.py`:

```python
# --- results-file row filtering ------------------------------------------

ROWS = [
    {"channel_id": 1, "channel_group": "Sports"},
    {"channel_id": 2, "channel_group": "Teamarr"},
    {"channel_id": 3, "channel_group": "Teamarr Live"},
    {"channel_id": 4, "channel_group": "No Group"},
]


def test_split_rows_by_ignore_drops_matching_rows(gs):
    kept, dropped = gs.split_rows_by_ignore(ROWS, "Teamarr*")
    assert [r["channel_id"] for r in kept] == [1, 4]
    assert [r["channel_id"] for r in dropped] == [2, 3]


def test_split_rows_by_ignore_is_case_insensitive(gs):
    kept, _ = gs.split_rows_by_ignore(ROWS, "teamarr")
    assert [r["channel_id"] for r in kept] == [1, 3, 4]


def test_split_rows_with_blank_ignore_keeps_everything(gs):
    kept, dropped = gs.split_rows_by_ignore(ROWS, " , ")
    assert len(kept) == 4 and dropped == []


def test_split_rows_matches_names_not_ids(gs):
    """Matching by NAME means a stale file still filters after the group was
    deleted - an id lookup would silently keep the row."""
    rows = [{"channel_id": 9, "channel_group": "Teamarr"}]
    kept, dropped = gs.split_rows_by_ignore(rows, "Teamarr")
    assert kept == [] and len(dropped) == 1


def test_split_rows_tolerates_a_missing_group_key(gs):
    kept, dropped = gs.split_rows_by_ignore([{"channel_id": 9}], "Teamarr")
    assert len(kept) == 1 and dropped == []
```

- [ ] **Step 2: Implement `split_rows_by_ignore` in `group_scope.py`**

```python
def split_rows_by_ignore(rows, ignore_value, *, group_key='channel_group'):
    """Partition persisted result rows into (kept, dropped) by group NAME.

    The rename/tag actions replay a results file and never fetch channels, so
    the exclusion has to be applied here too. Matching on the stored NAME rather
    than an id means a stale file is still filtered after the group has been
    renamed or deleted.
    """
    tokens = parse_tokens(ignore_value)
    if not tokens:
        return list(rows), []

    present = sorted({r.get(group_key) for r in rows if r.get(group_key)})
    matched, _ = expand_patterns(tokens, present, ci_plain=True)
    ignored = set(matched)

    kept, dropped = [], []
    for row in rows:
        (dropped if row.get(group_key) in ignored else kept).append(row)
    return kept, dropped
```

Note this deliberately ignores unmatched tokens: refusing here would block a rename because
of a group absent from *this file*, while `_resolve_process_scope` already refuses a genuine
typo at scan time.

- [ ] **Step 3: Run the pure tests**

```bash
python -m pytest tests/test_group_scope.py -v
```

- [ ] **Step 4: Write the failing action tests**

Append to `tests/test_ignore_groups_actions.py`:

```python
import json


def _results_file(tmp_path, changes):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"changes": changes}), encoding="utf-8")
    return str(path)


def test_rename_skips_rows_in_an_ignored_group(
        plugin_instance, logger, tmp_path, monkeypatch):
    """The stale-file case: the results file predates the ignore setting."""
    changes = [
        {"channel_id": 1, "current_name": "a", "new_name": "A",
         "status": "Renamed", "channel_group": "Sports"},
        {"channel_id": 2, "current_name": "b", "new_name": "B",
         "status": "Renamed", "channel_group": "Teamarr"},
    ]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    captured = []
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)

    result = plugin_instance.rename_channels_action(
        {"dry_run_mode": False, "ignore_groups": "Teamarr"}, logger)

    assert [u["id"] for u in captured] == [1]
    assert result["status"] == "success"
    assert "1" in result["message"]


def test_tag_unknown_skips_rows_in_an_ignored_group(
        plugin_instance, logger, tmp_path, monkeypatch):
    changes = [
        {"channel_id": 1, "current_name": "a", "status": "Skipped",
         "channel_group": "Sports"},
        {"channel_id": 2, "current_name": "b", "status": "Skipped",
         "channel_group": "Teamarr"},
    ]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    captured = []
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda updates, fields, lg: captured.extend(updates))
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)

    plugin_instance.rename_unknown_channels_action(
        {"unknown_suffix": " [Unk]", "ignore_groups": "Teamarr"}, logger)

    assert [u["id"] for u in captured] == [1]


def test_rename_reports_when_everything_was_ignored(
        plugin_instance, logger, tmp_path, monkeypatch):
    changes = [{"channel_id": 2, "current_name": "b", "new_name": "B",
                "status": "Renamed", "channel_group": "Teamarr"}]
    monkeypatch.setattr(plugin_instance, "results_file",
                        _results_file(tmp_path, changes))
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda *a, **k: pytest.fail("must not write"))

    result = plugin_instance.rename_channels_action(
        {"dry_run_mode": False, "ignore_groups": "Teamarr"}, logger)
    assert result["status"] == "success"
    assert "ignored" in result["message"].lower()
```

- [ ] **Step 5: Implement in `rename_channels_action`**

Replace `plugin.py:1255-1259` with:

```python
            all_changes = data.get('changes', [])
            channels_to_rename = [c for c in all_changes if c.get('status') == 'Renamed']

            # These actions replay a persisted file and never fetch channels, so
            # the exclusion has to be applied here too - the file may predate the
            # current ignore_groups value.
            channels_to_rename, ignored_rows = split_rows_by_ignore(
                channels_to_rename, settings.get("ignore_groups"))
            if ignored_rows:
                logger.info(
                    f"{PLUGIN_LOG_PREFIX} Skipped {len(ignored_rows)} channel(s) "
                    f"in ignored groups."
                )

            if not channels_to_rename:
                if ignored_rows:
                    return {"status": "success", "message":
                            f"No channels renamed - all {len(ignored_rows)} "
                            f"pending change(s) are in ignored groups."}
                return {"status": "success", "message": "No channels need to be renamed."}
```

Then in the success message block (`:1268`), after the first entry append:

```python
            if ignored_rows:
                message_parts.append(
                    f"Skipped {len(ignored_rows)} channel(s) in ignored groups.")
```

- [ ] **Step 6: Implement the same in `rename_unknown_channels_action`**

Replace `:1303-1307` with the analogous block, filtering `skipped_channels` and using the
wording "No unknown channels to rename." for the no-rows case; add the same
`ignored_rows` line to its message block.

- [ ] **Step 7: Run and commit**

```bash
python -m pytest tests/test_ignore_groups_actions.py tests/test_group_scope.py -v
python -m pytest -q
git add Channel-Maparr/group_scope.py Channel-Maparr/plugin.py tests/test_group_scope.py tests/test_ignore_groups_actions.py
git commit -m "fix(scope): apply the exclusion to the file-driven rename and tag actions

Rename Channels and Tag Unknown Channels replay a persisted results file and
never fetch channels, so wiring the five fetch sites did not protect them: run
Load & Process, set ignore_groups later, click Rename, and the excluded
channels were renamed anyway behind a green toast.

Rows are matched on the stored group NAME, not an id, so a stale file is still
filtered after the group has been renamed or deleted."
```

---

### Task 13: The two write directions that point into an ignored group

**Context:** everything so far filters channels *out of* a scan. Organize-by-Category builds
`groups_needed` from channel-database category names and creates/reuses those groups, then
moves channels in — so `ignore_groups=Sports*` plus a database category `Sports` would create
or adopt an ignored group and fill it. And `_ensure_category_groups_exist` can create channels
into an ignored group via `m3u_custom_group_name` or a matching category.

**Files:**
- Modify: `Channel-Maparr/group_scope.py` (add `is_ignored_name`)
- Modify: `Channel-Maparr/plugin.py` (`organize_by_category_action` ~1850-1890;
  `_ensure_category_groups_exist` :2240)
- Modify: `tests/test_group_scope.py`, `tests/test_ignore_groups_actions.py`

**Interfaces:**
- Produces: `group_scope.is_ignored_name(name, ignore_value) -> bool`

- [ ] **Step 1: Write the failing pure test**

Append to `tests/test_group_scope.py`:

```python
def test_is_ignored_name(gs):
    assert gs.is_ignored_name("Teamarr", "Teamarr") is True
    assert gs.is_ignored_name("teamarr", "Teamarr") is True
    assert gs.is_ignored_name("Teamarr Live", "Teamarr*") is True
    assert gs.is_ignored_name("Sports", "Teamarr*") is False
    assert gs.is_ignored_name("Sports", " , ") is False
    assert gs.is_ignored_name("", "Teamarr") is False
```

- [ ] **Step 2: Implement in `group_scope.py`**

```python
def is_ignored_name(name, ignore_value):
    """True if a group name the plugin is about to CREATE or write into is ignored.

    The scope filters channels out of a scan; this is the other direction -
    nothing should create or adopt a group the operator declared untouchable.
    """
    if not name:
        return False
    tokens = parse_tokens(ignore_value)
    if not tokens:
        return False
    matched, _ = expand_patterns(tokens, [name], ci_plain=True)
    return bool(matched)
```

- [ ] **Step 3: Write the failing action tests**

Append to `tests/test_ignore_groups_actions.py`:

```python
def test_import_refuses_a_custom_group_that_is_ignored(
        plugin_instance, logger, fake_groups):
    """Import is not group-scoped, but it must not write INTO an ignored group."""
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(Exception) as exc:
        plugin_instance._ensure_category_groups_exist(
            ["Sports"],
            {"m3u_custom_group_name": "Teamarr", "ignore_groups": "Teamarr"},
            logger)
    assert "Teamarr" in str(exc.value)


def test_import_refuses_a_category_group_that_is_ignored(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    with pytest.raises(Exception) as exc:
        plugin_instance._ensure_category_groups_exist(
            ["Teamarr"], {"ignore_groups": "Teamarr*"}, logger)
    assert "Teamarr" in str(exc.value)


def test_import_proceeds_when_nothing_is_ignored(
        plugin_instance, logger, fake_groups, monkeypatch):
    fake_groups(GROUPS_WITH_TEAMARR)
    monkeypatch.setattr(plugin_instance, "_get_or_create_group",
                        lambda name, lg: type("G", (), {"id": 77})())
    mapping = plugin_instance._ensure_category_groups_exist(
        ["Sports"], {"ignore_groups": "Teamarr"}, logger)
    assert mapping == {"Sports": 10}
```

- [ ] **Step 4: Guard `_ensure_category_groups_exist`**

At the top of the method (after `custom_group_name` is read at `:2251`), insert:

```python
        ignore_value = settings.get("ignore_groups")

        # The exclusion also forbids writing INTO a group. Refuse rather than
        # create or adopt a group the operator declared untouchable.
        blocked = [
            name for name in ([custom_group_name] if custom_group_name else list(categories))
            if is_ignored_name(name, ignore_value)
        ]
        if blocked:
            raise GroupScopeError(
                f"Import would create or write into group(s) listed in 'Channel "
                f"Groups to Ignore': {', '.join(sorted(set(blocked)))}. Change the "
                f"import target or remove them from the ignore list."
            )
```

Also change `:2251` to `custom_group_name = (settings.get("m3u_custom_group_name") or "").strip()`.

Then in `import_m3u_streams_action` and `import_m3u_streams_dry_run_action`, catch it so the
user sees a red error rather than a traceback — find their `except Exception` handlers and add
ahead of each:

```python
        except GroupScopeError as exc:
            return self._scope_error_return(exc)
```

Because the real import runs in a background thread (`_try_start_thread`) whose result the
card never shows, **also** call `_ensure_category_groups_exist`'s guard before backgrounding
— or, simpler, validate the destination in the action body before the thread starts:

```python
            custom_group = (settings.get("m3u_custom_group_name") or "").strip()
            if is_ignored_name(custom_group, settings.get("ignore_groups")):
                return self._scope_error_return(GroupScopeError(
                    f"'Imported Channel Group Name' is '{custom_group}', which is "
                    f"listed in 'Channel Groups to Ignore'."))
```

- [ ] **Step 5: Write the failing Organize test**

Append to `tests/test_ignore_groups_actions.py`:

```python
def test_organize_never_creates_or_fills_an_ignored_group(
        plugin_instance, logger, fake_groups, fake_channel, monkeypatch):
    """ignore_groups must block the write direction too: a channel-database
    category whose name is ignored must not be created or filled."""
    fake_groups([{"id": 10, "name": "Sports"}, {"id": 30, "name": "Teamarr"}])
    created = []
    monkeypatch.setattr(plugin_instance, "_get_or_create_group",
                        lambda name, lg: created.append(name) or type("G", (), {"id": 99})())
    monkeypatch.setattr(plugin_instance, "_bulk_update_channels",
                        lambda *a, **k: None)
    monkeypatch.setattr(plugin_instance, "_trigger_frontend_refresh",
                        lambda *a, **k: None)
    # Force every channel to map to the ignored category name.
    monkeypatch.setattr(plugin_instance, "_build_category_mapping",
                        lambda *a, **k: {"A": "Teamarr", "B": "Teamarr"})

    plugin_instance.organize_by_category_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr", "dry_run_mode": False},
        logger)

    assert "Teamarr" not in created
```

Check the real helper name that produces the channel→category mapping in
`organize_by_category_action` and monkeypatch **that** — if the mapping is built inline rather
than in a helper, instead assert on `created` after seeding a country database category, or
extract the mapping into a helper as part of this task.

- [ ] **Step 6: Guard `organize_by_category_action`**

Where `groups_needed` is populated (`:1851-1852`) and where moves are appended, skip ignored
targets:

```python
                if category:
                    new_group_name = category

                    if is_ignored_name(new_group_name, settings.get("ignore_groups")):
                        ignored_targets.add(new_group_name)
                        continue

                    if new_group_name not in group_name_to_id:
                        groups_needed.add(new_group_name)
```

Initialise `ignored_targets = set()` alongside `groups_needed`, and after the move loop add
to the result message:

```python
            if ignored_targets:
                message_parts.append(
                    f"Skipped {len(ignored_targets)} ignored target group(s): "
                    f"{', '.join(sorted(ignored_targets))}.")
```

Apply the same guard in `category_groups_dry_run_action` so the preview matches the real run.

- [ ] **Step 7: Run and commit**

```bash
python -m pytest tests/test_ignore_groups_actions.py tests/test_group_scope.py -v
python -m pytest -q
git add Channel-Maparr/group_scope.py Channel-Maparr/plugin.py tests/test_group_scope.py tests/test_ignore_groups_actions.py
git commit -m "feat(scope): forbid writing INTO an ignored group

The scope filtered channels out of a scan but nothing stopped the plugin
creating or filling an ignored group from the other direction: Organize builds
its target groups from channel-database category names, and Import can target
one via m3u_custom_group_name or a matching category.

Organize now skips ignored targets and reports them; Import refuses with a
visible error, validated BEFORE backgrounding because the card never shows a
background thread's result."
```

---

### Task 14: Surfacing — Validate Settings, CSV header, progress file

**Note on a deliberate simplification:** the spec's §5.4 sketch said the CSV would report
`40 channel(s) in 1 group(s)`. The channel count is not known where the header is written, so
this reports **groups and names** — honest and cheap. Where channel counts *are* known (the
file-driven actions, Task 12) they are already reported.

**Files:**
- Modify: `Channel-Maparr/plugin.py` (`validate_settings_action`,
  `_generate_csv_settings_header`, `ProgressTracker` usage)
- Modify: `tests/test_ignore_groups_actions.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ignore_groups_actions.py`:

```python
def test_validate_settings_reports_a_resolved_exclusion(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "ignore_groups": "Teamarr"}, logger)
    body = result.get("message") or result.get("error")
    assert "Ignore" in body and "Teamarr" in body


def test_validate_settings_flags_an_ignore_typo_as_a_red_error(
        plugin_instance, logger, fake_groups):
    fake_groups(GROUPS_WITH_TEAMARR)
    result = plugin_instance.validate_settings_action(
        {"channel_databases": "US", "ignore_groups": "Teamar"}, logger)
    assert result["status"] == "error"
    assert result.get("error"), "an ignore typo rendered as a green toast"
    assert "Teamar" in result["error"]


def test_csv_header_records_the_exclusion(plugin_instance):
    header = plugin_instance._generate_csv_settings_header(
        {"ignore_groups": "Teamarr", "channel_databases": "US"})
    assert "Channel Groups to Ignore: Teamarr" in header
```

- [ ] **Step 2: Add the Validate Settings section**

In `validate_settings_action`, after the channel-database block (~`:2795`) and before the M3U
filters section, insert:

```python
            # 2b. Group scope (the first group validation in this action)
            try:
                scope = self._resolve_process_scope(settings, logger)
                if scope.ignored_names:
                    validation_results.append(
                        f"✅ Ignore: {len(scope.ignored_names)} group(s) — "
                        f"{', '.join(scope.ignored_names)}")
                for name in scope.out_of_scope_names:
                    validation_results.append(
                        f"⚠️ Ignore: '{name}' is outside the selected scope (no effect)")
                    warning_count += 1
            except GroupScopeError as exc:
                validation_results.append(f"❌ Ignore: {exc}")
                error_count += 1
```

`error_count` non-zero already routes the return through the `error` branch added in Task 4,
so the ❌ arrives as persistent red.

- [ ] **Step 3: Record the scope in the progress file**

In `load_and_process_channels_action`, where `ProgressTracker` is set up, pass `scope.info`
through so `plugin_status_action` can report it. Find the tracker's constructor or its first
`update` call and include the scope string in the persisted payload, then confirm
`build_status_message` in `progress_status.py` renders any extra key it is given (if it
whitelists keys, add the new one there).

- [ ] **Step 4: Run and commit**

```bash
python -m pytest tests/test_ignore_groups_actions.py -v
python -m pytest -q
git add Channel-Maparr/plugin.py Channel-Maparr/progress_status.py tests/test_ignore_groups_actions.py
git commit -m "feat(scope): surface the exclusion in Validate Settings, CSVs and status

Validate Settings gains the first group validation in the action - resolved
exclusions, out-of-scope no-ops as warnings, and a typo as a persistent red
error. The CSV header records the setting so a dry-run preview is
self-describing: an empty CSV alone cannot distinguish 'excluded 40 channels'
from 'the include filter was a typo'."
```

---

### Task 15: Ship Slice B

**Files:**
- Modify: `Channel-Maparr/plugin.py`, `plugin.json` (version, via script)
- Modify: `docs/CHANGELOG.md`, `README.md` / `Channel-Maparr.txt`, `.wolf/*`

- [ ] **Step 1: Run every gate**

```bash
python -m pytest -q ; echo "pytest exit: $?"
ruff check .
python scripts/check_version_sync.py
python -m pytest tests/test_core_parity.py tests/test_matcher_golden.py -q
```

The matcher core is untouched, but CI runs the parity and golden gates — they must pass.
Remember the working tree carries an **uncommitted** bug-126 re-vendor of `matching_core.py`;
leave it alone and never `git add -A`.

- [ ] **Step 2: Bump the version and build the zip**

```bash
python scripts/bump_version.py
python scripts/package_plugin.py
python scripts/validate_zip.py Channel-Maparr.zip
```

- [ ] **Step 3: Verify the two new modules are actually IN the zip**

A missing new module is an import-time death on install — the failure that already hit
`matching_core.py`:

```bash
python -c "import zipfile; ns=zipfile.ZipFile('Channel-Maparr.zip').namelist(); print([n for n in ns if 'wildcard' in n or 'group_scope' in n]); assert any('wildcard_match.py' in n for n in ns); assert any('group_scope.py' in n for n in ns)"
```

- [ ] **Step 4: Byte-check line endings**

`validate_zip.py` checks the bug-087 backslash issue, not line endings, and this repo is not
renormalized (bug-118):

```bash
python -c "import zipfile; z=zipfile.ZipFile('Channel-Maparr.zip'); bad=[n for n in z.namelist() if n.endswith('.py') and b'\r\n' in z.read(n)]; print('CRLF:', bad); assert not bad"
```

- [ ] **Step 5: Write the CHANGELOG entry**

```markdown
## v<new-version> (July 26, 2026)

**New setting: "Channel Groups to Ignore"** — process every group except the ones you name.
Requested by a user running Teamarr, which owns its own static channel group.

- Comma-separated, supports `*` and `?` wildcards, case-insensitive. Composes with
  "Channel Groups to Process" (include first, then subtract), so leaving that blank and
  ignoring one group gives "everything except that group".
- **Enforced everywhere it has to be**, not just where it was easy: the five channel-fetch
  sites, the two actions that replay a persisted results file without fetching channels
  (Rename Channels, Tag Unknown Channels — a stale file used to bypass any scope), and the
  two write directions that could otherwise create channels *into* an ignored group
  (Organize by Category's target groups, and Import M3U Streams' destination, which now
  refuses).
- **Fail-closed on a typo.** An entry matching no group refuses the run rather than
  degrading to "process everything" — silent damage to the channels you were protecting is
  the failure this setting exists to prevent. A stray comma reads as blank, not as an
  unmatched entry. Validate Settings reports the resolved exclusion.
- Does not apply to Import M3U Streams' stream matching or duplicate detection; those read
  channel names in ignored groups but never write to them.

**Divergence from EPG-Janitor, deliberately:** EPG-Janitor raises when both its group filters
are set, though its help text says the ignore list is applied after the include filter.
Channel-Maparr implements the help text. See the spec's follow-ups.
```

- [ ] **Step 6: Update the docs and OpenWolf files**

- `README.md` / `Channel-Maparr.txt`: add the setting to the settings table and note where it
  sits in the documented 8-step run order (it is a scope setting — it applies to all of them).
- `.wolf/anatomy.md`: entries for `wildcard_match.py`, `group_scope.py` and the four new test
  files.
- `.wolf/cerebrum.md`: the Key Learnings and the Decision Log entry recording the deliberate
  EPG-Janitor divergence (make EPG-Janitor compose later; do **not** align its text to the
  worse behaviour).
- `.wolf/buglog.json`: entries for anything found during implementation.
- `.wolf/memory.md`: one line per session.

- [ ] **Step 7: Commit and tag**

```bash
git add Channel-Maparr/ docs/ README.md Channel-Maparr.txt .wolf/ tests/
git commit -m "chore(release): v<new-version> - Channel Groups to Ignore"
git tag <new-version>          # BARE tag; the GitHub Release TITLE carries the v prefix
```

- [ ] **Step 8: Hand the deploy and acceptance to the user**

You cannot run any of this. Give the user spec §9 verbatim, with the backup first:

1. **Backup** (hard rule — steps 6-7 mutate):
   `apps.backups.tasks.create_backup_task.apply()`
2. `docker cp` `plugin.py`, `group_scope.py`, `wildcard_match.py`, then **`plugin.json` last**
   (hot-reload fires on its mtime) into `/data/plugins/channel-mapparr/`, then
   `docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/channel-mapparr`.
   Never exec plugin code as root against `/data` (E3 trap).
3. Read-only checks: §9 steps 1-5.
4. **Live-write checks: §9 steps 6-7** — record the Teamarr channels' `name` and
   `channel_group_id`, run Rename + Tag + Organize with `dry_run_mode` OFF, and re-query to
   confirm they are byte-identical. Assert on the rows, never on the toast. Step 7 is the
   stale-results-file case, which is the one that fails against any design that only patches
   the fetch sites.

Only open a Hub PR if the user asks — the workspace record is explicit that he has previously
said not to push Channel-Maparr to the Hub.

---

## Plan Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| §4 matching / combining / refusal table | 7, 8 |
| §4 empty tokens dropped | 7 (`parse_tokens`), 8 |
| §4.1 empty-set footgun (bug-044) | 1, 3 |
| §4.2 ungrouped channels | 2 |
| §4.3 file-driven actions | 12 |
| §4.4a Organize write direction | 13 |
| §4.4b Import destination guard | 13 |
| §4.5 `error` visibility | 4 |
| §5.1 vendored helper + provenance pin | 6 |
| §5.2 the setting, both places, null-safe, help text | 11 |
| §5.3 pure resolver + thin glue + `include_key` validation | 7, 9 |
| §5.4 Validate Settings / CSV / progress | 14 |
| §6.1 recording fake queryset | 1 |
| §6.2 resolver tests incl. unicode limits | 7, 8 |
| §6.3 file-driven action tests | 12 |
| §6.4 AST wiring guard + self-tests + `Channel.objects` ban | 10 |
| §6.5 contract gaps (reverse parity, BMP over plugin.py) | 11 |
| §8 gates, zip contents, CRLF, deploy | 5, 15 |
| §9 acceptance incl. live writes + backup | 15 |

**Deviations from the spec, deliberate:** (1) the CSV excluded-count reports groups and names
rather than a channel count, because the count is not available where the header is written
(Task 14 states this); (2) `include_ungrouped` is implemented as a Python post-filter rather
than `django.db.models.Q`, avoiding a new Django import that conftest does not mock (Task 2
states this); (3) Slice A unifies the include-error behaviour by adding the guard inline at the
two logo sites, which Task 10 then replaces with the resolver — a few lines of rework in
exchange for two independently revertible slices.

**Two tasks carry a verify-before-you-write instruction** rather than final code, because the
target could not be pinned from the spec: Task 14 step 3 (whether `build_status_message`
whitelists keys) and Task 13 step 5 (the real name of the channel→category mapping helper in
`organize_by_category_action`). Both say explicitly what to check and what to do in either
case; neither is a placeholder for a decision.

**Type consistency:** `GroupScope` fields (`group_ids`, `include_ungrouped`, `ignored_names`,
`out_of_scope_names`, `info`) are used with those exact names in Tasks 9, 10, 13 and 14.
`_get_all_channels(logger, group_ids=..., include_ungrouped=...)` matches Task 2's signature
at every later call. `split_rows_by_ignore` and `is_ignored_name` are defined in Tasks 12 and
13 before use.
