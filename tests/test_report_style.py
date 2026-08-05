"""Guards on the HTML report's presentation.

Each guard below corresponds to a defect that shipped in a sibling plugin's
report before it was fixed, and each was verified here by planting the
regression and watching the test fail.

The palette and the two surface colours it sits on were validated all-pairs for
colourblind safety with a validator that lives in none of these repositories, so
the numbers CANNOT be re-derived. Pinning them is the only way to notice that
someone changed one.
"""
import pathlib
import re

import pytest
from conftest import PLUGIN_DIR, _load_plugin_package  # noqa: F401

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "sample_report.html"

PALETTE_LIGHT = {"--never": "#2a78d6", "--watched": "#1baf7a",
                 "--tuned": "#e34948", "--toonew": "#898781",
                 "--track": "#e1e0d9", "--ok": "#0ca30c", "--bad": "#d03b3b"}
PALETTE_DARK = {"--never": "#3987e5", "--watched": "#199e70",
                "--tuned": "#e66767", "--toonew": "#898781",
                "--track": "#2c2c2a", "--ok": "#0ca30c", "--bad": "#d03b3b"}


@pytest.fixture(scope="module")
def reports():
    _load_plugin_package()
    import channel_maparr.reports as reports_module  # noqa: E402
    return reports_module


# Stripping CSS comments FIRST is load bearing rather than tidiness. Every rule
# below is explained in prose that necessarily quotes the very values it forbids,
# so without the strip each guard fires on its own documentation and the only way
# to make it pass would be to delete the explanation.
def _rule_body(css):
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return without_comments[without_comments.index("body {"):]


def _dark_block(css):
    """Just the dark media query, not everything that follows it.

    Slicing to the end of the file instead would make the zebra-striping guard
    below vacuous, because every ordinary rule sits after the media query and
    would count as being inside it. That is not hypothetical: the first version
    of this helper did exactly that and the guard failed on correct CSS.
    """
    start = css.index("@media (prefers-color-scheme: dark)")
    end = css.index("\n}\n", start) + len("\n}\n")
    return css[start:end]


# --------------------------------------------------------------------------- #
# The token layer
# --------------------------------------------------------------------------- #

def test_the_light_palette_is_pinned(reports):
    light = reports._CSS[:reports._CSS.index("prefers-color-scheme: dark")]
    for name, value in PALETTE_LIGHT.items():
        assert f"{name}: {value}" in light, f"{name} missing or changed in light"


def test_the_dark_palette_is_pinned(reports):
    dark = _dark_block(reports._CSS)
    for name, value in PALETTE_DARK.items():
        assert f"{name}: {value}" in dark, f"{name} missing or changed in dark"


def test_the_measured_ink_ramp_is_pinned(reports):
    """Measured against the two validated surfaces. --ink-dim is the weakest at
    5.24:1 on the light surface, clear of the 4.5:1 floor for normal text.
    Changing a value here without re-measuring puts text below that floor with
    nothing to say so."""
    css = reports._CSS
    light = css[:css.index("prefers-color-scheme: dark")]
    dark = _dark_block(css)
    for name, value in (("--ink", "#16181d"), ("--ink-muted", "#5c616b"),
                        ("--ink-dim", "#656a76")):
        assert f"{name}: {value}" in light, f"{name} changed in light"
    for name, value in (("--ink", "#e8eaed"), ("--ink-muted", "#a7adb8"),
                        ("--ink-dim", "#9aa0ab")):
        assert f"{name}: {value}" in dark, f"{name} changed in dark"


def test_every_variable_referenced_is_defined(reports):
    """A var reference with no definition resolves to nothing. For a fill that
    means black, which is an invisible graphic on the dark surface."""
    css = reports._CSS
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    defined = set(re.findall(r"(--[a-z0-9-]+):", css))
    assert referenced <= defined, f"undefined: {sorted(referenced - defined)}"


def test_no_rule_hardcodes_a_colour(reports):
    """A literal colour in a rule is one that light mode and dark mode cannot
    both be right about. That is how the previous version of this file ended up
    with four important overrides in its dark block."""
    stray = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b",
                                  _rule_body(reports._CSS))))
    assert not stray, f"use a token instead of these literals: {stray}"


