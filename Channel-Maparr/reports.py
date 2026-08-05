"""Report model and rendering for Channel-Maparr's emailed reports.

Newsflasharr sends an attachment verbatim and unredacted, so anything reaching
this model can leave the box by email. Two structural decisions follow from that,
and both are pinned by tests:

The model is built by COPYING a named allow-list of columns out of the row dicts
the actions already hold in memory. It is never built by reading a CSV export.
The exports in /data/exports open with a settings header that names the
configured M3U sources, which on a real installation is the provider hostname,
so a report built by re-reading an export would carry that hostname by
construction. Building from an allow-list also means a column added to a CSV
writer later cannot start being emailed on its own.

The account-name scrub is the PRIMARY redaction input here, not a backstop. In
Stream-Mapparr the raw stream names never carried an account label and the scrub
was a second line of defence, so an empty account list degraded safely. That is
not true here, so build_model refuses when the account name list is None, which
is how a caller reports that the lookup failed.
"""
import base64
import csv
import datetime
import html
import io
import os
import re
import time

# Reports are written here, deliberately NOT under /data/logos: Dispatcharr's
# nginx serves that tree unauthenticated to the entire local network.
REPORT_DIR = "/data/channel_mapparr_reports"

# Every report file starts with this. The pruner matches on it, so a copied and
# unrenamed prefix would make pruning match nothing and the directory would grow
# forever with no error.
FILENAME_PREFIX = "channel_mapparr_report_"

# How many of each file type to keep.
KEEP_REPORTS = 8

# Newsflasharr re-reads an attachment path on every delivery retry, across a
# documented worst case of 30 + 300 + 1800 seconds. A report file younger than
# this is never pruned, however many newer ones exist, because deleting it would
# strip the attachment from mail that is already queued.
RETRY_WINDOW_SECONDS = 2400

# The row cap, applied ONCE before rendering. A render, measure, drop rows,
# re-render loop is deliberately not used: an M3U import can carry seventeen
# thousand rows, and three of the four paths that build a report run inside the
# uWSGI request, where a pure Python loop performs no input or output and so
# never yields. Under gevent that freezes the whole worker, not just the request
# that started it.
#
# At a measured density of about 136 bytes per CSV row this is roughly 270 KB of
# CSV, well inside Newsflasharr's 1048576 byte attachment cap, and the larger
# HTML rendering still fits. Anyone raising this constant must measure in UTF-8
# BYTES of the written file, not characters: the matcher supports Cyrillic and
# CJK names, which are two to three times longer in bytes than in characters.
MAX_REPORT_ROWS = 2000

# The container path of the exports. Named in the report so a reader knows where
# the complete, unredacted file is. It is a locator, not a browsable link, and it
# is never a Windows drive mapping.
EXPORTS_LOCATION = "/data/exports"

# The only settings that reach the report. Everything else is dropped, including
# m3u_sources, which is the measured leak, and default_logo, which is an operator
# typed URL and the most likely route for a local network address to enter this
# plugin at all.
_SAFE_SETTINGS = (
    ("dry_run_mode", "Dry run"),
    ("match_sensitivity", "Match sensitivity"),
)

# Matches a bare IPv4 address.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Matches an IPv6 address, including the compressed forms. Deliberately requires
# either a double colon or at least three colon separated groups, so an ordinary
# clock time such as 20:30 is not mistaken for an address.
_IPV6_RE = re.compile(
    r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"
    r"|(?:[0-9A-Fa-f]{1,4}:)+:(?:[0-9A-Fa-f]{1,4})?"
    r"|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4}"
)

# Collapses the run of spaces left behind when a value is removed from the middle
# of a name.
_MULTISPACE_RE = re.compile(r"\s{2,}")


