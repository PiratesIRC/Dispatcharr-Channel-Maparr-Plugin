"""Every file the plugin ships, pinned.

This exists because of how the plugin is published. The GitHub release archive
is built with ``git archive HEAD:Channel-Maparr`` and therefore always carries
whatever the package contains. The Dispatcharr Plugin Hub listing is different:
this plugin is a STANDARD listing, meaning its source is copied file by file
into ``plugins/channel-mapparr/`` in the Dispatcharr/Plugins repository. A file
added here and not copied there is simply absent from every Hub install.

That is not a theoretical risk. Measured on 2026-08-12, the Hub listing was
missing four files this package ships, and two of them are imported at module
level, so a Hub install would have raised ModuleNotFoundError and the plugin
would not have loaded at all:

    market_index.py         imported by fuzzy_matcher.py  -> import fails
    channel_seeder.py       imported by plugin.py         -> import fails
    station_affiliation.py  imported by nothing yet       -> loads
    networks_supplemental.json  read behind an exists check -> loads

Adding or removing a shipped file therefore has to fail this test, so that the
Hub listing is updated in the same breath. Update SHIPPED_FILES below and copy
the file to the Hub listing in the same change.
"""
import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "Channel-Maparr"

SHIPPED_FILES = {
    "AU_channels.json",
    "BR_channels.json",
    "CA_channels.json",
    "DE_channels.json",
    "ES_channels.json",
    "FR_channels.json",
    "IN_channels.json",
    "MX_channels.json",
    "NL_channels.json",
    "NO_channels.json",
    "UK_channels.json",
    "US_channels.json",
    "__init__.py",
    "aliases.py",
    "channel_seeder.py",
    "fuzzy_matcher.py",
    "group_scope.py",
    "logo.png",
    "logo_matcher.py",
    "logo_report.png",
    "market_index.py",
    "matching_core.py",
    "networks.json",
    "networks_supplemental.json",
    "notify_bridge.py",
    "notify_client.py",
    "plugin.json",
    "plugin.py",
    "progress_status.py",
    "readme.txt",
    "report_counter.py",
    "reports.py",
    "station_affiliation.py",
    "wildcard_match.py",
}


def _tracked_package_files():
    """The files git would put in the release archive.

    Read from the index rather than the working tree, because the archive is
    built from the index and an untracked file never ships.
    """
    out = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD:Channel-Maparr"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def test_the_shipped_file_list_is_exactly_what_is_pinned():
    tracked = _tracked_package_files()
    added = sorted(tracked - SHIPPED_FILES)
    removed = sorted(SHIPPED_FILES - tracked)
    assert not added, (
        "these files are shipped but not pinned: %s. Add them to SHIPPED_FILES "
        "AND copy them into plugins/channel-mapparr/ in the Dispatcharr/Plugins "
        "repository, or a Hub install will not have them." % added)
    assert not removed, (
        "these files are pinned but no longer shipped: %s. Remove them from "
        "SHIPPED_FILES and delete them from the Hub listing." % removed)


def _package_module_names():
    return {name[:-3] for name in SHIPPED_FILES if name.endswith(".py")}


@pytest.mark.parametrize("source_file", sorted(
    name for name in SHIPPED_FILES if name.endswith(".py")))
def test_every_module_a_shipped_file_imports_is_itself_shipped(source_file):
    """A relative import of a module that is not shipped fails at load time.

    This walks the syntax tree rather than importing, so it needs no Django and
    covers a module the runtime happens not to reach.
    """
    tree = ast.parse((PACKAGE_DIR / source_file).read_text(encoding="utf-8"))
    modules = _package_module_names()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        if node.module:
            # from .module import name
            wanted = [node.module]
        else:
            # from . import module, and the names are the modules
            wanted = [alias.name for alias in node.names]
        for name in wanted:
            assert name in modules, (
                "%s imports .%s, which is not a shipped file"
                % (source_file, name))


def test_the_two_modules_that_break_the_import_are_present():
    """Named individually, because their absence is not a degraded feature.

    Both are imported at module level, so the plugin does not load at all
    without them. This was measured against a simulated Hub install on
    2026-08-12: removing either produced ModuleNotFoundError, while removing
    station_affiliation.py or networks_supplemental.json did not.
    """
    for name in ("market_index.py", "channel_seeder.py"):
        assert (PACKAGE_DIR / name).is_file(), "%s must ship" % name
        assert name in SHIPPED_FILES
