# Emailing Channel-Maparr's CSV exports as reports via Newsflasharr

Status: design, approved in outline, not implemented.
Date: 2026-08-02.

Channel-Maparr writes CSV exports to `/data/exports` and there is no way to get one off the box
except by reading it inside the container. This design gives the plugin the same emailed-report
capability Stream-Mapparr has, using the Newsflasharr notification plugin as the delivery path.

## 1. Measured facts this design rests on

Everything in this section was read from the code or from live configuration, not assumed.

1. **Channel-Maparr has no scheduler.** A search for `schedule`, `shared_task` and `PeriodicTask`
   in `Channel-Maparr/plugin.py` returns nothing. Every action is a button press by the operator.
2. **Three code paths write a CSV**, all into `PluginConfig.EXPORT_DIR` = `/data/exports`:
   - `preview_changes_action` (`plugin.py:1191`), the channel-rename preview. Columns: Channel ID,
     Channel Number, Group, Current Name, New Name, Status, Matcher, Match Method, Reason.
     Writes atomically through a temporary file and `os.replace` at `plugin.py:1262`.
   - `category_groups_dry_run_action` (`plugin.py:1661`), the category-organization preview.
     Columns: Channel ID, Channel Name, Current Group, New Group, Category, Match Type, Match
     Value, Group Exists. Writes with a plain `open()` at `plugin.py:1829`, **not** atomically.
   - `_export_m3u_import_preview` (`plugin.py:2675`), which serves BOTH the M3U import dry run
     (called at `plugin.py:2841`) and the completed import (called at `plugin.py:2910`). Columns:
     Stream ID, Stream Name, M3U Source, Priority, Match Type, Match Method, Category, Target
     Group, Group Exists, Will Import, Notes. Writes atomically, `os.replace` at `plugin.py:2760`.
     It hardcodes the filename `channel_mapparr_m3u_import_preview_*` and the header text
     `# M3U Import Preview` for both callers.
3. **`organize_by_category_action` on a real run writes no CSV at all.** Only its dry-run branch
   does. Verified by searching `plugin.py:1880-2091` for `csv`: zero occurrences.
4. **Only `import_m3u_streams` runs in a background thread.** `_try_start_thread`
   (`plugin.py:521`) has exactly one call site, `plugin.py:2968`. The `"background": True` flag on
   `organize_by_category` at `plugin.py:466` is a manifest hint; the handler runs inside the uWSGI
   request. Under gevent a pure-Python loop that performs no input or output never yields, so it
   blocks the whole worker, not just its own request.
5. **Every CSV opens with a settings header** written by `_generate_csv_settings_header`
   (`plugin.py:552`). One of its lines is `M3U Sources: <value>`, which on this installation is a
   real provider hostname. This is the one confirmed leak in the export files.
6. **The CSV bodies are clean.** The `M3U Source` column is written as `M3U-<account id>`, an
   integer, never a name (`plugin.py:2727`, `plugin.py:2746`). No stream URL is ever fetched:
   `plugin.py:2117` selects only `id`, `name`, `m3u_account_id`, `channel_group_id`.
7. **The M3U account name list has one source**, `M3UAccount.objects.all().values('id','name')`
   at `plugin.py:255`, inside a bare `except Exception: pass`. That property is documented at
   `plugin.py:242` as running on Dispatcharr's per-request hot path.
8. **A persisted results file already exists** at `/data/channel_mapparr_loaded_channels.json`
   (`PluginConfig.RESULTS_FILE`, `plugin.py:98`). `preview_changes_action` (`plugin.py:1196`),
   `rename_channels_action` (`plugin.py:1298`) and `rename_unknown_channels_action`
   (`plugin.py:1361`) all replay it rather than re-querying the database.
9. **Newsflasharr's limits**, read from its source: attachment cap 1,048,576 bytes
   (`newsflasharr/attachment.py:13`, rejected when strictly greater); allowed extensions `.html`,
   `.htm`, `.csv` (`attachment.py:14`); delivery retry backoff 30 + 300 + 1800 seconds
   (`newsflasharr/deliver.py:17`), so 2130 seconds worst case.
10. **An attachment-bearing event bypasses three gates**: quiet hours (`gates.py:223`), the hourly
    send cap (`gates.py:258`) and duplicate collapsing (`gates.py:185`).
