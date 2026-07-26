# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## FIXED 2026-07-26 - the E3 root-file trap on the live box (keep this, the cause recurs)

**Resolved the same day it was found.** `chown -R dispatch:dispatch` was applied
and verified BY EFFECT (`find ... ! -user dispatch` went from 31 entries to 0),
then the container was restarted. Re-verified after the later
`1.26.2071409` deploy: still 0. Post-fix state is `dispatch:dispatch`
throughout, container healthy, 1440 channels / 947 groups unchanged.

Kept because the CAUSE recurs on every deploy, and because the reasoning below
is the argument for why the chown is mandatory rather than tidy-up.

**What was observed:** every file and directory under
`/data/plugins/channel-mapparr` was `root:root`, including `__pycache__`, while
Dispatcharr's uWSGI workers and Celery pools run as **`dispatch`** and so could
not write anything there. The plugin was `enabled=True` at the time.

### Why this happens
`docker exec` defaults to **root**, and `docker cp` has no ownership flag. Any
deploy done without `-u dispatch` (or without a follow-up `chown`) creates
root-owned files. This is the workspace-wide **E3 root-file trap**.

### Why it is dangerous rather than merely untidy
It fails **silently**. Nothing raises, nothing logs, the action returns its usual
green toast. The same trap caused **bug-078** in Metricsarr, where the scheduled
report returned SUCCESS while writing nothing because `/data/logos/metricsarr`
was `root:root` - the counts were computed before the write, so a green Celery
result proved only that the task returned.

It was latent here because this plugin's writes go to the DB rather than to its
own directory. It stops being latent the moment any code writes beside its own
module: a cache, a state file, an export, a downloaded lineup. Root-owned
`__pycache__` also means the workers cannot refresh bytecode after a code update,
which is the part that bit the 2026-07-26 deploy: the deploying agent reasoned
the chown was unnecessary "because the plugin only reads these files", which is
true of the module files and false of `__pycache__`.

### The fix (already applied; this is the recipe for next time)
```bash
docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/channel-mapparr
docker restart dispatcharr
```
Then verify by EFFECT, never by exit code:
```bash
docker exec dispatcharr find /data/plugins/channel-mapparr ! -user dispatch
```
Empty output means fixed.

### Preventing the recurrence
Deploy with `docker exec -u dispatch`, and run the `chown` above after every
`docker cp` into `/data/plugins/`. The chown is mandatory, not optional.

# OpenWolf

@.wolf/OPENWOLF.md

This project uses OpenWolf for context management. Read and follow .wolf/OPENWOLF.md every session. Check .wolf/cerebrum.md before generating code. Check .wolf/anatomy.md before reading files.

# Project

Channel Maparr is a **Dispatcharr plugin** (Dispatcharr is a self-hosted TV channel/IPTV manager). It standardizes broadcast (OTA) and premium/cable channel names, organizes channels by category, and imports M3U streams, using fuzzy matching against curated per-country channel databases (~42K channels across 12 countries) plus a 1,915-station US FCC table (`networks.json`) for OTA/callsign matching.

The shippable plugin is the `Channel-Maparr/` subdirectory only. The repo root holds docs, the OpenWolf workspace (`.wolf/`), and packaging helpers.

## Runtime model (important)

This is **not** a standalone app — there is no build, lint, or test suite. The plugin runs **inside Dispatcharr's Django backend process** and is imported by Dispatcharr's plugin loader. Consequences:

- `from apps.channels.models import ...`, `from apps.m3u.models import ...`, `from core.utils import ...`, and `from django.db import ...` resolve only inside the Dispatcharr runtime. They will not import locally — do not try to "fix" them or run the plugin directly.
- All data access is **Django ORM**, not HTTP. See `MIGRATION_GUIDE.md` for ORM patterns and pitfalls (the plugin was migrated from an HTTP-API design; do not reintroduce `urllib`/REST calls for data access).
- `__init__.py` must export only the `Plugin` class — that is the loader contract.
- The user is responsible for deploying into a live Dispatcharr instance and reporting behavior. You cannot execute or test the plugin here; verify by code inspection and ask the user to run it.

## Packaging / release

