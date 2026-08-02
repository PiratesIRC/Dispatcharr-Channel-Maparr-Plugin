"""Tests for Channel-Maparr's Newsflasharr emit layer.

The caller contract requires proving BOTH directions of the toggle: that nothing
is emitted when notifications are off, AND that an emit actually happens when
they are on. A test that only checks the off case passes just as happily against
an emit path that is dead in both directions.
"""
import json
import pathlib

import pytest
from conftest import _load_plugin_package  # noqa: F401


@pytest.fixture(scope="module")
def bridge():
    _load_plugin_package()
    import channel_maparr.notify_bridge as bridge_module  # noqa: E402
    return bridge_module


class RecordingNotify:
    """Stands in for notify_client.notify and records what it was called with.

    Injected rather than imported so a test never needs a spool directory.
    """

    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def written(tmp_path):
    html_path = tmp_path / "channel_mapparr_report_1.html"
    csv_path = tmp_path / "channel_mapparr_report_1.csv"
    html_path.write_text("<html></html>", encoding="utf-8")
    csv_path.write_text("a,b\n", encoding="utf-8")
    return {"html_path": str(html_path), "csv_path": str(csv_path), "error": None}


ON = {"notify_enabled": True}


# --------------------------------------------------------------------------- #
# The master toggle
# --------------------------------------------------------------------------- #

def test_the_toggle_is_off_by_default(bridge):
    assert bridge.is_enabled({}) is False


def test_the_toggle_accepts_the_string_dispatcharr_sometimes_stores(bridge):
    """A checkbox arrives as a string on some paths, and bool("false") is True."""
    assert bridge.is_enabled({"notify_enabled": "true"}) is True
    assert bridge.is_enabled({"notify_enabled": "false"}) is False


# --------------------------------------------------------------------------- #
# The two select settings
# --------------------------------------------------------------------------- #

def test_the_trigger_defaults_to_every_run_when_unset(bridge):
    assert bridge.resolve_report_trigger({}) == "every_run"


def test_an_unrecognised_stored_trigger_resolves_to_the_default(bridge):
    """Dispatcharr never prunes a stored setting when its field is removed, so a
    value left behind by an earlier version must not decide behaviour. The value
    "scheduled" is the one this plugin dropped, because it has no scheduler."""
    assert bridge.resolve_report_trigger({"notify_report_on": "scheduled"}) == "every_run"
    assert bridge.resolve_report_trigger({"notify_report_on": 7}) == "every_run"


def test_the_trigger_honours_never(bridge):
    assert bridge.resolve_report_trigger({"notify_report_on": "never"}) == "never"


def test_the_format_defaults_to_html_so_one_run_sends_one_email(bridge):
    assert bridge.resolve_report_format({}) == "html"


def test_an_unrecognised_stored_format_resolves_to_the_default(bridge):
    assert bridge.resolve_report_format({"notify_report_format": "pdf"}) == "html"


def test_an_unrecognised_stored_value_is_reported_for_the_operator(bridge):
    """The caller contract requires an unknown enum value to reach a surface the
    operator reads, not to be silently coerced."""
    problems = bridge.unknown_setting_values(
        {"notify_report_on": "scheduled", "notify_report_format": "pdf"})
    assert len(problems) == 2
    assert any("scheduled" in p for p in problems)
    assert any("pdf" in p for p in problems)


def test_recognised_values_produce_no_operator_warning(bridge):
    assert bridge.unknown_setting_values(
        {"notify_report_on": "never", "notify_report_format": "both"}) == []


# --------------------------------------------------------------------------- #
# Would the mail actually reach email?
# --------------------------------------------------------------------------- #

def _rules(*rules):
    return {"routing_rules": json.dumps(list(rules))}


def test_a_rule_naming_this_source_and_event_routes_to_smtp(bridge):
    settings = _rules({"match": {"source": "channel-mapparr", "event": "usage_report"},
                       "channels": ["smtp"]})
    assert bridge.routes_to_smtp(settings) is True


def test_another_plugins_rule_does_not_count(bridge):
    settings = _rules({"match": {"source": "dustarr", "event": "usage_report"},
                       "channels": ["smtp"]})
    assert bridge.routes_to_smtp(settings) is False


def test_a_wildcard_rule_counts(bridge):
    settings = _rules({"match": {}, "channels": ["smtp"]})
    assert bridge.routes_to_smtp(settings) is True


def test_smtp_among_the_default_channels_counts_when_no_rule_matches(bridge):
    settings = dict(_rules(), default_channels="apprise, smtp")
    assert bridge.routes_to_smtp(settings) is True


def test_apprise_alone_as_the_default_does_not_count(bridge):
    """This is the live configuration on the box. Attachments are smtp only, so
    an unrouted report would be delivered as text with no file while the queue
    write succeeded."""
    settings = {"routing_rules": "[]", "default_channels": "apprise"}
    assert bridge.routes_to_smtp(settings) is False