def sanitise_label(label, account_names):
    """Remove an M3U account name wherever it appears, and nothing else.

    Matching is case insensitive and is not limited to the bracketed form,
    because an account name on a real installation is a literal provider
    hostname and "ESPN backup provider.tv" leaks exactly as much as
    "ESPN [provider.tv]".

    Account names are matched longest first: "provider.tv" is a prefix of
    "provider.tv-alt1", and matching the shorter one first would leave a "-alt1"
    fragment behind.

    An unknown bracketed value is left alone. On this installation a bracketed
    value in a channel name holds the market, and for an over the air station the
    market is its whole identity, so removing every bracketed group would collapse
    dozens of distinct stations into one indistinguishable name.
    """
    text = str(label if label is not None else "")
    for account in sorted([a for a in (account_names or []) if a], key=len, reverse=True):
        escaped = re.escape(str(account))
        text = re.sub(r"\s*\[" + escaped + r"\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\(" + escaped + r"\)", "", text, flags=re.IGNORECASE)
        text = re.sub(escaped, "", text, flags=re.IGNORECASE)
    return _MULTISPACE_RE.sub(" ", text).strip()


def _scrub(value, account_names):
    """Apply every content rule to one free text value."""
    cleaned = sanitise_label(value, account_names)
    cleaned = _IPV4_RE.sub("", cleaned)
    cleaned = _IPV6_RE.sub("", cleaned)
    return _MULTISPACE_RE.sub(" ", cleaned).strip()


def build_model(title, columns, rows, *, account_names, settings, databases,
                version, now, export_filename=None):
    """Build the report model from in-memory rows.

    `columns` is a list of (row key, display header) pairs and IS the allow list.
    A key absent from it is never copied, whatever the row carries.

    `account_names` of None means the M3U account lookup failed, and this raises
    rather than sending an unscrubbed report. An empty list is a different thing
    and is allowed: an installation can legitimately have no M3U accounts.
    """
    if account_names is None:
        raise ValueError(
            "account_names is None, which means the M3U account lookup failed. "
            "The report is not built, because the account names are the primary "
            "redaction input and an empty scrub would ship unredacted names.")

    rows = list(rows or [])
    total_rows = len(rows)
    kept = rows[:MAX_REPORT_ROWS]

    entries = []
    for row in kept:
        entries.append([_scrub(row.get(key), account_names) for key, _ in columns])

    settings = settings if isinstance(settings, dict) else {}
    summary = [("Plugin version", str(version)),
               ("Generated", _fmt_ts(now)),
               ("Databases loaded", ", ".join(str(d) for d in (databases or [])) or "none")]
    for key, label in _SAFE_SETTINGS:
        summary.append((label, str(settings.get(key, ""))))
    summary.append(("Rows", f"{len(entries)} of {total_rows}"))

    return {
        "title": str(title),
        "generated_ts": float(now or 0),
        "summary": summary,
        "headers": [header for _, header in columns],
        "entries": entries,
        "total_rows": total_rows,
        "shown_rows": len(entries),
        "truncated": total_rows > len(entries),
        "export_filename": export_filename,
    }


def truncation_notice(model):
    """The one line stating that rows were dropped, or None when none were.

    It names a count and a filename only. Never the M3U source, never the group
    scope, and never a Windows drive mapping.
    """
    if not model.get("truncated"):
        return None
    name = model.get("export_filename") or "the export file"
    return (f"Showing the first {model['shown_rows']} of {model['total_rows']} rows. "
            f"The complete file is {name} in {EXPORTS_LOCATION} inside the container.")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

