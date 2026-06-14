"""Integrity of the per-country channel databases and the alias table.

These files are hand-curated (see docs/TODO.md), so mechanical checks guard
against the failure modes humans introduce: malformed JSON, accidental
duplicate rows that bloat the candidate index, and structurally broken aliases.
"""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "Channel-Maparr"
COUNTRY_CODES = ["US", "UK", "CA", "AU", "BR", "DE", "ES", "FR", "MX", "NL", "IN"]


def _load(cc):
    return json.loads((PLUGIN_DIR / f"{cc}_channels.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("cc", COUNTRY_CODES)
def test_country_db_structure(cc):
    d = _load(cc)
    assert d["country_code"]
    assert d["country_name"]
    assert isinstance(d["channels"], list) and d["channels"], f"{cc} has no channels"
    for c in d["channels"]:
        assert c.get("channel_name"), f"{cc} has a channel with no channel_name: {c}"


@pytest.mark.parametrize("cc", COUNTRY_CODES)
def test_no_identical_duplicate_rows(cc):
    """No fully-identical channel objects — they are dead weight in the index.

    Duplicate *names* across different categories are allowed (a channel can be
    listed under more than one category); only byte-identical rows are rejected.
    """
    d = _load(cc)
    seen, dupes = set(), []
    for c in d["channels"]:
        key = json.dumps(c, sort_keys=True, ensure_ascii=False)
        (dupes.append(c["channel_name"]) if key in seen else seen.add(key))
    assert not dupes, f"{cc} has {len(dupes)} identical duplicate rows, e.g. {dupes[:5]}"


@pytest.mark.parametrize("cc", COUNTRY_CODES)
def test_country_db_is_bmp_only(cc):
    """Astral-plane characters in shipped data risk the loader's reject path."""
    text = (PLUGIN_DIR / f"{cc}_channels.json").read_text(encoding="utf-8")
    offenders = sorted({hex(ord(c)) for c in text if ord(c) > 0xFFFF})
    assert not offenders, f"{cc}_channels.json has non-BMP characters: {offenders}"


# --- Alias table ---
def _aliases():
    import sys
    sys.path.insert(0, str(PLUGIN_DIR))
    from aliases import CHANNEL_ALIASES  # noqa: E402
    return CHANNEL_ALIASES


def test_alias_table_shape():
    aliases = _aliases()
    assert isinstance(aliases, dict) and aliases
    for canonical, variants in aliases.items():
        assert isinstance(canonical, str) and canonical.strip(), f"bad canonical key: {canonical!r}"
        assert isinstance(variants, list) and variants, f"{canonical!r} has no variants"
        for v in variants:
            assert isinstance(v, str) and v.strip(), f"{canonical!r} has a blank variant: {variants!r}"


def test_alias_variants_have_no_duplicates_within_entry():
    # Exact (case-sensitive) duplicates only. Case variants like
    # "Fox Business"/"FOX Business" are intentional redundancy that the matcher's
    # normalization collapses; an exact repeat is pure noise.
    aliases = _aliases()
    offenders = {
        canonical: variants
        for canonical, variants in aliases.items()
        if len(variants) != len(set(variants))
    }
    assert not offenders, f"alias entries with exact-duplicate variants: {list(offenders)[:5]}"
