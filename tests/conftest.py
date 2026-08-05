"""Shared pytest fixtures for the Channel-Maparr test suite.

The plugin runs *inside* Dispatcharr's Django backend and is never importable
on its own (it does `from apps.channels.models import ...`, `from django.db ...`
etc.). To test the Django-free logic — the fuzzy matcher, the JSON databases,
the pure helper modules, and the plugin's static field/action contract — we:

  1. Register MagicMock stand-ins for every Dispatcharr/Django module the plugin
     imports, so `import` statements resolve without a live backend.
  2. Load the shippable ``Channel-Maparr/`` directory as a real Python package
     (it can't be imported by its hyphenated folder name, and ``plugin.py`` uses
     relative imports like ``from .fuzzy_matcher import FuzzyMatcher``), under the
     synthetic name ``channel_maparr``.

Nothing here touches the network or a database. The ``fields`` property on the
Plugin class still reads the DB (it lists M3U accounts), so tests assert the
field/action contract against static sources rather than by executing it.

The property no longer makes a network call: the GitHub update check was
removed on 2026-07-26 because it ran on Dispatcharr's per-request hot path.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
PKG_NAME = "channel_maparr"

# Country databases loaded for matching tests. Mirrors the .wolf/ harness and the
# project's recommended default set (see docs/TODO.md "Add UK/CA to defaults").
TEST_DATABASES = ["US", "UK", "CA"]

# Every Dispatcharr/Django module the plugin imports. Mocked so imports resolve.
_MOCK_MODULES = [
    "django", "django.db", "django.db.transaction",
    "apps", "apps.channels", "apps.channels.models",
    "apps.m3u", "apps.m3u.models",
    "apps.epg", "apps.epg.models",
    "core", "core.utils",
]


def _install_mocks():
    for name in _MOCK_MODULES:
        sys.modules.setdefault(name, MagicMock())


def _load_plugin_package():
    """Load Channel-Maparr/ as the importable package ``channel_maparr``."""
    if PKG_NAME in sys.modules:
        return sys.modules[PKG_NAME]
    _install_mocks()
    spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = pkg
    spec.loader.exec_module(pkg)
    return pkg


# ---------------------------------------------------------------------------
# Container paths
#
# The plugin writes to absolute paths under /data, which exist inside the
# Dispatcharr container and nowhere else. A test that lets plugin code write to
# one of them can still pass on Windows, where such a path resolves to a
# directory at the current drive root that the development machine happens to
# have, and fails on Linux, where it does not exist. That is exactly how one
# test stayed green locally while failing every continuous integration run for a
# week (bug-105).
#
# REAL_CONTAINER_PATHS holds the production values, captured before any test
# redirects them, for the few tests that must assert on the truth: that emailed
# reports are not written where nginx serves them unauthenticated, and that the
# export cleaner cannot reach them.
# ---------------------------------------------------------------------------

REAL_CONTAINER_PATHS = {}


def _capture_real_container_paths():
    if REAL_CONTAINER_PATHS:
        return
    _load_plugin_package()
    import channel_maparr.plugin as plugin_module  # noqa: E402
    import channel_maparr.report_counter as counter_module  # noqa: E402
    import channel_maparr.reports as reports_module  # noqa: E402
    REAL_CONTAINER_PATHS.update({
        "RESULTS_FILE": plugin_module.PluginConfig.RESULTS_FILE,
        "EXPORT_DIR": plugin_module.PluginConfig.EXPORT_DIR,
        "PROGRESS_FILE": plugin_module.PROGRESS_FILE,
        "REPORT_DIR": reports_module.REPORT_DIR,
        "COUNTER_DIR": counter_module.COUNTER_DIR,
    })


@pytest.fixture(autouse=True)
def redirect_container_paths(tmp_path, monkeypatch):
    """Point every path the plugin writes to at a temporary directory.

    Autouse on purpose. The value of this fixture is that a new test cannot
    forget it: the failure it prevents is invisible on the machine the test is
    written on and only appears on Linux.

    A test that needs the production value reads it from REAL_CONTAINER_PATHS
    instead, which is captured before this fixture ever runs.
    """
    _capture_real_container_paths()
    import channel_maparr.plugin as plugin_module  # noqa: E402
    import channel_maparr.report_counter as counter_module  # noqa: E402
    import channel_maparr.reports as reports_module  # noqa: E402

    monkeypatch.setattr(counter_module, "COUNTER_DIR",
                        str(tmp_path / "counter"))
    monkeypatch.setattr(plugin_module.PluginConfig, "RESULTS_FILE",
                        str(tmp_path / "loaded_channels.json"))
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORT_DIR",
                        str(tmp_path / "exports"))
    monkeypatch.setattr(plugin_module, "PROGRESS_FILE",
                        str(tmp_path / "progress.json"))
    monkeypatch.setattr(reports_module, "REPORT_DIR",
                        str(tmp_path / "reports"))


@pytest.fixture(scope="session")
def plugin_dir():
    return PLUGIN_DIR


@pytest.fixture(scope="session")
def plugin_module():
    """The imported ``channel_maparr.plugin`` module (Django mocked)."""
    _load_plugin_package()
    import channel_maparr.plugin as plugin_module  # noqa: E402
    return plugin_module


@pytest.fixture(scope="session")
def fuzzy_module():
    """The imported ``channel_maparr.fuzzy_matcher`` module.

    Exposes the module-level normalization helpers (`_is_decorative_char`,
    `_strip_stylized_tokens`, `_normalize_emoji`, `RESOLUTION_PATTERNS`) for
    direct unit testing, independent of any loaded channel database.
    """
    _load_plugin_package()
    import channel_maparr.fuzzy_matcher as fuzzy_matcher_module  # noqa: E402
    return fuzzy_matcher_module


@pytest.fixture(scope="session")
def matcher():
    """A FuzzyMatcher loaded with the US/UK/CA databases, normalizations primed.

    Session-scoped: loading ~33K channel names and precomputing normalizations
    is the expensive part, so we pay it once for the whole run.
    """
    _load_plugin_package()
    from channel_maparr.fuzzy_matcher import FuzzyMatcher  # noqa: E402

    fm = FuzzyMatcher(plugin_dir=str(PLUGIN_DIR), match_threshold=80)
    fm.reload_databases(TEST_DATABASES)
    fm.precompute_normalizations(fm.premium_channels)
    return fm


# ---------------------------------------------------------------------------
# Plugin-instance fixtures
#
# Plugin() is safe to construct: __init__ only sets attributes, builds a
# FuzzyMatcher (lazy - see tests/test_lazy_db_load.py) and logs. It does no
# network or DB I/O. The `fields` PROPERTY does both, so never touch it here.
# ---------------------------------------------------------------------------


class FakeQuerySet:
    """Minimal queryset that implements real filter semantics AND records calls.

    A bare MagicMock returns [] for every input, which makes assertions about
    filtering vacuously true. Recording the .filter() kwargs is what lets a
    test prove the filter was actually applied.
    """

    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def filter(self, **kwargs):
        self.calls.append(kwargs)
        ids = kwargs["channel_group_id__in"]
        kept = [r for r in self.rows if r["channel_group_id"] in ids]
        return FakeQuerySet(kept, self.calls)

    def values(self, *fields):
        return [{k: r[k] for k in fields} for r in self.rows]


CHANNEL_ROWS = [
    {"id": 1, "name": "A", "channel_number": 1.0, "channel_group_id": 10, "logo_id": None},
    {"id": 2, "name": "B", "channel_number": 2.0, "channel_group_id": 20, "logo_id": None},
    {"id": 3, "name": "Orphan", "channel_number": 3.0, "channel_group_id": None, "logo_id": None},
]


@pytest.fixture
def plugin_instance(plugin_module):
    return plugin_module.Plugin()


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def fake_channel(monkeypatch, plugin_module):
    """Install a recording fake Channel.objects. Returns the recorded filter calls.

    plugin_module is session-scoped and shared across the whole run, so this MUST
    go through monkeypatch (auto-undone) and never a bare setattr.
    """
    calls = []
    channel = MagicMock()
    channel.objects.all = lambda: FakeQuerySet(list(CHANNEL_ROWS), calls)
    monkeypatch.setattr(plugin_module, "Channel", channel)
    return calls


@pytest.fixture
def fake_groups(monkeypatch, plugin_module):
    """Install a fake ChannelGroup.objects so the REAL _get_all_groups runs.

    Tests drive the real producer (_get_all_groups plus the name-map building)
    rather than injecting a finished dict into the consumer.
    """

    def _install(rows):
        group = MagicMock()
        group.objects.all.return_value.values.return_value = list(rows)
        monkeypatch.setattr(plugin_module, "ChannelGroup", group)
        return rows

    return _install
