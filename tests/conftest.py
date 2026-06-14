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
Plugin class *does* (live GitHub version check + ORM query), so tests assert the
field/action contract against static sources, never by executing that property.
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
