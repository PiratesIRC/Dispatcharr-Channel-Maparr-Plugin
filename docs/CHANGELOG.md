# Channel Maparr — Changelog

## v1.26.1801833 (2026-06-29)

Vendor-sync of the shared matcher core (`matching_core.py`) with the **bug-098** callsign-rescue hardening landed in the workspace source. **Behavior is unchanged for Channel-Maparr** — it overrides `_compute_callsign_with_confidence` (its `channel_lookup` rescue is already gated to parenthesized positions), so the core method is shadowed and never runs here. This keeps the vendored core byte-identical to the workspace source (parity gate) and is pure future-proofing should that override ever be dropped.

### Matching (core)

- **Denylisted common-word callsign rescue is now gated.** In the shared core, `FuzzyMatcherCore._compute_callsign_with_confidence` no longer rescues a denylisted word (KING/WHO/WOLF/WAVE/WOOD/WEEK...) at end-of-name, and rescues it at the loose path only in OTA branding context (immediately followed by a channel number — new `_OTA_NUMBER_CONTEXT`). Branded `KING 5` / `WAVE 3` / `WOOD TV8` / `WHO 13` are preserved; bare program words like `King of the Hill` / `Doctor Who` extract no callsign. Parenthesized positions keep the full rescue.

### Tooling / CI

- **Core re-pinned.** `scripts/core_manifest.json` updated to the new `matching_core.py` SHA-256; parity + golden gates green (golden baseline unchanged). PR #13 merged to `main`; not released/tagged.

## v1.26.1791324 (2026-06-28)

Matcher shared-core migration. `fuzzy_matcher.py` now **subclasses a single shared, vendored matcher core** — `FuzzyMatcherCore`, defined in the workspace `_shared/matching_core.py` and vendored byte-identically into the plugin folder as `matching_core.py`. This ends the era of a copy-pasted, drifting `fuzzy_matcher.py`: matcher fixes now go to the shared core (edit `_shared/matching_core.py`, re-vendor with `sync_core.py`, regenerate the golden baseline if behavior changed), instead of being hand-ported to four separate copies. The earlier hand-port guide `docs/MATCHER-NORMALIZATION-PORT.md` is now superseded. PR merged; not yet released/tagged.

### Matching

- **`FuzzyMatcher` subclasses `FuzzyMatcherCore`** — Channel-Maparr is a **partial** subclass: it keeps its own `normalize_name`, its `channel_lookup`-rescue callsign ladder, its single-digit token-overlap guard, and its lazy-load `__init__` (no eager 42K-channel load in the constructor), inheriting only the body-compatible primitives from the core.
- **`strip_bare_region` opt-in (core)** — the shared core gained an opt-in bare-region (time-zone word) strip via a `_STRIP_BARE_REGION` class attribute (default off; Channel-Maparr does not opt in).
- **`calculate_similarity` `>= min_ratio` gate (core)** — the core's `calculate_similarity` now uses a Python `>= min_ratio` early-exit gate, replacing the rapidfuzz `score_cutoff` path.

### Tooling / CI

- **Hash-pinned vendored core** — `matching_core.py` is pinned by `scripts/core_manifest.json` (SHA-256) and guarded by a parity gate plus a golden gate in CI; `.gitattributes` keeps it LF.

## (2026-06-25)

