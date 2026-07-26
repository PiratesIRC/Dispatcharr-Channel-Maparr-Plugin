"""The vendored glob helper. Frozen - see test_provenance_hash."""
import hashlib
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"


@pytest.fixture(scope="module")
def expand():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wildcard_match_under_test", PLUGIN_DIR / "wildcard_match.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.expand_patterns


NAMES = ["Teamarr", "Teamarr Live", "Sports", "US: PPV"]


def test_literal_is_case_insensitive_when_ci_plain(expand):
    assert expand(["teamarr"], NAMES, ci_plain=True)[0] == ["Teamarr"]


def test_literal_is_case_sensitive_when_not_ci_plain(expand):
    assert expand(["teamarr"], NAMES, ci_plain=False)[0] == []


def test_glob_matches_case_insensitively(expand):
    matched, unmatched = expand(["teamarr*"], NAMES, ci_plain=True)
    assert matched == ["Teamarr", "Teamarr Live"]
    assert unmatched == []


def test_question_mark_is_a_glob(expand):
    assert expand(["Sport?"], NAMES, ci_plain=True)[0] == ["Sports"]


def test_unmatched_tokens_are_reported_in_input_order(expand):
    matched, unmatched = expand(["Nope", "Sports", "Nada"], NAMES, ci_plain=True)
    assert matched == ["Sports"]
    assert unmatched == ["Nope", "Nada"]


def test_results_are_deduplicated(expand):
    assert expand(["Teamarr", "teamarr"], NAMES, ci_plain=True)[0] == ["Teamarr"]


def test_provenance_hash():
    """This file is a verbatim copy of EPG-Janitor's wildcard_match.py.

    If you edit it deliberately, update this hash AND record the divergence in
    docs/CHANGELOG.md - an ungated copy is the pre-shared-core divergence state.
    """
    raw = (PLUGIN_DIR / "wildcard_match.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest()[:16] == "da28897dddff6223"
