"""Pure, Django-free helpers for Channel-Maparr progress/status presentation.

Lives outside plugin.py so it can be imported and unit-tested without
Dispatcharr/Django. ProgressTracker writes the progress file during
background operations; the "Show Status" action reads it back via
build_status_message() so the user can check progress on demand instead
of watching transient toast notifications or the container logs.
"""

import json
import os
import tempfile
import time as _time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Dispatcharr ships Python 3.13
    ZoneInfo = None

# A "running" record whose last update is older than this is treated as
# stale: the operation almost certainly died or the container restarted
# mid-run (ProgressTracker updates at most every 10s).
STALE_AFTER_SECONDS = 120

IDLE = {"status": "idle"}


def _display_tz():
    """Resolve the timezone for user-facing timestamps. Dispatcharr pins
    Django's TIME_ZONE and the container $TZ to UTC, so prefer Dispatcharr's
    own configured system timezone when available."""
    if ZoneInfo is None:
        return None
    try:
        from core.models import CoreSettings  # Django-optional local import
        tz_name = CoreSettings.get_system_time_zone()
        if tz_name:
            return ZoneInfo(tz_name)
    except Exception:
        pass
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return None


def format_local_timestamp(unix_ts, fmt="%Y-%m-%d %H:%M %Z"):
    """Format a Unix timestamp in the operator's configured timezone."""
    tz = _display_tz()
    if tz is not None:
        return datetime.fromtimestamp(unix_ts, tz=tz).strftime(fmt).strip()
    return datetime.fromtimestamp(unix_ts).strftime(fmt).strip()


def format_eta(seconds):
    """Human-readable duration. Negative/zero -> '0s'."""
    s = int(seconds)
    if s <= 0:
        return "0s"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def load_progress(path):
    """Return the progress dict, or {'status': 'idle'} if missing/corrupt."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(IDLE)
        return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return dict(IDLE)


def save_progress_atomic(path, data):
    """Write data as JSON via temp file + os.replace (atomic, no torn reads)."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".channel_maparr_prog_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


ACTION_LABELS = {
    "validate_settings": "Validate Settings",
    "process_channels": "Load & Process Channels",
    "rename_channels": "Rename Channels",
    "tag_unknown": "Tag Unknown Channels",
    "organize_by_category": "Organize by Category",
    "import_m3u_streams": "Import M3U Streams",
    "apply_logos": "Apply Logos",
    "preview_changes": "Preview Changes",
}


def _action_label(action):
    return ACTION_LABELS.get(action, action or "Operation")


def build_status_message(progress, now=None):
    """Build the user-facing message for the Show Status action.

    - status 'running' -> live progress line with percent and ETA, or a
      stale warning if progress updates have stopped;
    - status 'done'    -> completion line plus the operation summary;
    - anything else    -> a friendly "nothing has run yet" prompt.
    """
    if now is None:
        now = _time.time()
    status = progress.get("status")

    if status == "running":
        cur = int(progress.get("current", 0))
        total = int(progress.get("total", 0))
        label = _action_label(progress.get("action"))
        pct = (cur / total * 100) if total > 0 else 0
        updated = progress.get("updated_at")
        if updated is not None and (now - updated) > STALE_AFTER_SECONDS:
            ago = format_eta(now - updated)
            return (f"⚠️ {label} — {cur}/{total} ({pct:.0f}%), "
                    f"no progress update in {ago}. The operation may have "
                    f"stopped — check the container logs.")
        start = progress.get("start_time")
        if start is not None and cur > 0:
            remaining = ((now - start) / cur) * (total - cur)
            eta = f"ETA {format_eta(remaining)}"
        else:
            eta = "ETA calculating…"
        return f"\U0001f504 {label} — {cur}/{total} ({pct:.0f}%) · {eta}"

    if status == "done":
        label = _action_label(progress.get("action"))
        fin = progress.get("finished_at")
        when = format_local_timestamp(fin) if fin else "recently"
        msg = f"✅ {label} finished {when}"
        summary = progress.get("summary")
        if summary:
            msg += f"\n{summary}"
        return msg

    return ("No Channel-Maparr operation has run yet. Run one of the actions "
            "above and click 📊 Show Status to watch progress.")