Second matcher-normalization batch ported from Lineuparr (PR #13), plus a `calculate_similarity` reconciliation and two unrelated matcher fixes that also landed on main. Purely additive/behavioral-fix work in `fuzzy_matcher.py`: noisy provider stream names with non-Latin scripts and box-bar bouquet tags now normalize to the same form as clean channel-database names, and the similarity backend agrees with the other three matcher copies. See `docs/MATCHER-NORMALIZATION-PORT.md`.

### Matching

- **Non-ASCII preservation in `process_string_for_matching`** — NFKD-fold then keep any `char.isalnum()`, replacing the old ASCII-only `a-z0-9` filter. Previously a name written entirely in Cyrillic / CJK / Arabic was erased to `''`, which then matched everything at 100% (false positives). Non-Latin scripts now survive normalization and compare on their own characters.
- **Leading box-bar bouquet-tag strip (`normalize_name`)** — `_LEADING_BAR_TAG_RE` removes a leading `┃…┃` provider/bouquet tag, so `┃CANAL+┃ NPO 1` → `NPO 1` instead of failing to match `NPO 1`.
- **Box-bar delimiters** — `┃` (U+2503) and `│` (U+2502) added to `GEOGRAPHIC_PATTERNS` and `PROVIDER_PREFIX_PATTERNS`, covering both a single bar after a country/provider code and matched bar pairs.
- **`calculate_similarity` reconciled (bug-026)** — now computes `1 - distance/max(len)` via rapidfuzz `Levenshtein.normalized_similarity` (fast path) with a matching pure-Python max-len fallback, replacing the previous `fuzz.ratio` (Indel) fast path + sum-len pure-Python fallback whose two code paths disagreed. This brings Channel-Maparr in line with the other three `fuzzy_matcher.py` copies.
- **3-letter callsign anchoring in parentheses (bug-062)** — anchors 3-letter callsigns appearing in parentheses (ported from the shared matcher work).
- **Bare-timezone over-strip fix (bug-066)** — ported from Stream-Mapparr; stops over-stripping a bare timezone token.

### Tests

- Full local suite: **195 passing** (up from 184).

## v1.26.1701952 (2026-06-20)

GitHub release: https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases/tag/1.26.1701952

Restores OTA (over-the-air) broadcast matching, which had been silently inert, and makes the rendered network label correct. The per-country `*_channels.json` databases carry only premium `National`/`Regional` entries — no `broadcast` type, no `callsign` field — so `broadcast_channels` was always empty, `ota_attempted` stayed 0, and every local-affiliate stream fell through to premium fuzzy matching. Reported by two users (both `ota_attempted: 0`): a 267-channel "US: ABC" group renamed only 25, with 217 affiliates (`ABC 5 (WEWS) CLEVELAND HD`, `ABC 7 (KGO) SAN FRANCISCO HD`, …) reporting "No match found". Validated live across the four major US network groups — **ABC 167, CBS 213, FOX 200, NBC 513 renames** — with the rendered network verified correct on every one.

### Fixed

- **OTA callsign matching restored** — ships `networks.json` (1,915-station US FCC table: `callsign → network_affiliation / community_served_city / community_served_state`) and loads it into `broadcast_channels` + `channel_lookup` whenever the US database is selected. The existing OTA pipeline (`match_broadcast_channel` → `Plugin._format_ota_name`) now renders the configured `OTA Name Format` (default `{NETWORK} - {STATE} {CITY} ({CALLSIGN})`): `ABC 5 (WEWS) CLEVELAND HD → ABC - OH Cleveland (WEWS)`, `FOX (KTVU) → FOX - CA Oakland (KTVU)`. Previously-generic premium matches (`US: ABC (WXYZ) → ABC`) now resolve to the specific station (`ABC - MI Detroit (WXYZ)`).
- **Honor the stream's stated network** — `Plugin._extract_stream_network` reads the network a stream names (`US: CBS 7 (WBBJ-DT3) …`) and `_format_ota_name` prefers it over the FCC station's primary affiliation, which disagrees for subchannels and network-owned independents. `CBS 7 (WBBJ-DT3) JACKSON HD → CBS - TN Jackson (WBBJ)` (instead of ABC, WBBJ's main affiliation). On US: CBS this cut wrong/malformed network labels from **23 → 0**.
- **Hardened `_parse_network_affiliation`** — the fallback when a stream states no network now handles the messy real FCC strings: case-insensitive subchannel markers (`CBS Ch 3.1`), multi-network joins (`CBS & FOX`, `CBS. FOX, CW`, `ABC,CBS,CW`), callsign-prefixed (`KALB/NBC → NBC`), and parenthetical annotations (`ABC (main) CBS (multicast) → ABC`).
- **Parenthesized-callsign override** — a callsign in parentheses is an explicit signal, so `_compute_callsign_with_confidence` Priority 1 now accepts a denylisted English word there *if it is a real loaded station*: `KING` (Seattle NBC), `WOOD` (Grand Rapids NBC), `WAVE` (Louisville NBC). The callsign denylist exists to stop Priority 4 mis-reading prose (`King of the Hill → KING`); the gate is `callsign in self.channel_lookup`, so non-station words stay rejected (`HBO (WEST)`, `Disney (KIDS)`) and **unparenthesized** matches keep the strict denylist. Recovered +10 OTA renames on US: NBC.

### Data

- **`networks.json`** — US FCC station table (callsign → network/city/state, 1,915 stations), ported from Stream-Mapparr. US-only; absent for non-US deployments (loader degrades gracefully, OTA simply disabled).

### Tests

- `tests/test_broadcast_ota.py` (station-table loading + callsign→station resolution) and `tests/test_ota_network.py` (affiliation parsing, stream-network extraction, parenthesized-override + regression guards). Full suite: **184 passing**.

## v1.26.1651015 (2026-06-14)

GitHub release: https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases/tag/1.26.1651015
Dispatcharr/Plugins PR: https://github.com/Dispatcharr/Plugins/pull/128

Matcher hardening, channel-data cleanup, and a manifest fix. Ports three `normalize_name` input-cleaning fixes from Stream-Mapparr (the matcher template) so noisy provider stream names normalize to the same form as clean channel-database names — purely additive to `fuzzy_matcher.py` (122 insertions, 0 deletions; no existing matching logic changed). Also deduplicates the channel databases, adds Norwegian support, and corrects two corrupted `plugin.json` button labels. See `docs/MATCHER-NORMALIZATION-PORT.md`.

### Matching

- **Stylized-Unicode decoration stripping (bug-048)** — Drops whole tokens that are pure stylized decoration (superscript / small-capital tier markers, bullets) before the ASCII tag pipeline, detected by Unicode character *name* rather than code-point range. A superscript "RAW" suffix no longer blocks a match to `WeatherNation`. Real ASCII tier words (Gold/VIP) and non-Latin scripts (Arabic/Cyrillic/CJK) are preserved.
- **Emoji-as-letter normalization (bug-051)** — Maps an emoji used as a letter inside a word (`SP⚽RTS` → `SPoRTS`, the beIN family) to its letter when flanked by ASCII letters, and strips emoji used purely as decoration plus zero-width selectors. `beIN SP⚽RTS` now matches `beIN Sports`.
- **Numeric resolution markers (bug-055)** — Strips `720p` / `1080p` / `2160p` / `3840P`-style markers (a 3–4 digit run glued to p/i) that the keyword quality list misses, while keeping bare numbers (`Channel 4`, `Studio 1080`), 5-digit runs, and spaced standalone roman numerals (`Volume 100 I`) intact.

Beneficial side effect: the NFKD canonicalization in the stylized-strip step unifies accented and ASCII spellings of the same channel, so `UniMás`/`UniMas` and `TeleFórmula`/`TeleFormula` now match where they previously did not. Verified: 0 changes to any ASCII channel name across all 42,246 database names; no different-channel false-merges.

### Data

- **Deduplicated channel databases** — removed 651 fully-identical rows across 7 country files (UK 168, MX 206, DE 136, CA 62, BR 43, FR 19, ES 17); all `*_channels.json` normalized to LF.
- **Norwegian channel database (`NO_channels.json`)** — 94 channels; registered `NO → norway` in `COUNTRY_DIR_MAP` so the per-channel logo action resolves them. Coverage is now **12 countries**.

### Fixed

- **plugin.json button labels** — `Apply Per-Channel Logos` and `Show Status` showed a literal `?` instead of the ❖ / ⓘ icons (the earlier BMP-icon fix patched `plugin.py` but `plugin.json` drifted; the running plugin was unaffected since it uses the class labels). Now match `plugin.py` exactly (U+2756 / U+24D8).

### Tests

- `tests/test_normalization_port.py` (48 cases) locks the three matcher fixes at the helper, regex, and full-pipeline levels, with editor-proof escaped Unicode constants, plus a CI-enforced corpus no-regression gate (0 ASCII-name changes across ~41.5K names). `test_plugin_contract.py` gains exact `button_label` parity (plugin.json ↔ Plugin.actions) and a no-`?`-placeholder guard. Full suite: **151 passing**.

## v1.26.1430910 (2026-05-23)

GitHub release: https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases/tag/1.26.1430910
Dispatcharr/Plugins PR: https://github.com/Dispatcharr/Plugins/pull/95

Feature release ported from Lineuparr. Adds an alias-driven Stage 0 to the matching pipeline, per-channel logos from the tv-logo/tv-logos GitHub repo, a persistent progress file with a Show Status button, and full help text on every settings field.

### Hotfixes folded into this release

- **Per-channel logos used the wrong GitHub API** (would have silently truncated to 1000 logos per country). Switched to the Git Trees API with `recursive=1` so `united-states` and other large directories return complete results. 403 rate-limit errors now surface a distinct user-facing message instead of generic "failed".
- **Progress file path was unwritable** by the uwsgi `dispatch` user (plugin dir is `root:root` mode 755). Moved to `/data/channel_mapparr_progress.json`, matching the convention other Dispatcharr plugins use. Write failures now log at WARNING (log-once) instead of silently swallowed at DEBUG.
- **Astral-plane emoji button labels** (🎨, 📊) were silently rejected by Dispatcharr's plugin loader (surrogate-pair validator). Swapped to BMP symbols (`❖`, `ⓘ`).
- **Alias lookup was inverted**: original implementation assumed query=channel, but Channel-Maparr's pipeline calls `fuzzy_match(stream, channel_candidates)`. Built a reverse alias index (`normalized_variant → canonical_channel`) so stream-side queries hit the alias map.
- **CSV "Match Type: None" was a literal string** for unmatched rows, reading like a bug. Now writes empty string.
- **Multi-token country prefixes** like `CA FR:` only stripped half (leaving `"CA"` stranded). Extended geographic prefix regex to handle two-token prefixes.
- **User-reported `RDS / TVA Sports / TSN` mismatches**: The CA database stores bare RDS as `Réseau des Sports (RDS) HD`, but the `(RDS)` parenthetical is stripped during normalization, leaving only `"Réseau des Sports"` — unreachable from streams named `RDS`. Added 13 Canadian aliases (`Réseau des Sports (RDS) HD` ← `RDS`, `RDS HD`, `RDS 1`, `RDS 1 HD`; `RDS2 HD` ← `RDS 2`; `TVA Sports`; `TSN 1-5 HD` ← `TSN n RAW`/`TSN n BK`). User's full failing case set now passes 14/14.

### New

- **Channel-alias Stage 0 (`aliases.py`, 205 entries)** — Curated `channel_name → [variants]` map runs before the fuzzy stages. An O(1) exact-or-near-exact alias hit short-circuits fuzzy scoring, so "FOX News Channel" finds streams named "FNC" or "Fox News" instantly and reliably. Users can add their own via `FuzzyMatcher.set_user_aliases()`.
- **Per-channel logos (`apply_tv_logos` action + `logo_matcher.py`)** — Fuzzy-matches each channel without a logo to the [tv-logo/tv-logos](https://github.com/tv-logo/tv-logos) repo, creates Logo records pointing at the raw GitHub URLs, and assigns them in bulk. Iterates the country codes from `channel_databases`. The existing single-default-logo action is preserved as **Apply Default Logo**.
- **Show Status action + persistent progress (`progress_status.py`)** — `ProgressTracker` now persists state to `.channel_maparr_progress.json` next to the plugin. Click **📊 Show Status** to see live percent + ETA without watching container logs. Surfaces stale-run warnings if updates stop for >2 min.
- **`help_text` on every settings field** — All 15 settings now ship with a one-sentence explanation of what they do and when to change them.

### Tooling

- `set_user_aliases()` on `FuzzyMatcher` lets callers merge custom aliases on top of the builtin set without rebuilding the matcher.

## v1.26.1430845 (2026-05-23)

Matching-accuracy release. Six improvements ported from Lineuparr's recent rework. Together: ~12-point accuracy gain on a curated 46-case harness (baseline 88% → 100%) with no regressions in true positives. Returns `None` instead of a wrong sibling/zone variant when the database lacks a precise match — a quiet miss is better than a confident wrong answer.

### Matching accuracy (fuzzy_matcher.py)

- **Callsign denylist** — 50-word frozenset blocks K/W-shape English words (WITH, WATCH, WWE, KING, KIDS, WORLD, …) from extracting as US broadcast callsigns. Eliminates false positives like "Bizarre Foods *with* Andrew Zimmern" → callsign "WITH".
- **Callsign confidence + cache** — `extract_callsign` now returns `(callsign, is_high_confidence)`. Parenthesized/end-of-name extractions are high-confidence; loose mid-name matches are low-confidence. Cached in `_callsign_cache` per channel name. Foundation for asymmetric callsign anchoring (used by future stages).
- **CamelCase + number-word + dot normalization** — `JusticeCentral.TV` → `Justice Central TV`, `DangerTV` → `Danger TV`, `BBC Three` → `BBC 3`. 4-char floor on the acronym split protects `MeTV`/`truTV`.
- **East/West parenthetical promotion** — `(W)`/`(E)`/`(West)`/`(East)` are converted to bare `West`/`East` words *before* parenthetical stripping, so zoned lineup entries can survive normalization with their zone intact.
- **Token-overlap guard in exact stage** — The 97%+ same-string branch now requires majority token overlap, catching `ABC News` vs `BBC News` (93% similar, only `news` shared).
- **Smarter `_has_token_overlap` (majority mode)** — Now demotes `network`/`channel`/`television` to common (they're brand suffixes, not distinguishing). Adds three guards: subset (one side is subset and larger has a distinctive >=5-char token), divergent (both sides have unique >=4-char tokens), numeric (both sides have unique numeric/ordinal tokens). Catches sibling-channel false positives like `Sky Cinema Disney` vs `Sky Cinema Decades` and `BBC One` vs `BBC Two`.
- **Always-majority fuzzy stage + trailing-number guard** — `_trailing_number` rejects `Foo 1` vs `Foo 2` (`HBO 1` vs `HBO 2`, `ESPN 1` vs `ESPN 2`). All matching stages always require majority overlap, not the previous score-dependent toggle.
- **Inside-loop guards** — Stage 2 substring and stage 3 fuzzy now apply the overlap/threshold guards *inside* the per-candidate loop. Previously, a high-scoring but guard-rejected candidate suppressed lower-scoring valid candidates — fixed.

### Tooling

- **Test harness (`.wolf/test_matching.py`)** — 46-case standalone harness loads the matcher against real US/UK/CA databases and exercises true positives, true negatives, exact-expected matches, expected-none cases, and callsign extraction. Not shipped with the plugin.

## v1.26.1001200 (2026-04-10)

Performance and reliability release. All items from docs/TODO.md completed. M3U import matching reduced from 32 hours to 6 seconds (19K streams against 31K channels).

### Performance

- **Token-based candidate pre-filter** — Inverted index (`build_token_index()` / `get_candidates()`) maps normalized tokens to channel names. Fuzzy matching now searches ~50-200 candidates instead of 31K. Applied to all 4 fuzzy match code paths: process_channels, organize dry run, organize live, and M3U import. Benchmark: 19,200x speedup on M3U import matching (32 hours -> 6 seconds).
- **Early termination in Levenshtein** — `calculate_similarity()` now accepts a `min_ratio` parameter. Length-difference pre-check skips impossible matches instantly; row-level early termination aborts the DP matrix when min distance is already exceeded. `find_best_match()` uses dynamic `min_ratio` (raises cutoff as better scores are found).
- **`rapidfuzz` integration** — Conditional import chain: `rapidfuzz` (10-100x faster C extension) -> `thefuzz` -> built-in Levenshtein. `score_cutoff` optimization used only with `rapidfuzz` (not supported by `thefuzz`).
- **Django query optimizations** — Removed unnecessary `select_related` on `.values()` queries. Added `.only()` to stream fetches to limit selected columns. Prefetched all Stream objects before import loop to eliminate N+1 `Stream.objects.get()` queries.

### Reliability

- **Atomic CSV writes** — Both `preview_changes_action()` and `_export_m3u_import_preview()` now write to a temp file and atomically rename via `os.replace()`. Orphan temp files are cleaned up on exception.
- **Background threading for Organize by Category** — Added `"background": True` to the organize action to prevent uwsgi worker timeout kills on long-running category matching.

### Bugfixes

- **`thefuzz` compatibility** — `thefuzz.fuzz.ratio()` does not support the `score_cutoff` parameter (rapidfuzz-only). Added `_HAS_SCORE_CUTOFF` flag to avoid `TypeError` when only `thefuzz` is installed.
- **Stream query fix** — Removed non-existent `group_title` field from `.only()` queries which caused `FieldDoesNotExist` at runtime. The Stream model uses `channel_group` (FK) not `group_title`.
- **Prefetch safety** — `stream_objects[stream_id]` replaced with `.get()` + skip/log to handle streams deleted between prefetch and import loop.

### New Features

- **`match_all_streams()` method** — Returns all matches above threshold sorted by score (exact -> substring -> fuzzy pipeline). Useful for CSV preview exports showing top N alternatives per channel.

### Distribution

- Published to GitHub: [v1.26.1001200](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases/tag/1.26.1001200)
- Submitted to official Dispatcharr Plugin Repository: [Dispatcharr/Plugins#31](https://github.com/Dispatcharr/Plugins/pull/31)
- Added plugin repo metadata to `plugin.json`: `license`, `repo_url`, `discord_thread`, `min_dispatcharr_version`
- Removed legacy files from repo: `channels.json`, `channels.txt`, `networks.json`
- Updated README with current features, settings, performance docs, and troubleshooting

---

## v1.26.1000740 (2026-04-10)

Major optimization release porting proven patterns from Lineuparr. Requires Dispatcharr v0.20.0+.

### Performance

- **Normalization caching** — `precompute_normalizations()` pre-computes and caches normalized forms for all candidate names before matching loops. Avoids redundant re-normalization on every query. Three cache layers: `_norm_cache`, `_norm_nospace_cache`, `_processed_cache`.
- **False-positive guards** — Length-scaled thresholds (95% for short names <=4 chars, 90% for medium <=8) and token overlap checks prevent false matches like "ACC"/"AMC" or "abc news"/"fox news".
- **Provider prefix stripping** — `PROVIDER_PREFIX_PATTERNS` automatically strip IPTV prefixes (`US:`, `USA|`, `(FR)`, etc.) from stream names before matching.
- **East/West preservation** — Regional variants (HBO East, HBO West) are no longer merged during normalization. They are treated as distinct channels.

### New Features

- **Background threading** — M3U import runs in a daemon thread to avoid HTTP timeouts. Includes `_try_start_thread()` locking and `_stop_event` for graceful cancellation via the UI.
- **ProgressTracker** — Replaces ad-hoc progress logging with a dedicated class. Sends WebSocket updates with percentage and ETA to the Dispatcharr UI. Adaptive update intervals (3s for small jobs, 10s for large).
- **SmartRateLimiter** — Configurable delay between database writes during large imports (None/Low/Medium/High).
- **Dynamic M3U source dropdown** — The M3U Source field is now a select dropdown populated from `M3UAccount` objects in the database, replacing the old manual text input.
- **Match sensitivity dropdown** — Replaced the numeric threshold (0-100) with a select dropdown matching Lineuparr's pattern: Relaxed (70), Normal (80), Strict (90), Exact (95). Legacy numeric settings are still supported as fallback.

### Architecture

- **PluginConfig class** — All configuration constants extracted from the Plugin class into a dedicated `PluginConfig` class for cleaner organization.
- **Improved `run()` method** — Logs action start/end, sends WebSocket notifications for completed non-background actions, handles the `background` flag for threaded operations.
- **`stop()` method** — Called by Dispatcharr when the user requests cancellation. Sets `_stop_event` which is checked in all long-running loops (channel processing, stream matching, M3U import).

### Plugin Settings (GUI)

All settings are rendered in the Dispatcharr plugin card UI. Settings persist across plugin updates automatically via Dispatcharr's `PluginConfig` model.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Channel Databases | string | `US` | Comma-separated country codes (AU, BR, CA, DE, ES, FR, IN, MX, NL, UK, US) |
| Match Sensitivity | select | `normal` | Relaxed (70), Normal (80), Strict (90), Exact (95) |
| Channel Groups to Process | string | *(empty)* | Limit rename/logo actions to specific groups |
| Category Organization Groups | string | *(empty)* | Source groups for category-based reorganization |
| M3U Source | select | `All sources` | Filter streams to a specific M3U account |
| M3U Group Filter | string | *(empty)* | Pre-match filter by M3U group-title |
| Category Filter | string | *(empty)* | Post-match filter by database category |
| Custom Import Group Name | string | *(empty)* | Override category-based group naming |
| OTA Name Format | string | `{NETWORK} - {STATE} {CITY} ({CALLSIGN})` | Format template for broadcast channel names |
| Unknown Channel Suffix | string | ` [Unk]` | Appended to unmatched channels |
| Ignored Tags | string | `[4K], [FHD], [HD], [SD], [Unknown], [Unk], [Slow], [Dead]` | Tags stripped before matching |
| Default Logo | string | *(empty)* | Logo display name from Dispatcharr's Logos page |
| Dry Run Mode | boolean | `false` | Preview changes without modifying anything |
| Rate Limiting | select | `None` | Delay between DB writes (None/Low/Medium/High) |

### Action Buttons

| Action | Color | Description |
|--------|-------|-------------|
| Validate Settings | blue/outline | Check DB connectivity, databases, and settings |
| Load & Process Channels | green/filled | Scan groups and determine standardized names |
| Rename Channels | green/filled | Apply names (or CSV preview in Dry Run) |
| Tag Unknown Channels | green/filled | Append suffix to unmatched channels |
| Apply Logos | green/filled | Assign default logo to channels without one |
| Organize by Category | green/filled | Move channels into category groups (or CSV preview) |
| Import M3U Streams | violet/filled | Background import from M3U (or CSV preview) |
| Clear CSV Exports | red/outline | Delete all plugin CSV files |

### Version Format

Changed from semantic versioning (`0.7.0a`) to timestamp format (`Major.YY.DDDHHMM`) matching the Lineuparr convention. Example: `1.26.1000740` = major 1, year 2026, day 100 (Apr 10), 07:40.

---

## v0.7.0 (2025)

- Migrated from HTTP API pattern to Django ORM (see `MIGRATION_GUIDE.md`)
- Removed credential fields (`dispatcharr_url`, username, password)
- Added WebSocket notifications via `send_websocket_update()`
- Added M3U stream import with category-based organization

## v0.6.0a (2025)

- Initial release with HTTP API pattern
- OTA broadcast and premium/cable channel matching
- 11 country channel databases
