# Channel Mapparr user guide

This guide is task-oriented: it walks through doing things. For a field-by-field reference, see
the **Settings Reference** table in the [README](../README.md).

- [What this plugin does, and what it will not do](#what-this-plugin-does-and-what-it-will-not-do)
- [Your first run](#your-first-run)
- [Dry Run Mode, and why to leave it on at first](#dry-run-mode-and-why-to-leave-it-on-at-first)
- [Choosing which channels are touched](#choosing-which-channels-are-touched)
- [How broadcast station names are built](#how-broadcast-station-names-are-built)
- [When a channel does not match](#when-a-channel-does-not-match)
- [Emailed reports, end to end](#emailed-reports-end-to-end)
- [Reading a report](#reading-a-report)
- [Where everything is written](#where-everything-is-written)
- [Fixing things by symptom](#fixing-things-by-symptom)

---

## What this plugin does, and what it will not do

It renames and organizes channel entries that already exist in your Dispatcharr installation. It
compares each channel's current name against bundled reference data and proposes a standardized
name.

It does **not** create channels out of nothing, fetch any stream, or contact any provider. The one
exception to "no outbound connections" is **Apply Per-Channel Logos**, which downloads a public
list of logo filenames from GitHub.

Two things surprise people:

- **Renaming does not change what a channel plays.** It changes the display name only.
- **Nothing happens until you press a button.** There is no schedule and no background sweep. If
  you want a report on a cadence, you have to press the button on that cadence.

## Your first run

Do this once, in order, with **Dry Run Mode on**.

1. **Validate Settings.** It checks database connectivity and your settings, and reports only
   problems. A clean run says so briefly and leaves nothing behind. If it reports an error, fix
   that before going further; the later actions depend on the same settings.

2. **Load & Process Channels.** This is the matching pass. It reads your channels, matches each
   one, and writes what it decided to a file. It changes nothing. Everything after this step
   replays that file rather than re-matching, which is why it comes first.

3. **Rename Channels.** With Dry Run Mode on this writes a CSV preview to `/data/exports` instead
   of renaming anything. Open it and read it. This is the moment to catch a bad match, and it is
   much cheaper than undoing renames afterwards.

4. Once the preview looks right, turn **Dry Run Mode off** and run **Rename Channels** again to
   apply it.

The remaining actions (tagging unknowns, logos, category organization, M3U import) are independent
of each other. Run them when you want them, and preview each in Dry Run first.

## Dry Run Mode, and why to leave it on at first

Dry Run Mode is a single switch that changes what the mutating actions do: instead of writing to
the database, they write a CSV describing what they *would* have done.

Two details worth knowing:

- **The preview is generated from the same decisions the real run would use**, not from a separate
  code path, so what you see is what you would get.
- **Organize by Category behaves differently from the others.** Its Dry Run writes a preview CSV;
  its real run writes no CSV at all. That matters if you have emailed reports on, because Organize
  can only email a report in Dry Run.

## Choosing which channels are touched

There are two settings, and they solve opposite problems.

**Channel Groups to Process** is an allow list. Name the groups you want touched; leave it empty
for all groups.

**Channel Groups to Ignore** is a deny list, and it wins over the allow list. It supports `*` and
`?` wildcards and is case-insensitive, so `PPV*` excludes `PPV Events` and `PPV Extra` together.

Three behaviours are deliberate:

- **A token that matches no existing group refuses the run.** A typo like `Locls` stops the action
  with an error rather than quietly processing everything. This is on purpose: the failure mode it
  prevents is an ignore list that silently does nothing.
- **It applies to writes as well as reads.** Organize by Category skips an ignored destination
  group and carries on; Import M3U Streams refuses to run at all if its destination group is
  ignored.
- **It does not filter Import's stream matching or duplicate detection.** Those look at streams,
  not channel groups.

Run **Validate Settings** after setting it. It reports what the exclusion actually resolved to,
which is the only way to confirm a wildcard matched what you expected.

## How broadcast station names are built

For United States local affiliates, the plugin matches on the station callsign rather than on the
channel name, using a bundled table of 1,915 stations.

Given a stream called `ABC 5 (WEWS) CLEVELAND HD`, it extracts `WEWS`, looks it up, and renders the
name using your **OTA Channel Name Format**. The default format produces
`ABC - OH Cleveland (WEWS)`.

Available tags are `{NETWORK}`, `{STATE}`, `{CITY}` and `{CALLSIGN}`. A channel missing a field is
skipped rather than rendered with a gap.

Two refinements that matter in practice:

- **The network comes from the stream's own label when it states one.** So `CBS 7 (WBBJ-DT3)`
  becomes `CBS - TN Jackson (WBBJ)` rather than taking the parent station's network, which is how
  subchannels resolve correctly.
- **Callsigns that are also ordinary words are handled carefully.** `KING`, `WOOD` and `WAVE` are
  real stations, so `(KING)` in parentheses is accepted, while the words appearing loose in prose
  such as `King of the Hill` are not treated as callsigns.

## When a channel does not match

Work through these in order; the first is by far the most common.

1. **Lower the Match Sensitivity.** Strict (90) rejects a lot. Normal (80) is the usual working
   setting, Relaxed (70) more permissive at the cost of wrong matches.
2. **Check the country databases.** A channel from a country you have not loaded cannot match.
3. **Check your Ignored Tags.** Provider decorations such as `[HD]` are stripped before matching.
   If a provider uses an unusual marker, add it.
4. **Add an alias.** If one specific channel is repeatedly wrong, adding it to `aliases.py` makes
   it match exactly and instantly, ahead of any fuzzy scoring. This is the right fix for a name the
   fuzzy matcher will never get right, such as an abbreviation the normalizer strips.

Unmatched channels can be marked with **Tag Unknown Channels**, which appends your configured
suffix so they are easy to filter for later.

## Emailed reports, end to end

Channel Mapparr can email a report of a run. It does not send the mail itself: it hands the report
to the **Newsflasharr** plugin, which owns all the delivery settings.

**Before you start**, understand what is emailed and what is not. The CSV exports in
`/data/exports` open with a header listing your plugin settings, including the names of your
configured M3U sources, which on a real installation is your provider's hostname. **Those exports
are never emailed.** The emailed report is a separate file, built from the same rows, with that
header replaced by a short safe summary and with M3U account names and IP addresses scrubbed out
of every cell.

### Setting it up

1. **Install and enable Newsflasharr**, and configure its email settings (server, username,
   password, recipients). Send yourself a test notification from Newsflasharr and confirm it
   arrives. Do not skip this: if email is not working, nothing downstream will tell you clearly.

2. **Add a routing rule in Newsflasharr** so this plugin's reports go to email. Match on source
   `channel-mapparr` and event `usage_report`, and send to `smtp`. Scope it by both source and
   event, not by source alone.

   Without this rule the report is still queued successfully and delivered to whatever
   Newsflasharr's default channel is, **without its attachment**, because attachments are
   email-only. Nothing about that looks like a failure from this side, which is why the rule
   matters.

   Reload the Newsflasharr settings page in your browser before editing it. A stale tab re-posts
   its old form state when you click anything and can silently revert rules you just set.

3. **In Channel Mapparr, turn on "Send notifications to Newsflasharr."** It is off by default.

4. **Choose when and what.** *Email A Report After* selects `never` or every run that produces an
   export. *Email Report Format* selects the HTML page, the CSV, or both. A notification carries
   one attachment, so choosing both sends **two emails per run**, not one email with two files.

5. **Press Email Report Now.** It builds a report from your last processed channels and queues it.
   Crucially, it refuses with a visible red message if Newsflasharr is missing, disabled, missing
   its email settings, missing the routing rule, or if its collector is not running. That refusal
   is the point: it tells you the mail could not have arrived, rather than reporting success and
   leaving you to wonder.

6. **Run Validate Settings.** With notifications on, it reports any of the above as warnings.

### Which runs actually email

| Action | Emails a report? |
| :--- | :--- |
| Rename Channels, in Dry Run | Yes |
| Organize by Category, in Dry Run | Yes |
| Organize by Category, real run | No, it produces no export |
| Import M3U Streams, in Dry Run | No, to avoid two emails for one import |
| Import M3U Streams, completed | Yes |
| Email Report Now | Yes, on demand |

### Things worth knowing before you turn it on

- **"Queued" is not "delivered."** The button says queued because that is what is true: the report
  has been written to Newsflasharr's queue. Delivery happens afterwards on its retry schedule.
- **A report email bypasses Newsflasharr's quiet hours and its hourly cap**, because it carries an
  attachment. There is no way to throttle it from this side, which is why the format default is a
  single file.
- **This plugin has no schedule.** Reports arrive when you press buttons, not on a cadence. Do not
  configure Newsflasharr to expect a report from this plugin every N days; it would report a
  problem every time you simply did not press anything.

## Reading a report

The HTML page opens with a summary card: plugin version, generation time in UTC, which country
databases were actually loaded, whether it was a dry run, the match sensitivity, and how many rows
are shown out of how many exist.

Below it is the table. **Click any column heading to sort by it**, or move focus to it and press
Enter. Click again to reverse. Numeric columns are compared as numbers, so channel 18 sorts before
31 rather than after 102.

Sorting needs the page open in a browser. Mail clients strip scripts, so previewing the attachment
inside your mail client shows every row but cannot reorder them. Save the file and open it to sort.

Very large runs are capped at 2000 rows. When that happens both the HTML and the CSV say so at the
top and name the complete export file, so the truncation is never silent.

## Where everything is written

| Path | What it is |
| :--- | :--- |
| `/data/channel_mapparr_loaded_channels.json` | What the last matching pass decided. Replayed by the rename and report actions. |
| `/data/channel_mapparr_m3u_import_results.json` | Results of the last M3U import. |
| `/data/channel_mapparr_progress.json` | Live progress, read by **Show Status**. |
| `/data/exports/` | CSV previews. These contain your M3U source names and are never emailed. |
| `/data/channel_mapparr_reports/` | The HTML and CSV files built for emailing. |

All of these must be writable by Dispatcharr's `dispatch` user. If an action reports success but
writes nothing, that is the first thing to check.

Report files are pruned to the newest 8 of each type, except that a file younger than 40 minutes is
never deleted, because a delivery retry re-reads the attachment from disk.

**Clear CSV Exports** deletes files from `/data/exports` only. It cannot reach the emailed reports.

## Fixing things by symptom

**An action reports success but nothing changed.** Check Dry Run Mode. In Dry Run, mutating actions
write a CSV and change nothing, by design.

**"No processed channels found."** Run **Load & Process Channels** first. The rename and report
actions replay its output rather than re-matching.

**A group I named is being ignored, or a typo stopped the run.** Run **Validate Settings**, which
reports what your ignore list actually resolved to. A token matching no group refuses the run on
purpose.

**Matching is slow.** Install `rapidfuzz` in your Dispatcharr container. The logs say which backend
is in use. The difference is large.

**Per-channel logos fail with "No file lists could be fetched."** GitHub limits anonymous API use
to 60 requests per hour per address. Wait an hour, or use an authenticated proxy.

**Show Status says no operation has run.** `/data/` is not writable by the `dispatch` user.

**No report email arrives.** Run **Validate Settings**; it reports the reason as a warning. The
usual cause is a missing routing rule in Newsflasharr, which is invisible from this side because
the queue write succeeds.

**The email arrives without its attachment.** The report exceeded Newsflasharr's 1 MB limit, or the
file was gone when delivery was attempted. Newsflasharr records this and still sends the message.

**Emailed reports stopped after an update.** The master switch defaults to off. Confirm it is
still on.
