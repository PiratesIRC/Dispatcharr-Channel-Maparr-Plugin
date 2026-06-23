"""Regression locks for the three normalize_name fixes ported from Stream-Mapparr.

Per the workspace drift rule, `fuzzy_matcher.py` is copy-pasted across plugins;
matcher fixes + their regression tests are ported until the shared-core refactor
lands. These cover the three `normalize_name` input-cleaning fixes shipped in
Stream-Mapparr v1.26.1650009 (see docs/MATCHER-NORMALIZATION-PORT.md):

  * bug-048 — stylized-Unicode decoration stripping (WeatherNation fix)
  * bug-051 — emoji-as-letter substitution + emoji decoration (beIN SP⚽RTS fix)
  * bug-055 — numeric resolution markers (720p/1080p/3840P) the keyword
              QUALITY_PATTERNS miss

Unicode test inputs are written with explicit ``\\u`` escapes (and a glyph
comment) so they survive any editor that "cleans" stylized / zero-width
characters. The module-level helper cases assert exact values identical to the
canonical Stream-Mapparr suite; the ``normalize_name`` cases assert the values
produced by Channel-Maparr's own (longer) pipeline, verified semantically
correct during the port (markers removed, ASCII/ non-Latin content preserved).
"""
import re

import pytest


# --------------------------------------------------------------------------- #
# Stylized-Unicode markers (documented by code point so they stay unambiguous)
# --------------------------------------------------------------------------- #
RAW = "\u1d3f\u1d2c\u1d42"  # superscript R A W
HD = "\u1d34\u1d30"  # superscript H D
FHD = "\ua730\u029c\u1d05"  # small-cap F H D
FISH = "\u25c9"  # FISHEYE bullet (curated ornament)
FPS60 = "\u2076\u2070\u1da0\u1d56\u02e2"  # superscript "60fps"
ARABIC = "\u0627\u0644\u0645"  # real non-Latin word (kept)
CYRILLIC = "\u0420\u043e\u0441\u0441\u0438\u044f"  # real non-Latin word (kept)
BALL = "\u26bd"  # SOCCER BALL (emoji-as-letter 'o')
VS16 = "\ufe0f"  # VARIATION SELECTOR-16 (zero-width)
NOTE = "\u266c"  # BEAMED SIXTEENTH NOTES (ornament)