def test_every_spacing_value_comes_from_the_scale(reports):
    """Margins, paddings and gaps pick a step off --s1 to --s5. Sizes, font
    sizes, radii and border widths are not spacing and are out of scope."""
    offenders = []
    for prop, value in re.findall(r"\b(margin|padding|gap)\s*:\s*([^;}]+)",
                                  _rule_body(reports._CSS)):
        for token in value.split():
            if token.endswith("px") and token not in ("0", "0px"):
                offenders.append(f"{prop}: {token}")
    assert not offenders, f"off-scale spacing, use var(--sN): {offenders}"


def test_text_hierarchy_uses_ink_tokens_and_never_opacity(reports):
    """An opacity value paints a different colour on every surface it lands on,
    so the contrast ratio moves silently whenever a background changes, and the
    fade applies to everything nested inside."""
    faded = re.findall(r"([^{}]+)\{[^}]*opacity:", _rule_body(reports._CSS))
    assert not faded, f"use an --ink token instead of opacity on: {faded}"


def test_light_and_dark_differ_only_in_token_values(reports):
    """If a theme needs an important override, the tokens are wrong."""
    assert "!important" not in reports._CSS


def test_zebra_striping_is_declared_once_for_both_themes(reports):
    """It used to be declared inside the dark block only, so the two themes
    rendered visibly different tables for months."""
    css = reports._CSS
    assert css.count("tr:nth-child(even) td") == 1
    assert "tr:nth-child(even) td" not in _dark_block(css)


def test_the_focus_ring_on_a_section_heading_is_never_removed(reports):
    """That ring is how the page is driven by a television remote's directional
    pad."""
    body = _rule_body(reports._CSS)
    summary_rules = re.findall(r"summary[^{}]*\{[^}]*\}", body)
    assert summary_rules
    for rule in summary_rules:
        assert "outline" not in rule, rule


# --------------------------------------------------------------------------- #
# Page structure
# --------------------------------------------------------------------------- #

COLUMNS = [("channel_name", "Channel Name"), ("new_name", "New Name")]


def _model(reports, count=3):
    return reports.build_model(
        "Rename preview", COLUMNS,
        [{"channel_name": f"Channel {n}", "new_name": f"New {n}"}
         for n in range(count)],
        account_names=["provider.tv"],
        settings={"dry_run_mode": True},
        databases=["US"],
        version="1.26.0000000",
        now=1_700_000_000.0)


def test_the_section_is_collapsed_by_default(reports):
    """The page is an index, not a wall of table."""
    page = reports.render_html(_model(reports))
    assert "<details>" in page
    assert "<details open>" not in page


def test_the_section_count_equals_the_rows_beneath_it(reports):
    """Same meaning in every section, no exceptions. A reader looking at a
    collapsed page cannot see any distinction a bare heading was drawing."""
    for count in (0, 1, 7):
        page = reports.render_html(_model(reports, count))
        assert f'<span class="count">{count}</span>' in page
        assert page.count("<tr>") - page.count("<thead>") == count


def test_the_section_says_what_it_holds_and_mentions_find_in_page(reports):
    page = reports.render_html(_model(reports))
    assert 'class="sub"' in page
    assert "Find-in-page" in page


def test_the_section_uses_details_and_needs_no_script_to_expand(reports):
    """A client that does not implement details renders everything expanded, so
    the failure mode is everything visible, never content lost."""
    page = reports.render_html(_model(reports))
    opening = page[:page.index("<details>")]
    assert "<script>" not in opening


def test_the_page_is_entirely_self_contained(reports):
    """It is opened off disk, mailed as an attachment, and read on a television
    browser with no route to the internet."""
    page = reports.render_html(_model(reports))
    assert "<link" not in page
    assert "url(" not in page
    external = re.findall(r'(?:src|href)="(?!data:|https://github\.com/)[^"]*"',
                          page)
    assert not external, external


def test_the_logo_is_embedded_rather_than_linked(reports):
    page = reports.render_html(_model(reports))
    assert 'src="data:image/png;base64,' in page


def test_a_logo_that_cannot_be_read_renders_no_image_and_does_not_fail(
        reports, monkeypatch):
    """A relative path resolves against nothing in a mail client, so a missing
    logo must degrade to no masthead image rather than to a broken one."""
    monkeypatch.setattr(reports, "_LOGO_CACHE", [])
    monkeypatch.setattr(reports, "_LOGO_FILENAME", "there_is_no_such_file.png")
    page = reports.render_html(_model(reports))
    assert "<img" not in page
    assert "<h1>" in page