11. **This installation has no routing rule for `channel-mapparr`.** The live rules cover
    `dustarr`, `stream-mapparr` and `sentinelarr` only, each scoped by both source and event, and
    `default_channels` is `apprise`. Attachments are SMTP-only, so an unrouted report would be
    delivered to Apprise as text with no file, while the queue write succeeded.
12. **Newsflasharr's `report_expect` setting is blank**, so its older source-blind absence check is
    the one in force: `deliver.py:382` stamps one global `last_attachment_delivered_ts` on any
    successful attachment email from any plugin.
13. **The vendored notification client's canonical source is `_shared/notify_client.py`**, sha256
    `c7dac8c11b11630a0db78812c5434fa3ace747aa3a236b0cae2988fb57e37133`, byte-identical to
    `notifier/newsflasharr/notify_client.py` and to Stream-Mapparr's vendored copy. There is no
    file at `notifier/notify_client.py`.
14. **`scripts/sync_core.py` rebuilds `scripts/core_manifest.json` from scratch** (`sync_core.py:57`
    and `:76`) using only `SHARED_FILES = ["matching_core.py"]`. Any other key placed there is
    deleted on the next matcher-core re-vendor, and `tests/test_core_parity.py:27` parametrizes
    over the manifest's own keys, so the check would disappear with a fully green test run.
15. **`validate_settings_action` is errors-only** since commit `fe9e1ff`. It filters
    `validation_results` into error and warning lists by leading glyph (`plugin.py:3141`), compares
    the counts against separately maintained counters, logs a bookkeeping-drift warning on
    mismatch (`plugin.py:3146`), and returns a fixed success string when clean. A line carrying
    neither glyph renders nowhere and trips that warning on every run.
16. **`plugin.json` currently declares 15 fields and 10 actions.**
    `tests/test_plugin_contract.py:133` fails if a field id exists in the Python source but not in
    the manifest.
17. **`clear_csv_exports_action` (`plugin.py:3184`) deletes anything matching
    `channel_mapparr_*.csv` inside `EXPORT_DIR` only.**

## 2. Decisions taken

| Question | Decision |
|---|---|
| Which exports are emailable | All of them, subject to section 3 |
| Output formats | HTML and CSV, both purpose-built for email |
| Trigger | Automatic after a run that produces an export, plus a button |
| Default format | HTML only, so one run sends one email |
| Over the size cap | A fixed row cap applied once, plus a link to the complete file |
| What the button does | Rebuilds a fresh report from the persisted results file |
| `report_expect` on Newsflasharr | Raise it with the operator again at deploy time, change nothing now |

## 3. What emits, and what does not

| Path | Emits | Note |
|---|---|---|
| `preview_changes_action` | Yes | The rename preview |
| `category_groups_dry_run_action` | Yes | Dry run only, because the real run writes nothing |
| `organize_by_category_action`, real run | No | It produces no rows to report on |
| M3U import dry run (`plugin.py:2841`) | No | Avoids two emails for one import |
| M3U import completed (`plugin.py:2910`) | Yes | Titled "M3U import results", not "preview" |
| `email_report_now_action` | Yes | On demand, see section 7 |

Worst case for one working session is therefore three emails at the default format, or six if the
operator selects both formats. This matters because attachment-bearing events bypass quiet hours
and the hourly cap (measured fact 10), so the volume cannot be throttled from Newsflasharr's side.
The format field's help text must state the arithmetic.

The report title and mode are passed in by the caller. `_export_m3u_import_preview` serves two
callers with one hardcoded "preview" title, so the emit call goes at the two **call sites**, not
inside that function, and each passes its own title.

## 4. New file: `Channel-Maparr/notify_client.py`

A byte-identical copy of `_shared/notify_client.py`. Never hand-edited. When the shared file
changes the whole file is re-copied; editing the vendored copy is not a sanctioned direction, and
editing `_shared/notify_client.py` itself is a change affecting every caller plugin and needs
sign-off before it is made.

The sha256 goes in a new `scripts/client_manifest.json` with a new `tests/test_client_parity.py`,
copying Stream-Mapparr's pair exactly. It must **not** go in `scripts/core_manifest.json`
(measured fact 14).

Channel-Maparr does not hard-depend on Newsflasharr. With Newsflasharr absent, `notify()` returns
False and nothing raises.

## 5. New file: `Channel-Maparr/reports.py`

One generic table report builder used by every emitting path, rather than one per export.

