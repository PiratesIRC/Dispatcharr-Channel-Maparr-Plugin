#!/usr/bin/env python3
"""Build the distributable Channel-Maparr.zip — cross-platform replacement for zip.cmd.

zip.cmd hardcoded the author's machine paths and required 7-Zip. This uses the
stdlib `zipfile`, resolves paths relative to the repo, and ships only the files
Dispatcharr's loader needs (.py .png .txt .json), with the plugin folder as the
top-level directory inside the archive (loader expects `Channel-Maparr/...`).

Usage:
    python scripts/package_plugin.py [--output PATH]

Exits non-zero (and writes nothing) if a pre-flight check fails:
  - any shipped .py fails to compile
  - plugin.json contains an astral-plane (> U+FFFF) character. The loader's
    surrogate-pair validator silently drops action definitions that contain
    them; the manifest is where that is fatal. (Astral emoji inside runtime
    UI strings like status messages are fine and intentionally allowed.)
  - the shipped paths (Channel-Maparr/ and scripts/core_manifest.json) have
    uncommitted changes in git, since this script builds from the WORKING
    TREE, not from HEAD -- a dirty tree means the zip does not correspond to
    any committed state and is not reproducible from the branch. Pass
    --allow-dirty to proceed anyway (prints a loud warning naming every
    dirty path instead of refusing).
"""
import argparse
import py_compile
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
SHIP_SUFFIXES = {".py", ".png", ".txt", ".json"}
# Never ship these even if they match a suffix above.
EXCLUDE_PARTS = {"__pycache__", ".claude", ".serena", "nul"}
# Paths whose working-tree state actually affects what ends up in the zip.
# core_manifest.json isn't shipped itself, but it pins the vendored core that IS.
DIRTY_CHECK_PATHS = ["Channel-Maparr", "scripts/core_manifest.json"]


def _dirty_paths():
    """Return shipped-relevant paths with uncommitted changes, or None if git
    is unavailable / this isn't a repo (distinct from an empty, clean result).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *DIRTY_CHECK_PATHS],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"WARNING: could not run git ({exc}); skipping dirty-tree check", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"WARNING: 'git status' failed (not a repo? exit {result.returncode}); "
            "skipping dirty-tree check",
            file=sys.stderr,
        )
        return None
    paths = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain format: XY <path> (renames carry "old -> new")
        paths.append(line[3:].split(" -> ")[-1])
    return paths


def _shipped_files():
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SHIP_SUFFIXES:
            continue
        if any(part in EXCLUDE_PARTS for part in path.relative_to(PLUGIN_DIR).parts):
            continue
        if path.name in EXCLUDE_PARTS:
            continue
        yield path


def _preflight(files):
    problems = []
    for path in files:
        if path.suffix.lower() == ".py":
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"compile error: {path.name}: {exc.msg.strip()}")
    # BMP check is fatal only for the manifest, where the loader drops actions.
    manifest = PLUGIN_DIR / "plugin.json"
    astral = sorted({hex(ord(c)) for c in manifest.read_text(encoding="utf-8") if ord(c) > 0xFFFF})
    if astral:
        problems.append(f"non-BMP characters in plugin.json: {astral}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=str(REPO_ROOT / "Channel-Maparr.zip"),
        help="Destination zip path (default: repo-root/Channel-Maparr.zip)",
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="Build even if shipped paths have uncommitted changes (prints a warning naming them).",
    )
    args = parser.parse_args()

    dirty = _dirty_paths()
    if dirty:
        if args.allow_dirty:
            print("WARNING: building from a DIRTY working tree -- this zip will NOT be "
                  "reproducible from the committed branch. Uncommitted paths:", file=sys.stderr)
            for p in dirty:
                print(f"  - {p}", file=sys.stderr)
        else:
            print("ERROR: refusing to package -- the following shipped paths have "
                  "uncommitted changes, so the zip would embed changes that do not "
                  "correspond to any committed state:", file=sys.stderr)
            for p in dirty:
                print(f"  - {p}", file=sys.stderr)
            print("Commit or stash these, or pass --allow-dirty to build anyway.", file=sys.stderr)
            return 1

    files = list(_shipped_files())
    if not files:
        print("ERROR: no files matched for packaging", file=sys.stderr)
        return 1

    problems = _preflight(files)
    if problems:
        print("Pre-flight FAILED — refusing to package:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    out = Path(args.output)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            arcname = Path(PLUGIN_DIR.name) / path.relative_to(PLUGIN_DIR)
            zf.write(path, arcname.as_posix())

    print(f"Packaged {len(files)} files -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
