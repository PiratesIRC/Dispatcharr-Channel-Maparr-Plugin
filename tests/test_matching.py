"""Stream-name -> channel matching accuracy.

Ported from the standalone .wolf/test_matching.py harness into parametrized
pytest cases so each stream name is reported individually on failure. The
matcher runs the same pipeline plugin.py uses: ``fuzzy_match(stream, channels)``
where the query is the STREAM and candidates are the channel DB names.

Guiding principle (cerebrum Decision Log, 2026-05-23): "None is better than
wrong." A confident-but-wrong match silently routes a stream to the wrong
channel; a None surfaces a visible "no match" the user can fix manually.
"""
import pytest


def _match(matcher, stream):
    return matcher.fuzzy_match(stream, matcher.premium_channels)


# (stream_name, expected_substring) — the match must contain this substring.
TRUE_POSITIVES = [
    # geographic prefixes
    ("US: USA Network HD", "USA Network"),
    ("US| ESPN", "ESPN"),
    ("[US] CNN", "CNN"),
    ("UK: BBC One", "BBC"),
    ("UK| ITV 1", "ITV"),
    # quality suffixes
    ("Discovery Channel 4K", "Discovery"),
    ("HBO HD", "HBO"),
    ("ESPN [FHD]", "ESPN"),
    # number-word vs digit
    ("BBC Three", "BBC"),
    ("BBC Four", "BBC"),
    ("Three Angels Broadcasting Network", "Angels Broadcasting"),
    # CamelCase
    ("JusticeCentral.TV", "Justice"),
    ("DangerTV", "Danger"),
    # East/West regional
    ("HBO East", "HBO"),
    ("HBO West", "HBO"),
    # provider/PRIME prefixes
    ("(PRIME) FOX News", "Fox News"),
    ("(D1) CBS", "CBS"),
    # callsigns
    ("WABC-TV", "ABC"),
    ("KCBS", "CBS"),
]

# (stream_name, wrong_channel) — matcher must NOT return this specific channel.
TRUE_NEGATIVES = [
    ("ABC News Live", "BBC News"),
    ("BBC News HD", "ABC News"),
    ("BBC One HD", "BBC Two"),
    ("BBC Two", "BBC One"),
    ("ESPN 1", "ESPN 2"),
    ("HBO 2", "HBO 3"),
    ("Sky Cinema Disney", "Sky Cinema Decades"),
    ("Sky Cinema Disney", "Sky Cinema Family"),
    ("In Country Television", "Country Music Television"),
]

# (stream_name, exact_channel) — must match this specific channel, not a sibling.
EXACT_EXPECTED = [
    ("BBC One HD", "BBC One"),
    ("BBC Two", "BBC Two"),
    ("BBC Three", "BBC Three"),
    ("USA: HBO East", "HBO East"),
    ("USA: HBO West", "HBO West"),
]

# Streams where returning None is the CORRECT behavior: the only too-similar DB
# entries are sibling/wrong-zone variants, so any match would be wrong.
EXPECTED_NONE = [
    "ESPN 1",  # ESPN exists but is too short; ESPN2 is the wrong sibling
]

# Names with no real callsign — extraction must return None (denylist guard).
CALLSIGN_NONE = [
    "Bizarre Foods with Andrew Zimmern",
    "World War II in Color",
    "Watch What Happens Live",
    "Women's World Cup",
    "Wild Kingdom",
    "WWE Raw",
    "Kids' Choice Awards",
    "King of the Hill",
]

# (name, expected_callsign) — extraction must return exactly this callsign.
CALLSIGN_EXPECTED = [
    ("WABC", "WABC"),
    ("KCBS-TV", "KCBS-TV"),
    ("NBC 4 (WNBC)", "WNBC"),
    ("WFAA-TV (ABC Dallas)", "WFAA-TV"),
]


@pytest.mark.parametrize("stream,expected", TRUE_POSITIVES)
def test_true_positive(matcher, stream, expected):
    name, score, mtype = _match(matcher, stream)
    assert name is not None, f"{stream!r} returned no match (expected ~{expected!r})"
    assert expected.lower() in name.lower(), (
        f"{stream!r} -> {name!r} ({score} {mtype}); expected substring {expected!r}"
    )


@pytest.mark.parametrize("stream,wrong", TRUE_NEGATIVES)
def test_true_negative(matcher, stream, wrong):
    name, score, mtype = _match(matcher, stream)
    assert not (name and name.lower() == wrong.lower()), (
        f"{stream!r} wrongly matched {name!r} ({score} {mtype})"
    )


@pytest.mark.parametrize("stream,expected", EXACT_EXPECTED)
def test_exact_expected(matcher, stream, expected):
    name, score, mtype = _match(matcher, stream)
    assert name is not None and name.lower() == expected.lower(), (
        f"{stream!r} -> {name!r} ({score} {mtype}); expected exactly {expected!r}"
    )


@pytest.mark.parametrize("stream", EXPECTED_NONE)
def test_expected_none(matcher, stream):
    name, score, mtype = _match(matcher, stream)
    assert name is None, (
        f"{stream!r} should return None (wrong-match worse than no-match) but got "
        f"{name!r} ({score} {mtype})"
    )


@pytest.mark.parametrize("name", CALLSIGN_NONE)
def test_callsign_none(matcher, name):
    assert matcher.extract_callsign(name) is None, (
        f"{name!r} extracted a callsign but should not have"
    )


@pytest.mark.parametrize("name,expected", CALLSIGN_EXPECTED)
def test_callsign_expected(matcher, name, expected):
    assert matcher.extract_callsign(name) == expected