- `zip.cmd` is a Windows 7-Zip script that zips the `Channel-Maparr/` folder (`.py .png .txt .json`) into `Channel-Maparr.zip` for distribution. Paths are hardcoded to the author's machine. Prefer `scripts/package_plugin.py` (cross-platform).
- **Release zips must use forward-slash separators (bug-087).** 7-Zip / `package_plugin.py` / `git archive` are fine; NEVER PowerShell `Compress-Archive` or .NET Framework `ZipFile.CreateFromDirectory` — they write backslash separators that break install on Dispatcharr's Linux host ("missing plugin.py or package __init__.py"). Gate every zip with `python scripts/validate_zip.py Channel-Maparr.zip`.
- Versioning convention: `Major.YY.DDDHHMM` (e.g. `1.26.1001200`). Bump `version` in `Channel-Maparr/plugin.json` and add a `docs/CHANGELOG.md` entry on release.
- Distribution targets: GitHub repo `PiratesIRC/Dispatcharr-Channel-Maparr-Plugin` and the `Dispatcharr/Plugins` submission repo.

## Architecture

**`Channel-Maparr/plugin.py`** (~2800 lines) — the entry point. Dispatcharr calls `Plugin.run(action, params, context)`, which dispatches via an `action_map` to `*_action` methods. `context` carries `settings` (from the `fields` defined in `plugin.json`) and a `logger`. UI surfaces (`fields`, `actions`) are declared both in `plugin.json` AND in the `Plugin.fields` property + `Plugin.actions` class attribute — **the Python class is the source of truth at runtime**, so changes to `plugin.json` alone won't take effect. Buttons must define `button_label` or Dispatcharr renders generic "Run".

Key actions (recommended run order): **Validate Settings → Load & Process Channels → Rename Channels → Tag Unknown Channels → Apply Default Logo → Apply Per-Channel Logos (tv-logos) → Organize by Category → Import M3U Streams**. **Show Status** and **Clear CSV Exports** are utility actions. `dry_run_mode` makes mutating actions export a CSV preview instead of writing. Long actions (`organize_by_category`, `import_m3u_streams`) set `"background": True` and run via `_try_start_thread` to avoid uwsgi worker-timeout kills.

Support classes in `plugin.py`: `ProgressTracker` (WebSocket progress + ETA + persistent JSON at `/data/channel_mapparr_progress.json` so `plugin_status_action` can render live status), `SmartRateLimiter` (throttles DB writes per `rate_limiting`), `PluginConfig` (includes `TV_LOGOS_REPO`/`TV_LOGOS_BRANCH`/`COUNTRY_DIR_MAP` for the tv-logos action).