```
build_model(title, columns, rows, account_names, settings, now) -> model
render_html(model) -> str
render_csv(model) -> str
write_report(model, report_dir, now) -> {"html_path", "csv_path", "error"}
```

### 5.1 The model is built by copying a named allow-list

`build_model` receives the **in-memory row lists** the actions already hold (`all_changes` at
`plugin.py:1245`, `moves` at `plugin.py:1843`, the matched and unmatched stream lists at
`plugin.py:2719`). It copies only the keys named in its column list.

`reports.py` never opens a path under `/data/exports` and never calls
`_generate_csv_settings_header`. This is the property that makes the settings-header leak
impossible by construction rather than by remembering to omit it, and it means a column added to
a CSV writer later cannot start being emailed on its own. Two tests pin it: one feeds a row
carrying an extra key and asserts the key is absent from both renderings, and one asserts the
module contains no `open(` and no reference to `EXPORT_DIR`.

### 5.2 What replaces the settings header

A fixed safe subset: plugin version, generation time in UTC, dry-run state, match sensitivity,
the **resolved** country codes the matcher actually loaded (not the raw `channel_databases`
setting string, which is a free-text field at `plugin.py:262`), and row counts.

Never included: `m3u_sources`, `selected_groups`, `ignore_groups`, `category_groups`,
`m3u_group_filter`, `m3u_category_filter`, `m3u_custom_group_name`, `ota_format`,
`unknown_suffix`, `ignored_tags`, `default_logo`. Two of those deserve naming as near-misses if
anyone later argues to keep them: `default_logo` is an operator-typed URL and is the most likely
route for a LAN address to enter this plugin, and `ignored_tags` on this installation already
contains a provider-specific group prefix.

The `# Ignore resolved to: {scope.info}` line the category writer emits at `plugin.py:1836` is
also not reproduced; it carries channel-group names.

### 5.3 Scrubbing, and it fails closed

Every free-text cell is scrubbed against the M3U account names, case-insensitively and
longest-match-first, and against IPv4 and IPv6 addresses. The logic is copied from
`Stream-Mapparr/reports.py` (`sanitise_stream_label`, `_scrub`, `_IPV4_RE`, `_IPV6_RE`).

**The account-name list is the primary redaction input here, not a backstop**, which is the
opposite of its role in Stream-Mapparr, where raw stream names never carried an account label.
So it must fail closed. A lookup that raises, or that cannot be performed, makes the emit return
`{"sent": 0, "skipped_reason": "the M3U account name lookup failed"}` and send nothing.
`build_model` rejects `account_names=None` rather than treating it as an empty list. The lookup is
a new read; it must not reuse the `fields` property at `plugin.py:255`, which is on the
per-request hot path.

An installation with genuinely zero M3U accounts is distinguished from a failed lookup and is
allowed to proceed.

### 5.4 Size handling: a fixed row cap, not a trim loop

A render-measure-drop-re-render loop is rejected. On a 17,000-stream import (the size
`ProgressTracker` at `plugin.py:193` is written for) that is thousands of re-serialisations of a
multi-megabyte string, inside a greenlet that never yields, which freezes the whole worker.

Instead: a constant `MAX_REPORT_ROWS`, applied once before rendering, in the order the action
produced the rows. When rows are dropped, both renderings carry one visible line stating how many
of how many rows are shown and naming the complete file, by its **container** path
(`/data/exports/<name>`), never a Windows mapping. The line names a count and a filename only,
never the M3U source or the group scope.

Every notification also carries `url=` alongside `attachment=`, holding the report's path inside
the container, so a recipient whose gateway strips the attachment still knows where the file is.
That path is not browsable from outside the container; it is a locator, not a link. It must never
be a Windows drive mapping, and the report directory is deliberately not under `/data/logos`,
which would be browsable but is served unauthenticated to the whole local network.

The initial value is 2000 rows. At the measured density of about 136 bytes per CSV row
(`channel_mapparr_NBC_preview_003833.csv`: 584 lines, 79,341 bytes) that is roughly 270 KB of CSV,
and the HTML is larger but still well inside the 1,048,576-byte cap. Size is never measured at
runtime; if the constant is ever raised, whoever raises it measures in **UTF-8 bytes of the
written file**, because the matcher supports Cyrillic and CJK names that are two to three times
longer in bytes than in characters.

### 5.5 Output location and pruning