# --------------------------------------------------------------------------- #
# Fix 1 — stylized-Unicode decoration (bug-048): module-level helpers
# --------------------------------------------------------------------------- #
def test_is_decorative_char_classifies_markers(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("ᴿ") is True   # superscript modifier-letter R
    assert f("ꜰ") is True   # Latin small capital F
    assert f("◉") is True   # FISHEYE bullet (curated)
    assert f("⁶") is True   # superscript six


def test_is_decorative_char_keeps_real_letters(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("G") is False
    assert f("4") is False
    assert f("я") is False  # Cyrillic small ya
    assert f("ا") is False  # Arabic alef


def test_strip_stylized_tokens_drops_superscript_token(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("WEATHERNATION " + RAW) == "WEATHERNATION"


def test_strip_stylized_tokens_drops_punct_glued_ornament(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens(FISH + ": CNN") == "CNN"


def test_strip_stylized_tokens_keeps_ascii_tier_word(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("Gold " + RAW) == "Gold"


def test_strip_stylized_tokens_is_non_latin_safe(fuzzy_module):
    # glued Arabic + superscript HD: Arabic token KEPT; NFKD folds the superscript HD to "HD".
    assert fuzzy_module._strip_stylized_tokens(ARABIC + HD) == ARABIC + "HD"


def test_strip_stylized_tokens_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("Fox Sports 1") == "Fox Sports 1"


def test_strip_stylized_tokens_empty_and_all_decoration(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("") == ""
    assert fuzzy_module._strip_stylized_tokens(RAW) == ""


# --------------------------------------------------------------------------- #
# Fix 2 — emoji-as-letter (bug-051): module-level helpers
# --------------------------------------------------------------------------- #
def test_normalize_emoji_ball_midword_to_o(fuzzy_module):
    f = fuzzy_module._normalize_emoji
    assert f("SP" + BALL + "RTS") == "SPoRTS"
    assert f("Sp" + BALL + "rts") == "Sports"


def test_normalize_emoji_edge_ball_stripped_not_mapped(fuzzy_module):
    assert fuzzy_module._normalize_emoji("BE" + BALL) == "BE"


def test_normalize_emoji_strips_zero_width(fuzzy_module):
    assert fuzzy_module._normalize_emoji(VS16 + "HULU") == "HULU"


def test_normalize_emoji_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._normalize_emoji("Fox Sports") == "Fox Sports"


def test_normalize_emoji_leaves_non_emoji_nonascii(fuzzy_module):
    assert fuzzy_module._normalize_emoji(CYRILLIC) == CYRILLIC


def test_normalize_emoji_leading_and_adjacent_balls(fuzzy_module):
    f = fuzzy_module._normalize_emoji
    # leading ball: no ASCII letter before it -> not mapped, stripped as ornament
    assert f(BALL + "SPORTS") == "SPORTS"
    # adjacent balls mid-word: neither flanked by two ASCII letters -> both stripped
    assert f("A" + BALL + BALL + "B") == "AB"


# --------------------------------------------------------------------------- #
# Fix 3 — numeric resolution markers (bug-055): the RESOLUTION_PATTERNS regex
# --------------------------------------------------------------------------- #
def _apply_resolution(fuzzy_module, text):
    out = text
    for pat in fuzzy_module.RESOLUTION_PATTERNS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    return out


def test_resolution_pattern_strips_glued_markers(fuzzy_module):
    assert _apply_resolution(fuzzy_module, "ESPN 1080p").strip() == "ESPN"
    assert _apply_resolution(fuzzy_module, "Sky 720P").strip() == "Sky"
    assert _apply_resolution(fuzzy_module, "Foo 2160p").strip() == "Foo"
    assert _apply_resolution(fuzzy_module, "Foo 1080i").strip() == "Foo"


def test_resolution_pattern_keeps_bare_and_spaced(fuzzy_module):
    # bare numbers, 5-digit runs, and a spaced standalone P/I (roman numeral) survive
    assert _apply_resolution(fuzzy_module, "Channel 4") == "Channel 4"
    assert "10800" in _apply_resolution(fuzzy_module, "Foo 10800p")   # 5-digit, not a marker
    assert _apply_resolution(fuzzy_module, "Volume 100 I") == "Volume 100 I"
    assert "1080" in _apply_resolution(fuzzy_module, "Foo 1080 p")    # spaced, not glued


# --------------------------------------------------------------------------- #
# Integration through Channel-Maparr's full normalize_name pipeline.
# Expected values were captured from the ported pipeline and verified correct.
# --------------------------------------------------------------------------- #

# (input, expected) — stylized-Unicode decoration removed, content preserved.
STYLIZED_NORMALIZE = [
    ("WEATHERNATION " + RAW, "WEATHERNATION"),
    ("C-SPAN2 " + HD, "C SPAN 2"),
    ("ESPN " + FHD, "ESPN"),
    (FISH + ": CNN", "CNN"),
    ("ENTERTAINMENT " + HD + "/" + RAW + " " + FPS60, "ENTERTAINMENT"),
    # collision guard: real ASCII tier words and non-Latin scripts are untouched
    ("Gold", "Gold"),
    ("VIP", "VIP"),
    (CYRILLIC, CYRILLIC),
]

# (input, expected) — emoji handling.
EMOJI_NORMALIZE = [
    ("SP" + BALL + "RTS", "SPoRTS"),
    ("Sp" + BALL + "rts", "Sports"),
    ("UEFA CHAMPIONS LEAGUE " + BALL, "UEFA CHAMPIONS LEAGUE"),
    (NOTE * 3 + " MUSIC TV " + NOTE * 3, "MUSIC"),
]

# (input, expected) — resolution markers gated by ignore_quality (default True).
RESOLUTION_NORMALIZE = [
    ("BEIN SPORTS GOLD 3840P", "BEIN SPORTS GOLD"),
    ("RELAX 1 3840P", "RELAX 1"),
    ("Sky Sports 720P", "Sky Sports"),
    ("Foo 1080i", "Foo"),
    ("Foo 2160p", "Foo"),
    ("Foo 480p", "Foo"),
    ("Channel 4", "Channel 4"),
    ("Studio 1080", "Studio 1080"),
    ("Volume 100 I", "Volume 100 I"),
    ("Sky Sports 720P HD", "Sky Sports"),
]

# Plain-ASCII names that must come out byte-identical (no-regression).
ASCII_IDENTITY = [
    ("Fox Sports 1", "Fox Sports 1"),
    ("Fox Sports 2", "Fox Sports 2"),
    ("CNN HD", "CNN"),
    ("ITV1", "ITV 1"),
    ("ESPN", "ESPN"),
    ("HBO East", "HBO East"),   # Channel-Maparr keeps East/West (separate feeds)
    ("HBO West", "HBO West"),
]


@pytest.mark.parametrize("inp,expected", STYLIZED_NORMALIZE)
def test_normalize_name_stylized(matcher, inp, expected):
    assert matcher.normalize_name(inp) == expected


@pytest.mark.parametrize("inp,expected", EMOJI_NORMALIZE)
def test_normalize_name_emoji(matcher, inp, expected):
    assert matcher.normalize_name(inp) == expected


def test_normalize_name_bein_sports_recovers(matcher):
    # The real-world beIN fix: the emoji-as-letter name normalizes to contain "sports".
    assert "sports" in matcher.normalize_name("beIN SP" + BALL + "RTS").lower()


@pytest.mark.parametrize("inp,expected", RESOLUTION_NORMALIZE)
def test_normalize_name_resolution(matcher, inp, expected):
    assert matcher.normalize_name(inp) == expected


def test_normalize_name_resolution_five_digit_preserved(matcher):
    assert "10800" in matcher.normalize_name("Foo 10800p")


def test_normalize_name_resolution_respects_ignore_quality_flag(matcher):
    assert "1080" in matcher.normalize_name("Foo 1080p", ignore_quality=False)


@pytest.mark.parametrize("inp,expected", ASCII_IDENTITY)
def test_normalize_name_ascii_no_regression(matcher, inp, expected):
    assert matcher.normalize_name(inp) == expected


# --------------------------------------------------------------------------- #
# bug-066 — bare " Pacific"/" Central"/" Mountain"/" Atlantic" are brand tokens
# far more often than timezone feeds. Removing them from REGIONAL_PATTERNS stops
# distinct channels collapsing onto one grouping key (ported from Stream-Mapparr).
# Channel-Maparr already keeps bare East/West, so only the rarer zones change.
# --------------------------------------------------------------------------- #
def test_brand_central_not_stripped_as_timezone(matcher):
    assert "central" in matcher.normalize_name("Comedy Central", ignore_regional=True).lower()


def test_comedy_central_distinct_from_comedy_tv(matcher):
    assert (matcher.normalize_name("Comedy Central", ignore_regional=True)
            != matcher.normalize_name("Comedy TV", ignore_regional=True))


def test_brand_atlantic_not_stripped_as_timezone(matcher):
    assert "atlantic" in matcher.normalize_name("The Atlantic", ignore_regional=True).lower()


def test_parenthesized_central_still_stripped(matcher):
    """Guard: an explicit "(Central)" timezone tag is a genuine feed marker and
    must still strip — only the BARE word is preserved."""
    assert matcher.normalize_name("ESPN (Central)", ignore_regional=True).strip().upper() == "ESPN"


# --------------------------------------------------------------------------- #
# CI-enforced corpus no-regression gate (baseline-free).
#
# The manual ship gate asserts the port changes 0 ASCII channel names. We lock
# that into CI without an old-vs-new baseline by asserting the invariant that
# makes it true: the two unconditional input-cleaning helpers are identity on
# every ASCII name (they short-circuit on `name.isascii()`), and no ASCII DB
# name contains a glued NNN[pi] resolution marker the gated strip would remove.
# If a future DB edit or matcher change violates any of these, CI fails loudly.
# --------------------------------------------------------------------------- #
def _all_ascii_db_names(plugin_dir):
    import glob
    import json
    import os
    names = []
    for path in glob.glob(os.path.join(str(plugin_dir), "*_channels.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data["channels"] if isinstance(data, dict) else data
        names += [(r.get("channel_name") or "").strip() for r in rows]
    return [n for n in names if n and n.isascii()]


def test_corpus_ascii_names_unaffected_by_fixes(fuzzy_module, plugin_dir):
    ascii_names = _all_ascii_db_names(plugin_dir)
    # guard against a vacuous pass if the corpus failed to load
    assert len(ascii_names) > 1000, f"expected the full DB corpus, got {len(ascii_names)} ASCII names"

    altered_emoji = [n for n in ascii_names if fuzzy_module._normalize_emoji(n) != n]
    altered_styl = [n for n in ascii_names if fuzzy_module._strip_stylized_tokens(n) != n]
    res_re = re.compile(fuzzy_module.RESOLUTION_PATTERNS[0])
    res_hits = [n for n in ascii_names if res_re.search(n)]

    assert not altered_emoji, f"_normalize_emoji altered ASCII DB names: {altered_emoji[:5]}"
    assert not altered_styl, f"_strip_stylized_tokens altered ASCII DB names: {altered_styl[:5]}"
    assert not res_hits, (
        "ASCII DB names contain a glued resolution marker the strip would remove "
        f"(review before shipping): {res_hits[:5]}"
    )
