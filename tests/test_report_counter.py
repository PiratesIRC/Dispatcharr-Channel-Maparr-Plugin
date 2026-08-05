"""Tests for the published report counter, /data/<source>/report_count.json.

Newsflasharr's "Show status" action reads that file to print a report count
beside this plugin. Its reader refuses a malformed file SILENTLY: no count is
shown and nothing is logged anywhere. So every condition it enforces is pinned
here, on the reader's terms rather than on this module's terms.

The reader these cases were written against is newsflasharr/report_count.py in
the Newsflasharr repository, read on 2026-08-05, with MAX_BYTES = 4096. It lives
in another repository and cannot be hash pinned from here, so it is named rather
than copied.
"""
import json
import os
import stat

import pytest
from conftest import REAL_CONTAINER_PATHS

# The reader refuses a file whose size is GREATER THAN this. A file of exactly
# this size is accepted, which is why the boundary is tested on both sides.
READER_MAX_BYTES = 4096


@pytest.fixture
def counter_module():
    import channel_maparr.report_counter as module
    return module


@pytest.fixture
def counter_dir(tmp_path):
    return str(tmp_path / "counter")


def _counter_path(counter_dir):
    return os.path.join(counter_dir, "report_count.json")


def _write_raw(counter_dir, payload_bytes):
    """Put arbitrary bytes at the counter path, creating the directory."""
    os.makedirs(counter_dir, exist_ok=True)
    with open(_counter_path(counter_dir), "wb") as handle:
        handle.write(payload_bytes)


def _write_json(counter_dir, payload):
    _write_raw(counter_dir, json.dumps(payload).encode("utf-8"))


def _read_as_the_reader_does(counter_dir):
    """Open and parse exactly the way newsflasharr/report_count.py does.

    Reading the file back through this module's own reader would prove only
    that the module agrees with itself. A byte order mark, for instance, is
    invisible to a lenient reader and fatal to that one.
    """
    path = _counter_path(counter_dir)
    if os.stat(path).st_size > READER_MAX_BYTES:
        raise AssertionError("the reader would refuse this file on size")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------- #
# The path
# --------------------------------------------------------------------------- #

def test_the_counter_directory_is_named_for_the_notification_source():
    """The directory name IS the source string on the notification, because
    that is what the reader looks up. Deriving one from the other is what stops
    them drifting apart.

    Reads the captured production value, never the live module attribute: the
    autouse fixture in conftest redirects that to a temporary directory, so an
    assertion against the live value would either fail or be vacuous.
    """
    import channel_maparr.notify_bridge as bridge
    assert REAL_CONTAINER_PATHS["COUNTER_DIR"] == "/data/" + bridge.SOURCE


def test_the_production_counter_directory_was_actually_captured():
    """The test above is only meaningful if the captured value is the real one.
    Without this, a conftest change that captured the redirected temporary path
    would make it pass while proving nothing."""
    assert REAL_CONTAINER_PATHS["COUNTER_DIR"] == "/data/channel-mapparr"


def test_the_counter_directory_is_not_where_nginx_serves_files(counter_module):
    """Dispatcharr's nginx serves /data/logos to the whole local network with no
    authentication."""
    assert not REAL_CONTAINER_PATHS["COUNTER_DIR"].startswith("/data/logos")


def test_no_function_defaults_to_the_module_level_directory(counter_module):
    """A default argument binds at import time, which defeats the autouse
    redirect in conftest and lets a test write to the real container path. That
    passes on Windows, where the path resolves under the current drive root, and
    fails in continuous integration (bug-105)."""
    import inspect
    for name in ("read_count", "bump"):
        signature = inspect.signature(getattr(counter_module, name))
        first = list(signature.parameters.values())[0]
        assert first.name == "counter_dir"
        assert first.default is inspect.Parameter.empty, (
            f"{name} defaults its directory, which defeats the test redirect")


# --------------------------------------------------------------------------- #
# Reader conditions. Each one makes the reader show no count and log nothing.
# --------------------------------------------------------------------------- #

