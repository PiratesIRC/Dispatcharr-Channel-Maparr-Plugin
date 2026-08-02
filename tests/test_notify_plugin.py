"""Tests for the plugin-side wiring of the emailed report.

These cover the pieces that live on the Plugin class rather than in reports.py or
notify_bridge.py: reading the M3U account names, deciding whether an emailed
report could actually arrive, building and emitting a report, and the on-demand
button.

Dispatcharr's plugin card renders only `message`, `error` and `file`. `status`
renders nowhere, so a failure that sets only `status` is pixel-identical to
success. Several tests below exist purely to pin that every failure path sets
`error`.
"""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def plugin(plugin_instance):
    return plugin_instance


# --------------------------------------------------------------------------- #
# Reading the M3U account names, which are the primary redaction input
# --------------------------------------------------------------------------- #

def test_the_account_names_are_returned_when_the_lookup_works(
        plugin, logger, monkeypatch, plugin_module):
    account = MagicMock()
    account.objects.all.return_value.values.return_value = [
        {"name": "provider.tv"}, {"name": "provider.tv-alt1"}]
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    assert plugin._get_m3u_account_names(logger) == ["provider.tv", "provider.tv-alt1"]


def test_a_failed_account_lookup_returns_none_rather_than_an_empty_list(
        plugin, logger, monkeypatch, plugin_module):
    """None and [] must not be confused. An empty list makes the scrub a silent
    no-op, and the account names are the primary redaction input here."""
    account = MagicMock()
    account.objects.all.side_effect = RuntimeError("database is down")
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    assert plugin._get_m3u_account_names(logger) is None


def test_an_installation_with_no_accounts_returns_an_empty_list(
        plugin, logger, monkeypatch, plugin_module):
    account = MagicMock()
    account.objects.all.return_value.values.return_value = []
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    assert plugin._get_m3u_account_names(logger) == []


# --------------------------------------------------------------------------- #
# Could an emailed report actually arrive?
# --------------------------------------------------------------------------- #

READY = {
    "enabled": True,
    "settings": {
        "smtp_server": "mail.example", "smtp_username": "u",
        "smtp_password": "p", "smtp_to": "someone@example",
        "routing_rules": '[{"match": {"source": "channel-mapparr", '
                         '"event": "usage_report"}, "channels": ["smtp"]}]',
    },
}


def _with_config(plugin, monkeypatch, config):
    monkeypatch.setattr(plugin, "_read_newsflasharr_config", lambda: config)


def test_a_fully_configured_newsflasharr_reports_no_problems(plugin, monkeypatch):
    _with_config(plugin, monkeypatch, READY)
    assert plugin._newsflasharr_readiness() == []


def test_newsflasharr_being_absent_is_reported(plugin, monkeypatch):
    _with_config(plugin, monkeypatch, None)
    problems = plugin._newsflasharr_readiness()
    assert problems and "not installed" in problems[0].lower()


def test_newsflasharr_being_disabled_is_reported(plugin, monkeypatch):
    _with_config(plugin, monkeypatch, dict(READY, enabled=False))
    assert any("not enabled" in p.lower() for p in plugin._newsflasharr_readiness())


def test_incomplete_smtp_settings_are_reported_without_echoing_a_value(
        plugin, monkeypatch):
    config = {"enabled": True,
              "settings": dict(READY["settings"], smtp_password="")}
    _with_config(plugin, monkeypatch, config)
    problems = plugin._newsflasharr_readiness()
    assert any("smtp" in p.lower() for p in problems)
    # The password value must never appear, and neither must any other value.
    assert not any("mail.example" in p for p in problems)


def test_a_missing_routing_rule_is_reported(plugin, monkeypatch):
    """Without a rule the queue write succeeds and the mail is delivered
    somewhere other than email, with no attachment, and nothing says so."""
    config = {"enabled": True,
              "settings": dict(READY["settings"], routing_rules="[]",
                               default_channels="apprise")}
    _with_config(plugin, monkeypatch, config)
    assert any("routing" in p.lower() for p in plugin._newsflasharr_readiness())


# --------------------------------------------------------------------------- #
# Building and emitting
# --------------------------------------------------------------------------- #

COLUMNS = [("channel_name", "Channel Name")]
ROWS = [{"channel_name": "WFLA"}]


