"""Deleting this plugin's own CSV exports by age.

/data/exports is SHARED. Measured on the live installation while this was
written: 65 stream_mapparr_ files, 26 epg_janitor_, 14
event_channel_managarr_, 10 lineuparr_, 5 iptv_checker_results_ and 4
channel_mapparr_. Selecting on the .csv suffix alone, or on a *.csv glob, would
have destroyed over a hundred files belonging to five other projects on the
first run with a seven day setting. So selection is scoped to this plugin's own
filename prefix AND the .csv suffix, and a mutation that drops either check is
caught here.

Four further rules exist because this deletes files on other people's
installations:

  - it is off unless a positive number of days is set, so nobody loses files
    merely by upgrading;
  - the file just written is never deleted, whatever the age arithmetic says;
  - at least one of this plugin's files always survives, so a small number
    cannot empty the directory;
  - it never raises, because it runs immediately after a successful export and
    a failure to tidy up must not turn that export into a reported error.

Note on how these tests are written: every test of the age rule and of the
off-by-default rule uses SEVERAL old files. With a single file the
one-always-survives rule keeps it regardless, so a single-file test passes even
with the guard it names deleted. That happened during the equivalent work on
IPTV Checker and was found by mutation testing, not by reading the tests.
"""

import logging
import os
import time

import pytest

DAY = 86400.0
NOW = 1_800_000_000.0
MINE = "channel_mapparr_"

# Real filename prefixes of the other plugins sharing /data/exports.
FOREIGN = [
    "stream_mapparr_sorted_20260101_000000.csv",
    "epg_janitor_report_20260101_000000.csv",
    "event_channel_managarr_20260101_000000.csv",
    "lineuparr_preview_20260101_000000.csv",
    "iptv_checker_results_20260101_000000.csv",
]


def _entry(name, days_old):
    return (name, NOW - days_old * DAY)


def _plan(plugin_instance, entries, days=5, now=NOW, protect=None):
    return plugin_instance._csv_exports_to_delete(entries, days, now, protect)


# --- off unless configured --------------------------------------------------

@pytest.mark.parametrize("days", [0, None, "", "   ", -1, -30, "abc", object()])
def test_no_retention_configured_deletes_nothing(plugin_instance, days):
    """Several old files, not one, or the survivor rule would pass this anyway."""
    entries = [_entry(MINE + "a.csv", 400), _entry(MINE + "b.csv", 300),
               _entry(MINE + "c.csv", 500), _entry(MINE + "d.csv", 600)]
    assert _plan(plugin_instance, entries, days=days) == []


# --- the age rule -----------------------------------------------------------

def test_files_older_than_the_limit_are_deleted(plugin_instance):
    entries = [_entry(MINE + "old_a.csv", 9), _entry(MINE + "old_b.csv", 30),
               _entry(MINE + "old_c.csv", 60), _entry(MINE + "new.csv", 1)]
    assert _plan(plugin_instance, entries, days=5) == [
        MINE + "old_a.csv", MINE + "old_b.csv", MINE + "old_c.csv"]


def test_files_younger_than_the_limit_are_kept(plugin_instance):
    entries = [_entry(MINE + "a.csv", 1), _entry(MINE + "b.csv", 2),
               _entry(MINE + "c.csv", 4)]
    assert _plan(plugin_instance, entries, days=5) == []


def test_the_boundary_is_not_deleted(plugin_instance):
    """Exactly five days old is not OLDER than five days."""
    entries = [_entry(MINE + "edge_a.csv", 5), _entry(MINE + "edge_b.csv", 5),
               _entry(MINE + "keep.csv", 0)]
    assert _plan(plugin_instance, entries, days=5) == []


def test_just_past_the_boundary_is_deleted(plugin_instance):
    entries = [_entry(MINE + "edge_a.csv", 5.001),
               _entry(MINE + "edge_b.csv", 5.002),
               _entry(MINE + "keep.csv", 0)]
    assert _plan(plugin_instance, entries, days=5) == [
        MINE + "edge_a.csv", MINE + "edge_b.csv"]


# --- never another plugin's files -------------------------------------------

