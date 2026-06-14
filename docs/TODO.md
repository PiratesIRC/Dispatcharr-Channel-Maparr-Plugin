# Channel Maparr — TODO

## Completed (v1.26.1001200)

- [x] **Token-based candidate pre-filter** — Inverted index reduces fuzzy matching from O(streams * channels) to O(streams * ~200). M3U import: 32 hours -> 6 seconds.
- [x] **Early termination in Levenshtein** — `min_ratio` parameter with length pre-check and row-level abort.
- [x] **`rapidfuzz` integration with fallback** — rapidfuzz -> thefuzz -> built-in Levenshtein.
- [x] **Atomic file writes for CSV exports** — tempfile + os.replace() with exception cleanup.
- [x] **`match_all_streams()` method** — Returns all matches above threshold sorted by score.
- [x] **Optimize Django queries** — Removed unnecessary select_related, added .only(), prefetched streams.
- [x] **Background threading for Organize by Category** — Prevents uwsgi worker timeout.

## Completed (v1.26.1430910)

- [x] **Alias system** — `aliases.py` with 218 entries (`CHANNEL_ALIASES = {canonical: [variants]}`); reverse index for O(1) stream→canonical lookup; `FuzzyMatcher.alias_match` as Stage 0 before exact/substring/fuzzy. Supersedes the alias bits from PR #2.
- [x] **Per-channel logos** — `logo_matcher.py` + `apply_tv_logos_action` fuzzy-match channel names against tv-logo/tv-logos repo (Git Trees API, recursive). Per-session cache to respect GitHub anonymous rate limits.
- [x] **Show Status / persistent progress** — `progress_status.py`; `ProgressTracker` writes `/data/channel_mapparr_progress.json` on every tick; `plugin_status_action` reads it back.
- [x] **Callsign denylist + confidence cache** — 50-word K/W-shape English denylist; `_compute_callsign_with_confidence` returns `(callsign, is_high_confidence)` with `_callsign_cache` memoization.
- [x] **Smarter `_has_token_overlap`** — subset, divergent, and numeric-sibling guards (catches BBC One≠BBC Two, Sky Cinema Disney≠Decades, ABC News≠BBC News). Demoted network/channel/television to common words.
- [x] **Trailing-number anchor** — `_trailing_number` rejects ESPN 1 vs ESPN 2 / HBO 1 vs HBO 2 collisions.
- [x] **Inside-loop guard placement** — high-scoring guard-rejected candidates no longer suppress lower-scoring valid ones.
- [x] **Multi-token country prefix stripping** — `CA FR:`, `US ES:`, `UK FHD:` now strip cleanly.
- [x] **CamelCase / number-word / dot normalization** — `JusticeCentral.TV` → `Justice Central TV`; `BBC Three` ↔ `BBC 3`; East/West parenthetical preservation.
- [x] **`help_text` on every settings field** — all 15 fields self-documented in the UI.
- [x] **`button_label` on every action** — Dispatcharr no longer renders generic "Run".
- [x] **CSV cosmetic fix** — unmatched rows write empty `Match Type` (was literal "None").

## Completed (v1.26.1651015)

- [x] **Dev tooling + CI** — pytest suite (`tests/`), GitHub Actions workflow, cross-platform `package_plugin.py`, `bump_version.py`, and a py-compile hook. Replaces the old `.wolf/test_matching.py` harness.
- [x] **Deduplicated channel databases** — removed 651 fully-identical rows across 7 country files (UK/MX/DE/CA/BR/FR/ES); all `*_channels.json` normalized to LF.
- [x] **Norwegian channel database** — `NO_channels.json` (94 channels) + `NO → norway` in `COUNTRY_DIR_MAP`. Coverage now 12 countries.
- [x] **`normalize_name` hardening (bug-048/051/055)** — stylized-Unicode decoration strip, emoji-as-letter (`beIN SP⚽RTS` → `SPORTS`), and numeric resolution markers (`720p`/`3840P`), ported byte-accurate from Stream-Mapparr. Adds `tests/test_normalization_port.py` regression locks + a CI-enforced corpus no-regression gate (0 ASCII-name changes across 42K names). Ported to all four `fuzzy_matcher.py` copies per the drift rule — see `docs/MATCHER-NORMALIZATION-PORT.md`.
- [x] **plugin.json manifest fix + parity guard** — corrected two button labels corrupted to `?` (→ ❖/ⓘ, matching plugin.py); `test_plugin_contract.py` now enforces exact button_label parity and rejects `?` placeholders.
- [x] **Dispatcharr/Plugins submission** — v1.26.1651015 submitted to the public registry (Dispatcharr/Plugins PR #128).

## Future Work

- [ ] **Improve "United States" category granularity** — A large share of matched M3U streams still lands in the "United States" catch-all category. Refine `US_channels.json` to assign specific genres (Entertainment, Sports, etc.) instead of "United States" for channels that have a clear genre.

- [ ] **Add UK/CA channel databases to default config** — M3U sources contain UK Entertainment, UK Kids, UK Sports groups. The `DEFAULT_CHANNEL_DATABASES = "US"` constant should likely become `"US, UK, CA"` to raise M3U import match rate out of the box. Existing users keep their current setting; only new installs would change.

- [ ] **Aliases expansion** — Look at common provider-naming patterns in user CSV exports' `No match` rows (especially Bloomberg/HLN/Telemundo variants, Sky tier names, regional sports). Add to `aliases.py`. Threshold: if it would take a 4+-char fuzzy reach to find, an alias is faster and safer.

- [ ] **EPG matching** — Lineuparr has `apply_epg_match` that fuzzy-assigns EPG channel names to lineup channels via `EPGSource`. Channel-Maparr currently has no EPG action — channels imported via M3U get no program-guide attachment. ~8-12hr port; needs country filtering, `tvg-id` parsing, fuzzy fallback for unmatched IDs.

- [ ] **Dynamic field discovery for `selected_groups` / `category_groups`** — Currently free-form text. Both could be auto-populated as multi-select dropdowns from `ChannelGroup.objects` in the `Plugin.fields` property (`m3u_sources` already does this). UX win — fewer typo errors.

- [ ] **`Réseau des Sports`-style aliasing for other parens-in-name channels** — The CA `(RDS)` pattern likely repeats: any DB entry where the official name has a parenthesized abbreviation (e.g. `Music Television (MTV)`, `Public Broadcasting Service (PBS)`) is unreachable from streams using just the abbreviation. Audit the country JSONs and add aliases.

- [ ] **PR #2 (`RedShieldArr`)** — Closed-out by v1.26.1430910 (alias support superseded). Remaining unique bits:
  - `_expand_ignored_tags()` DRY helper for the 4 duplicated bracket/paren expansion blocks.
  - **Debug Match Export** action + `debug_top_n` setting. Must route through `get_candidates()` + normalization cache (the PR's version bypassed the token pre-filter).
