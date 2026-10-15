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


# Filenames below are real names from the tv-logos united-kingdom folder. Each
# case was a wrong match or a missed one, measured on 181 UK channels before
# the change.
UK_LOGOS = [
    "itv-1-uk.png", "itv-1-plus-uk.png", "itv-2-uk.png", "itv-2-plus-uk.png",
    "e-4-uk.png", "e-4-plus-uk.png", "film-4-uk.png", "4-seven-uk.png",
    "food-network-uk.png", "food-network-plus-uk.png", "bbc-uhd-uk.png",
    "sky-sports-f1-uk.png", "sky-sports-plus-hz-uk.png", "dmax-uk.png",
    "talking-pictures-tv-uk.png",
]


@pytest.mark.parametrize("channel, expected", [
    # a letter-digit hyphen in the filename no longer fails Exact
    ("E4", "e-4-uk.png"),
    ("Film4", "film-4-uk.png"),
    ("4seven", "4-seven-uk.png"),
    ("ITV2", "itv-2-uk.png"),
    # "+1" picks the -plus file, never the channel numbered 1
    ("ITV2 +1", "itv-2-plus-uk.png"),
    ("E4+1", "e-4-plus-uk.png"),
    ("ITV2 +1 HD", "itv-2-plus-uk.png"),
    # Network, Channel and TV are stripped from the filename as well
    ("Food Network", "food-network-uk.png"),
    ("Food Network +1", "food-network-plus-uk.png"),
    ("Talking Pictures TV", "talking-pictures-tv-uk.png"),
    # no -plus file: falls back to the plain channel's logo
    ("DMAX +1", "dmax-uk.png"),
])
def test_match_channel_to_logo_at_exact(channel, expected):
    assert lm.match_channel_to_logo(channel, UK_LOGOS, "uk", threshold=95) == expected


def test_a_quality_tag_in_a_filename_is_part_of_the_name():
    """bbc-uhd-uk.png must not become "bbc": that matched CBBC to it."""
    assert lm.normalize_logo_filename("bbc-uhd-uk.png", "uk") == "bbc uhd"
    assert lm.match_channel_to_logo("CBBC", UK_LOGOS, "uk", threshold=80) is None


def test_a_bare_plus_prefers_the_plus_logo():
    assert lm.match_channel_to_logo("Sky Sports+ HD", UK_LOGOS, "uk",
                                    threshold=80) == "sky-sports-plus-hz-uk.png"


@pytest.mark.parametrize("name, expected", [
    ("ITV2 +1", ("ITV2", True)),
    ("E4+1", ("E4", True)),
    ("ITV2 +1 HD", ("ITV2", True)),
    ("Sky Sports+", ("Sky Sports", True)),
    ("Crime+Investigation", ("Crime+Investigation", False)),
    ("BBC One", ("BBC One", False)),
])
def test_split_timeshift(name, expected):
    assert lm.split_timeshift(name) == expected


def test_build_logo_url_shape():
    url = lm.build_logo_url("tv-logos/tv-logos", "main", "united-states", "cnn-us.png")
    assert url.startswith("https://raw.githubusercontent.com/")
    assert url.endswith("/cnn-us.png")
    assert "united-states" in url