def test_an_absent_file_is_not_an_error(counter_module, counter_dir):
    """Absent means "this plugin publishes no counter", which is a legitimate
    state and not a fault."""
    assert counter_module.read_count(counter_dir) is None


def test_an_absent_directory_is_not_an_error(counter_module, tmp_path):
    assert counter_module.read_count(str(tmp_path / "nothing" / "here")) is None


def test_a_directory_in_place_of_the_file_is_refused(counter_module, counter_dir):
    os.makedirs(_counter_path(counter_dir), exist_ok=True)
    assert counter_module.read_count(counter_dir) is None


def test_a_zero_count_is_legal_and_is_not_confused_with_absence(
        counter_module, counter_dir):
    """Zero and absent are different facts. Zero means the counter exists and
    has never incremented, which is worth printing."""
    _write_json(counter_dir, {"reports_built": 0})
    assert counter_module.read_count(counter_dir) == 0


def test_a_boolean_is_refused(counter_module, counter_dir):
    """bool is a subclass of int in Python, so true would otherwise be accepted
    and displayed as "1 built"."""
    _write_json(counter_dir, {"reports_built": True})
    assert counter_module.read_count(counter_dir) is None


def test_a_float_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, {"reports_built": 2.9})
    assert counter_module.read_count(counter_dir) is None


def test_an_integral_float_is_refused_too(counter_module, counter_dir):
    """2.0 is still a JSON float. It is refused rather than truncated, because
    a silent under report looks deliberate."""
    _write_raw(counter_dir, b'{"reports_built": 2.0}')
    assert counter_module.read_count(counter_dir) is None


def test_a_negative_count_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, {"reports_built": -1})
    assert counter_module.read_count(counter_dir) is None


def test_a_string_of_digits_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, {"reports_built": "42"})
    assert counter_module.read_count(counter_dir) is None


def test_a_null_value_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, {"reports_built": None})
    assert counter_module.read_count(counter_dir) is None


def test_a_missing_key_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, {"something_else": 4})
    assert counter_module.read_count(counter_dir) is None


def test_a_non_object_top_level_is_refused(counter_module, counter_dir):
    _write_json(counter_dir, [42])
    assert counter_module.read_count(counter_dir) is None


def test_unparseable_content_is_refused(counter_module, counter_dir):
    _write_raw(counter_dir, b"not json at all")
    assert counter_module.read_count(counter_dir) is None


def test_extra_keys_alongside_the_count_are_tolerated(
        counter_module, counter_dir):
    """The reader reads one key and ignores the rest. Pinned so a later strict
    schema here cannot start refusing a file the reader accepts."""
    _write_json(counter_dir, {"reports_built": 7, "written_at": "yesterday"})
    assert counter_module.read_count(counter_dir) == 7


def test_a_byte_order_mark_is_refused(counter_module, counter_dir):
    """The reader opens with encoding utf-8, and a byte order mark makes its
    parse raise. This is why the writer must not emit one."""
    _write_raw(counter_dir, b"\xef\xbb\xbf" + b'{"reports_built": 3}')
    assert counter_module.read_count(counter_dir) is None


# The size cap is tested on both sides of the boundary rather than with one
# oversized file, because the reader refuses on GREATER THAN, so the two
# adjacent sizes are the only ones that pin which comparison was written.

def test_a_file_at_the_size_cap_is_accepted(counter_module, counter_dir):
    padding = "x" * (READER_MAX_BYTES - len('{"reports_built": 5, "pad": ""}'))
    _write_json(counter_dir, {"reports_built": 5, "pad": padding})
    assert os.stat(_counter_path(counter_dir)).st_size == READER_MAX_BYTES
    assert counter_module.read_count(counter_dir) == 5


