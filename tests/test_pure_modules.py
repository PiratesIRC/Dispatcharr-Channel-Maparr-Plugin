"""Unit tests for the deliberately Django-free helper modules.

progress_status.py and logo_matcher.py import nothing from Dispatcharr/Django,
so they are tested directly with no mocking. They back the Show Status action
and the per-channel tv-logos action respectively.
"""
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Channel-Maparr"
sys.path.insert(0, str(PLUGIN_DIR))

import progress_status as ps  # noqa: E402
import logo_matcher as lm  # noqa: E402


# --- progress_status ---
@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (5, "5s"),
    (65, "1m 5s"),
    (3700, "1h 1m"),
    (-1, "0s"),  # clamps negatives rather than emitting nonsense
])
def test_format_eta(seconds, expected):
    assert ps.format_eta(seconds) == expected


def test_load_progress_missing_returns_idle_default(tmp_path):
    result = ps.load_progress(tmp_path / "nope.json")
    assert result == {"status": "idle"}


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "progress.json"
    data = {"status": "running", "action": "import_m3u_streams", "current": 10, "total": 100}
    ps.save_progress_atomic(path, data)
    assert ps.load_progress(path) == data


def test_build_status_message_returns_string():
    # Must not raise on an empty/partial progress dict.
    assert isinstance(ps.build_status_message({}), str)
    assert isinstance(ps.build_status_message({"status": "running", "current": 5, "total": 10}), str)


# --- logo_matcher ---
def test_normalize_channel_name_is_idempotent():
    once = lm.normalize_channel_name("CNN HD (East)")
    assert isinstance(once, str)
    assert lm.normalize_channel_name(once) == once


def test_match_channel_to_logo_finds_obvious_match():
    logos = ["cnn-us.png", "bbc-news-uk.png", "espn-us.png"]
    assert lm.match_channel_to_logo("CNN", logos, "us") == "cnn-us.png"


def test_match_channel_to_logo_returns_none_when_no_match():
    assert lm.match_channel_to_logo("Totally Unknown Channel XYZ", ["cnn-us.png"], "us") is None


def test_build_logo_url_shape():
    url = lm.build_logo_url("tv-logos/tv-logos", "main", "united-states", "cnn-us.png")
    assert url.startswith("https://raw.githubusercontent.com/")
    assert url.endswith("/cnn-us.png")
    assert "united-states" in url