def test_another_plugins_csv_is_never_deleted(plugin_instance):
    """The directory is shared with five other plugins. This is the whole risk."""
    entries = [_entry(name, 400) for name in FOREIGN]
    entries += [_entry(MINE + "mine_a.csv", 400), _entry(MINE + "mine_b.csv", 300),
                _entry(MINE + "recent.csv", 0)]

    plan = _plan(plugin_instance, entries, days=5)

    assert plan == [MINE + "mine_a.csv", MINE + "mine_b.csv"]
    for name in FOREIGN:
        assert name not in plan, f"another plugin's file was selected: {name}"


def test_a_prefix_that_merely_contains_ours_is_not_ours(plugin_instance):
    """The prefix must match at the START, not anywhere in the name."""
    entries = [_entry("backup_channel_mapparr_old.csv", 400),
               _entry("old_channel_mapparr_preview.csv", 400),
               _entry(MINE + "recent.csv", 0)]
    assert _plan(plugin_instance, entries, days=5) == []


def test_a_file_that_is_not_a_csv_is_never_deleted(plugin_instance):
    entries = [_entry(MINE + "notes.txt", 400), _entry(MINE + "state.json", 400),
               _entry(MINE + "dump.csv.gz", 400),
               _entry(MINE + "real_a.csv", 400), _entry(MINE + "real_b.csv", 300),
               _entry(MINE + "recent.csv", 0)]

    plan = _plan(plugin_instance, entries, days=5)

    assert plan == [MINE + "real_a.csv", MINE + "real_b.csv"]


# --- protections ------------------------------------------------------------

def test_the_file_just_written_is_never_deleted(plugin_instance):
    """Protected whatever the arithmetic says, so its age here is 400 days."""
    entries = [_entry(MINE + "just_written.csv", 400),
               _entry(MINE + "other_a.csv", 400),
               _entry(MINE + "other_b.csv", 300)]

    plan = _plan(plugin_instance, entries, days=5,
                 protect=MINE + "just_written.csv")

    assert MINE + "just_written.csv" not in plan
    assert plan == [MINE + "other_a.csv", MINE + "other_b.csv"]


def test_at_least_one_file_always_survives(plugin_instance):
    """Every file is old. Keeping the newest stops a small number emptying it."""
    entries = [_entry(MINE + "a.csv", 400), _entry(MINE + "b.csv", 300),
               _entry(MINE + "c.csv", 500)]

    plan = _plan(plugin_instance, entries, days=5)

    assert MINE + "b.csv" not in plan, "the newest of this plugin's files must survive"
    assert plan == [MINE + "a.csv", MINE + "c.csv"]


def test_a_single_old_file_is_kept(plugin_instance):
    assert _plan(plugin_instance, [_entry(MINE + "only.csv", 900)], days=1) == []


def test_the_survivor_is_never_another_plugins_file(plugin_instance):
    """A newer foreign file must not license deleting every one of ours."""
    entries = [_entry("stream_mapparr_sorted_x.csv", 0),
               _entry(MINE + "mine_a.csv", 400), _entry(MINE + "mine_b.csv", 300)]

    plan = _plan(plugin_instance, entries, days=5)

    assert plan == [MINE + "mine_a.csv"]
    assert MINE + "mine_b.csv" not in plan


# --- unreadable timestamps --------------------------------------------------

def test_an_empty_directory_is_fine(plugin_instance):
    assert _plan(plugin_instance, [], days=5) == []


# --- a retention value that is a number but not an integer -------------------

@pytest.mark.parametrize("stored", ["7.0", "7.5", 7.0, 7.9, " 7 "])
def test_a_retention_value_that_is_not_a_whole_number_is_still_honoured(
        plugin_instance, stored):
    """int("7.0") raises, so the whole setting used to be a silent no-op.

    The setting is a number field, and nothing here controls what Dispatcharr
    stores behind it. A value the operator explicitly set failing to parse, and
    then behaving exactly like the 0 that means keep everything, is the worst of
    both: the files are not deleted and nothing says why.
    """
    entries = [_entry(MINE + "a.csv", 400), _entry(MINE + "b.csv", 300),
               _entry(MINE + "c.csv", 500)]
    plan = _plan(plugin_instance, entries, days=stored)
    assert plan == [MINE + "a.csv", MINE + "c.csv"], plan


