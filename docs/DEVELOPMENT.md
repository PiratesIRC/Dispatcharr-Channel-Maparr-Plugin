# Development Guide

How to work on Channel Maparr locally — setup, testing, the automation that
guards the codebase, and how to cut a release. For *what the plugin does* and its
architecture, see the repo `CLAUDE.md`; for ORM patterns, see `MIGRATION_GUIDE.md`.

## Runtime model (read this first)

Channel Maparr is **not a standalone app**. It is a Dispatcharr plugin that runs
*inside* Dispatcharr's Django backend process. There is no `runserver`, no local
HTTP server, no way to execute the plugin end-to-end on a dev machine.

- Imports like `from apps.channels.models import ...`, `from django.db import ...`,
  and `from core.utils import ...` resolve **only** inside the Dispatcharr runtime.
  They will not import locally — that is expected, not a bug to fix.
- All data access is the Django ORM, never HTTP. Do not reintroduce
  `urllib`/REST calls for data access (see `MIGRATION_GUIDE.md`).
- The shippable plugin is the `Channel-Maparr/` subdirectory only. The repo root
  holds docs, tests, tooling, and the OpenWolf workspace (`.wolf/`).
- Full-system verification means: deploy into a live Dispatcharr instance and
  exercise the actions. Everything else is verified by the test suite and by code
  inspection.

## Prerequisites

- **Python 3.11+** (CI runs 3.11 and 3.12).
- **Node.js** — only for the OpenWolf `.wolf/hooks/*` and the optional context7 MCP.
- **`gh` CLI** (authenticated) — for releases and PR work.

```
pip install -r requirements-dev.txt
```

`requirements-dev.txt` is dev/CI only — the shipped plugin installs nothing of its
own; it uses whatever is in Dispatcharr's environment. `rapidfuzz` is included to
mirror production's preferred fuzzy backend, but the matcher also works without it
(falls back to `thefuzz`, then a built-in Levenshtein).

## Testing

```
python -m pytest          # full suite (~184 cases, a few seconds)
python -m pytest -k alias  # filter by name
```

The suite lives in `tests/` and runs **without** a Dispatcharr/Django runtime:
`tests/conftest.py` installs `MagicMock` stand-ins for every Dispatcharr module and
loads the hyphenated `Channel-Maparr/` directory as the importable package
`channel_maparr` (needed because `plugin.py` uses relative imports).

What the suites cover:

| File | What it guards |
|------|----------------|
| `tests/test_matching.py` | Stream→channel accuracy: true positives, true negatives, exact-expected, expected-None, callsign extraction. The query is the STREAM; candidates are channel DB names. |
| `tests/test_broadcast_ota.py` | OTA matching: `networks.json` populates `broadcast_channels` when US is loaded, and representative affiliate streams resolve callsign → station (`ABC 5 (WEWS) CLEVELAND HD` → Cleveland/OH/ABC). |
| `tests/test_ota_network.py` | OTA network resolution: `_parse_network_affiliation` on messy FCC strings, `_extract_stream_network` (stream-stated network), and the parenthesized-callsign override (`(KING)` accepted, `King of the Hill` not). |
| `tests/test_normalization_port.py` | The three `normalize_name` input-cleaning fixes ported from Stream-Mapparr (helper, regex, full-pipeline) + a corpus no-regression gate (0 ASCII-name changes). |
| `tests/test_plugin_contract.py` | `plugin.json` ↔ `Plugin` class parity: action-id match, every action has a `button_label`, **exact button_label parity** (catches a symbol corrupted to a literal `?`, which the BMP-only check misses), version agreement, and **BMP-only** (no astral-plane characters the loader silently drops). |
| `tests/test_data_integrity.py` | Per-country JSON structure, **no byte-identical duplicate rows**, BMP-only data, alias-table shape. |
| `tests/test_pure_modules.py` | `progress_status.py` and `logo_matcher.py` (both deliberately Django-free). |

Guiding principle for matching changes (cerebrum, 2026-05-23): **"None is better
than wrong."** A confident-but-wrong match silently routes a stream to the wrong
channel; returning `None` makes the miss visible so the user can map it manually.
Test accuracy changes — do not trust them by inspection. There is also an older
standalone harness at `.wolf/test_matching.py` (`python .wolf/test_matching.py`).