def test_a_file_one_byte_over_the_size_cap_is_refused(
        counter_module, counter_dir):
    padding = "x" * (READER_MAX_BYTES + 1 - len('{"reports_built": 5, "pad": ""}'))
    _write_json(counter_dir, {"reports_built": 5, "pad": padding})
    assert os.stat(_counter_path(counter_dir)).st_size == READER_MAX_BYTES + 1
    assert counter_module.read_count(counter_dir) is None


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows does not honour POSIX permission bits")
def test_an_unreadable_file_is_refused_rather_than_raising(
        counter_module, counter_dir):
    _write_json(counter_dir, {"reports_built": 9})
    os.chmod(_counter_path(counter_dir), 0)
    try:
        if os.access(_counter_path(counter_dir), os.R_OK):
            pytest.skip("running as a user that ignores the permission bits")
        assert counter_module.read_count(counter_dir) is None
    finally:
        os.chmod(_counter_path(counter_dir), 0o600)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def test_the_first_bump_creates_the_directory_and_the_file(
        counter_module, counter_dir):
    assert counter_module.bump(counter_dir) == 1
    assert _read_as_the_reader_does(counter_dir) == {"reports_built": 1}


def test_successive_bumps_count_up(counter_module, counter_dir):
    assert counter_module.bump(counter_dir) == 1
    assert counter_module.bump(counter_dir) == 2
    assert counter_module.bump(counter_dir) == 3
    assert counter_module.read_count(counter_dir) == 3


def test_a_bump_on_an_existing_directory_does_not_fail(
        counter_module, counter_dir):
    os.makedirs(counter_dir, exist_ok=True)
    assert counter_module.bump(counter_dir) == 1


def test_the_written_file_is_what_the_reader_accepts(
        counter_module, counter_dir):
    """Read back the way the reader reads, not through this module."""
    counter_module.bump(counter_dir)
    payload = _read_as_the_reader_does(counter_dir)
    assert isinstance(payload, dict)
    assert payload["reports_built"] == 1
    assert not isinstance(payload["reports_built"], bool)


def test_the_written_file_carries_no_byte_order_mark(
        counter_module, counter_dir):
    counter_module.bump(counter_dir)
    with open(_counter_path(counter_dir), "rb") as handle:
        assert not handle.read(3).startswith(b"\xef\xbb\xbf")


def test_no_temporary_file_is_left_behind(counter_module, counter_dir):
    counter_module.bump(counter_dir)
    counter_module.bump(counter_dir)
    assert os.listdir(counter_dir) == ["report_count.json"]


def test_a_stale_temporary_file_does_not_wedge_the_next_write(
        counter_module, counter_dir):
    """A process killed between creating the temporary file and renaming it
    leaves one behind. A fixed temporary name plus an exclusive create would
    then fail forever, silently, and the counter would freeze at its last value
    while the reader kept printing it."""
    os.makedirs(counter_dir, exist_ok=True)
    with open(os.path.join(counter_dir, ".report_count_stale.tmp"), "w") as handle:
        handle.write("{}")
    assert counter_module.bump(counter_dir) == 1
    assert counter_module.bump(counter_dir) == 2


def test_a_corrupt_existing_file_restarts_the_count_rather_than_failing(
        counter_module, counter_dir):
    """The number is a floor, not an audit. Refusing to write because the old
    value could not be read would turn a recoverable state into a permanent
    one."""
    _write_raw(counter_dir, b"garbage")
    assert counter_module.bump(counter_dir) == 1


