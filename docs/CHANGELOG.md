# Channel Maparr — Changelog

## v1.26.2170811 (August 5, 2026)

**The HTML report looks different. It says exactly the same things.** No column was added,
removed or renamed, and no number changed. This is presentation only.

- **The rows now sit in a section that starts collapsed.** The page opens as an index: the title,
  the run summary, then one closed section headed with a coloured dot, the word Results and the
  row count. Click the heading to expand it. The section uses the browser's own disclosure
  element rather than JavaScript, so a mail client that does not support it shows everything
  expanded instead of hiding it. Because a collapsed section is invisible to find-in-page in some
  browsers, the section says so.
- **Every colour now comes from a named token, with a matching value for dark mode.** Four
  consequences you may notice: the light and dark themes are now consistent with each other;
  **zebra striping on table rows now appears in both themes**, where before it was declared for
  dark mode only and the two themes rendered visibly different tables; text that used to be faded
  with transparency now uses a measured grey, which keeps its contrast readable whatever it sits
  on; and spacing comes from one scale instead of fourteen hand-picked values.
- **The logo now appears beside the title**, embedded in the page so it still shows when the
  report is opened from disk or read as an email attachment. **This adds one file to the plugin,
  `logo_report.png`**, which is a smaller copy made for this purpose. The plugin card's own logo
  is unchanged. The report grows by about 16 KB; embedding the existing card logo would have
  added 288 KB, which is seven times the size of a whole report.
- **A footer links to the project and its issue tracker.** Nothing is fetched from the network:
  the page requests no stylesheet, font or image from anywhere, exactly as before.

The report is still one self-contained file, still sortable by clicking a column heading, and
still carries the same note about what is and is not included in it.

## v1.26.2170748 (August 5, 2026)

**Channel Maparr now publishes a count of the reports it has built, so the Newsflasharr plugin's
Show Status action can display it.** Newsflasharr already lists every plugin that sends it
reports; until now this plugin appeared in that list with no number beside it, because it did not
write the file the number comes from.

- **New file: `/data/channel-mapparr/report_count.json`**, containing one key, `reports_built`,
  holding one non-negative integer. It is written atomically, so a reader never sees a partial
  file, and it is private to the account the plugin runs as.
- **The number counts report BUILDS, not emails and not deliveries.** One increment means one HTML
  file and one CSV file were both written to disk. Two consequences worth knowing: if the report
  format is set to Both, one build sends two emails, so this number will be half the number of
  emails you received; and a report that was built but whose email later failed still counts,
  because the file was written.
- **A build that failed to write does not increment it.** That is the point of the file. The
  report writer reports a failure rather than raising, so without this rule a failed publish would
  be indistinguishable from a successful one.
- **Nothing else changes.** The counter cannot fail a report run: if it cannot be written, the
  report is still built and the email is still queued, and the reason is written to the
  Dispatcharr log as a warning.

Note for anyone verifying this by hand: do not create `/data/channel-mapparr` yourself.
`docker exec` runs as root by default, and a root-owned directory there can never be written by
the plugin afterwards. Let the plugin create it.

## v1.26.2141433 (August 2, 2026)

GitHub release: https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin/releases/tag/1.26.2141433

This release adds emailed reports, delivered by the Newsflasharr plugin, as an HTML page and a
CSV built specifically for sending. It also documents the plugin properly for the first time: a
task-oriented user guide, a disclaimer, and a screenshot of what an emailed report looks like.
The three entries below cover the work in the order it was done.

### Documentation

- **New [user guide](USER-GUIDE.md)**, task-oriented rather than reference: a first run, what Dry
  Run actually changes, how to scope which channels are touched, how broadcast station names are
  built, what to do when a channel will not match, and a full walkthrough for setting up emailed
  reports including the Newsflasharr routing rule that is otherwise invisible when missing.
- **The README gained a Documentation table, a Disclaimer, and a screenshot** of an emailed report
  built from invented data.
- **Corrected a stale README entry**: it documented a version cache file at
  `/data/channel_mapparr_version_check.json`. The self-update check was removed in v1.26.2071908
  and nothing writes that file now. Any copy on your installation is left over and can be deleted.
