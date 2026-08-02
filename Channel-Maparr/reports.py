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
# client, where an external stylesheet would not resolve. Colours and layout
# follow Stream-Mapparr's report so the two look like one family.
_CSS = """
:root { color-scheme: light dark; --accent: #2a78d6; }
body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0; padding: 24px; background: #fbfbfd; color: #16181d; }
@media (prefers-color-scheme: dark) {
  :root { --accent: #3987e5; }
  body { background: #14161a; color: #e8eaed; }
  th { background: #1e2127 !important; }
  tr:nth-child(even) td { background: #191c21; }
  .card { background: #1a1d22 !important; border-color: #2a2e35 !important; }
  .notice { background: #2a2410 !important; border-color: #5a4a18 !important; }
}
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { opacity: .7; font-size: 15px; margin-bottom: 20px; }
.card { background: #fff; border: 1px solid #e3e5ea; border-radius: 10px;
        padding: 14px 16px; margin-bottom: 18px; }
.notice { background: #fff8e1; border: 1px solid #e8d9a0; border-radius: 8px;
          padding: 10px 14px; margin-bottom: 18px; font-size: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e6e8ec;
         vertical-align: top; }
th { background: #f2f3f6; }
th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
th.sortable::after { content: " \\2195"; opacity: .35; font-size: 12px; }
th.sortable[aria-sort="ascending"]::after { content: " \\2191"; opacity: 1; }
th.sortable[aria-sort="descending"]::after { content: " \\2193"; opacity: 1; }
.empty { opacity: .7; font-style: italic; }
.note { font-size: 14px; opacity: .7; margin-top: 20px; }
dl.meta { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 2px 14px; }
dl.meta dt { opacity: .7; }
dl.meta dd { margin: 0; }
"""


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


def render_html(model):
    """Render the model to one self contained HTML page.

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
    notice_html = f"<div class=\"notice\">{_esc(notice)}</div>\n" if notice else ""

    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>Channel-Maparr: {_esc(model.get('title'))}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<h1>Channel-Maparr: {_esc(model.get('title'))}</h1>\n"
        f"<div class=\"sub\">{_esc(model.get('shown_rows', 0))} row(s) shown</div>\n"
        + notice_html +
        f"<div class=\"card\"><dl class=\"meta\">{meta}</dl></div>\n"
        f"<div class=\"card\">{table}</div>\n"
        "<p class=\"note\">Click a column heading to sort by it, or focus it and "
        "press Enter. Sorting needs the page open in a browser; a mail client "
        "previewing this file shows every row but cannot reorder them.</p>\n"
        "<p class=\"note\">Names in this report are shown without their M3U source "
        "label, and the plugin settings that name your M3U sources are not "
        f"included. The complete export, which does include them, stays in "
        f"{EXPORTS_LOCATION} inside the container and is not emailed.</p>\n"
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