Files go to `/data/channel_mapparr_reports/`, deliberately not under `/data/logos`, which
Dispatcharr's nginx serves unauthenticated to the whole local network. Filenames carry the run
timestamp and are never rewritten in place, because Newsflasharr re-reads the attachment path on
every delivery retry.

Pruning keeps the newest 8 of each extension but never deletes a file younger than 2400 seconds,
which covers the 2130-second retry ladder with margin. The filename prefix used by the pruner is
`channel_mapparr_report_`; copying Stream-Mapparr's prefix unchanged would make the pruner match
nothing and the directory grow forever with no error.

`clear_csv_exports_action` cannot reach this directory, because it only deletes inside
`EXPORT_DIR`. That is an invariant one refactor away from deleting an attachment out from under a
queued retry, so it gets its own test.

### 5.6 Other content rules

CSV cells beginning `=`, `+`, `-` or `@` are prefixed with an apostrophe so they are not evaluated
as formulas. HTML is escaped. The HTML footer states that the complete unredacted export lives in
`/data/exports` and is not emailed.

## 6. New file: `Channel-Maparr/notify_bridge.py`

The guard boundary. Adapted from `Stream-Mapparr/notify_bridge.py`; every public function returns
a failure rather than raising.

- `SOURCE = "channel-mapparr"`, `EVENT = "usage_report"`. That event name is the convention the
  other report senders on this installation already use, and it does not collide: every existing
  rule is scoped by source and event together.
- `is_enabled(settings)`, keeping Stream-Mapparr's string coercion at `notify_bridge.py:88`.
  Dispatcharr stores a checkbox as a string on some paths and `bool("false")` is True.
- `resolve_report_trigger(settings)` returns `"never"` or `"every_run"` only. The value
  `"scheduled"` is dropped because there is no scheduler. **The default is `"every_run"`**, and
  `_DEFAULT_TRIGGER` must be changed to match; leaving the copied `"scheduled"` default while
  removing it from the accepted set produces a value outside the enum and a silent always-emit or
  never-emit. The `is_scheduled` parameter of `should_emit` is removed rather than left as a
  vestige.
- `resolve_report_format(settings)` returns `"html"`, `"csv"` or `"both"`, **default `"html"`**.
- `routes_to_smtp(nf_settings)` parses Newsflasharr's `routing_rules`, which is stored as a JSON
  string rather than a list.
- `emit_reports(notify_fn, settings, written)` returns `{"sent", "skipped_reason"}`. `notify_fn`
  is injected so tests observe the call without a spool directory. Every notification carries
  `severity="info"` and no `dedup_key`: a report is not an incident and must not compete with a
  critical for bypass treatment.

Stream-Mapparr's `last_scheduled_run_ts` file is deliberately not copied. It exists to prove a
schedule is alive despite Newsflasharr's absence detector being unable to tell a scheduled send
from a button press. There is no schedule here, so the file would prove nothing.

## 7. Changes to `plugin.py`

### 7.1 Three new settings fields

Added to both `Plugin.fields` and `plugin.json`, or `tests/test_plugin_contract.py:133` fails.
These three ids are permanent: Dispatcharr never prunes a stored setting when its field is
removed, so a rename leaves the old value stored forever while the new id reads its default.

| id | type | default | notes |
|---|---|---|---|
| `notify_enabled` | boolean | `False` | Opting in is the operator's decision; a released plugin must not begin writing into another plugin's queue on upgrade |
| `notify_report_on` | select | `every_run` | Values: never, every run that produces an export |
| `notify_report_format` | select | `html` | Values: html, csv, both. Help text states that one notification carries one attachment, so "both" doubles the email count |

### 7.2 One new action

`email_report_now_action`, labelled "Email Report Now". It:

1. Refuses before doing any work, returning a red persistent `error`, when notifications are off,
   when Newsflasharr is absent or disabled, when its SMTP settings are incomplete, when no routing
   rule sends `channel-mapparr`/`usage_report` to SMTP, or when
   `notify_client.notifier_alive(base_dir)` reports the collector is not running. That last check
   is required: `notify()` creates the spool directory it writes into, so it returns True with the
   collector dead and the event then sits in a directory nobody reads.
2. **Builds a fresh report** by replaying `/data/channel_mapparr_loaded_channels.json`, the same
   file `preview_changes_action` replays. It does not re-send files already on disk: those are old
   enough to be prune-eligible, so a later run could delete one while its email was still being
   retried, and the attachment would silently vanish.