- The Settings Reference, action list and File Locations sections now include the emailed-report
  settings, the **Email Report Now** action, and `/data/channel_mapparr_reports/`.

### Sorting

**The HTML report's table can now be sorted by clicking a column heading.** Requested by the
operator. Click a heading to sort by it, click again to reverse, or move focus to it and press
Enter. An arrow on the heading shows which column is sorted and in which direction.

- **Numbers sort as numbers.** Channel Number 10 comes after 2, not before it, which is what a
  plain text comparison would give. Text sorts ignoring letter case.
- The comparison reads a `data-v` attribute carrying the same value the cell displays, so what is
  sorted is always what is shown.
- **The script is embedded in the page and requests nothing from anywhere.** The report is opened
  from a file path or from a mail attachment, where an external request would not resolve and
  would disclose that the report had been opened.
- **Sorting is an addition, not a requirement.** Every row is in the page markup, so a reader
  whose mail client strips scripts still sees the whole table and simply cannot reorder it. The
  page says so in its own footer. In practice sorting works when the attachment is saved and
  opened in a browser, which is the ordinary way to read an HTML attachment.

**The sorting is tested by running it, not by looking at it.** A test that only checks a script
tag is present would pass for months while proving nothing. `tests/report_sort_harness.js` builds
a minimal document model from the real rendered page and runs the shipped script in Node, then
checks the row order that comes out. It is skipped when Node is absent, so it is a local safety
net rather than a build gate, and `tests/test_reports.py` still covers the markup everywhere. The
harness carries its own control: a second test feeds it a script that does nothing and requires
it to fail.

**Note on the request.** This was asked for on the basis that Stream-Mapparr already does it.
Measured: Stream-Mapparr's report is not sortable, and `Stream-Mapparr/tests/test_reports_render.py`
actively asserts its report page contains no script element at all. This plugin now deliberately
differs from that sibling.

## v1.26.2141418 (August 2, 2026)

**The emailed report no longer sends a hyperlink that cannot be opened.** Reported by the
operator on the first real delivery: the email arrived with
`/data/channel_mapparr_reports/channel_mapparr_report_20260802_191252.html` rendered as a
clickable link. That path exists only inside the Dispatcharr container, so clicking it from a
mail client does nothing.

The cause was a design decision made in the previous release. The report's path was passed as
the notification's `url` field, on the reasoning that it was a locator rather than a link.
Newsflasharr's email template renders `url` as a hyperlink, so that distinction did not survive
contact with a real mail client.

- No `url` is sent now. Nothing goes in that field unless it is genuinely reachable from an
  inbox.
- The same information is stated as plain text in the notification body instead: the attachment
  filename, then `Kept in /data/channel_mapparr_reports inside the container.` No mail client
  turns that into a link.
- Two tests pin the behaviour, one asserting no `url` is sent and one asserting the body still
  names both the file and its directory. Both were checked by reintroducing the defect and
  confirming they fail.

Nothing else changed. The attachment itself, the report content and the redaction rules are
unaffected.

## v1.26.2141319 (August 2, 2026)

**Reports can now be emailed, using the Newsflasharr plugin as the delivery path.**
Channel Maparr writes its CSV exports to `/data/exports`, and until now there was no way
to get one off the box.

- Three new settings. **Send notifications to Newsflasharr** is the master toggle and is
  **off by default**, because a released plugin must not start writing into another
  plugin's queue the moment it is upgraded. **Email A Report After** chooses between
  `never` and every run that produces an export. **Email Report Format** chooses HTML,
  CSV, or both; the default is HTML alone, so one run sends one email.
- One new action, **Email Report Now**, builds a report from the last processed channels
  and queues it. It refuses, in the persistent red area of the plugin card, when
  Newsflasharr is absent, disabled, missing email settings, missing a routing rule for
  this plugin, or when its collector is not running. It says *queued*, never *sent*:
  `notify()` returning true means durably written to Newsflasharr's queue, not delivered.
- The emailed report is a **new, purpose-built pair of files** in
  `/data/channel_mapparr_reports`, an HTML page and a CSV. The exports in `/data/exports`
  are unchanged and are never emailed.