def test_a_bump_returns_none_and_does_not_raise_when_it_cannot_write(
        counter_module, tmp_path):
    """A file where the directory should be. Reporting the failure must never
    become the reason a report run fails."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    assert counter_module.bump(str(blocker)) is None


def test_a_failure_is_logged_at_warning_not_debug(counter_module, tmp_path):
    """The container runs at log level INFO, so a debug line reaches nothing.
    A counter that can never be written looks exactly like a plugin that
    publishes no counter, on both the reading and the writing side, so the
    container log is the only place it can be seen at all."""
    from unittest.mock import MagicMock
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    logger = MagicMock()
    counter_module.bump(str(blocker), logger)
    assert logger.warning.called
    assert not logger.debug.called


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows does not honour POSIX permission bits")
def test_the_written_file_is_private_to_its_owner(counter_module, counter_dir):
    counter_module.bump(counter_dir)
    mode = stat.S_IMODE(os.stat(_counter_path(counter_dir)).st_mode)
    assert mode == 0o600


@pytest.mark.skipif(os.name == "nt",
                    reason="Windows does not honour POSIX permission bits")
def test_a_wider_mode_is_still_read(counter_module, counter_dir):
    """The writer sets 0600 but must not REQUIRE it. A file repaired by hand,
    or written by an earlier version, comes back at 0644 and must still count.
    A sibling plugin already publishes this same file at 0644."""
    counter_module.bump(counter_dir)
    os.chmod(_counter_path(counter_dir), 0o644)
    assert counter_module.read_count(counter_dir) == 1
    assert counter_module.bump(counter_dir) == 2


# --------------------------------------------------------------------------- #
# Module hygiene, matching the guards the sibling modules carry
# --------------------------------------------------------------------------- #

def test_the_module_imports_nothing_from_django_or_dispatcharr(plugin_dir):
    """Every module here except plugin.py loads standalone. An import of apps.*
    or django.* would make this one unloadable outside the container and would
    break the test suite's synthetic package."""
    import ast
    source = (plugin_dir / "report_counter.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not name.startswith(("apps", "django", "core.")), name


def test_the_module_uses_no_em_dashes(plugin_dir):
    """A standing operator instruction across this workspace."""
    source = (plugin_dir / "report_counter.py").read_text(encoding="utf-8")
    assert "—" not in source


# --------------------------------------------------------------------------- #
# Wiring: the counter moves when a report is built, and only then
#
# Unit tests on the module above prove it counts. They do not prove anything
# calls it. These drive the real Plugin._build_and_emit_report.
# --------------------------------------------------------------------------- #

COLUMNS = [("channel_name", "Channel Name")]
ROWS = [{"channel_name": "WFLA"}]


@pytest.fixture
def wired(plugin_instance, monkeypatch, plugin_module, tmp_path):
    """A plugin whose report path can run end to end without a container.

    Returns (plugin, counter_dir). The counter directory is set through the
    module attribute the plugin reads at call time, never as a default argument.
    """
    from unittest.mock import MagicMock

    import channel_maparr.report_counter as counter_module

    account = MagicMock()
    account.objects.all.return_value.values.return_value = [{"name": "provider.tv"}]
    monkeypatch.setattr(plugin_module, "M3UAccount", account)
    monkeypatch.setattr(plugin_instance, "_newsflasharr_readiness", lambda: [])
    monkeypatch.setattr(plugin_instance, "_notify_send", lambda **kw: True)

    counter_dir = str(tmp_path / "wired_counter")
    monkeypatch.setattr(counter_module, "COUNTER_DIR", counter_dir)
    return plugin_instance, counter_dir


def _run_report(plugin, logger, tmp_path, settings=None):
    return plugin._build_and_emit_report(
        settings if settings is not None else {"notify_enabled": True},
        logger,
        title="Rename preview", columns=COLUMNS, rows=ROWS,
        export_filename="channel_mapparr_preview_1.csv",
        report_dir=str(tmp_path / "reports"))


def test_a_successful_build_moves_the_counter_by_exactly_one(
        wired, logger, tmp_path, counter_module):
    plugin, counter_dir = wired
    assert counter_module.read_count(counter_dir) is None
    _run_report(plugin, logger, tmp_path)
    assert counter_module.read_count(counter_dir) == 1
    _run_report(plugin, logger, tmp_path)
    assert counter_module.read_count(counter_dir) == 2


def test_a_report_that_failed_to_write_does_not_move_the_counter(
        wired, logger, tmp_path, counter_module, monkeypatch):
    """The whole point of the counter. A report writer that degrades instead of
    raising makes a failed publish look identical to a good one, and a counter
    that incremented anyway would turn that failure into apparent success."""
    plugin, counter_dir = wired
    reports = plugin._reports()
    monkeypatch.setattr(
        reports, "write_report",
        lambda model, report_dir, now: {"html_path": None, "csv_path": None,
                                        "error": "could not write the report"})
    outcome = _run_report(plugin, logger, tmp_path)
    assert outcome["sent"] == 0
    assert counter_module.read_count(counter_dir) is None


def test_notifications_switched_off_leaves_the_counter_untouched(
        wired, logger, tmp_path, counter_module):
    plugin, counter_dir = wired
    _run_report(plugin, logger, tmp_path, settings={"notify_enabled": False})
    assert counter_module.read_count(counter_dir) is None


def test_both_formats_send_two_notifications_but_count_one_build(
        wired, logger, tmp_path, counter_module):
    """One build writes an HTML file and a CSV file and can send two emails. The
    number counts builds, so it must not follow the email count."""
    plugin, counter_dir = wired
    outcome = _run_report(plugin, logger, tmp_path,
                          settings={"notify_enabled": True,
                                    "notify_report_format": "both"})
    assert outcome["sent"] == 2
    assert counter_module.read_count(counter_dir) == 1


def test_a_build_whose_delivery_fails_still_counts(
        wired, logger, tmp_path, counter_module, monkeypatch):
    """The file was written, which is what the number reports. Delivery is
    Newsflasharr's business and is recorded separately by it."""
    plugin, counter_dir = wired

    def explode(**kwargs):
        raise RuntimeError("the queue is unreachable")

    monkeypatch.setattr(plugin, "_notify_send", explode)
    outcome = _run_report(plugin, logger, tmp_path)
    assert outcome["sent"] == 0
    assert counter_module.read_count(counter_dir) == 1


def test_a_counter_that_cannot_be_written_does_not_break_the_report(
        wired, logger, tmp_path, monkeypatch):
    """A file where the counter directory should be. The report must still be
    built and the notification must still be queued."""
    plugin, _ = wired
    import channel_maparr.report_counter as module
    blocker = tmp_path / "blocked_counter"
    blocker.write_text("not a directory")
    monkeypatch.setattr(module, "COUNTER_DIR", str(blocker))
    outcome = _run_report(plugin, logger, tmp_path)
    assert outcome["sent"] == 1
    assert not outcome["skipped_reason"]


# --------------------------------------------------------------------------- #
# An AST guard pinning WHERE the increment happens.
#
# The behavioural tests above catch outright deletion. They do not catch a
# SECOND call site being added later, which would double count, nor the call
# being moved into a function that runs on some other path. Carries synthetic
# self-tests, because an AST guard with no positive fixture is inert: it returns
# exit 0 for months while proving nothing.
# --------------------------------------------------------------------------- #

def _functions_calling(tree, method_name):
    import ast
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == method_name):
                found.add(node.name)
    return found