def test_no_svg_uses_a_variable_as_a_presentation_attribute(reports):
    """Support is patchy and it fails silently to black, which is an invisible
    graphic on the dark surface. Colour goes on a CSS rule keyed on a class."""
    page = reports.render_html(_model(reports))
    assert not re.findall(r'(?:fill|stroke)="var\(', page)


# --------------------------------------------------------------------------- #
# Rendered copy
# --------------------------------------------------------------------------- #

def _visible_text(page):
    """The copy a reader sees. The style and script blocks are removed first:
    the CSS legitimately contains double hyphens in every variable name."""
    without = re.sub(r"<(style|script)\b.*?</\1>", " ", page,
                     flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", without)


CONTRACTIONS = ("doesn't", "don't", "can't", "won't", "it's", "isn't",
                "aren't", "didn't", "you're", "they're", "we're", "that's",
                "there's", "wasn't", "hasn't", "haven't", "wouldn't",
                "couldn't", "shouldn't", "let's", "what's")


def test_the_rendered_copy_uses_no_dashes_that_read_as_an_em_dash(reports):
    """A double hyphen renders as an em dash on the page, so it is forbidden
    alongside the real em dash and en dash."""
    text = _visible_text(reports.render_html(_model(reports)))
    for forbidden, name in (("—", "em dash"), ("–", "en dash"),
                            ("--", "double hyphen")):
        assert forbidden not in text, f"the rendered copy contains a {name}"


def test_the_rendered_copy_uses_no_contractions(reports):
    text = _visible_text(reports.render_html(_model(reports))).lower()
    found = [word for word in CONTRACTIONS if word in text]
    assert not found, found


# --------------------------------------------------------------------------- #
# The rendered fixture
# --------------------------------------------------------------------------- #

def test_the_rendered_fixture_still_matches(reports):
    """A render change failing this is the point of it, not a nuisance. Look at
    the difference, decide whether it is what you meant, then regenerate with
    scripts/regen_report_fixture.py and commit the new fixture alongside the
    change that caused it."""
    assert FIXTURE.is_file(), (
        f"{FIXTURE} is missing; regenerate it with "
        f"scripts/regen_report_fixture.py")
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                           / "scripts"))
    import regen_report_fixture
    current = regen_report_fixture.render(reports)
    assert current == FIXTURE.read_text(encoding="utf-8"), (
        "the rendered report changed; review the difference, then run "
        "python scripts/regen_report_fixture.py")


# --------------------------------------------------------------------------- #
# Synthetic self-tests: prove the comment strip is doing the work it claims.
# Without these, a change to _rule_body could make every guard above vacuous.
# --------------------------------------------------------------------------- #

def test_the_comment_strip_hides_values_quoted_in_prose():
    planted = "/* the old value was #ff0000 and 13px */\nbody { color: red; }"
    stripped = _rule_body(planted)
    assert "#ff0000" not in stripped
    assert "13px" not in stripped


def test_the_dark_block_slice_stops_at_the_end_of_the_media_query():
    """If it ran to the end of the file, every ordinary rule would count as
    being inside the dark block and the zebra-striping guard would be vacuous."""
    planted = ("@media (prefers-color-scheme: dark) {\n"
               "  :root {\n    --bg: #000000;\n  }\n"
               "}\n"
               "tr:nth-child(even) td { background: var(--zebra); }\n")
    block = _dark_block(planted)
    assert "--bg: #000000" in block
    assert "tr:nth-child(even) td" not in block


def test_the_colour_guard_sees_a_literal_when_one_is_present():
    planted = "body { color: #ff0000; }"
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", _rule_body(planted))


def test_the_spacing_guard_sees_an_off_scale_value_when_one_is_present():
    planted = "body { padding: 13px; }"
    found = [t for _, v in re.findall(r"\b(margin|padding|gap)\s*:\s*([^;}]+)",
                                      _rule_body(planted))
             for t in v.split() if t.endswith("px")]
    assert found == ["13px"]


def test_the_opacity_guard_sees_an_opacity_when_one_is_present():
    planted = "body { color: red; }\n.sub { opacity: .7; }"
    assert re.findall(r"([^{}]+)\{[^}]*opacity:", _rule_body(planted))