**The emailed report cannot carry your provider's hostname, by construction.**
Every CSV export opens with a settings header naming the configured M3U sources, which on
a real installation is the provider hostname, and Newsflasharr sends an attachment
verbatim and unredacted.

- `reports.py` builds its model by copying a named allow list of columns out of the rows
  the actions already hold in memory. It never opens an export file, which is pinned by a
  test that walks the module's syntax tree. A column added to a CSV writer later therefore
  cannot start being emailed on its own.
- The settings header is replaced by a fixed safe subset: plugin version, generation time
  in UTC, dry run state, match sensitivity, the country databases actually resolved on
  disk, and row counts.
- Every free text cell is scrubbed of the M3U account names, case insensitively and
  longest match first, and of IPv4 and IPv6 addresses.
- That scrub **fails closed**. If the M3U account lookup raises, no report is built at
  all, rather than one whose redaction was a silent no operation. These names are the
  primary redaction input here, not a backstop.

**Which runs report, and why the list is shorter than it looks.**

- The channel rename preview reports. The category organization preview reports. A
  completed M3U import reports.
- **Organize by Category reports only in Dry Run**, because a real run of it writes no
  export at all. This is stated in the setting's help text rather than left to surprise.
- **The M3U import dry run does not report**, so one import produces one report rather
  than two.
- An audit of the syntax tree pins this list, so a fourth export writer added later cannot
  quietly skip its report.

**Size handling is a fixed row cap, not a trimming loop.** An M3U import can carry
seventeen thousand rows, and three of the paths that build a report run inside the web
request, where a pure Python loop performs no input or output and so never yields. Under
the server's async model that would freeze the whole worker. The cap is applied once, and
both renderings carry a visible line saying how many rows of how many are shown and naming
the complete file.

**The category organization export is now written atomically**, through a temporary file
and a rename, like the other two export writers already were. A plain write left a
truncated file at the final path when it failed part way, with no temporary file to clean
up, and that truncated file was the one an operator would later believe was complete.

**Validate Settings reports emailed-report problems**, using the warning glyph so the
lines actually reach the operator: an unrecognised stored value in either new select, a
Newsflasharr configuration that could not deliver, and a failing M3U account lookup.

**Newsflasharr must be configured on its own settings page for any of this to arrive.**
A routing rule keyed on source `channel-mapparr` and event `usage_report`, sending to
email. Without it the queue write succeeds and the mail is delivered somewhere else and
without its attachment. **Do not** set Newsflasharr's report-absence expectation for this
plugin: that detector measures cadence, and this plugin has no schedule.

**Repository hygiene, done in the same change.** Plugin export files at the repository
root were untracked but not ignored, and each carried the provider hostname; the ignore
rules were widened and the files removed. `.publish-audit.json` was added and every one of
its deny rules was proved to fire against a planted test file. Developer machine paths
were removed from the tracked notes.

## v1.26.2071908 (July 26, 2026)

**Validate Settings reports only what needs acting on.** It previously returned its
entire readout, so every failure parked a wall of mostly-OK lines permanently under the
settings form (Dispatcharr renders `error` persistently on the plugin card and `message`
as a transient toast).

- A failure now returns the failing lines and nothing else, in `error`. No `DB OK`, no
  `Dry Run: ON`, no OK lines at all.
- A clean run returns a short toast and leaves nothing behind:
  `All settings validated successfully.` If an exclusion is configured it adds what that
  exclusion actually resolved to, since confirming that is the reason it is reported here.
- Warnings are not failures, so they ride in the success toast rather than creating a
  persistent card.
- Severity is read from the glyph each report line is built with, now named by
  `_VALIDATION_ERROR_GLYPH` / `_VALIDATION_WARNING_GLYPH`, and a mismatch between a
  counter and its lines is logged rather than silently mis-reported.

**Removed the plugin's self-update check.** `Plugin.fields` is read on Dispatcharr's
per-request hot path, and it made a live call to GitHub's releases API (plus a `/data`
cache write) every time the settings page was rendered. Plugin settings therefore could
not display without outbound network access, and a slow or unreachable GitHub stalled the
request.

- Removed the check itself, the three helpers (`_get_latest_version`,
  `_should_check_for_updates`, `_save_version_check`), the `VERSION_CHECK_FILE` constant
  and its `/data` cache, and both `urllib` imports from `plugin.py`.