def test_a_malformed_routing_rules_value_does_not_raise(bridge):
    assert bridge.routes_to_smtp({"routing_rules": "not json at all"}) is False
    assert bridge.routes_to_smtp({"routing_rules": None}) is False
    assert bridge.routes_to_smtp(None) is False


def test_routing_rules_given_as_a_list_are_accepted(bridge):
    settings = {"routing_rules": [{"match": {"source": "channel-mapparr"},
                                   "channels": ["smtp"]}]}
    assert bridge.routes_to_smtp(settings) is True


# --------------------------------------------------------------------------- #
# should_emit
# --------------------------------------------------------------------------- #

def test_nothing_is_emitted_when_notifications_are_off(bridge):
    allowed, reason = bridge.should_emit({})
    assert allowed is False
    assert "off" in reason.lower() or "switched" in reason.lower()


def test_nothing_is_emitted_when_the_trigger_is_never(bridge):
    allowed, reason = bridge.should_emit({"notify_enabled": True,
                                          "notify_report_on": "never"})
    assert allowed is False
    assert "never" in reason.lower()


def test_emitting_is_allowed_when_the_toggle_is_on(bridge):
    allowed, reason = bridge.should_emit(ON)
    assert allowed is True
    assert reason is None


# --------------------------------------------------------------------------- #
# emit_reports, both directions
# --------------------------------------------------------------------------- #

def test_the_off_direction_sends_nothing(bridge, written):
    notify = RecordingNotify()
    result = bridge.emit_reports(notify, {"notify_enabled": False}, written)
    assert notify.calls == []
    assert result["sent"] == 0
    assert result["skipped_reason"]


def test_the_on_direction_actually_sends(bridge, written):
    notify = RecordingNotify()
    result = bridge.emit_reports(notify, ON, written)
    assert result["sent"] == 1
    assert len(notify.calls) == 1


def test_the_default_format_sends_the_html_only(bridge, written):
    notify = RecordingNotify()
    bridge.emit_reports(notify, ON, written)
    assert notify.calls[0]["attachment"].endswith(".html")


def test_choosing_csv_sends_the_csv_only(bridge, written):
    notify = RecordingNotify()
    bridge.emit_reports(notify, dict(ON, notify_report_format="csv"), written)
    assert len(notify.calls) == 1
    assert notify.calls[0]["attachment"].endswith(".csv")


def test_choosing_both_sends_two_separate_emails(bridge, written):
    """A notification carries one attachment, so both is two emails, not one
    email with two files."""
    notify = RecordingNotify()
    result = bridge.emit_reports(notify, dict(ON, notify_report_format="both"), written)
    assert result["sent"] == 2
    assert {pathlib.Path(c["attachment"]).suffix for c in notify.calls} == {".html", ".csv"}


def test_every_notification_is_informational_and_carries_no_dedup_key(bridge, written):
    """A report is not an incident. There is nothing to collapse a storm of, and
    it must not compete with a critical for gate bypass treatment."""
    notify = RecordingNotify()
    bridge.emit_reports(notify, dict(ON, notify_report_format="both"), written)
    for call in notify.calls:
        assert call["severity"] == "info"
        assert call["dedup_key"] is None
        assert call["source"] == bridge.SOURCE
        assert call["event"] == bridge.EVENT


def test_the_report_path_is_sent_as_the_url_as_well_as_the_attachment(bridge, written):
    """So a recipient whose gateway strips the attachment still knows where the
    file is."""
    notify = RecordingNotify()
    bridge.emit_reports(notify, ON, written)
    assert notify.calls[0]["url"] == notify.calls[0]["attachment"]


def test_a_file_that_is_not_on_disk_is_not_sent(bridge, written, tmp_path):
    """A green task result does not prove an artifact was published."""
    notify = RecordingNotify()
    missing = dict(written, html_path=str(tmp_path / "gone.html"))
    result = bridge.emit_reports(notify, ON, missing)
    assert result["sent"] == 0
    assert notify.calls == []


def test_a_failed_report_write_is_reported_rather_than_sent(bridge):
    notify = RecordingNotify()
    result = bridge.emit_reports(notify, ON, {"error": "disk full"})
    assert result["sent"] == 0
    assert result["skipped_reason"] == "disk full"
    assert notify.calls == []


def test_a_raising_notify_function_is_contained(bridge, written):
    """The isolation invariant: a bug in the emit path must never break the run
    that produced the data."""
    def explode(**kwargs):
        raise RuntimeError("boom")

    result = bridge.emit_reports(explode, ON, written)
    assert result["sent"] == 0
    assert "boom" in result["skipped_reason"]


def test_a_notify_function_returning_false_is_not_counted_as_sent(bridge, written):
    notify = RecordingNotify(result=False)
    result = bridge.emit_reports(notify, ON, written)
    assert result["sent"] == 0


def test_the_module_uses_no_em_dashes(bridge):
    source = pathlib.Path(bridge.__file__).read_text(encoding="utf-8")
    assert "—" not in source