# Styling is inlined because the page is read from a file path or inside an email
# client, where an external stylesheet would not resolve. There is no CDN, no web
# font, no external image and no url(): the page is also read on a television
# browser with no route to the internet.
#
# EVERY colour is a token, and light and dark differ ONLY in token values. A
# literal colour inside a rule is one that light mode and dark mode cannot both
# be right about, which is how the previous version of this file ended up with
# four "important" overrides in its dark block. Text hierarchy uses the --ink
# ramp and never "opacity": an opacity value paints a different colour on every
# surface it lands on, so the contrast ratio moves silently whenever a background
# changes, and the fade applies to everything nested inside.
#
# Spacing picks a step off --s1 to --s5. The steps sit about a third apart
# because a linear ramp makes small steps look identical and large ones look
# arbitrary.
#
# The palette below, and the two surface colours it was measured against, were
# validated all-pairs for colourblind safety with a validator that lives in none
# of these repositories, so the numbers CANNOT be re-derived here. Reuse a
# semantic slot rather than inventing a hex. tests/test_report_style.py pins
# every one of them, and pins the ink ramp separately: --ink-dim is the weakest
# at 5.24:1 against the light surface, clear of the 4.5:1 floor.
_CSS = """
:root {
  color-scheme: light dark;
  --never: #2a78d6; --watched: #1baf7a; --tuned: #e34948; --toonew: #898781;
  --track: #e1e0d9; --ok: #0ca30c; --bad: #d03b3b;

  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px;

  --bg: #fbfbfd; --raised: #ffffff; --zebra: #f7f8fa; --head: #f2f3f6;
  --ink: #16181d; --ink-muted: #5c616b; --ink-dim: #656a76;
  --line: #e3e5ea; --line-soft: #e6e8ec;
  --warn-bg: #fff4e5; --warn-line: #ffb84d; --warn-ink: #7a4b00;
  --lift: 0 1px 2px rgba(16, 18, 29, .05), 0 4px 12px rgba(16, 18, 29, .04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --never: #3987e5; --watched: #199e70; --tuned: #e66767; --toonew: #898781;
    --track: #2c2c2a; --ok: #0ca30c; --bad: #d03b3b;

    --bg: #14161a; --raised: #1a1d22; --zebra: #191c21; --head: #1e2127;
    --ink: #e8eaed; --ink-muted: #a7adb8; --ink-dim: #9aa0ab;
    --line: #2a2e35; --line-soft: #262a31;
    --warn-bg: #2e2312; --warn-line: #8a6320; --warn-ink: #f2c98a;
    --lift: 0 1px 2px rgba(0, 0, 0, .35), 0 4px 12px rgba(0, 0, 0, .25);
  }
}
body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0; padding: var(--s5); background: var(--bg); color: var(--ink); }
/* The logo sits beside the title rather than above it, so the masthead costs
   one line of vertical space instead of three. */
.masthead { display: flex; align-items: center; gap: var(--s3);
            margin-bottom: var(--s5); }
.mark { flex: none; width: 48px; height: 48px; display: block; }
/* The type scale is 15 / 17 / 22, hand picked and deliberately sparse. A step
   seven percent from the body size reads as an accident rather than a decision,
   and this page is sized to be read at television distance, so nothing here
   shrinks. Supporting text is separated by colour instead. */
h1 { font-size: 22px; line-height: 1.2; letter-spacing: -.01em;
     margin: 0 0 var(--s1); }
.masthead .sub { margin-bottom: 0; }
.sub { color: var(--ink-muted); font-size: 15px; margin-bottom: var(--s5); }
.card { background: var(--raised); border: 1px solid var(--line);
        border-radius: 10px; box-shadow: var(--lift);
        padding: var(--s3) var(--s4); margin-bottom: var(--s4); }
.banner { background: var(--warn-bg); border: 1px solid var(--warn-line);
          border-radius: 10px; box-shadow: var(--lift);
          padding: var(--s3) var(--s4); margin-bottom: var(--s4);
          color: var(--warn-ink); }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
.scroll { overflow-x: auto; }
/* Row padding is one step up from the old hand picked pair, because this page
   is read at distance and the scale has no step between them anyway. */
th, td { text-align: left; padding: var(--s2) var(--s3);
         border-bottom: 1px solid var(--line-soft); vertical-align: top; }
th { background: var(--head); position: sticky; top: 0; }
/* Zebra striping used to be declared in the dark block ONLY, so the two themes
   rendered visibly different tables. One rule, one token, both modes. */
tr:nth-child(even) td { background: var(--zebra); }
th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
th.sortable::after { content: " \\2195"; color: var(--ink-dim); }
th.sortable[aria-sort="ascending"]::after { content: " \\2191"; color: var(--ink); }
th.sortable[aria-sort="descending"]::after { content: " \\2193"; color: var(--ink); }
.empty { color: var(--ink-muted); font-style: italic; }
dl.meta { margin: 0; display: grid; grid-template-columns: auto 1fr;
          gap: var(--s1) var(--s4); }
dl.meta dt { color: var(--ink-muted); }
dl.meta dd { margin: 0; }
/* Sections are details and summary, so they need no JavaScript at all. A client
   that does not implement them renders the content EXPANDED, so the failure mode
   is everything visible, never content lost. */
details { border-top: 1px solid var(--track); padding: var(--s1) 0 var(--s2); }
/* Never add an outline of none here. That focus ring is how the page is driven
   by a television remote's directional pad. */
summary { font-size: 17px; font-weight: 600; cursor: pointer;
          padding: var(--s2) var(--s1); list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '\\25B8'; display: inline-block; width: 1em;
                  color: var(--ink-dim); transition: transform .12s; }
details[open] > summary::before { transform: rotate(90deg); }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
       margin-right: var(--s2); vertical-align: baseline; }
.dot-neutral { background: var(--track); }
/* The heading is 600; the count staying at 400 is what separates the two, so the
   number reads as data rather than as part of the label. */
.count { font-weight: 400; color: var(--ink-dim);
         font-variant-numeric: tabular-nums; }
/* Emoji ignore the colour property, so this cannot be used to carry meaning even
   by accident. It is spacing only. */
.glyph { margin-right: var(--s2); }
.hint { font-size: 15px; color: var(--ink-dim); margin: 0 0 var(--s2); }
.colophon { margin-top: var(--s5); padding-top: var(--s4);
            border-top: 1px solid var(--track); color: var(--ink-dim); }
.colophon p { margin: 0 0 var(--s1); }
.colophon a { color: var(--never); }
"""

