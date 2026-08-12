# Channel Maparr — TODO

## Open (added 2026-08-10)

- [x] **Match a station by market city and channel number** (done 2026-08-12) - a name stating a market with no
  callsign, such as `US: FOX 13 HD [SEATTLE]`, resolved to nothing. `Channel-Maparr/market_index.py` now parses
  the market and channel number out of the name and resolves it against the station table in two stages: first
  among the stations licensed to that community, then, when that community has no station carrying the network,
  among the stations of that state on that channel number. A station is returned only when exactly one fits.
  The behaviour is behind the `ota_market_fallback` setting, "Match by Market When No Callsign", which defaults
  to OFF, and both OTA call sites in `plugin.py` pass it explicitly; an AST guard fails if a call site omits it.

  Measured on the live provider feed 2026-08-12, over the 1,190 distinct names in the nine network stream
  groups: 997 already matched by callsign, 193 did not, and the market stage resolves **61** of those 193 with
  none wrong on inspection. Stage one accounts for 53, stage two for the remaining 8 (Seattle to KCPQ in
  Tacoma, San Francisco to KTVU in Oakland, Las Vegas to KVVU in Henderson, Charlotte to WJZY in Belmont,
  Raleigh to WTVD in Durham, Palm Beach to WFLX, Gainesville to WOGX in Ocala, Greensboro to WGHP in High
  Point). Only three alias entries were needed, not the dozen first sketched, because stage two covers most
  neighbouring-community cases without one.

  Two refusals are deliberate and pinned by tests. A name carrying `PLUS` or `XTRA` is refused, because
  "FOX 9 PLUS" in Minneapolis is WFTC and not KMSP, so folding it into the main station would give two
  channels claiming to be the same one. And stage two runs only when the market city has no station carrying
  the network at all: when it has some that cannot be told apart, widening the search to the whole state
  returns a station in a third city. That was a real wrong match, `US: ABC 4 HD [CHARLESTON]` resolving to
  WOAY-TV in Oak Hill, found by running the module over the live corpus and fixed before it shipped.

- [ ] **An action that creates channels for streams that have none** - the work of creating 270 channels on
  2026-08-10 was done with throwaway scripts, not through the plugin. `Import M3U Streams` cannot do it: it
  creates one channel per stream, so a provider serving each station once per M3U account produces four suffixed
  channels per station, where the established layout is one channel holding the four streams. Plan at
  `docs/superpowers/plans/2026-08-10-create-channels-from-streams.md` (gitignored).

- [x] **`_extract_stream_network` ignored a prefix longer than three letters** (fixed 2026-08-12) - the
  pattern that strips a leading provider or country prefix before the colon accepted only two or three
  letters, so a name beginning `CITY: PBS KETC ST. LOUIS` read as stating no network and KETC took the FCC
  affiliation `ETV` rather than the `PBS` the stream states. The prefix pattern now accepts up to 12 letters,
  matching the pattern already used to read the network token itself. Measured on the live installation
  2026-08-12: four provider groups use a word as the prefix (`CITY` 820 streams, `PRIME` 3,033, `TUBI` 608,
  `NEXT` 26), and 1,257 stream names carry one of them directly ahead of a network name. Only 3 CHANNELS are
  affected today, because the rest were already renamed and no longer carry the prefix; the value of the fix
  is on future imports. `tests/test_ota_network.py` adds 11 name cases plus a guard that no entry in
  `_STREAM_NETWORKS` may exceed the 12 letter token pattern, since a longer one would silently stop being
  recognized as a prefix.

- [ ] **The supplemental station file can add but not correct** - `networks_supplemental.json` is loaded after
  `networks.json` with `setdefault`, so the main table always wins. A wrong record in the main table, such as
  WBMA-LD having no virtual channel, cannot be corrected without either a corrections file applied by
  `scripts/build_networks_json.py` or a precedence change.

- [ ] **The FCC affiliation field is not maintained to a standard** - measured on the 2026-08-10 dump: KTVU is
  recorded as `Independent` although it is a Fox owned station, WANF as `Independent` although it carries CBS,
  one record holds a web address, 26 records separate networks with semicolons, and `H&I` appears in 8. The
  rebuild script keeps a previous specific affiliation rather than accepting a vague one, which covered 15
  stations, but the underlying data stays unreliable and each new dump needs the report read.

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

## Completed (v1.26.1701952)

- [x] **OTA broadcast matching restored** — bundled `networks.json` (1,915-station US FCC table) loaded into `broadcast_channels` + `channel_lookup` via `FuzzyMatcher._load_broadcast_stations()` when US is selected. The `*_channels.json` DBs have no broadcast/callsign entries, so the previously-inert OTA pipeline (`ota_attempted` was always 0) now resolves local affiliates by callsign. Validated live: ABC 167, CBS 213, FOX 200, NBC 513 renames.
- [x] **Correct OTA network label** — `_extract_stream_network` honors the network a stream states (subchannels: `CBS 7 (WBBJ-DT3) → CBS …` not ABC); `_parse_network_affiliation` hardened for messy FCC strings (`CBS & FOX`, `CBS Ch 3.1`, `KALB/NBC`). CBS wrong-network outputs 23 → 0.
- [x] **Parenthesized-callsign override** — Priority 1 accepts a denylisted English word in parens when it's a real station (`(KING)`/`(WOOD)`/`(WAVE)`); unparenthesized prose still guarded. `tests/test_broadcast_ota.py` + `tests/test_ota_network.py`.

