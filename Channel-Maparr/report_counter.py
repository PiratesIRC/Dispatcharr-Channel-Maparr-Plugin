"""Publishes the report counter that Newsflasharr's status readout displays.

THE CONTRACT, set by the plugin that READS the file:

    /data/<source>/report_count.json   ->   {"reports_built": 42}

One key, one non-negative integer. `<source>` is the string this plugin puts on
every notification, which is why the directory name is derived from SOURCE
rather than typed again: the two must never drift apart. Note that the directory
uses the HYPHEN form, channel-mapparr, while every other artifact this plugin
writes uses the underscore form (channel_mapparr_reports,
channel_mapparr_progress.json). A cleanup or backup sweep grepping for the
underscore form will not find this one.

WHAT THE NUMBER COUNTS. One increment is one successful report BUILD, meaning
one HTML file and one CSV file both written to disk. Not one file, not one
email, not one delivery. With the report format set to "both" a single build
sends two notifications, so this number can be half the delivery count, and a
build whose delivery later fails still counts. Nothing increments while
notifications are switched off, because this plugin does not build a report at
all in that case.

THE READER REFUSES A MALFORMED FILE IN SILENCE. It shows no count and logs
nothing, on either side, so every condition below is load bearing:
the top level is an object; the value is an int, is not a bool (bool subclasses
int in Python, so true would display as "1 built"), and is not negative; a float
is refused rather than truncated; the whole file is under 4096 bytes; it is a
regular file readable by the user the plugin runs as. The reader these rules
were taken from is newsflasharr/report_count.py in the Newsflasharr repository,
read on 2026-08-05, with MAX_BYTES = 4096. It is in another repository and
cannot be hash pinned from here, so it is named instead.

MODE 0600 IS NOT A SECURITY BOUNDARY HERE. The reader runs in the same container
as the same user, the content is a single integer, and Dispatcharr's nginx
workers also run as that user. What keeps this file off the network is that
nginx serves only /data/logos, not that the mode is narrow. The narrow mode is
kept because nothing needs a wider one.

OWNERSHIP. The plugin runs as `dispatch`, so files it creates are owned by
`dispatch`. A root-owned FILE inside a dispatch-owned directory recovers on its
own, because os.replace needs write permission on the DIRECTORY rather than on
the file it overwrites. A root-owned DIRECTORY can never be written and never
recovers. That is why nothing here or in any runbook may create this directory
by hand: `docker exec` defaults to root, so one probe run without
`-u dispatch` wedges the counter permanently.
"""
import json
import os
import tempfile

try:
    from .notify_bridge import SOURCE
except ImportError:                      # loaded standalone (tests, or a non-package path)
    from notify_bridge import SOURCE

# The directory the reader looks in. Nothing in this module defaults an argument
# to it: a default binds at import time, which would defeat the test suite's
# redirect of container paths and let a test write to the real path. That passes
# on Windows, where an absolute /data path resolves under the current drive root,
# and fails on Linux (bug-105).
COUNTER_DIR = "/data/" + SOURCE

FILENAME = "report_count.json"

# The reader refuses a file LARGER than this, so a file of exactly this size is
# still accepted. The cap exists because the reader runs on an operator click
# inside a uWSGI worker.
MAX_BYTES = 4096

KEY = "reports_built"


def _path(counter_dir):
    return os.path.join(counter_dir, FILENAME)


def read_count(counter_dir):
    """Return the published count, or None when there is no usable one.

    Applies the reader's rules rather than this module's preferences, so a value
    this returns is a value the reader would also accept. Returns None on every
    unexpected condition instead of raising: this is called on the report path,
    where a failure to read a cosmetic number must never become a failure to
    produce a report.
    """
    path = _path(counter_dir)
    try:
        if not os.path.isfile(path) or os.stat(path).st_size > MAX_BYTES:
            return None
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(KEY)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def bump(counter_dir, logger=None):
    """Add one to the published count. Returns the new value, or None.

    NEVER RAISES. Publishing this number is not the plugin's real work, and a
    failure here must not fail the report that had already been written.

    There is NO LOCK. An unlocked read, modify, write across several uWSGI and
    Celery workers can lose an increment when two reports finish in the same
    instant. That is accepted deliberately: the number is a floor, not an audit,
    and a file lock on a request path is a worse trade for a cosmetic number. Do
    not "fix" this by adding one.

    There is NO fsync, on the file or on the directory. os.replace already makes
    a partial file impossible to observe, which is the requirement. fsync is a
    blocking call that gevent does not yield on, and three of the four paths that
    reach here run inside the uWSGI request, where blocking stalls every greenlet
    in the worker rather than only the request being served. Durability across a
    power loss is not required for a number that is explicitly a floor.

    An unreadable or malformed existing file restarts the count from zero rather
    than refusing to write, for the same reason: a recoverable state must not
    become a permanent one.

    A failure is logged at WARNING, not debug. The container runs at log level
    INFO, so a debug line reaches nothing, and a counter that can never be
    written looks exactly like a plugin that publishes no counter, from both the
    reader's side and the plugin card. The container log is the only place the
    condition can be seen at all.
    """
    handle = None
    tmp = None
    try:
        os.makedirs(counter_dir, mode=0o700, exist_ok=True)
        current = read_count(counter_dir)
        value = (current if current is not None else 0) + 1
        # mkstemp gives a name unique by construction and creates at 0600. A
        # fixed name plus an exclusive create would wedge permanently: a process
        # killed between the create and the rename leaves the file behind and
        # every later write then fails. The pattern in reports.py, which puts the
        # process id in the name, is NOT safe to copy here, because uWSGI runs
        # 400 greenlets that share one process id.
        fd, tmp = tempfile.mkstemp(dir=counter_dir, prefix=".report_count_",
                                   suffix=".tmp")
        handle = os.fdopen(fd, "w", encoding="utf-8")
        # json.dump writes no byte order mark. The reader opens with utf-8 and a
        # byte order mark would make its parse raise, which it reports as no
        # count at all.
        json.dump({KEY: value}, handle)
        handle.close()
        handle = None
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path(counter_dir))
        tmp = None
        return value
    except Exception as error:
        if logger is not None:
            logger.warning(
                f"Channel-Maparr: the report counter at {counter_dir} could not "
                f"be written, so Newsflasharr will show no count: {error}")
        return None
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
