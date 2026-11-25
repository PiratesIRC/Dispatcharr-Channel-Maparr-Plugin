"""Dispatcharr 0.34 task banner for the two long actions.

On 0.34 every plugin action runs in a Celery task. A daemon thread ends that
task at once, so the banner only ever said "started in background". The two
long actions are therefore marked "async" (plugin.json and the Plugin.actions
list) and run inline when a Celery task is current. Outside a task (0.33) the
thread path is kept. Progress also goes to the task context's reporter.
"""
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ASYNC_IDS = {"import_m3u_streams", "create_channels_from_streams"}
PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


def _manifest():
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


def _bare_plugin(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    inst._thread = None
    inst._thread_lock = threading.Lock()
    inst._stop_event = threading.Event()
    inst._last_bg_result = None
    return inst


def _in_celery(monkeypatch, plugin_module, value):
    monkeypatch.setattr(plugin_module.Plugin, "_in_celery_task",
                        staticmethod(lambda: value))


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #
def test_plugin_json_marks_exactly_the_two_actions_async():
    served = {a["id"] for a in _manifest()["actions"] if a.get("async")}
    assert served == ASYNC_IDS


def test_plugin_py_marks_the_same_two_actions_async(plugin_module):
    served = {a["id"] for a in plugin_module.Plugin.actions if a.get("async")}
    assert served == ASYNC_IDS


def test_plugin_json_declares_background_tasks():
    assert "background_tasks" in _manifest()["capabilities"]


# --------------------------------------------------------------------------- #
# Import M3U Streams: inline inside a task, thread outside
# --------------------------------------------------------------------------- #
def test_import_runs_inline_inside_a_celery_task(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)
    real = {"status": "success", "message": "Imported 4 channels."}
    monkeypatch.setattr(inst, "_do_import_m3u_streams", lambda s, l: dict(real))
    monkeypatch.setattr(inst, "_try_start_thread",
                        lambda *a, **k: pytest.fail("thread path used inside a task"))

    result = inst.import_m3u_streams_action({}, MagicMock())

    assert result == real
    assert "background" not in result
    assert inst._thread is None, "the operation slot must be released"


def test_import_inline_failure_is_returned_not_raised(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)

    def boom(settings, logger):
        raise RuntimeError("stream vanished")

    monkeypatch.setattr(inst, "_do_import_m3u_streams", boom)

    result = inst.import_m3u_streams_action({}, MagicMock())

    assert result["status"] == "error"
    assert "stream vanished" in result["error"]


def test_import_keeps_the_thread_path_outside_a_task(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, False)
    started = []

    def fake_start(target, args):
        started.append(target)
        return True

    monkeypatch.setattr(inst, "_try_start_thread", fake_start)

    result = inst.import_m3u_streams_action({}, MagicMock())

    assert started == [inst._do_import_m3u_streams_bg]
    assert result["background"] is True


# --------------------------------------------------------------------------- #
# The operation slot
# --------------------------------------------------------------------------- #
def test_inline_run_refuses_while_another_operation_holds_the_slot(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)
    release = threading.Event()
    holder = threading.Thread(target=release.wait, daemon=True)
    holder.start()
    inst._thread = holder
    body = MagicMock(return_value={"status": "success"})
    monkeypatch.setattr(inst, "_do_import_m3u_streams", body)
    try:
        result = inst.import_m3u_streams_action({}, MagicMock())
    finally:
        release.set()
        holder.join(timeout=5)

    assert result["status"] == "error"
    assert "already running" in result["error"]
    body.assert_not_called()
    assert inst._thread is holder, "a refused run must not clear another operation's slot"


def test_inline_run_holds_the_slot_while_it_works(plugin_module):
    inst = _bare_plugin(plugin_module)
    seen = {}

    def body():
        seen["holder"] = inst._thread
        return {"status": "success"}

    inst._run_inline_task(body)

    assert seen["holder"] is threading.current_thread()
    assert inst._thread is None


def test_inline_slot_is_released_when_the_body_raises(plugin_module):
    inst = _bare_plugin(plugin_module)

    def body():
        raise ValueError("bad")

    with pytest.raises(ValueError):
        inst._run_inline_task(body)
    assert inst._thread is None


# --------------------------------------------------------------------------- #
# Create Channels From Streams: inline inside a task
# --------------------------------------------------------------------------- #
def test_create_body_returns_the_real_result(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    monkeypatch.setattr(plugin_module, "send_websocket_update", MagicMock())
    monkeypatch.setattr(inst, "_create_seed_channels", lambda plan, gid, s, l: [1, 2])
    monkeypatch.setattr(inst, "_export_seed_preview",
                        lambda plan, s, l: ("/data/exports/x.csv", "x.csv"))

    result = inst._create_channels_body({}, MagicMock(), MagicMock(), 7)

    assert result["status"] == "success"
    assert result["message"].startswith("Created 2 channels.")
    assert result["file"] == "/data/exports/x.csv"
    assert inst._last_bg_result == result


def test_create_body_returns_errors_not_raises(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    monkeypatch.setattr(plugin_module, "send_websocket_update", MagicMock())

    def boom(plan, gid, s, l):
        raise RuntimeError("group gone")

    monkeypatch.setattr(inst, "_create_seed_channels", boom)

    result = inst._create_channels_body({}, MagicMock(), MagicMock(), 7)

    assert result == {"status": "error", "error": "group gone"}


def test_create_action_runs_inline_inside_a_task(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)
    settings = {"seed_source_groups": "Stream Group", "seed_target_group": "Target"}
    monkeypatch.setattr(inst, "_check_group_destinations_not_ignored", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_get_all_groups", lambda logger: [{"id": 7, "name": "Target"}])
    monkeypatch.setattr(inst, "_load_channel_data", lambda s, l: True)
    monkeypatch.setattr(inst, "_collect_unattached_streams",
                        lambda names, logger: [{"id": 1, "name": "US: ABC 7"}])
    monkeypatch.setattr(inst, "_seed_resolver",
                        lambda s, l: ((lambda name: None), (lambda name: False)))
    monkeypatch.setattr(inst, "_existing_channel_names", lambda: set())
    plan = MagicMock(create=[1], skip=[], excluded=[], unresolved=[])
    monkeypatch.setattr(plugin_module, "build_seed_plan", lambda *a, **k: plan)
    real = {"status": "success", "message": "Created 1 channels."}
    monkeypatch.setattr(inst, "_create_channels_body", lambda s, l, p, gid: dict(real))
    monkeypatch.setattr(inst, "_try_start_thread",
                        lambda *a, **k: pytest.fail("thread path used inside a task"))

    result = inst.create_channels_from_streams_action(settings, MagicMock())

    assert result == real


# --------------------------------------------------------------------------- #
# Progress forwarding
# --------------------------------------------------------------------------- #
def test_run_hands_the_reporter_to_an_async_action_inside_a_task(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)
    monkeypatch.setattr(inst, "_report_usage", lambda *a, **k: None)
    reporter = MagicMock()
    seen = {}

    def body(action, params, context):
        seen["during"] = plugin_module._TASK_REPORTER
        return {"status": "success"}

    monkeypatch.setattr(inst, "_run_body", body)
    inst.run("import_m3u_streams", {}, {"settings": {}, "logger": MagicMock(),
                                        "report_progress": reporter})

    assert seen["during"] is reporter
    assert plugin_module._TASK_REPORTER is None, "the reporter must not outlive the run"


def test_run_does_not_hand_a_reporter_to_a_sync_action(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, True)
    monkeypatch.setattr(inst, "_report_usage", lambda *a, **k: None)
    seen = {}

    def body(action, params, context):
        seen["during"] = plugin_module._TASK_REPORTER
        return {"status": "success"}

    monkeypatch.setattr(inst, "_run_body", body)
    inst.run("rename_channels", {}, {"settings": {}, "logger": MagicMock(),
                                     "report_progress": MagicMock()})

    assert seen["during"] is None


def test_run_outside_a_task_hands_no_reporter(plugin_module, monkeypatch):
    inst = _bare_plugin(plugin_module)
    _in_celery(monkeypatch, plugin_module, False)
    monkeypatch.setattr(inst, "_report_usage", lambda *a, **k: None)
    seen = {}

    def body(action, params, context):
        seen["during"] = plugin_module._TASK_REPORTER
        return {"status": "success"}

    monkeypatch.setattr(inst, "_run_body", body)
    inst.run("import_m3u_streams", {}, {"settings": {}, "logger": MagicMock(),
                                        "report_progress": MagicMock()})

    assert seen["during"] is None


def test_progress_tracker_finish_reaches_the_reporter(plugin_module, monkeypatch):
    reporter = MagicMock()
    monkeypatch.setattr(plugin_module, "_TASK_REPORTER", reporter)
    monkeypatch.setattr(plugin_module, "send_websocket_update", MagicMock())
    tracker = plugin_module.ProgressTracker(3, "import_streams", MagicMock())

    tracker.finish("Imported 3 channels.")

    reporter.assert_called_with(100, "Imported 3 channels.")


def test_progress_tracker_update_reaches_the_reporter(plugin_module, monkeypatch, tmp_path):
    reporter = MagicMock()
    monkeypatch.setattr(plugin_module, "_TASK_REPORTER", reporter)
    monkeypatch.setattr(plugin_module, "send_websocket_update", MagicMock())
    monkeypatch.setattr(plugin_module.PluginConfig, "PROGRESS_FILE", str(tmp_path / "progress.json"))
    tracker = plugin_module.ProgressTracker(4, "import_streams", MagicMock())
    tracker.last_update_time = 0.0

    tracker.update(2)

    percent, message = reporter.call_args[0]
    assert percent == 50
    assert "import_streams" in message


def test_forwarding_clamps_and_never_raises(plugin_module, monkeypatch):
    reporter = MagicMock(side_effect=RuntimeError("socket closed"))
    monkeypatch.setattr(plugin_module, "_TASK_REPORTER", reporter)

    plugin_module._forward_task_progress(150, "over")
    plugin_module._forward_task_progress(-5, "under")

    assert [c[0] for c in reporter.call_args_list] == [(100, "over"), (0, "under")]


def test_forwarding_is_a_no_op_without_a_reporter(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module, "_TASK_REPORTER", None)
    plugin_module._forward_task_progress(10, "nothing listens")
