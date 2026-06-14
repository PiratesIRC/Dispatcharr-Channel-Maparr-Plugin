#!/usr/bin/env python3
"""PostToolUse hook: compile-check a Python file right after it is edited.

Wired on Write|Edit|MultiEdit. Reads the hook payload from stdin, and if the
edited file is a `.py`, byte-compiles it. On a syntax error it exits 2 with the
error on stderr so Claude sees it immediately instead of at deploy time.

Designed to be invisible unless something is actually broken: any non-.py file,
missing path, or unexpected condition exits 0 silently. It never blocks a
non-Python edit and never crashes the tool flow.
"""
import json
import py_compile
import sys


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # no/garbled payload — stay out of the way

    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("path")
    if not path or not str(path).endswith(".py"):
        return 0

    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as exc:
        sys.stderr.write(f"py_compile failed for {path}:\n{exc.msg.strip()}\n")
        return 2  # surface to Claude
    except FileNotFoundError:
        return 0
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