3. Returns a message saying **queued**, never sent. A True from `notify()` means durably spooled;
   delivery happens later on the retry ladder.
4. States in its own description that it proves nothing about the automatic path, because it runs
   in the web worker from the settings currently on screen.

It does not take the operation lock, because it runs no matching pass. It returns an `error` when
the results file does not exist.

### 7.3 The emit call sites

Three call sites, each after the export is confirmed written: `preview_changes_action`,
`category_groups_dry_run_action`, and the completed-import call at `plugin.py:2910`.

**Prerequisite change:** `category_groups_dry_run_action` at `plugin.py:1829` is converted to the
same temporary-file plus `os.replace` plus unlink-on-failure pattern the other two writers already
use. Without it there is no moment at which the export is confirmed written, and a failure
mid-write leaves a truncated CSV at the final path with no temporary file to clean up. This is a
change to an existing writer and is in scope; it does not change what the exports contain.

### 7.4 Surfacing the outcome

Only `message`, `error` and `file` render in Dispatcharr's plugin card. `status` renders nowhere,
`details` and `problems` render nowhere, and a toast shows roughly 280 characters clipped from the
middle with no ellipsis. So:

- Each emitting action appends a short clause to its existing `message`: `Report queued (1).` or
  `Report not sent: <reason>.` The whole message stays inside the toast budget.
- The automatic path runs the **same** readiness check as the button. When the route to email does
  not resolve, the action returns its normal `message` **and** sets the persistent `error` key, so
  the problem is not a four-second toast.
- Every emit outcome is logged at WARNING with its reason.
- `file` returns the report path.

The cases that must each be reported rather than passing silently: Newsflasharr absent; no routing
rule to SMTP; the attachment over the cap; the file missing or the extension rejected; the spool
full; `write_report` returning an error; and the account-name lookup failing.

### 7.5 Validate Settings

A **failed** route resolution, an unknown stored value in either select, or a failed account-name
lookup each add a line carrying the warning glyph and increment the warning counter. A healthy
configuration adds nothing, matching the errors-only design of that action. Adding a line with
neither glyph would render nowhere and would trip the bookkeeping-drift warning on every run.

## 8. Tests

| File | What it pins |
|---|---|
| `tests/test_reports.py` | The allow-list model, that the module opens no file, scrubbing including the fail-closed direction, the row cap and its banner, the formula guard, HTML escaping |
| `tests/test_notify_bridge.py` | Both directions of the toggle: nothing emitted when off, an emit observed when on. Enum resolution including unknown stored values. `severity`/`dedup_key`. |
| `tests/test_notify_readiness.py` | `routes_to_smtp` against wildcard rules, another plugin's rule, a malformed JSON string, and a missing key |
| `tests/test_notify_button.py` | Each refusal path returns `error`, and the success path says queued |
| `tests/test_notify_wiring.py` | An AST guard, in the shape of `tests/test_group_scope_wiring.py`: each of the three emit sites calls the helper, the count is pinned so a fourth writer cannot quietly skip it, and the guard carries synthetic self-tests so it cannot rot into a no-op |
| `tests/test_client_parity.py` | The vendored client matches `scripts/client_manifest.json`, and matches `_shared/notify_client.py` when present |
| `tests/test_plugin_contract.py` | Already enforces source-to-manifest field parity; the three new ids must appear in `plugin.json` |
| Report-directory isolation | `clear_csv_exports_action` cannot delete a file in `/data/channel_mapparr_reports` |
| Copy guards | No em dashes in operator-facing text; no contractions in code or comments |

Unit tests in isolation are not sufficient. Stream-Mapparr's own wiring test exists because an
earlier draft there built a report module that nothing ever called.

## 9. Deploy and verification

1. Take a backup before touching anything live.
2. Bump the version with `scripts/bump_version.py`, which writes both `plugin.json` and
   `plugin.py`. Hot reload keys on `plugin.json` mtime, so both must ship.
3. Add a `docs/CHANGELOG.md` entry.
4. Add `notify_client.py`, `notify_bridge.py` and `reports.py` to the hand-maintained module list
   in `.github/workflows/tests.yml`, or they are never compile-checked.
