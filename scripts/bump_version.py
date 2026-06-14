#!/usr/bin/env python3
"""Bump the plugin version in BOTH places that must agree.

The version lives in two files and the Python class is the runtime source of
truth, so they must never drift:
  - Channel-Maparr/plugin.json   ("version": "...")
  - Channel-Maparr/plugin.py     (PLUGIN_VERSION = "...")

Versioning convention (see CLAUDE.md): Major.YY.DDDHHMM
  Major  -> kept from the current version unless --major is given
  YY     -> 2-digit year
  DDD    -> zero-padded day-of-year
  HHMM   -> 24h local time
e.g. 1.26.1430910 = major 1, year 2026, day 143, 09:10.

Usage:
    python scripts/bump_version.py                 # compute from now, write both
    python scripts/bump_version.py --dry-run       # show old/new, write nothing
    python scripts/bump_version.py --set 1.26.999  # set an explicit version
    python scripts/bump_version.py --major 2       # roll the major component
"""
import argparse
import re
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
PLUGIN_PY = PLUGIN_DIR / "plugin.py"

JSON_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
PY_RE = re.compile(r'(PLUGIN_VERSION\s*=\s*")([^"]+)(")')


def current_version():
    m = JSON_RE.search(PLUGIN_JSON.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("ERROR: could not find \"version\" in plugin.json")
    return m.group(2)


def compute_version(major, now=None):
    now = now or datetime.now()
    return f"{major}.{now:%y}.{now:%j}{now:%H}{now:%M}"


def _replace(path, regex, new_version):
    text = path.read_text(encoding="utf-8")
    new_text, n = regex.subn(rf"\g<1>{new_version}\g<3>", text, count=1)
    if n != 1:
        raise SystemExit(f"ERROR: version pattern not found/unique in {path.name}")
    path.write_text(new_text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", dest="explicit", help="Set an explicit version string")
    parser.add_argument("--major", help="Override the major component")
    parser.add_argument("--dry-run", action="store_true", help="Print only; write nothing")
    args = parser.parse_args()

    old = current_version()
    if args.explicit:
        new = args.explicit
    else:
        major = args.major or old.split(".", 1)[0]
        new = compute_version(major)

    print(f"current: {old}")
    print(f"new:     {new}")
    if args.dry_run:
        print("(dry-run: no files changed)")
        return 0

    if new == old:
        print("ERROR: new version equals current — refusing to write", )
        return 1

    _replace(PLUGIN_JSON, JSON_RE, new)
    _replace(PLUGIN_PY, PY_RE, new)
    print(f"updated plugin.json and plugin.py -> {new}")
    print("Next: add a docs/CHANGELOG.md entry, run pytest, then package & release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
