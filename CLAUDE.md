# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OpenWolf

@.wolf/OPENWOLF.md

This project uses OpenWolf for context management. Read and follow .wolf/OPENWOLF.md every session. Check .wolf/cerebrum.md before generating code. Check .wolf/anatomy.md before reading files.

# Project

Channel Maparr is a **Dispatcharr plugin** (Dispatcharr is a self-hosted TV channel/IPTV manager). It standardizes broadcast (OTA) and premium/cable channel names, organizes channels by category, and imports M3U streams, using fuzzy matching against curated per-country channel databases (~42K channels across 12 countries).

The shippable plugin is the `Channel-Maparr/` subdirectory only. The repo root holds docs, the OpenWolf workspace (`.wolf/`), and packaging helpers.

## Runtime model (important)

This is **not** a standalone app — there is no build, lint, or test suite. The plugin runs **inside Dispatcharr's Django backend process** and is imported by Dispatcharr's plugin loader. Consequences:

- `from apps.channels.models import ...`, `from apps.m3u.models import ...`, `from core.utils import ...`, and `from django.db import ...` resolve only inside the Dispatcharr runtime. They will not import locally — do not try to "fix" them or run the plugin directly.
- All data access is **Django ORM**, not HTTP. See `MIGRATION_GUIDE.md` for ORM patterns and pitfalls (the plugin was migrated from an HTTP-API design; do not reintroduce `urllib`/REST calls for data access).
- `__init__.py` must export only the `Plugin` class — that is the loader contract.
- The user is responsible for deploying into a live Dispatcharr instance and reporting behavior. You cannot execute or test the plugin here; verify by code inspection and ask the user to run it.

## Packaging / release

- `zip.cmd` is a Windows 7-Zip script that zips the `Channel-Maparr/` folder (`.py .png .txt .json`) into `Channel-Maparr.zip` for distribution. Paths are hardcoded to the author's machine.
- Versioning convention: `Major.YY.DDDHHMM` (e.g. `1.26.1001200`). Bump `version` in `Channel-Maparr/plugin.json` and add a `docs/CHANGELOG.md` entry on release.
- Distribution targets: GitHub repo `PiratesIRC/Dispatcharr-Channel-Maparr-Plugin` and the `Dispatcharr/Plugins` submission repo.

## Architecture

**`Channel-Maparr/plugin.py`** (~2800 lines) — the entry point. Dispatcharr calls `Plugin.run(action, params, context)`, which dispatches via an `action_map` to `*_action` methods. `context` carries `settings` (from the `fields` defined in `plugin.json`) and a `logger`. UI surfaces (`fields`, `actions`) are declared both in `plugin.json` AND in the `Plugin.fields` property + `Plugin.actions` class attribute — **the Python class is the source of truth at runtime**, so changes to `plugin.json` alone won't take effect. Buttons must define `button_label` or Dispatcharr renders generic "Run".

Key actions (recommended run order): **Validate Settings → Load & Process Channels → Rename Channels → Tag Unknown Channels → Apply Default Logo → Apply Per-Channel Logos (tv-logos) → Organize by Category → Import M3U Streams**. **Show Status** and **Clear CSV Exports** are utility actions. `dry_run_mode` makes mutating actions export a CSV preview instead of writing. Long actions (`organize_by_category`, `import_m3u_streams`) set `"background": True` and run via `_try_start_thread` to avoid uwsgi worker-timeout kills.

Support classes in `plugin.py`: `ProgressTracker` (WebSocket progress + ETA + persistent JSON at `/data/channel_mapparr_progress.json` so `plugin_status_action` can render live status), `SmartRateLimiter` (throttles DB writes per `rate_limiting`), `PluginConfig` (includes `TV_LOGOS_REPO`/`TV_LOGOS_BRANCH`/`COUNTRY_DIR_MAP` for the tv-logos action).