5. Run `python -m pytest -q` and `ruff check .`.
6. Run the publish audit before any push or release:
   `python ../.claude/skills/pre-publish-audit/audit_publish.py --ref main --rules .publish-audit.json`
7. Deploy from the git index, not a directory copy:
   `git -c core.autocrlf=false -c core.eol=lf archive HEAD:Channel-Maparr | docker exec -i dispatcharr sh -c 'tar -x -C /data/plugins/channel-mapparr'`
8. `docker exec dispatcharr chown -R dispatch:dispatch /data/plugins/channel-mapparr`, then verify
   by effect: `docker exec dispatcharr find /data/plugins/channel-mapparr ! -user dispatch` must
   print nothing. This is mandatory, and it matters more than usual here because the feature adds
   a new runtime write path, `/data/channel_mapparr_reports/`, which must also be writable by
   `dispatch`.
9. **Ask the operator before restarting the container**, and immediately before the restart write
   the reason and who is doing it to PID 1's stdout.
10. Run the declared-versus-served gate in the container. Assert **18 fields declared == 18
    served** and **11 actions declared == 11 served** through the real `_normalize_fields` and
    `_normalize_actions`, and confirm the three new field ids appear in the normalized list rather
    than only checking the counts. Two of the three are selects, and a malformed select is dropped
    with only a `logger.warning` and pinned to its default forever.
11. Trigger one real emit and confirm the ledger row in `/data/newsflasharr/notifications.jsonl`
    **and** the actual arrival in the inbox. A green result proves only that the action returned.

## 10. Operator actions outside this repository

None of these are code, and none are done by this change.

1. **Add a routing rule in Newsflasharr** matching `source=channel-mapparr` and
   `event=usage_report`, sending to `smtp`. Scope it by event, not by source alone. Without it the
   report falls through to `default_channels`, which is `apprise`, and attachments are SMTP-only,
   so every report would be delivered as text with no file while the queue write succeeded.
2. **Do NOT arm `report_expect_days` for this caller.** That detector measures cadence, and this
   caller has no cadence.
3. **Populate `report_expect` for the existing report senders** before switching Channel-Maparr's
   notifications on. It is currently blank, so Newsflasharr's older source-blind check is in force
   and `dustarr`, `stream-mapparr` and `sentinelarr` all sit behind one shared timestamp. A
   Channel-Maparr report would keep that timestamp permanently fresh and hide any of the three
   going silent. This is a change to another project's configuration and is to be raised with the
   operator again at deploy time, not made now.
4. **Reload the Newsflasharr settings page before touching its interface.** A stale tab re-POSTs
   its old form state on any button click and silently reverts rules.
5. **Verify the routing with the `show_routing` action**, not by reading the rule text.

## 11. Out of scope

No scheduler and no Celery task. No change to what the existing `/data/exports` CSVs contain; the
only change to an existing writer is making the category writer atomic. No writes to any project
other than Channel-Maparr.

## 12. Repository hygiene completed on 2026-08-02, before this design was written

Not part of the feature, but done first because the feature's whole premise is that the provider
hostname must not leave the box, while the same string was one `git add -A` from leaving by git.

- `.gitignore` widened to `channel_mapparr_*.csv` and `channel_mapparr_*.json`. Seven stale export
  files at the repository root, each carrying the provider hostname on its settings-header line,
  were moved out of the repository. No tracked file became ignored.
- `.publish-audit.json` created, adapted from Stream-Mapparr's, with each pattern written using a
  character class so the rules file does not itself contain what it looks for.
- Every deny rule was proved to fire by planting a file containing each forbidden string at a
  scanned path and watching all seven report it. The planted file was then removed.
- The audit on the current tree reports one deny-rule hit and four medium built-in hits, all
  pre-existing and all already pushed to the public remote. They are recorded in section 13.

## 13. Pre-existing findings, open

- `CLAUDE.md:202` contains this machine's Windows drive mapping of the container volumes. It was
  introduced by commit `0d05faf` and `origin/main` is at the same commit as local `HEAD`, so it is
  already published on `PiratesIRC/Dispatcharr-Channel-Maparr-Plugin`. A history scan with the
  pickaxe found this string in that one commit and found no occurrence of any other denied string
  anywhere in history.
- `docs/MATCHER-NORMALIZATION-PORT.md` lines 43, 65, 360 and 384 carry absolute developer machine
  paths. Flagged by the built-in patterns, not by a deny rule, and also already published.