def test_a_retention_value_that_cannot_be_read_at_all_is_reported(
        plugin_instance, plugin_module, exports_dir, caplog):
    """A silent no-op on a setting somebody turned on is indistinguishable from
    the default. It has to say something."""
    _seed(exports_dir, MINE + "a.csv", 400)
    _seed(exports_dir, MINE + "b.csv", 300)
    _seed(exports_dir, MINE + "keep.csv", 0)

    with caplog.at_level(logging.WARNING):
        removed = plugin_instance._prune_csv_exports("every other Tuesday")

    assert removed == 0
    assert any("every other Tuesday" in record.getMessage()
               for record in caplog.records), \
        "nothing was logged about a retention value that could not be read"


def test_a_retention_value_of_zero_is_not_reported(
        plugin_instance, plugin_module, exports_dir, caplog):
    """Zero is the default and means keep everything. Warning about it every
    time an export is written would train the operator to ignore the log."""
    _seed(exports_dir, MINE + "a.csv", 400)

    with caplog.at_level(logging.WARNING):
        plugin_instance._prune_csv_exports(0)

    assert caplog.records == [], [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("mtime", [None, "not a number", object()])
def test_an_unreadable_timestamp_is_left_alone(plugin_instance, mtime):
    entries = [(MINE + "odd.csv", mtime), _entry(MINE + "recent.csv", 0)]
    assert _plan(plugin_instance, entries, days=5) == []


def test_a_timestamp_that_is_not_a_number_cannot_become_the_survivor(plugin_instance):
    """Keeping a not-a-number timestamp would let it stand in as the survivor.

    Comparisons against it are all false, so it wins the "which is newest" test,
    and then every real file is deleted instead of one being kept.
    """
    entries = [(MINE + "odd.csv", float("nan")),
               _entry(MINE + "old_a.csv", 400),
               _entry(MINE + "old_b.csv", 300)]

    plan = _plan(plugin_instance, entries, days=5)

    assert plan == [MINE + "old_a.csv"]
    assert MINE + "old_b.csv" not in plan, "the newest real file must survive"
    assert MINE + "odd.csv" not in plan


# --- the half that touches the filesystem -----------------------------------

@pytest.fixture
def exports_dir(plugin_module, tmp_path):
    """The redirected export directory, created.

    Reads PluginConfig.EXPORT_DIR rather than setting it, so this test file
    stays inside the autouse redirect in conftest and cannot write to /data.
    """
    directory = plugin_module.PluginConfig.EXPORT_DIR
    assert str(tmp_path) in str(directory), "the export directory was not redirected"
    os.makedirs(directory, exist_ok=True)
    import pathlib
    return pathlib.Path(directory)


def _seed(directory, name, days_old, now=None):
    now = time.time() if now is None else now
    path = directory / name
    path.write_text("x", encoding="utf-8")
    stamp = now - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_pruning_removes_only_the_old_files_of_this_plugin(
        plugin_instance, exports_dir):
    old_a = _seed(exports_dir, MINE + "old_a.csv", 30)
    old_b = _seed(exports_dir, MINE + "old_b.csv", 60)
    recent = _seed(exports_dir, MINE + "recent.csv", 1)
    foreign = [_seed(exports_dir, name, 30) for name in FOREIGN]

    removed = plugin_instance._prune_csv_exports(5)

    assert removed == 2
    assert not old_a.exists()
    assert not old_b.exists()
    assert recent.exists()
    for path in foreign:
        assert path.exists(), f"another plugin's file was deleted: {path.name}"


def test_pruning_protects_the_file_just_written(plugin_instance, exports_dir):
    just_written = _seed(exports_dir, MINE + "just.csv", 30)
    other_a = _seed(exports_dir, MINE + "other_a.csv", 30)
    other_b = _seed(exports_dir, MINE + "other_b.csv", 40)

    removed = plugin_instance._prune_csv_exports(5, protect=MINE + "just.csv")

    assert just_written.exists()
    assert removed == 2
    assert not other_a.exists()
    assert not other_b.exists()


def test_pruning_a_missing_directory_does_not_raise(plugin_instance, plugin_module,
                                                    monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORT_DIR",
                        str(tmp_path / "not-there"))
    assert plugin_instance._prune_csv_exports(5) == 0


def test_pruning_survives_a_file_it_cannot_delete(plugin_instance, plugin_module,
                                                  exports_dir, monkeypatch):
    """It runs after a successful export and must never turn one into a failure."""
    _seed(exports_dir, MINE + "a.csv", 30)
    _seed(exports_dir, MINE + "b.csv", 40)
    _seed(exports_dir, MINE + "keep.csv", 0)

    def boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr(plugin_module.os, "remove", boom)

    assert plugin_instance._prune_csv_exports(5) == 0


def test_pruning_asks_the_age_of_this_plugins_files_only(
        plugin_instance, plugin_module, exports_dir, monkeypatch):
    """The shared directory holds far more of other plugins' files than of ours.

    Measured on the live installation: 120 of about 124 files belong to five
    other plugins. Asking the filesystem for the age of each of those, only to
    discard it on the name, is work done inside the web request for two of the
    four export paths. The name test in _csv_exports_to_delete is the guard and
    must stay; this pins that the same test is applied before the stat.
    """
    _seed(exports_dir, MINE + "ours.csv", 30)
    for name in FOREIGN:
        _seed(exports_dir, name, 30)
    _seed(exports_dir, "notes.txt", 30)

    asked = []
    real_getmtime = plugin_module.os.path.getmtime

    def counting(path):
        asked.append(os.path.basename(path))
        return real_getmtime(path)

    monkeypatch.setattr(plugin_module.os.path, "getmtime", counting)
    plugin_instance._prune_csv_exports(5)

    assert asked == [MINE + "ours.csv"], asked


def test_pruning_reads_no_directory_at_all_when_it_is_switched_off(
        plugin_instance, plugin_module, exports_dir, monkeypatch):
    """At the default of 0 nothing can be deleted, so nothing needs listing."""
    _seed(exports_dir, MINE + "old.csv", 900)

    def refuse(path):
        raise AssertionError(f"the export directory was listed anyway: {path}")

    monkeypatch.setattr(plugin_module.os, "listdir", refuse)

    assert plugin_instance._prune_csv_exports(0) == 0


def test_pruning_survives_an_unreadable_directory_entry(
        plugin_instance, plugin_module, exports_dir, monkeypatch):
    """A file that vanishes between listing and asking its age is not an error."""
    _seed(exports_dir, MINE + "a.csv", 30)
    _seed(exports_dir, MINE + "keep.csv", 0)
    real_getmtime = plugin_module.os.path.getmtime

    def sometimes(path):
        if path.endswith(MINE + "a.csv"):
            raise OSError("vanished")
        return real_getmtime(path)

    monkeypatch.setattr(plugin_module.os.path, "getmtime", sometimes)

    assert plugin_instance._prune_csv_exports(5) == 0


# --- wired into a real export writer ----------------------------------------

def _seed_plan():
    import importlib
    seeder = importlib.import_module("channel_maparr.channel_seeder")
    item = seeder.SeedItem(proposed_name="US: ABC 4 HD",
                           source_names=["ABC 4"], stream_ids=[1], accounts=[6])
    return seeder.SeedPlan(create=[item], skip=[], unresolved=[], excluded=[])


def _export(plugin_instance, plugin_module, settings):
    return plugin_instance._export_seed_preview(
        _seed_plan(), settings, logging.getLogger("test"))


def test_exporting_prunes_old_files(plugin_instance, plugin_module, exports_dir):
    old_a = _seed(exports_dir, MINE + "old_a.csv", 30)
    old_b = _seed(exports_dir, MINE + "old_b.csv", 60)
    recent = _seed(exports_dir, MINE + "recent.csv", 1)
    foreign = _seed(exports_dir, FOREIGN[0], 400)

    csv_path, _ = _export(plugin_instance, plugin_module,
                          {"csv_retention_days": 5})

    assert os.path.exists(csv_path), "the export wrote nothing, so this proves nothing"
    assert not old_a.exists()
    assert not old_b.exists()
    assert recent.exists()
    assert foreign.exists()


def test_exporting_without_the_setting_prunes_nothing(
        plugin_instance, plugin_module, exports_dir):
    """Control: nobody loses files merely by upgrading."""
    old = [_seed(exports_dir, MINE + f"old_{n}.csv", 900) for n in "abc"]

    csv_path, _ = _export(plugin_instance, plugin_module, {})

    assert os.path.exists(csv_path)
    for path in old:
        assert path.exists()


def test_exporting_never_deletes_the_file_it_just_wrote(
        plugin_instance, plugin_module, exports_dir):
    _seed(exports_dir, MINE + "old_a.csv", 900)
    _seed(exports_dir, MINE + "old_b.csv", 800)

    csv_path, filename = _export(plugin_instance, plugin_module,
                                 {"csv_retention_days": 1})

    assert os.path.exists(csv_path), f"{filename} was deleted by its own export"


def test_clearing_all_exports_ignores_the_age_setting(
        plugin_instance, exports_dir):
    """Someone pressing Clear CSV Exports expects everything of ours cleared."""
    recent = _seed(exports_dir, MINE + "recent.csv", 0)
    old = _seed(exports_dir, MINE + "old.csv", 900)
    foreign = _seed(exports_dir, FOREIGN[0], 900)

    result = plugin_instance.clear_csv_exports_action(
        {"csv_retention_days": 30}, logging.getLogger("test"))

    assert result["status"] == "success"
    assert not recent.exists()
    assert not old.exists()
    assert foreign.exists(), "Clear CSV Exports deleted another plugin's file"


# --------------------------------------------------------------------------- #
# AST guards pinning WHERE the pruning happens.
#
# The tests above catch outright deletion of the pruning code. They do not
# catch a fifth export writer being added later that calls os.replace directly
# and so never prunes, nor a second pruning call site appearing somewhere that
# runs on a different path. Each guard carries a synthetic self-test, because a
# guard with no positive fixture returns exit 0 for months while proving
# nothing.
# --------------------------------------------------------------------------- #

def _functions_calling_method(tree, method_name):
    """Names of functions containing a call to ``<something>.method_name(...)``."""
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


def _functions_calling_os(tree, attr):
    """Names of functions containing a call to ``os.<attr>(...)`` specifically."""
    import ast
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == attr
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "os"):
                found.add(node.name)
    return found