**`Channel-Maparr/fuzzy_matcher.py`** (~1300 lines) — `FuzzyMatcher` class, the matching engine. Pipeline: **alias (Stage 0) → exact → substring → fuzzy token-sort**. The alias stage uses a reverse index built from `aliases.py` so a STREAM-side query (Channel-Maparr's actual call pattern) can hit an O(1) lookup before fuzzy. The token inverted index (`build_token_index`/`get_candidates`) still pre-filters ~31K names down to ~50-200 candidates before fuzzy scoring (the 32h→6s optimization — do not bypass). Scoring backend is a conditional import chain: `rapidfuzz` → `thefuzz` → built-in Levenshtein. `normalize_name` runs three input-cleaning fixes up front (ported byte-accurate from Stream-Mapparr — see `docs/MATCHER-NORMALIZATION-PORT.md`): emoji-as-letter (`SP⚽RTS`→`SPoRTS`), stylized-Unicode decoration stripping, and numeric resolution-marker removal (`720p`/`3840P`); all three short-circuit on `isascii()` so curated DB names are byte-unchanged. False-positive guards in `_has_token_overlap` (majority mode): subset / divergent / numeric-sibling rules, plus the trailing-number guard (`_trailing_number`) that rejects `ESPN 1` vs `ESPN 2`. Callsigns extracted via `_compute_callsign_with_confidence` (returns `(callsign, is_high_confidence)`) backed by `_CALLSIGN_DENYLIST` (50 K/W-shape English words) and a per-name cache.

**`Channel-Maparr/aliases.py`** — 218-entry `CHANNEL_ALIASES = {canonical_channel: [stream-name variants]}`. Loaded at `FuzzyMatcher.__init__` time and merged with `set_user_aliases()` if the caller provides custom entries. The reverse index is rebuilt on every mutation via `_rebuild_reverse_alias_index()`. Add entries here whenever a canonical DB channel name has a parenthesized abbreviation that normalization strips (e.g. `Réseau des Sports (RDS) HD` ← `RDS`).

**`Channel-Maparr/logo_matcher.py`** — Stateless tv-logo/tv-logos fetcher. `fetch_tv_logos_filelist` uses the **Git Trees API with `recursive=1`** (the Contents API silently truncates at 1000 entries, which breaks `united-states`). Caller (`apply_tv_logos_action`) caches the result on `self._tv_logos_cache` per `(repo, branch, country_dir)` to respect GitHub anonymous rate limits (60 req/hr/IP).

**`Channel-Maparr/progress_status.py`** — Django-free helpers: `load_progress`, `save_progress_atomic`, `build_status_message`. Used by `ProgressTracker._persist` and `plugin_status_action`. Path **must** live in `/data/` (not the plugin dir, which is `root:root` mode 755 and unwritable by the uwsgi `dispatch` user).

**`Channel-Maparr/<CC>_channels.json`** — static per-country channel/category databases (US, UK, CA, BR, DE, ES, FR, MX, NL, AU, IN, NO). `channel_databases` setting selects which to load via `FuzzyMatcher.reload_databases()`. Editing these JSON files is how match coverage and category granularity are tuned (see `docs/TODO.md`).

## Reference docs

- `MIGRATION_GUIDE.md` — ORM patterns/recipes and common pitfalls (`.values()` returns `logo_id` not `logo`; `bulk_update` can't use `@property` fields; Stream uses `channel_group` FK, not `group_title`).
- `docs/CHANGELOG.md` — release history and rationale (latest: v1.26.1430910 — aliases, per-channel logos, Show Status, matching fixes).
- `docs/TODO.md` — open work (US category granularity, adding UK/CA to defaults, EPG matching, test suite).
- `Channel-Maparr.txt` — implementation-status notes (older; CHANGELOG is more current).
- `.wolf/cerebrum.md` — accumulated do-not-repeat lessons (alias asymmetry, BMP-only emojis, `/data/` writability, parenthesized-abbreviation matching limitation).