- `CLAUDE.md` is tracked in this repository, unlike several siblings where it is ignored. Whether
  it should be published at all is an open question for the operator.

## 14. As built, on 2026-08-02: where the implementation differs from the design above

Recorded so the design and the code do not quietly disagree.

1. **The vendored client's hash is not the one this design first recorded.** Two reviewers
   reported `c7dac8c1...` for `_shared/notify_client.py`; the measured value when the file was
   actually copied was `605531...`, matching Stream-Mapparr's committed pin. The shared file was
   then changed twice more during the implementation session by something outside it, and every
   sibling plugin's vendored copy was updated in step. The pin now records
   `97ea354b44550f5906801ab43226494143e182b03d13ed78e95bf5261b204add`. The parity test caught each
   change, which is what it is for, but the moving target is worth knowing about: coordinate before
   assuming a pin value from a document.
2. **Validate Settings does surface warnings.** Section 7.5 read as though a warning would not
   reach the operator. It does: `validate_settings_action` includes warning lines in its success
   message. The real constraint is narrower and is what the code follows: a line must start with a
   recognised glyph, or it is dropped from the output and trips the bookkeeping-drift warning.
3. **The new field labels carry no icon.** An envelope and paperclip emoji were written first;
   both are outside the Basic Multilingual Plane, which Dispatcharr's loader rejects, and
   `tests/test_plugin_contract.py::test_plugin_action_labels_are_bmp_only` caught it. The action
   button uses the Basic Multilingual Plane envelope instead, and the field labels are plain text,
   which also matches every other field in this plugin.
4. **The M3U report allow list is narrower than the export.** The "M3U Source" and "Group Exists"
   columns are not carried into the report. The first is written as an account id rather than a
   name and so does not leak, but an allow list is only worth having if it stays narrow.
5. **Still outstanding, deliberately not done:** `group_scope.py` and `wildcard_match.py` are
   absent from the hand-maintained compile-check list in `.github/workflows/tests.yml`. That
   predates this change and fixing it would widen the scope of this one.

## 15. Correction after the first real delivery, 2026-08-02

Section 5.4 said every notification should carry `url=` holding the report's path inside the
container, "a locator, not a link", so a recipient whose gateway stripped the attachment would
still know where the file is. Two of the four design reviewers recommended it, citing the caller
contract's advice to pair `url` with `attachment`.

**That was wrong, and the first real delivery proved it.** The operator received the email with
`/data/channel_mapparr_reports/channel_mapparr_report_20260802_191252.html` rendered as a
hyperlink. Newsflasharr's email template renders `url` as a link, so calling it a locator changed
nothing about how it reached the reader: it arrived as a link to a path that exists only inside
the container and cannot resolve from a mail client.

Corrected in `1.26.2141418`: no `url` is sent at all. The same information is stated as plain
text in the notification body, where no mail client turns it into a link. `notify_bridge.py`
gains a named `REPORT_LOCATION` constant, and two tests pin both halves.

**The general lesson, which is why this is recorded rather than quietly fixed:** a field's meaning
is decided by what RENDERS it, not by what the caller intends. "It is a locator, not a link" was a
statement about intent that the receiving template was never going to honour. Nothing may go in a
`url` field unless it is reachable from wherever that field is displayed.

## 16. Addition on 2026-08-02: the HTML table sorts

Requested by the operator. Section 5 said nothing about sorting; the report table is now sortable
by clicking or keyboard-activating a column heading, with numeric columns compared as numbers.

The request was made on the basis that Stream-Mapparr already did this. Measured, it does not:
`Stream-Mapparr/Stream-Mapparr/reports.py` contains no sorting code, and
`Stream-Mapparr/tests/test_reports_render.py` asserts its report page contains no script element
at all. This plugin therefore deliberately differs from that sibling, and the operator was told so
before it was built.

Constraints kept: the script is embedded with no external request of any kind, so the page still
resolves nothing when opened from a file path or a mail attachment; and every row remains in the
markup, so a mail client that strips scripts shows the full table and only loses the ability to
reorder it.

**The sorting is verified by EXECUTING it**, not by asserting a script tag exists.
`tests/report_sort_harness.js` builds a minimal document model from the real rendered page and
runs the shipped script under Node. It is skipped when Node is absent, and a companion test feeds
it a script that does nothing and requires it to fail, so the harness cannot rot into something
that always passes.