@pytest.fixture(scope="module")
def plugin_tree():
    import ast
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent
            / "Channel-Maparr" / "plugin.py")
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_ast_helpers_find_what_they_look_for():
    """Synthetic self-test, so neither guard below can be inert."""
    import ast
    tree = ast.parse(
        "def outer():\n"
        "    os.replace(a, b)\n"
        "    self._prune_csv_exports(3)\n"
        "def unrelated():\n"
        "    text.replace('a', 'b')\n"
    )
    assert _functions_calling_os(tree, "replace") == {"outer"}
    assert _functions_calling_method(tree, "_prune_csv_exports") == {"outer"}
    assert _functions_calling_os(tree, "remove") == set()


def test_exactly_one_function_prunes_the_exports(plugin_tree):
    assert (_functions_calling_method(plugin_tree, "_prune_csv_exports")
            == {"_finish_csv_export"})


def test_every_csv_writer_finishes_through_that_same_function(plugin_tree):
    """Nothing may rename a temporary file into place except the one helper.

    That is what makes the pruning unavoidable: a new export writer has to go
    through _finish_csv_export to publish its file, and pruning happens there.
    """
    assert _functions_calling_os(plugin_tree, "replace") == {"_finish_csv_export"}


def test_all_four_export_writers_call_the_finishing_helper(plugin_tree):
    writers = _functions_calling_method(plugin_tree, "_finish_csv_export")
    assert writers == {
        "preview_changes_action",
        "category_groups_dry_run_action",
        "_export_m3u_import_preview",
        "_export_seed_preview",
    }