def _emit(plugin, settings, logger, tmp_path, **kwargs):
    params = dict(title="Rename preview", columns=COLUMNS, rows=ROWS,
                  export_filename="channel_mapparr_preview_1.csv",
                  report_dir=str(tmp_path))
    params.update(kwargs)
    return plugin._build_and_emit_report(settings, logger, **params)


@pytest.fixture
def accounts(monkeypatch, plugin_module):
    account = MagicMock()
    account.objects.all.return_value.values.return_value = [{"name": "provider.tv"}]
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    return account


def test_nothing_is_built_when_notifications_are_off(
        plugin, logger, tmp_path, accounts):
    outcome = _emit(plugin, {"notify_enabled": False}, logger, tmp_path)
    assert outcome["sent"] == 0
    assert outcome["skipped_reason"]
    assert list(tmp_path.iterdir()) == [], "no report should be built at all"


def test_a_report_is_built_and_emitted_when_notifications_are_on(
        plugin, logger, tmp_path, accounts, monkeypatch):
    calls = []
    monkeypatch.setattr(plugin, "_notify_send", lambda **kw: calls.append(kw) or True)
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    outcome = _emit(plugin, {"notify_enabled": True}, logger, tmp_path)
    assert outcome["sent"] == 1
    assert len(calls) == 1
    assert calls[0]["attachment"].endswith(".html")


def test_a_failed_account_lookup_stops_the_report_and_says_why(
        plugin, logger, tmp_path, monkeypatch, plugin_module):
    account = MagicMock()
    account.objects.all.side_effect = RuntimeError("database is down")
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notify_send", lambda **kw: True)
    outcome = _emit(plugin, {"notify_enabled": True}, logger, tmp_path)
    assert outcome["sent"] == 0
    assert "account" in outcome["skipped_reason"].lower()


def test_an_unreachable_email_route_sets_a_blocking_error_for_the_card(
        plugin, logger, tmp_path, accounts, monkeypatch):
    """The automatic path runs the same readiness check as the button. A four
    second green toast is not a surface for this."""
    monkeypatch.setattr(plugin, "_newsflasharr_readiness",
                        lambda: ["Newsflasharr is installed but not enabled."])
    outcome = _emit(plugin, {"notify_enabled": True}, logger, tmp_path)
    assert outcome["sent"] == 0
    assert outcome["blocking_error"]
    assert "not enabled" in outcome["blocking_error"]


def test_the_emit_path_never_raises_into_the_run_that_produced_the_data(
        plugin, logger, tmp_path, accounts, monkeypatch):
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])

    def explode(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin, "_notify_send", explode)
    outcome = _emit(plugin, {"notify_enabled": True}, logger, tmp_path)
    assert outcome["sent"] == 0
    assert outcome["skipped_reason"]


def test_the_outcome_clause_is_short_enough_for_the_toast(plugin):
    """Dispatcharr shows roughly 280 characters, clipped from the middle with no
    ellipsis, so the clause appended to an action message must be small."""
    clause = plugin._report_outcome_clause({"sent": 2, "skipped_reason": None,
                                            "blocking_error": None})
    assert len(clause) < 60
    assert "queued" in clause.lower()


def test_the_outcome_clause_never_claims_the_mail_was_sent(plugin):
    """notify() returning True means durably queued, not delivered."""
    clause = plugin._report_outcome_clause({"sent": 1, "skipped_reason": None,
                                            "blocking_error": None})
    assert "sent" not in clause.lower()


def test_a_skipped_report_is_reported_in_the_clause(plugin):
    clause = plugin._report_outcome_clause(
        {"sent": 0, "skipped_reason": "the report trigger is set to never",
         "blocking_error": None})
    assert "never" in clause


def test_no_clause_is_added_when_notifications_are_switched_off(plugin):
    """An operator who has not opted in must not see report chatter on every
    action."""
    clause = plugin._report_outcome_clause(
        {"sent": 0, "skipped_reason": "notifications to Newsflasharr are switched off",
         "blocking_error": None})
    assert clause == ""


# --------------------------------------------------------------------------- #
# The on-demand button
# --------------------------------------------------------------------------- #

def test_the_export_cleaner_cannot_reach_an_emailed_report(plugin, plugin_module):
    """Clear CSV Exports deletes channel_mapparr_*.csv inside the export
    directory. A report file is also named channel_mapparr_*, so the ONLY thing
    keeping the cleaner away from a queued attachment is that the two live in
    different directories. That is one refactor away from deleting an attachment
    out from under a delivery retry, so it is pinned here."""
    reports = plugin._reports()
    export_dir = plugin_module.PluginConfig.EXPORT_DIR.rstrip("/")
    report_dir = reports.REPORT_DIR.rstrip("/")
    assert report_dir != export_dir
    assert not report_dir.startswith(export_dir + "/")