CI runs the suite plus a `py_compile` check on every push and PR
(`.github/workflows/tests.yml`, Python 3.11 + 3.12).

## Automation that guards edits

- **PostToolUse `py_compile` hook** (`scripts/hook_py_compile.py`, wired in
  `.claude/settings.json`) — after any `.py` edit, the file is byte-compiled and a
  `SyntaxError` is surfaced immediately. Fails safe: non-Python edits and unexpected
  conditions are silent no-ops.
- **OpenWolf hooks** (`.wolf/hooks/*`) — session/context bookkeeping. See
  `.wolf/OPENWOLF.md`. After significant changes, update `.wolf/anatomy.md` (file
  map), append to `.wolf/memory.md`, and log bugs to `.wolf/buglog.json`.

## Editing the channel databases

`Channel-Maparr/<CC>_channels.json` (US, UK, CA, AU, BR, DE, ES, FR, MX, NL, IN, NO) are
the curated match databases — each a `{country_code, country_name, version, channels[]}`
object where every channel is `{channel_name, category, type}`.

- Duplicate channel **names** across categories are allowed (a channel can appear
  under more than one category). **Byte-identical rows are not** — they bloat the
  candidate index and are rejected by `test_no_identical_duplicate_rows`.
- Keep data **BMP-only** (no characters above U+FFFF).
- Aliases live in `Channel-Maparr/aliases.py` (`canonical_name → [stream variants]`).
  Add an alias whenever a canonical DB name has a parenthesized abbreviation that
  normalization strips (e.g. `Réseau des Sports (RDS) HD` ← `RDS`). Entries whose
  key matches no DB name are simply unused — harmless.
- `Channel-Maparr/networks.json` is a **separate** US-only FCC station table
  (`callsign → network_affiliation / community_served_city / community_served_state`,
  1,915 rows) — the *only* source of OTA/broadcast matches (the `*_channels.json`
  files have no callsign/broadcast entries). Loaded by
  `FuzzyMatcher._load_broadcast_stations()` when US is selected. Editing it is how
  OTA callsign coverage is tuned; absent it, `ota_attempted` stays 0.

## Releasing

Use the `/release` skill (`.claude/skills/release/SKILL.md`) for the guided flow,
or run the steps manually:

1. `python -m pytest` — must be green.
2. `python scripts/bump_version.py` — bumps `Major.YY.DDDHHMM` in **both**
   `Channel-Maparr/plugin.json` and the `PLUGIN_VERSION` constant in `plugin.py`
   (they must never drift; the Python class is the runtime source of truth).
   Use `--dry-run` to preview, `--set X.Y.Z` for an explicit version.
3. Add a `docs/CHANGELOG.md` entry (newest first, matching the existing format).
4. `python scripts/package_plugin.py` — builds `Channel-Maparr.zip` (cross-platform
   replacement for `zip.cmd`). Pre-flight compiles every `.py` and asserts
   `plugin.json` is BMP-only; a failure refuses to package.
5. Commit, `git tag <version>`, push.
6. `gh release create <version> Channel-Maparr.zip --repo PiratesIRC/Dispatcharr-Channel-Maparr-Plugin`.
7. Optional: submit/update `Dispatcharr/Plugins` (PR title exactly
   `[channel-mapparr] <description>`; see the submission notes in `.wolf/cerebrum.md`).

## Gotchas (learned the hard way)

- **Astral-plane characters break the loader.** Any character > U+FFFF in a plugin
  action label makes Dispatcharr silently drop the entire action. Keep `plugin.json`
  and the `Plugin.actions`/`fields` symbols BMP-only (✅ ⚖ ☰ ⇩ ▶ ⭐ ✏ ✗ ❖ ⓘ are safe).
  Runtime status strings can use emoji; manifest/action definitions cannot.
- **`plugin.json` alone is not enough.** The `Plugin` class (`fields` property +
  `actions` attribute) is the runtime source of truth. Change both, or the contract
  test will (correctly) fail.
- **Persistent state goes in `/data/`,** not the plugin dir (which is `root:root`
  mode 755 and unwritable by the uwsgi `dispatch` user).
- **Keep new matching code on the token pre-filter.** Any path that scores names
  must go through `get_candidates()` + cached normalizations, or it reintroduces the
  O(streams × channels) blowup the index fixed (32h → 6s).