# The masthead logo, embedded as a data URI read from this plugin's own
# directory. It is NOT linked: a relative path resolves against nothing when the
# page is opened off disk or read as a mail attachment, a remote address is
# blocked by default in most mail clients, and the repository is not a reliable
# host either. A logo that cannot be read renders no img element at all and never
# fails a build.
#
# This is a SEPARATE, smaller file from the plugin card's logo.png. That one is
# 357 by 379 pixels and 216 KB, which embeds as 288 KB, which is seven times the
# size of an entire report. This one is 192 pixels on its long edge with a
# quantised palette, which embeds as about 16 KB.
_LOGO_FILENAME = "logo_report.png"
_LOGO_CACHE = []

# Named in the footer so a reader who received the report by email knows where it
# came from and where to report a fault with it.
_REPO_URL = "https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin"
_ISSUES_URL = _REPO_URL + "/issues"


def _logo_data_uri():
    """The masthead logo as a data URI, or an empty string if it cannot be read.

    Cached after the first read because a report build must not pay for the file
    twice, and because three of the four paths that build a report run inside the
    uWSGI request.
    """
    if _LOGO_CACHE:
        return _LOGO_CACHE[0]
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _LOGO_FILENAME)
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        uri = "data:image/png;base64," + encoded
    except Exception:
        uri = ""
    _LOGO_CACHE.append(uri)
    return uri


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_ts(ts):
    """Render a time in UTC, labelled as such.

    Deliberately not local time: this module has no access to Dispatcharr's
    configured timezone, and a bare unlabelled clock that silently means UTC is
    how a reader gets the day wrong.
    """
    try:
        moment = datetime.datetime.fromtimestamp(float(ts or 0), datetime.timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


# Click to sort, embedded in the page rather than loaded from anywhere. The page
# is opened from a file path or from a mail attachment, so an external request
# would not resolve and would disclose that the report had been opened.
#
# This is an ADDITION, not a requirement. Every row is present in the markup, so
# a reader whose mail client strips scripts still sees the whole table; they
# simply cannot reorder it. Mail clients do strip scripts, so sorting works when
# the attachment is saved and opened in a browser, which is the ordinary way to
# read an HTML attachment.
#
# The comparison reads each cell's data-v attribute, which holds the same value
# the cell displays. Two values that both parse as numbers are compared as
# numbers, because comparing them as text puts 10 before 2.
_SORT_SCRIPT = """
(function () {
  var table = document.querySelector('table');
  if (!table || !table.tBodies.length) { return; }
  var headers = [].slice.call(table.querySelectorAll('th.sortable'));
  function value(row, index) {
    var cell = row.children[index];
    if (!cell) { return ''; }
    var raw = cell.getAttribute('data-v');
    return raw === null ? cell.textContent : raw;
  }
  function compare(a, b, index) {
    var x = value(a, index), y = value(b, index);
    var nx = Number(x), ny = Number(y);
    if (x !== '' && y !== '' && !isNaN(nx) && !isNaN(ny)) { return nx - ny; }
    return String(x).localeCompare(String(y), undefined,
                                   { numeric: true, sensitivity: 'base' });
  }
  function apply(header, index) {
    var ascending = header.getAttribute('aria-sort') !== 'ascending';
    headers.forEach(function (other) { other.setAttribute('aria-sort', 'none'); });
    header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
    var body = table.tBodies[0];
    var rows = [].slice.call(body.rows);
    rows.sort(function (a, b) {
      return ascending ? compare(a, b, index) : compare(b, a, index);
    });
    rows.forEach(function (row) { body.appendChild(row); });
  }
  headers.forEach(function (header, index) {
    header.addEventListener('click', function () { apply(header, index); });
    header.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        apply(header, index);
      }
    });
  });
})();
"""


# Keyed on the section's COLOUR CLASS rather than on its title, so a glyph can
# never disagree with the dot beside it. It is decoration on top of the dot and
# the words, never the only thing carrying the meaning, which is why it is marked
# as hidden from assistive technology. A client with no emoji font shows a box or
# nothing at all and the heading still reads correctly.
_SECTION_GLYPH = {
    "dot-neutral": "\N{CLIPBOARD}",   # the rows this run produced
}


def _section(title, count, body, open_by_default, dot_class):
    """One report section as a collapsible details element.

    `count` must equal the number of rows in the table directly beneath it, in
    every section without exception. A reader looking at a collapsed page cannot
    see any distinction a bare heading was trying to draw; it just looks like a
    section that forgot its number.
    """
    open_attr = " open" if open_by_default else ""
    number = "" if count is None else f' <span class="count">{int(count)}</span>'
    glyph = _SECTION_GLYPH.get(dot_class, "")
    badge = (f'<span class="glyph" aria-hidden="true">{glyph}</span>'
             if glyph else "")
    return (f'<details{open_attr}><summary>'
            f'<span class="dot {_esc(dot_class)}" aria-hidden="true"></span>'
            f'{badge}{_esc(title)}{number}</summary>{body}</details>')


def render_html(model):
    """Render the model to one self contained HTML page.

    The rows live in a section that starts COLLAPSED, so the page opens as an
    index rather than as a wall of table. The section carries its row count, one
    line saying what it holds and what to do about it, and a note about
    find-in-page, which does not reach inside a collapsed section on some
    browsers.

    The table is sortable by clicking or keyboard-activating a column header.
    See _SORT_SCRIPT for what that does and what it deliberately does not do.
    """
    rows = []
    for entry in model.get("entries") or []:
        cells = "".join(f"<td data-v=\"{_esc(cell)}\">{_esc(cell)}</td>" for cell in entry)
        rows.append(f"<tr>{cells}</tr>")
    headers = "".join(
        "<th class=\"sortable\" aria-sort=\"none\" tabindex=\"0\" role=\"columnheader\" "
        f"title=\"Sort by {_esc(h)}\">{_esc(h)}</th>"
        for h in model.get("headers") or [])
    if rows:
        table = ("<div class=\"scroll\"><table>"
                 "<thead><tr>" + headers + "</tr></thead>"
                 "<tbody>" + "".join(rows) + "</tbody>"
                 "</table></div>")
    else:
        table = "<p class=\"empty\">This run produced no rows.</p>"

    meta = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>"
                   for k, v in model.get("summary") or [])

    notice = truncation_notice(model)
    notice_html = f"<div class=\"banner\">{_esc(notice)}</div>\n" if notice else ""

    logo = _logo_data_uri()
    mark = (f"<img class=\"mark\" src=\"{logo}\" alt=\"\">" if logo else "")

    shown = model.get("shown_rows", 0)
    section = _section(
        "Results", shown,
        "<p class=\"sub\">Every row this run produced, with only the columns "
        "that are safe to send by email. Read them before you turn Dry Run Mode "
        "off, because that is the point at which the plugin starts writing to "
        "your channels.</p>\n"
        "<p class=\"hint\">Expand this to search it. Find-in-page does not reach "
        "inside a collapsed section on some browsers.</p>\n"
        "<p class=\"hint\">Click a column heading to sort by it, or move focus to "
        "it and press Enter. Sorting needs the page open in a browser; a mail "
        "client previewing this file shows every row but cannot reorder "
        "them.</p>\n"
        f"<div class=\"card\">{table}</div>",
        False, "dot-neutral")

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>Channel-Maparr: {_esc(model.get('title'))}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<div class=\"masthead\">{mark}<div>"
        f"<h1>Channel-Maparr: {_esc(model.get('title'))}</h1>\n"
        f"<div class=\"sub\">{_esc(shown)} row(s) shown</div>"
        "</div></div>\n"
        + notice_html +
        f"<div class=\"card\"><dl class=\"meta\">{meta}</dl></div>\n"
        f"{section}\n"
        "<div class=\"colophon\">\n"
        "<p>Names in this report are shown without their M3U source label, and "
        "the plugin settings that name your M3U sources are not included. The "
        f"complete export, which does include them, stays in {EXPORTS_LOCATION} "
        "inside the container and is not emailed.</p>\n"
        f"<p>Channel Maparr <a href=\"{_REPO_URL}\">{_REPO_URL}</a></p>\n"
        f"<p>Report a problem <a href=\"{_ISSUES_URL}\">{_ISSUES_URL}</a></p>\n"
        "</div>\n"
        f"<script>{_SORT_SCRIPT}</script>\n"
        "</body>\n</html>\n"
    )