## Completed (v1.26.2170831)

- [x] **Publishes a report count for Newsflasharr's status readout** — `report_counter.py` writes
  `/data/channel-mapparr/report_count.json` containing `{"reports_built": N}` after every successful
  report build. Newsflasharr's Show Status action reads it and prints the count beside this plugin,
  which previously appeared in its list with no number. One increment is one successful BUILD, meaning
  both the HTML and the CSV were written; a build that failed to write does not increment. Written
  atomically, no lock, no fsync, never raises into the report path. `tests/test_report_counter.py`
  pins every condition the reader enforces silently, plus an AST guard on the single call site.
- [x] **Report page restyled to match the Dustarr plugin's presentation** — rows in a `<details>`
  section that starts collapsed, a CSS token layer with matching dark-mode values, a spacing scale,
  the logo embedded beside the title, and a footer crediting Newsflasharr. Fixed four defects in the
  old styling: literal colours inside rules, `opacity` used for text hierarchy, four `!important`
  overrides, and zebra striping declared for dark mode only so the two themes rendered visibly
  different tables. `tests/test_report_style.py` guards all of it and
  `tests/fixtures/sample_report.html` pins the rendered output, regenerated by
  `scripts/regen_report_fixture.py`.
- [x] **Adds one shipped asset, `logo_report.png`** — 192 pixels on its long edge, quantised palette,
  11.9 KB. The plugin card's `logo.png` is 216 KB and embeds as 288 KB, seven times the size of a
  whole report, on every emailed attachment. The card logo is untouched.
- [x] **Released and shipped everywhere** — GitHub release `1.26.2170831` with its zip, merged into
  the Dispatcharr Plugin Hub (standard mode), and deployed on the live installation. Repository,
  tag, container and Hub manifest all measured as agreeing.
- [x] **Documentation structure completed** — `docs/README.md` is the index GitHub renders for the
  `docs/` folder, organised by who is reading. The README's report screenshots were replaced with
  current ones rendered from the committed fixture, since the previous image showed a layout the
  plugin no longer produces.

## Future Work

- [ ] **Premium HD-tag idempotency (NBC Sports RSNs)** — a few channels (`NBC Sports California (D)` ⇄ `NBC Sports California HD (D)`, `… Bay Area HD` ⇄ `… (with Warriors)`) flip a cosmetic tag on re-run because the premium exact-match canonical differs from the post-rename name. Pre-existing; ~7 channels on US: NBC.

- [x] **OTA station-table coverage** (v1.26.2221915) - `networks.json` is rebuilt from the FCC Licensing
  and Management System by `scripts/build_networks_json.py` and grows from 1,915 to 6,839 stations,
  including the low power classes. Measured: 12 of 14 provider stream names that could not be matched
  before now resolve. WGCL is handled by `networks_supplemental.json` because the station renamed itself
  to WANF and the FCC no longer lists the old callsign. KXJB and WSHM resolve from the rebuilt table.

- [ ] **Non-parenthesized callsign affiliates** — formats like `SEATTLE, WA KING NBC 5` (callsign not in parens, denylisted word) still skip, since loosening the unparenthesized denylist would mis-read prose. Would need a market/city-aware heuristic.

- [ ] **Improve "United States" category granularity** — A large share of matched M3U streams still lands in the "United States" catch-all category. Refine `US_channels.json` to assign specific genres (Entertainment, Sports, etc.) instead of "United States" for channels that have a clear genre.

- [ ] **Add UK/CA channel databases to default config** — M3U sources contain UK Entertainment, UK Kids, UK Sports groups. The `DEFAULT_CHANNEL_DATABASES = "US"` constant should likely become `"US, UK, CA"` to raise M3U import match rate out of the box. Existing users keep their current setting; only new installs would change.

- [ ] **Aliases expansion** — Look at common provider-naming patterns in user CSV exports' `No match` rows (especially Bloomberg/HLN/Telemundo variants, Sky tier names, regional sports). Add to `aliases.py`. Threshold: if it would take a 4+-char fuzzy reach to find, an alias is faster and safer.

- [ ] **EPG matching** — Lineuparr has `apply_epg_match` that fuzzy-assigns EPG channel names to lineup channels via `EPGSource`. Channel-Maparr currently has no EPG action — channels imported via M3U get no program-guide attachment. ~8-12hr port; needs country filtering, `tvg-id` parsing, fuzzy fallback for unmatched IDs.