- The **Plugin Version** field stays, and now simply reports `Installed: vX.Y.Z`. Operators
  still see what is installed; the plugin no longer has an opinion about what is newest.
- `tests/test_plugin_contract.py::test_no_update_check_remains` fails the build if any of
  that machinery returns to `plugin.py`.
- Unrelated but worth knowing: `logo_matcher.py` still uses the network for the tv-logos
  file list. That is expected, and it runs inside an action rather than on the request path.

## v1.26.2071409 (July 26, 2026)

**New setting: "Channel Groups to Ignore"** - process every group except the ones you name.
Requested by a user running Teamarr, which owns its own static channel group.

- Comma-separated, supports `*` and `?` wildcards, case-insensitive. Composes with
  "Channel Groups to Process" (include first, then subtract), so leaving that blank and
  ignoring one group gives "everything except that group".
- **Enforced everywhere it has to be**, not just where it was easy: the five channel-fetch
  sites, the two actions that replay a persisted results file without fetching channels
  (Rename Channels, Tag Unknown Channels, and the dry-run preview - a stale results file
  produced before the exclusion was set is still filtered, not just a live query), and the
  two write directions that could otherwise create channels *into* an ignored group
  (Organize by Category's target groups, and Import M3U Streams' destination, which now
  refuses the whole run rather than skip the one target). That asymmetry (Organize skips and
  continues, Import refuses the run) is deliberate: Organize is one write per category target,
  Import is one run per destination group.
- **Does not apply to Import M3U Streams' stream matching or duplicate detection.** Those
  read channel names in ignored groups to decide what already exists, but never write to
  them, so the exclusion has nothing to guard there.
- **Fail-closed on a typo.** An entry matching no group refuses the run rather than
  degrading to "process everything" - silent damage to the channels you were protecting is
  the failure this setting exists to prevent. A stray comma reads as blank, not as an
  unmatched entry. Validate Settings reports the resolved exclusion.
- **Organize by Category now also organizes channels that are in no group.** Previously they
  were silently skipped by the same `if group_ids:` gap that bug-044 fixed for the logo
  actions in the prior release. This is a real behaviour change for other installs; on this
  box it is inert (0 of 1440 channels are currently ungrouped).

**Divergence from EPG-Janitor, deliberately:** EPG-Janitor raises when both its group filters
are set, though its help text says the ignore list is applied after the include filter.
Channel-Maparr implements the help text as written. See the spec's follow-ups.

## v1.26.2071035 (July 26, 2026)

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

## v1.26.1930617 (2026-07-12)

Vendor-sync of the shared matcher core (`matching_core.py`) with the **bug-105** zero-width Unicode strip landed in the workspace source.

### Matching (core)

- **Invisible Unicode format characters are now stripped from names before matching.** Some IPTV providers pad names with category-`Cf` characters (ZERO WIDTH SPACE `U+200B`, the zero-width joiners, WORD JOINER `U+2060`, BOM `U+FEFF`, SOFT HYPHEN, bidi marks), typically wrapped around a decorative block glyph, so a name that renders as `UK | BBC 1` actually carries several invisible characters. `\s` does not match them and the decorative-symbol pass does not cover them, so they survived the entire `normalize_name` pipeline and silently destroyed the match rate for that provider's names. They are now removed (not replaced with a space, since they are zero-width, so a ZWSP inside a word does not split it) at the top of `normalize_name`.
- **No behavior change for names without these characters.** The matcher golden baseline is unchanged, since the test corpus contains no `Cf` characters. The vendored core stays byte-identical to the workspace canonical source (sha256 `aa5c8c647e19…`), keeping the parity + hash-pin gates green.

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

- Migrated from HTTP API pattern to Django ORM (see `docs/MIGRATION_GUIDE.md`)
- Removed credential fields (`dispatcharr_url`, username, password)
- Added WebSocket notifications via `send_websocket_update()`
- Added M3U stream import with category-based organization

## v0.6.0a (2025)

- Initial release with HTTP API pattern
- OTA broadcast and premium/cable channel matching
- 11 country channel databases
