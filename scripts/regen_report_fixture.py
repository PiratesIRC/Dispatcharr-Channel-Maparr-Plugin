"""Regenerate tests/fixtures/sample_report.html, the pinned rendered report.

Run this after a deliberate change to the report's rendering, and commit the new
fixture in the same change that caused it. tests/test_report_style.py compares
the live renderer against that file, so a render change failing that test is the
point of it rather than a nuisance: it forces somebody to look at the difference
and say whether it was meant.

The model below is fixed, invented data with a fixed timestamp and a fixed
version string. The version is NOT the plugin's real one on purpose: taking the
real one would make this fixture fail on every routine version bump, which trains
the reader to regenerate it without looking.

    python scripts/regen_report_fixture.py
"""
import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "sample_report.html"

COLUMNS = [("channel_name", "Channel Name"),
           ("proposed_name", "Proposed Name"),
           ("match_type", "Match Type"),
           ("score", "Score")]

ROWS = [
    {"channel_name": "WFLA HD", "proposed_name": "8.1 WFLA-TV NBC Tampa FL",
     "match_type": "callsign", "score": 100},
    {"channel_name": "ESPN 1", "proposed_name": "ESPN",
     "match_type": "alias", "score": 97},
    {"channel_name": "Discovery Channel East", "proposed_name": "Discovery",
     "match_type": "fuzzy", "score": 88},
    {"channel_name": "TNT West HD", "proposed_name": "TNT",
     "match_type": "fuzzy", "score": 84},
    {"channel_name": "CNN International", "proposed_name": "CNN",
     "match_type": "substring", "score": 91},
    {"channel_name": "KING 5 Seattle",
     "proposed_name": "5.1 KING-TV NBC Seattle WA",
     "match_type": "callsign", "score": 100},
]

FIXED_NOW = 1_700_000_000.0
FIXED_VERSION = "1.26.0000000"


def render(reports):
    """Render the fixture page from `reports`, which the test passes in."""
    model = reports.build_model(
        "Rename preview", COLUMNS, ROWS,
        account_names=["provider.tv"],
        # match_sensitivity, not match_threshold: the report reads the former, and
        # the wrong key left the Match sensitivity row blank in the rendered
        # fixture. Caught by looking at a screenshot of it, which is the argument
        # for rendering the fixture rather than only diffing its text.
        settings={"dry_run_mode": True, "match_sensitivity": "normal"},
        databases=["US"],
        version=FIXED_VERSION,
        now=FIXED_NOW,
        export_filename="channel_mapparr_preview_1.csv")
    return reports.render_html(model)


def _load_reports():
    path = REPO / "Channel-Maparr" / "reports.py"
    spec = importlib.util.spec_from_file_location("cm_reports_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["cm_reports_fixture"] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    page = render(_load_reports())
    FIXTURE.write_text(page, encoding="utf-8", newline="")
    print(f"wrote {FIXTURE} ({len(page)} characters)")