- [ ] **Dynamic field discovery for `selected_groups` / `category_groups`** — Currently free-form text. Both could be auto-populated as multi-select dropdowns from `ChannelGroup.objects` in the `Plugin.fields` property (`m3u_sources` already does this). UX win — fewer typo errors.

- [ ] **`Réseau des Sports`-style aliasing for other parens-in-name channels** — The CA `(RDS)` pattern likely repeats: any DB entry where the official name has a parenthesized abbreviation (e.g. `Music Television (MTV)`, `Public Broadcasting Service (PBS)`) is unreachable from streams using just the abbreviation. Audit the country JSONs and add aliases.

- [ ] **PR #2 (`RedShieldArr`)** — Closed-out by v1.26.1430910 (alias support superseded). Remaining unique bits:
  - `_expand_ignored_tags()` DRY helper for the 4 duplicated bracket/paren expansion blocks.
  - **Debug Match Export** action + `debug_top_n` setting. Must route through `get_candidates()` + normalization cache (the PR's version bypassed the token pre-filter).

## GUI / UX backlog (requested 2026-07-26)

- [ ] **Quick Start block at the top of the settings** â€” Mirror the pattern EPG-Janitor uses: a short
  "New here?" orientation paragraph naming the recommended run order, noting that every mutating
  action has a Preview/Dry Run to run first, and that long jobs continue in the background and are
  watched via Show Status. Must be written WITHOUT em dashes (use commas, semicolons, parentheses or
  a period). BMP-only characters, since Dispatcharr's loader silently drops an action containing any
  character above U+FFFF. EPG-Janitor's wording, kept here only as a shape reference:
  "Quick Start / New here? Typical workflow: 1) Validate 2) Scan Missing finds channels whose EPG has
  no program data 3) Preview Auto-Match, then Apply Auto-Match to assign EPG 4) Preview Heal, then
  Apply Heal to repair stale assignments. Every action that changes data has a Preview, run it first.
  Long jobs keep running in the background, click Status / Results to watch them."
  Channel-Maparr's own documented run order is: Validate Settings, Load and Process Channels, Rename
  Channels, Tag Unknown Channels, Apply Default Logo, Apply Per-Channel Logos (tv-logos), Organize by
  Category, Import M3U Streams, with Show Status and Clear CSV Exports as utilities.

- [ ] **Button in settings linking to the GitHub issues page**: a one-click way to file a bug or a
  feature request from inside Dispatcharr, pointing at
  `https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/issues`. Check first whether
  Dispatcharr's field/action schema can render a link or must use an action that returns the URL in
  its `message`; an action can only return text, so a true hyperlink may not be renderable.

- [x] **Remove the version checker** (DONE 2026-07-26): `Plugin.fields` performed a LIVE GitHub HTTP request
  (`_get_latest_version`) plus an ORM query every time the property is read, which is on Dispatcharr's
  per-request hot path, so plugin settings could not render without outbound network access and a
  slow or hung GitHub stalled the request. Removed: the check, the three helpers
  (`_get_latest_version`, `_should_check_for_updates`, `_save_version_check`), the
  `VERSION_CHECK_FILE` constant and cache, and both `urllib` imports. The "Plugin Version" field
  stays but is now static (`Installed: vX`). Pinned by
  `tests/test_plugin_contract.py::test_no_update_check_remains`. Note `logo_matcher.py` still uses
  the network for the tv-logos fetch, which is expected and runs inside an action rather than on the
  request path.

## Channel database refresh (measured 2026-07-26, do AFTER the ignore_groups slice ships)

- [ ] **Five country databases are stale; EPG-Janitor holds the newest data.** Channel-Maparr sits on
  `2025-11-10` / `2025-12-08` for AU, CA, ES, UK and US, while `EPG-Janitor/EPG-Janitor/*.json`
  carries `2026-05-17`. Net new channels after discounting duplicates: AU +126 (50 to 176, more than
  tripling), CA +107, ES +123, UK +45, US +171, so about **+572** genuinely new entries. The other
  seven databases (BR, DE, FR, IN, MX, NL, NO) are at the same version in every plugin.

  **Two traps, both measured:**

  1. **EPG-Janitor's newer files still contain 247 byte-identical duplicate rows** (CA 62, ES 17,
     UK 168). A straight file copy would fail this repo's own
     `tests/test_data_integrity.py::test_no_identical_duplicate_rows`. Re-run the dedup after
     importing, and keep the test as the gate.
  2. **Do NOT sync from Stream-Mapparr.** Its counts are higher at identical versions only because
     it still carries all 651 duplicate rows this repo removed on 2026-06-10 (BR 43, CA 62, DE 136,
     ES 17, FR 19, MX 206, UK 168). Channel-Maparr has zero duplicates in all 12 files; copying from
     Stream-Mapparr would be a regression, not an upgrade.

  `networks.json` needs no action: it is byte-identical to Stream-Mapparr's (1,915 FCC stations).

  Because +572 channels changes matching behaviour, give this its own version bump and its own
  before/after dry-run comparison rather than folding it into a feature release.
