"""Channel-Maparr's emit layer for the Newsflasharr notification service.

This module owns the guard boundary. The vendored client's notify() never
raises, but the code around it can, and a bug here must never break
Channel-Maparr's real work. Every public function in this file is written so a
failure is reported rather than thrown.

Settings are read from the dict passed in on every call and never cached on an
instance. A value primed on one entry path and read back with getattr on another
fails silently, with no crash and no log line, which is a failure this codebase
has shipped before.

Stream-Mapparr's scheduled-run timestamp file is deliberately not reproduced
here. It exists there to prove a schedule is still alive, because Newsflasharr's
own absence detector stamps a timestamp on any successful attachment send and
cannot tell a scheduled run from a button press. Channel-Maparr has no scheduler
at all, so such a file would prove nothing.
"""
import json
import os

# The plugin key. Newsflasharr routing and deduplication both key on it, so it
# must stay stable.
SOURCE = "channel-mapparr"

# The event name every report notification carries. Newsflasharr routing rules
# match on it, so it must stay stable. "usage_report" matches the naming the
# other report senders on this installation already use, and it does not
# collide, because every existing rule is scoped by source and event together.
EVENT = "usage_report"

# Everything Newsflasharr needs configured before it can send mail at all. Only
# the PRESENCE of each is ever checked or reported: smtp_password is one of them
# and its value must never reach a log line or a toast.
SMTP_REQUIRED = ("smtp_server", "smtp_username", "smtp_password", "smtp_to")

# Which runs email a report. Stream-Mapparr's third value, "scheduled", is
# absent because this plugin has no scheduler, and the default is changed to
# match: leaving "scheduled" as the default while removing it from the accepted
# set would resolve every unset value to something outside the set.
_TRIGGERS = ("never", "every_run")
_DEFAULT_TRIGGER = "every_run"

# Which report files are emailed. A notification carries ONE attachment, so
# "both" means two emails per run and either single format means one. The
# default is a single format deliberately: an attachment bearing event bypasses
# Newsflasharr's hourly cap and its quiet hours, so the email count cannot be
# throttled from the service side and is worth keeping low here.
_FORMATS = ("html", "csv", "both")
_DEFAULT_FORMAT = "html"


def is_enabled(settings):
    """Is the Newsflasharr master toggle on?

    Public on purpose. The Email Report Now button needs this check to fail fast
    before it does any work, and a caller in another module must not reach for a
    private helper: nothing would pin that call, so an ordinary rename in this
    file would break the button silently.

    A checkbox arrives as a string on some Dispatcharr paths, and bool("false")
    is True, so a string is coerced rather than passed to bool.
    """
    value = (settings or {}).get("notify_enabled", False)
    if isinstance(value, str):
        value = value.strip().lower() in ("true", "yes", "1", "on")
    return bool(value)


def resolve_report_trigger(settings):
    """Return "never" or "every_run", never anything else.

    An unrecognised or missing value resolves to the default rather than raising
    or guessing. Dispatcharr never prunes a stored setting when its field is
    removed, so a value written by an earlier version survives forever and must
    not be allowed to decide behaviour.
    """
    value = (settings or {}).get("notify_report_on", _DEFAULT_TRIGGER)
    if not isinstance(value, str):
        return _DEFAULT_TRIGGER
    value = value.strip().lower()
    return value if value in _TRIGGERS else _DEFAULT_TRIGGER


def resolve_report_format(settings):
    """Return "html", "csv" or "both", never anything else."""
    value = (settings or {}).get("notify_report_format", _DEFAULT_FORMAT)
    if not isinstance(value, str):
        return _DEFAULT_FORMAT
    value = value.strip().lower()
    return value if value in _FORMATS else _DEFAULT_FORMAT


def unknown_setting_values(settings):
    """Report stored values this module does not recognise, for the operator.

    Silently coercing an unrecognised value is how a setting quietly stops doing
    what its owner believes it does. Validate Settings surfaces whatever this
    returns, because a promise in a field's help text is not a surface.
    """
    settings = settings if isinstance(settings, dict) else {}
    problems = []
    for key, accepted in (("notify_report_on", _TRIGGERS),
                          ("notify_report_format", _FORMATS)):
        if key not in settings:
            continue
        value = settings.get(key)
        if isinstance(value, str) and value.strip().lower() in accepted:
            continue
        problems.append(
            f"{key} holds an unrecognised value ({value!r}); "
            f"it is being treated as the default. Accepted values: "
            f"{', '.join(accepted)}.")
    return problems


def routes_to_smtp(nf_settings, source=SOURCE, event=EVENT):
    """Would a report from this plugin actually reach the email channel?

    Newsflasharr sends an event to `default_channels` when no rule matches it,
    so a missing routing rule is invisible from this side: the queue write
    succeeds, a delivery is recorded, and the mail goes somewhere other than the
    inbox. Attachments are email only, so an unrouted report is delivered with no
    file at all. This is the check that makes that visible.

    `routing_rules` is stored as a JSON string, not a list, so it is parsed
    defensively; a list is accepted too in case that ever changes. A rule with no
    source or no event is a wildcard and matches. Never raises.
    """
    nf_settings = nf_settings if isinstance(nf_settings, dict) else {}
    raw = nf_settings.get("routing_rules")
    rules = raw if isinstance(raw, list) else []
    if isinstance(raw, str):
        try:
            rules = json.loads(raw)
        except (ValueError, TypeError):
            rules = []
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        match = rule.get("match") if isinstance(rule.get("match"), dict) else {}
        if match.get("source") not in (None, source):
            continue
        if match.get("event") not in (None, event):
            continue
        if any("smtp" in str(channel).lower() for channel in (rule.get("channels") or [])):
            return True
    return "smtp" in str(nf_settings.get("default_channels") or "").lower()


def should_emit(settings):
    """Return (bool, reason). The reason is operator readable when False."""
    if not is_enabled(settings):
        return False, "notifications to Newsflasharr are switched off"
    if resolve_report_trigger(settings) == "never":
        return False, "the report trigger is set to never"
    return True, None


def emit_reports(notify_fn, settings, written):
    """Emit one notification per report file. Returns {"sent", "skipped_reason"}.

    `notify_fn` is injected rather than imported so tests can observe the call
    without a queue directory.

    Each path must be a caller owned, never rewritten timestamped file that
    already exists on disk. An email send re-reads the attachment path on every
    retry attempt, so a file rewritten in place would be a different file on the
    second attempt. A path that is missing is skipped rather than sent, because a
    green task result does not prove an artifact was published.

    The report path is also sent as the url, so a recipient whose gateway strips
    the attachment still knows where the complete file is. It is a locator inside
    the container, not a browsable link.
    """
    result = {"sent": 0, "skipped_reason": None}
    try:
        allowed, reason = should_emit(settings)
        if not allowed:
            result["skipped_reason"] = reason
            return result
        written = written or {}
        if written.get("error"):
            result["skipped_reason"] = written["error"]
            return result
        wanted = resolve_report_format(settings)
        for key, label in (("html_path", "HTML report"), ("csv_path", "CSV report")):
            if wanted != "both" and not key.startswith(wanted):
                continue
            path = written.get(key)
            if not path or not os.path.isfile(path):
                continue
            sent = notify_fn(
                source=SOURCE,
                title=f"Channel-Maparr {label} ready",
                body=f"Attached: {os.path.basename(path)}",
                event=EVENT,
                severity="info",
                kind="event",
                dedup_key=None,
                url=path,
                attachment=path,
            )
            if sent:
                result["sent"] += 1
    except Exception as error:
        result["skipped_reason"] = f"the emit path raised and was contained: {error}"
    return result