# A cell beginning with any of these is evaluated as a formula by Excel,
# LibreOffice and Google Sheets when the file is opened.
_FORMULA_LEADS = ("=", "+", "-", "@")


def _csv_safe(value):
    """Prefix a formula shaped cell with an apostrophe so it stays text."""
    text = str(value if value is not None else "")
    if text[:1] in _FORMULA_LEADS:
        return "'" + text
    return text


def render_csv(model):
    """Render the model to CSV text, with the same content rules as the HTML."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([f"# Channel-Maparr: {model.get('title')}"])
    for key, value in model.get("summary") or []:
        writer.writerow([f"# {key}: {value}"])
    notice = truncation_notice(model)
    if notice:
        writer.writerow([f"# {notice}"])
    writer.writerow([])
    writer.writerow(list(model.get("headers") or []))
    for entry in model.get("entries") or []:
        writer.writerow([_csv_safe(cell) for cell in entry])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def _atomic_write(path, text):
    """Write through a temporary file and rename, so no partial file is ever
    visible at the destination path."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(dirpath, suffix, keep=KEEP_REPORTS, now=None):
    """Keep the newest `keep` report files with this suffix and delete the rest,
    except any file still young enough that a delivery retry could need it.

    The age guard is required, not defensive tidiness. Newsflasharr copies
    nothing: it re-reads the attachment path on every retry attempt across a
    worst case of 2130 seconds. Several actions run back to back produce several
    report pairs within minutes and can push an earlier one past the keep count
    while its mail is still being retried. The result would be an email arriving
    with its attachment missing, which Newsflasharr records as a degrade rather
    than an error.

    Never raises: losing an old report must not fail the run that produced a new
    one.
    """
    try:
        moment = time.time() if now is None else now
        entries = [os.path.join(dirpath, name) for name in os.listdir(dirpath)
                   if name.startswith(FILENAME_PREFIX) and name.endswith(suffix)]
        entries.sort(key=os.path.getmtime, reverse=True)
        for stale in entries[keep:]:
            try:
                if moment - os.path.getmtime(stale) < RETRY_WINDOW_SECONDS:
                    continue  # a queued delivery may still re-read this path
                os.unlink(stale)
            except OSError:
                pass
    except OSError:
        pass


def write_report(model, report_dir, now):
    """Write the HTML and CSV reports and return their paths.

    Returns {"html_path", "csv_path", "error"}. Never raises: reporting is not
    the plugin's real work, and a failure here is reported rather than thrown.

    Both files carry the run's timestamp in their name and are never rewritten,
    because an email send re-reads the attachment path on every retry attempt.
    """
    result = {"html_path": None, "csv_path": None, "error": None}
    try:
        os.makedirs(report_dir, exist_ok=True)
        stamp = datetime.datetime.fromtimestamp(
            float(now or 0), datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = os.path.join(report_dir, f"{FILENAME_PREFIX}{stamp}")
        html_path, csv_path = base + ".html", base + ".csv"
        _atomic_write(html_path, render_html(model))
        _atomic_write(csv_path, render_csv(model))
        result["html_path"], result["csv_path"] = html_path, csv_path
        _prune(report_dir, ".html")
        _prune(report_dir, ".csv")
    except Exception as error:
        result["error"] = f"could not write the report: {error}"
    return result
