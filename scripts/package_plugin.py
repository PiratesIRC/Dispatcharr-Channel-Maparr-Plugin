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
"""
import argparse
import py_compile
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
SHIP_SUFFIXES = {".py", ".png", ".txt", ".json"}
# Never ship these even if they match a suffix above.
EXCLUDE_PARTS = {"__pycache__", ".claude", ".serena", "nul"}


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
    args = parser.parse_args()

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