@pytest.fixture(scope="module")
def plugin_tree():
    import ast
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent
            / "Channel-Maparr" / "plugin.py")
    return ast.parse(path.read_text(encoding="utf-8"))


def test_exactly_one_function_increments_the_counter(plugin_tree):
    assert _functions_calling(plugin_tree, "bump") == {"_build_and_emit_report"}


def test_the_incrementing_function_is_the_one_that_writes_the_report(
        plugin_tree):
    """If a second report writer were ever added it would have to go through the
    same helper, so the counter cannot be bypassed."""
    assert (_functions_calling(plugin_tree, "write_report")
            == {"_build_and_emit_report"})


def test_the_guard_detects_a_call_when_one_is_present():
    import ast
    planted = ast.parse(
        "class C:\n"
        "    def writer(self):\n"
        "        self.counter().bump(self._counter_dir())\n")
    assert _functions_calling(planted, "bump") == {"writer"}


def test_the_guard_detects_the_absence_of_a_call():
    import ast
    planted = ast.parse(
        "class C:\n"
        "    def writer(self):\n"
        "        self.something_else()\n")
    assert _functions_calling(planted, "bump") == set()


def test_the_guard_would_fail_on_a_second_call_site():
    import ast
    planted = ast.parse(
        "class C:\n"
        "    def writer(self):\n"
        "        self.counter().bump(self._counter_dir())\n"
        "    def other(self):\n"
        "        self.counter().bump(self._counter_dir())\n")
    assert _functions_calling(planted, "bump") != {"writer"}