**`Channel-Maparr/fuzzy_matcher.py`** (~1300 lines) — `FuzzyMatcher` class, the matching engine. **`FuzzyMatcher` now subclasses the shared vendored matcher core `FuzzyMatcherCore`** (`matching_core.py`, vendored byte-identically from the workspace `_shared/matching_core.py`, hash-pinned in `scripts/core_manifest.json`, CI-guarded by parity + golden gates) — a **partial** subclass: it keeps its own `normalize_name`, the callsign ladder, the single-digit token-overlap guard, and its lazy-load `__init__`, inheriting only the body-compatible primitives. Matcher fixes now land in the shared core (edit `_shared/matching_core.py`, re-vendor via `sync_core.py`), no longer hand-ported across copies. Pipeline: **alias (Stage 0) → exact → substring → fuzzy token-sort**. The alias stage uses a reverse index built from `aliases.py` so a STREAM-side query (Channel-Maparr's actual call pattern) can hit an O(1) lookup before fuzzy. The token inverted index (`build_token_index`/`get_candidates`) still pre-filters ~31K names down to ~50-200 candidates before fuzzy scoring (the 32h→6s optimization — do not bypass). Scoring backend is a conditional import chain: `rapidfuzz` → `thefuzz` → built-in Levenshtein. `normalize_name` runs input-cleaning fixes up front (these primitives now live in the shared core; the earlier hand-port is documented — now superseded — in `docs/MATCHER-NORMALIZATION-PORT.md`): emoji-as-letter (`SP⚽RTS`→`SPoRTS`), stylized-Unicode decoration stripping, numeric resolution-marker removal (`720p`/`3840P`), and (2026-06-25, from Lineuparr PR #13) a leading box-bar bouquet-tag strip via `_LEADING_BAR_TAG_RE` (`┃CANAL+┃ NPO 1`→`NPO 1`) with `┃` (U+2503) / `│` (U+2502) box-bar delimiters in `GEOGRAPHIC_PATTERNS` and `PROVIDER_PREFIX_PATTERNS`; the emoji/stylized/resolution fixes short-circuit on `isascii()` so curated DB names are byte-unchanged. `process_string_for_matching` now NFKD-folds and keeps any `char.isalnum()` instead of an ASCII-only `a-z0-9` filter, so Cyrillic/CJK/Arabic names survive instead of being erased to `''` (which caused false 100% matches). `calculate_similarity` computes `1 - distance/max(len)` via rapidfuzz `Levenshtein.normalized_similarity` with a matching pure-Python max-len fallback (bug-026 reconciliation, now inherited from the shared core, which also added a `>= min_ratio` Python early-exit gate replacing the rapidfuzz `score_cutoff`). False-positive guards in `_has_token_overlap` (majority mode): subset / divergent / numeric-sibling rules, plus the trailing-number guard (`_trailing_number`) that rejects `ESPN 1` vs `ESPN 2`. Callsigns extracted via `_compute_callsign_with_confidence` (returns `(callsign, is_high_confidence)`) backed by `_CALLSIGN_DENYLIST` (50 K/W-shape English words) and a per-name cache. Priority 1 (parenthesized) bypasses the denylist when the word is a real loaded station (`callsign in self.channel_lookup`), so `(KING)`/`(WOOD)`/`(WAVE)` match while unparenthesized prose (`King of the Hill`) stays guarded. **bug-098 (2026-06-29):** the shared core now enforces the same principle by default — `FuzzyMatcherCore` no longer rescues a denylisted word at end-of-name and rescues it in the loose path only when followed by a channel number (`KING 5`/`WAVE 3`/`WOOD TV8`/`WHO 13`), so a future drop of this override can't reintroduce the false positives. Channel-Maparr's own ladder is unchanged (behavior-identical re-vendor). **bug-105 (2026-07-12, RELEASED as `1.26.1930617`):** the core also strips category-`Cf` zero-width chars (ZWSP/ZWNJ/ZWJ/WORD JOINER/BOM/SOFT HYPHEN/bidi marks) at the TOP of `normalize_name` — invisible padding some providers wrap around a decorative glyph, matched by neither `\s` nor `_DECORATOR_CATS`, which used to survive the whole pipeline and tank that provider's match rate. Removed, not spaced. Golden baseline unchanged (corpus has no `Cf`).

**`Channel-Maparr/aliases.py`** — 218-entry `CHANNEL_ALIASES = {canonical_channel: [stream-name variants]}`. Loaded at `FuzzyMatcher.__init__` time and merged with `set_user_aliases()` if the caller provides custom entries. The reverse index is rebuilt on every mutation via `_rebuild_reverse_alias_index()`. Add entries here whenever a canonical DB channel name has a parenthesized abbreviation that normalization strips (e.g. `Réseau des Sports (RDS) HD` ← `RDS`).

**`Channel-Maparr/logo_matcher.py`** — Stateless tv-logo/tv-logos fetcher. `fetch_tv_logos_filelist` uses the **Git Trees API with `recursive=1`** (the Contents API silently truncates at 1000 entries, which breaks `united-states`). Caller (`apply_tv_logos_action`) caches the result on `self._tv_logos_cache` per `(repo, branch, country_dir)` to respect GitHub anonymous rate limits (60 req/hr/IP).

**`Channel-Maparr/progress_status.py`** — Django-free helpers: `load_progress`, `save_progress_atomic`, `build_status_message`. Used by `ProgressTracker._persist` and `plugin_status_action`. Path **must** live in `/data/` (the plugin dir was historically `root:root` mode 755 and unwritable by the uwsgi `dispatch` user; it is `dispatch`-owned since 2026-07-26, but keep state in `/data/` regardless since any deploy can reintroduce root ownership).

**`Channel-Maparr/group_scope.py`** (new 2026-07-26) — Pure, Django-free channel-group scope resolution, the whole contract behind the **"Channel Groups to Ignore"** (`ignore_groups`) setting. Exports `parse_tokens`, `build_name_to_ids` (name -> **set** of ids, because Dispatcharr permits duplicate group names and a scalar map leaves one unprotected), `GroupScope` (frozen; `group_ids`, `include_ungrouped`, `ignored_names`, `out_of_scope_names`, `info` — note `ignored_names` is a **superset** of `out_of_scope_names`, so summing the two over-counts), `GroupScopeError`, `resolve_group_scope`, `split_rows_by_ignore`, `is_ignored_name`. Zero mocks needed to test it. **Two asymmetries are deliberate and both are pinned by tests:** `resolve_group_scope` **refuses** when an ignore token matches no group in the DB (a typo must not degrade to "process everything"), while `split_rows_by_ignore` deliberately does **not** refuse when a valid group simply has no rows in the current results file. `plugin.py` imports it with the same try-relative/except-absolute pattern `fuzzy_matcher.py` uses, because `tests/conftest.py` loads the folder as a synthetic package **without** putting it on `sys.path`.

**`Channel-Maparr/wildcard_match.py`** (new 2026-07-26) — `expand_patterns(tokens, available_names, ci_plain)`, a byte-identical vendored copy of EPG-Janitor's helper, pinned by a provenance-hash test. **Do not edit it**; that breaks the pin. Globs (`*`/`?`) are case-insensitive **unconditionally**; only literal tokens consult `ci_plain` (this plugin passes `True`, EPG-Janitor passes `False`). It uses `.lower()`, not `.casefold()`, so Turkish dotted-I and German sharp-s do not fold — a documented limit with a test pinning it.

**Where `ignore_groups` is enforced** (all of it, because none of these paths is optional): the five `_get_all_channels` call sites; `rename_channels_action`, `rename_unknown_channels_action` and `preview_changes_action`, which replay a persisted results file and never query the DB (so a file produced before the exclusion was set is still filtered, matched on the stored group **name** so it survives a rename or delete); and the two **write** directions, where Organize skips an ignored category target and continues while Import M3U Streams refuses the whole run. `tests/test_group_scope_wiring.py` is an AST guard pinning the fetch-site count, forbidding a literal `None` or a conditional `group_ids`, requiring both kwargs to trace to a resolver result, and confining `Channel.objects` to an allowlist — with synthetic self-tests so it cannot rot into a no-op.

**`Channel-Maparr/<CC>_channels.json`** — static per-country channel/category databases (US, UK, CA, BR, DE, ES, FR, MX, NL, AU, IN, NO). `channel_databases` setting selects which to load via `FuzzyMatcher.reload_databases()`. These contain ONLY premium `National`/`Regional` entries (no `broadcast` type, no `callsign` field) — they feed `premium_channels`, never `broadcast_channels`. Editing these JSON files is how match coverage and category granularity are tuned (see `docs/TODO.md`).

**`Channel-Maparr/networks.json`** — US FCC station table (1,915 stations: `callsign → network_affiliation / community_served_city / community_served_state`). This is the **only** source of OTA/broadcast matches — `FuzzyMatcher._load_broadcast_stations()` loads it into `broadcast_channels` + `channel_lookup` (keyed by full + base callsign) when US is in the selected databases. Without it `ota_attempted` stays 0 and local affiliates fall through to premium fuzzy. The OTA name is rendered by `Plugin._format_ota_name` using the configured `ota_format`; the `{NETWORK}` comes from the stream's stated network (`Plugin._extract_stream_network`) when present, else the parsed station affiliation. US-only; non-US deployments simply have no OTA table (loader degrades gracefully).

## Reference docs

- `MIGRATION_GUIDE.md` — ORM patterns/recipes and common pitfalls (`.values()` returns `logo_id` not `logo`; `bulk_update` can't use `@property` fields; Stream uses `channel_group` FK, not `group_title`).
- `docs/CHANGELOG.md` — release history and rationale (latest: 2026-07-26 — **v1.26.2071409**, the `ignore_groups` feature; and **v1.26.2071035** the same day, a hardening slice carrying bug-044. Both are DEPLOYED and accepted on the live box and merged to `main` (`9082303`, `--no-ff`), pushed to origin. **NOT tagged and NO Hub PR — neither was requested**, so the Hub still serves a much older version. Previous: 2026-07-12 v1.26.1930617 bug-105 zero-width `Cf` strip; 2026-06-29 v1.26.1801833 bug-098 callsign-rescue hardening; 2026-06-28 matcher shared-core migration).
- `docs/TODO.md` — open work (US category granularity, adding UK/CA to defaults, EPG matching, test suite).
- `Channel-Maparr.txt` — implementation-status notes (older; CHANGELOG is more current).
- `.wolf/cerebrum.md` — accumulated do-not-repeat lessons (alias asymmetry, BMP-only emojis, `/data/` writability, parenthesized-abbreviation matching limitation).


## Dispatcharr CLIPS every action toast at ~280 characters (measured 2026-07-26)

Found while fixing Newsflasharr. It is a property of **Dispatcharr's frontend**, not of any one
plugin, so it applies here too.

Dispatcharr mounts Mantine's `Notifications` with `containerWidth: 350` but leaves
**`notificationMaxHeight` at Mantine's default 200px**, and the notification body clips
(`overflow:hidden`). At that width and Mantine's `font-size-sm` / `line-height-sm` that is about
**7 lines of ~40 characters, so roughly 280 characters are visible.** Four details decide how you
should write an action result:

* it clips from the **MIDDLE**, not the tail, so "put the important part first" does NOT
  guarantee it is seen;
* `text-overflow: ellipsis` is **inert** without `white-space: nowrap`, so there is **no visual
  marker** that anything was cut. It just stops.
* nothing sets `whiteSpace`, so your newlines **collapse into one paragraph**;
* `autoClose` is the default **4000 ms**, so even the visible part gets four seconds.

**The card renders exactly three things from an action result:** `message` (the transient toast
above), `error` (red, persistent) and `file` (persistent, rendered as plain text
`Output: <path>`, and NOT a clickable link). **`details`, `problems` and `checks` appear in NO
frontend bundle** - returning them renders nothing at all, anywhere.

**Exposure in THIS plugin, measured 2026-07-26:** `Channel-Maparr/Channel-Maparr/plugin.py` has **41 `"message"` returns, of
which 29 are built with a `.join(...)` or an embedded `
`** - that is, multi-line readouts
the toast structurally cannot show. **Their actual lengths have NOT been measured.** Do that
before assuming they fit: `len(result["message"])` against the **DEPLOYED** code, not the source
you believe is deployed.

**The fix Newsflasharr shipped, if you want the pattern** (`1.26.2071641`): write the complete
readout to a file and return its path in `file`, keeping `message` to a short headline that fits.
Newsflasharr writes to `/config/newsflasharr/<action>.txt` (= `O:\docker\dispatcharr\config\...`,
browsable in Explorer). **Deliberately NOT `/data/logos/`**, which nginx serves
**unauthenticated** to the whole LAN. Design and the measured geometry:
`notifier/docs/superpowers/specs/2026-07-26-action-readout-files-design.md`; the operational
record is in `notifier/CLAUDE.md`.

**Newsflasharr's own numbers before and after**, as a sense of scale: `validate_settings` 652 ->
293 chars, `show_ticker_filter` **989 -> 147** (that one is an ffmpeg string whose entire purpose
is to be copied, of which ~28% was visible and the hidden tail could not even be selected).


## plugin_status button coloured (2026-07-26)

`plugin_status` was the only one of ten actions with no `button_color`; it is now
`outline`/`blue` (read-only report), matching the other nine. **Verified 10 declared == 10
served** through the container's real `_normalize_actions` -- an unrecognised colour or variant
makes the serializer drop the whole action silently.

**COMMITTED ON `feat/ignore-groups`, NOT on main and NOT DEPLOYED.** Byte-compared 2026-07-26:
the version string matches the container while the code differs, because this was not
version-bumped.

**Worth recording:** this plugin uses `button_color: "violet"` on `import_m3u_streams`, which is
OUTSIDE the `blue|cyan|green|orange|red` vocabulary the workspace CLAUDE.md documents. The
serializer accepts it (Mantine supports more colours than we wrote down). Left alone
deliberately -- it works, and churning a live plugin for consistency is not worth the risk.