def test_report_files_are_not_written_where_nginx_serves_them_unauthenticated(plugin):
    """Dispatcharr's nginx serves /data/logos to the whole local network with no
    authentication."""
    assert not plugin._reports().REPORT_DIR.startswith("/data/logos")


def test_the_button_refuses_when_notifications_are_off(plugin, logger):
    result = plugin.email_report_now_action({"notify_enabled": False}, logger)
    assert result.get("error")


def test_the_button_refuses_when_newsflasharr_is_not_ready(
        plugin, logger, monkeypatch):
    monkeypatch.setattr(plugin, "_newsflasharr_readiness",
                        lambda: ["Newsflasharr is not installed."])
    result = plugin.email_report_now_action({"notify_enabled": True}, logger)
    assert result.get("error")
    assert "not installed" in result["error"]


def test_the_button_refuses_when_the_collector_is_not_running(
        plugin, logger, monkeypatch):
    """notify() creates the queue directory it writes into, so it returns True
    with Newsflasharr's collector dead and the event then rots unread."""
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notifier_alive", lambda: False)
    result = plugin.email_report_now_action({"notify_enabled": True}, logger)
    assert result.get("error")


def test_the_button_refuses_when_no_processed_channels_exist(
        plugin, logger, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notifier_alive", lambda: True)
    monkeypatch.setattr(plugin, "results_file", str(tmp_path / "absent.json"))
    result = plugin.email_report_now_action({"notify_enabled": True}, logger)
    assert result.get("error")


def test_the_button_builds_a_fresh_report_rather_than_resending_an_old_file(
        plugin, logger, monkeypatch, tmp_path, accounts):
    """Re-sending the newest file on disk races the pruner: that file is old
    enough to be prune-eligible, so a later run can delete it while its mail is
    still being retried."""
    import json
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"changes": [
        {"channel_id": 1, "current_name": "WFLA", "new_name": "NBC Tampa",
         "status": "Renamed"}]}), encoding="utf-8")
    monkeypatch.setattr(plugin, "results_file", str(results))
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notifier_alive", lambda: True)
    monkeypatch.setattr(plugin, "_report_dir", lambda: str(tmp_path / "reports"))
    calls = []
    monkeypatch.setattr(plugin, "_notify_send", lambda **kw: calls.append(kw) or True)

    result = plugin.email_report_now_action({"notify_enabled": True}, logger)

    assert not result.get("error"), result
    assert len(calls) == 1
    built = sorted((tmp_path / "reports").iterdir())
    assert built, "the button must build a report, not re-send one"


def test_the_button_says_queued_and_not_sent(
        plugin, logger, monkeypatch, tmp_path, accounts):
    import json
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"changes": [
        {"channel_id": 1, "current_name": "A", "new_name": "B"}]}), encoding="utf-8")
    monkeypatch.setattr(plugin, "results_file", str(results))
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notifier_alive", lambda: True)
    monkeypatch.setattr(plugin, "_report_dir", lambda: str(tmp_path / "reports"))
    monkeypatch.setattr(plugin, "_notify_send", lambda **kw: True)
    result = plugin.email_report_now_action({"notify_enabled": True}, logger)
    assert "queued" in result["message"].lower()
    assert "sent" not in result["message"].lower()


def test_the_button_ignores_the_never_trigger_because_pressing_it_is_the_request(
        plugin, logger, monkeypatch, tmp_path, accounts):
    import json
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"changes": [
        {"channel_id": 1, "current_name": "A", "new_name": "B"}]}), encoding="utf-8")
    monkeypatch.setattr(plugin, "results_file", str(results))
    monkeypatch.setattr(plugin, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin, "_notifier_alive", lambda: True)
    monkeypatch.setattr(plugin, "_report_dir", lambda: str(tmp_path / "reports"))
    calls = []
    monkeypatch.setattr(plugin, "_notify_send", lambda **kw: calls.append(kw) or True)
    plugin.email_report_now_action(
        {"notify_enabled": True, "notify_report_on": "never"}, logger)
    assert len(calls) == 1
