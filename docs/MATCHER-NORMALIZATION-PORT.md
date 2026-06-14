# Matcher Normalization Port — Channel-Maparr

> **Status — 2026-06-14: PORTED.** All three fixes are live in `fuzzy_matcher.py` for
> Channel-Maparr (v1.26.1650854) and have been ported byte-accurate to EPG-Janitor,
> Lineuparr, and Metadata-Trackarr. Channel-Maparr ships regression tests
> (`tests/test_normalization_port.py`) plus a CI-enforced corpus no-regression gate.
> This document is retained as the reference for the port.

**Purpose.** This is a cross-port guide for three `normalize_name` fixes that were
developed and shipped in **Stream-Mapparr** (`fuzzy_matcher.py`). The workspace-root
`CLAUDE.md` drift rule requires it: *"fuzzy_matcher.py is copy-pasted across plugins
and drifting — port matcher fixes + their regression tests to all copies until the
shared-core refactor lands."* The canonical source of truth is
`C:\Users\User\docker\Stream-Mapparr\Stream-Mapparr\fuzzy_matcher.py`.

This document is a **GUIDE, not an auto-applied patch.** Channel-Maparr's
`normalize_name` is structured differently from Stream-Mapparr's (see Applicability),
so the porter MUST verify the insertion points against the live file, run this plugin's
own test suite (`python -m pytest -q`), and run the corpus-diff validation gate
(see Validation & tests) before shipping. Keep the ported code **byte-accurate** to the
canonical source — do not retype the regexes from memory.

The three fixes, all inside `fuzzy_matcher.py`'s `normalize_name`:

- **Fix 1 — Stylized-Unicode decoration** (bug-048): drop pure-decoration tokens
  (superscript / small-cap tier markers, bullets) before the ASCII tag pipeline.
- **Fix 2 — Emoji-as-letter** (bug-051): map an emoji used as a letter (`SP⚽RTS` → `SPoRTS`)
  to its letter, and strip emoji used purely as decoration.
- **Fix 3 — Numeric resolution markers** (bug-055): strip `720p` / `1080p` / `2160p`
  / `3840P` style markers the keyword `QUALITY_PATTERNS` miss.

---

## Applicability for Channel-Maparr

Inspection of `C:\Users\User\docker\Channel-Maparr\Channel-Maparr\fuzzy_matcher.py`:

| Check | Result |
|---|---|
| `normalize_name` exists | **Yes** (def at line ~618) |
| Top anchor `original_name = name` before ASCII tag regexes | **Yes** (line ~640, comment `# Store original for logging`) |
| `if ignore_quality:` loop over `QUALITY_PATTERNS` | **Yes** (lines ~647-649) |
| `import re` | **Yes** (line 8) |
| `import unicodedata` | **Yes** (line 11) |
| `_strip_stylized_tokens` already present | **No** |
| `_normalize_emoji` already present | **No** |
| `RESOLUTION_PATTERNS` already present | **No** |

**Verdict: port-with-adaptation.** All three fixes are absent and all required anchors
and imports exist, so the insertion points are present. However, Channel-Maparr's
`normalize_name` has **drifted** from the canonical and the porter must adapt around the
surrounding structure rather than copy the wiring block verbatim:

- **Extra preprocessing steps** sit between the quality block and the digit/letter spacer
  that Stream-Mapparr does NOT have: `NUM_WORDS_RE` number-word → digit, the CamelCase
  split (`([a-z])([A-Z][a-z])`), and the `(E)`/`(W)` → ` East `/` West ` paren promotion.
  These are downstream of the insertion points, so they are unaffected — but do NOT delete
  them while pasting.
- **`REGIONAL_PATTERNS` differs by design.** Channel-Maparr intentionally does NOT strip
  `East`/`West` (they distinguish separate feeds — see the comment at line ~73). This is
  unrelated to the three fixes; leave it alone.
- **`__init__` default `match_threshold=80`** (vs Stream-Mapparr `85`) and the alias /
  token-index machinery are Channel-Maparr specifics; not touched by this port.
- The module-level fix functions/constants (Fixes 1 & 2) go **before the `class FuzzyMatcher`**
  declaration (line ~146), matching where they live in the canonical source.

Net: the two module-level blocks and the `RESOLUTION_PATTERNS` constant paste in cleanly;
the in-function wiring needs care because the canonical "Combined wiring" block below shows
Stream-Mapparr's neighboring lines, which are not identical to Channel-Maparr's.

---

## Fix 1 — Stylized-Unicode decoration (bug-048)

**Where it goes.** Module level, **before** `class FuzzyMatcher`. The call
`name = _strip_stylized_tokens(name)` goes near the **top of `normalize_name`**, after
`original_name = name`, and runs **unconditionally** (not gated by `ignore_quality`).

**Gotcha (a).** Detect decoration by the Unicode **character name**
(`SUPERSCRIPT` / `SUBSCRIPT` / `SMALL CAPITAL` / `MODIFIER LETTER`), **NOT** by hard-coded
code-point ranges. Real markers fall outside the obvious blocks: small-cap H = `U+029C`
(IPA Extensions), modifier V = `U+2C7D` (Latin-Ext-C), modifier s = `U+02E2`. A range-based
check would silently miss them.

Canonical code (copy verbatim):

```python
# --------------------------------------------------------------------------- #
# Stylized-Unicode decoration stripping
# --------------------------------------------------------------------------- #
# Streams tag names with stylized-Unicode tier/format markers (superscript
# "WEATHERNATION RAW", small-cap "FHD", bullet-prefixed "CNN") that the ASCII tag
# regexes below cannot see. We drop whole tokens that are pure decoration BEFORE
# the ASCII pipeline runs. Detection is by Unicode character *name* (not code-point
# ranges), so it covers superscripts, "modifier letter" superscript capitals, and
# Latin small-caps wherever they live (e.g. small-cap H is U+029C in IPA Extensions
# and modifier V is U+2C7D in Latin-Ext-C, both outside the obvious blocks).

# Ornament glyphs whose Unicode name carries no decoration keyword.
_DECORATIVE_SYMBOLS = frozenset("◉")  # FISHEYE; add individual chars (not strings) here


def _is_decorative_char(ch):
    """True for a stylized letterform/ornament that carries no semantic content in a
    channel name (superscripts, subscripts, modifier-letter superscript capitals,
    Latin small-capitals, curated bullets). ASCII and ordinary letters return False."""
    if ch.isascii():
        return False
    if ch in _DECORATIVE_SYMBOLS:
        return True
    try:
        nm = unicodedata.name(ch)
    except ValueError:
        # unnamed code point (control char / lone surrogate) -> not decoration
        return False
    return ('SUPERSCRIPT' in nm or 'SUBSCRIPT' in nm
            or 'SMALL CAPITAL' in nm or 'MODIFIER LETTER' in nm)


def _strip_stylized_tokens(name):
    """Drop whitespace tokens that are pure stylized decoration, then NFKD-canonicalize
    the remainder. A token is decoration when it has >=1 decorative char, no ASCII
    alphanumeric, and every char is decorative or ASCII punctuation (so a bullet glued
    to a colon, or "HD/RAW" written in superscripts, are dropped too). Real ASCII words
    (Gold/VIP) and non-Latin letters (Arabic/Cyrillic/CJK) are always kept. ASCII-only
    input is returned unchanged via the fast path (no per-char work; NFKD is a no-op
    on ASCII, so skipping it changes nothing)."""
    if name.isascii():
        return name
    kept = []
    for tok in name.split():
        has_decorative = any(_is_decorative_char(c) for c in tok)
        has_ascii_alnum = any(c.isascii() and c.isalnum() for c in tok)
        only_decorative_or_punct = all(
            _is_decorative_char(c) or (c.isascii() and not c.isalnum()) for c in tok
        )
        if has_decorative and only_decorative_or_punct and not has_ascii_alnum:
            continue  # pure decoration -> drop the whole token
        kept.append(tok)
    return unicodedata.normalize('NFKD', ' '.join(kept))
```

---

## Fix 2 — Emoji-as-letter (bug-051)

**Where it goes.** Module level, **before** `class FuzzyMatcher`. The call
`name = _normalize_emoji(name)` goes at the top of `normalize_name`, after
`original_name = name` and **BEFORE** the `_strip_stylized_tokens(name)` call. Runs
**unconditionally** (input cleaning, not gated by `ignore_quality`).

Why before the stylized strip: the soccer ball stands in for a letter inside an
ASCII-alnum token (`SP⚽RTS`), so `_strip_stylized_tokens` would KEEP the token; we must
substitute the glyph for its letter first, otherwise `process_string_for_matching` turns
the ball into a space (`sp rts`) and it never matches `sports`.

Canonical code (copy verbatim):

```python
# --------------------------------------------------------------------------- #
# Emoji-as-letter + emoji decoration normalization
# --------------------------------------------------------------------------- #
# Some streams use an emoji AS A LETTER inside a word: "SP⚽RTS" / "Sp⚽rts" where the
# soccer ball stands in for 'o' (= SPORTS, the beIN family). _strip_stylized_tokens keeps
# the token (it has ASCII alnum) and process_string_for_matching would turn the ball into a
# space ("sp rts"), so it never matches "sports". We substitute the glyph for the letter it
# replaces (only when flanked by ASCII letters) and strip emoji used purely as decoration.

# Emoji that visually replace an ASCII letter when embedded in a word. Extensible.
_EMOJI_LETTER_MAP = {'⚽': 'o'}            # SOCCER BALL = 'o'  (SP⚽RTS -> SPORTS)
# Pictographic ornaments to delete. NOTE: ⚽ is intentionally in BOTH maps — the letter
# map handles it mid-word (-> 'o'); here it catches any ⚽ NOT flanked by ASCII letters
# (standalone/edge), which the substitution above leaves untouched.
_EMOJI_ORNAMENTS = frozenset('♬☾⚽')       # beamed notes, last-quarter moon, soccer ball
# Zero-width / invisible code points that only add noise to a name.
_ZERO_WIDTH = ('️', '‍')         # VARIATION SELECTOR-16, ZERO WIDTH JOINER


def _normalize_emoji(name):
    """Map emoji-as-letters to their letter and strip emoji decoration.

    The letter substitution fires ONLY when the glyph is flanked by ASCII letters
    (so "SP⚽RTS" -> "SPoRTS" but a standalone/edge "⚽" is treated as decoration and
    dropped). Zero-width selectors and ornament pictographs are deleted outright.
    ASCII-only input is returned unchanged (no emoji possible)."""
    if name.isascii():
        return name
    for zw in _ZERO_WIDTH:
        if zw in name:
            name = name.replace(zw, '')
    for glyph, letter in _EMOJI_LETTER_MAP.items():
        if glyph in name:
            name = re.sub(r'(?<=[A-Za-z])' + re.escape(glyph) + r'(?=[A-Za-z])', letter, name)
    if any(c in _EMOJI_ORNAMENTS for c in name):
        name = ''.join(c for c in name if c not in _EMOJI_ORNAMENTS)
    return name
```

> NOTE on `_ZERO_WIDTH`: the two entries are VARIATION SELECTOR-16 (`U+FE0F`) and
> ZERO WIDTH JOINER (`U+200D`). These are invisible code points — when pasting, verify
> the bytes survived your editor (an editor that "cleans" zero-width characters will
> silently empty the tuple). Best practice: copy the bytes directly from the canonical
> file rather than retyping.

---

## Fix 3 — Numeric resolution markers (bug-055)

**Where it goes.** Module level `RESOLUTION_PATTERNS` list (place it next to
`QUALITY_PATTERNS`). The loop runs **inside the `if ignore_quality:` block, BEFORE the
`QUALITY_PATTERNS` loop.**

**Gotcha (b).** `RESOLUTION_PATTERNS` MUST run **before** `QUALITY_PATTERNS`. The middle
quality pattern `\s+\b(4K|...)\b\s+` consumes **both** flanking spaces. If it runs first
on e.g. `SP⚽RTS 4K 3840P` → after emoji it's `SPoRTS 4K 3840P`, removing ` 4K ` glues
`SPoRTS` directly to `3840P` ("SPoRTS3840P"), which destroys the `\b` word-boundary anchor
that the resolution regex needs. Strip resolution first, while the digit run is still
boundary-delimited.

**Gotcha (c).** The resolution regex requires `p`/`i` **glued** to the digits
(`\b\d{3,4}[pi]\b`). Real markers are always written `720P` / `3840P` with no space. A
spaced `\s*` infix would over-strip a standalone roman numeral or letter — e.g.
`Volume 100 I` would lose its trailing `I`. The 3-digit lower bound excludes 2-digit
noise; the 4-digit upper bound excludes 5-digit numbers (`10800p` won't match). The `\b`
after `[pi]` keeps bare numbers intact (`1080`, `Channel 4`).

Canonical code (copy verbatim):

```python
# Numeric resolution markers the keyword QUALITY_PATTERNS miss: 720p, 1080p/i, 2160p,
# 3840P, 480p, etc. — a 3-4 digit run glued directly to p/i. The 3-digit lower bound
# excludes 2-digit noise; the 4-digit upper bound excludes 5-digit numbers (10800p won't
# match). The p/i must be GLUED to the digits (no space): real markers are always written
# "720P"/"3840P", and requiring the glue avoids stripping a spaced standalone P/I such as a
# roman numeral ("Volume 100 I"). The p/i \b anchor keeps bare numbers (1080, "Channel 4")
# intact. Applied with re.IGNORECASE in the ignore_quality block, like QUALITY_PATTERNS.
RESOLUTION_PATTERNS = [
    r'\b\d{3,4}[pi]\b',
]
```

---

## Combined wiring

This is the canonical top-of-`normalize_name` ordering in Stream-Mapparr. Channel-Maparr's
surrounding lines differ (see Applicability) — match the **ordering and the two anchors**,
not the exact neighbor comments. The required sequence:

1. `original_name = name` (existing anchor)
2. `name = _normalize_emoji(name)` — emoji-as-letter, **unconditional**
3. `name = _strip_stylized_tokens(name)` — stylized-Unicode strip, **unconditional**
4. inside `if ignore_quality:` → `RESOLUTION_PATTERNS` loop, **then** `QUALITY_PATTERNS` loop
5. existing digit/letter spacer (`([a-zA-Z])(\d)` / `(\d)([a-zA-Z])`) and the rest unchanged

Canonical excerpt (Stream-Mapparr):

```python
        # Store original for logging
        original_name = name

        # Map emoji-as-letters (⚽ = 'o' in "SP⚽RTS") and strip emoji decoration, before
        # the stylized-Unicode strip and ASCII regexes below — so "beIN SP⚽RTS" -> "beIN sports".
        name = _normalize_emoji(name)

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
        # bullets) up front so the ASCII tag regexes below see plain text. Runs
        # unconditionally: a token written in superscript/small-caps is decoration
        # regardless of tag_handling, and it would otherwise block matches
        # (e.g. a superscript-RAW suffix never matches channel "WeatherNation").
        name = _strip_stylized_tokens(name)

        # CRITICAL FIX (v25.019.0100): Apply quality patterns FIRST, before space normalization
        # ...
        if ignore_quality:
            # Strip numeric resolution markers (3840P/2160P/1080P/720P/...) before the
            # digit/letter spacer below would split "3840P" into "3840 P".
            # Must run before QUALITY_PATTERNS so that removing " 4K " does not glue
            # "SPoRTS" to "3840P" and break the word-boundary anchor.
            for pattern in RESOLUTION_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
```

**Adaptation note for Channel-Maparr.** In Channel-Maparr the existing block is:

```python
        # Store original for logging
        original_name = name

        # CRITICAL FIX (v25.019.0100): Apply quality patterns FIRST, ...
        if ignore_quality:
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
```

Insert the two `_normalize_emoji` / `_strip_stylized_tokens` calls **between**
`original_name = name` and the `if ignore_quality:` line, and add the `RESOLUTION_PATTERNS`
loop as the **first** loop inside `if ignore_quality:` (before the existing `QUALITY_PATTERNS`
loop). Leave the rest of Channel-Maparr's function — NUM_WORDS, CamelCase split, `(E)`/`(W)`
promotion, REGIONAL/GEOGRAPHIC/MISC handling — untouched.

**Gotcha (d).** Steps 2 and 3 (`_normalize_emoji`, `_strip_stylized_tokens`) run
**unconditionally** — they are input cleaning. Only the resolution-marker strip (step 4) is
gated by `ignore_quality`.

**Gotcha (e).** `process_string_for_matching` is **NOT** changed by any of these fixes.
Do not touch it.

> Minor: the `RESOLUTION_PATTERNS` `re.sub` can leave a trailing or double space (observed in
> Stream-Mapparr). That is harmless here because the existing `re.sub(r'\s+', ' ', name).strip()`
> at the end of `normalize_name` collapses it. Do not add extra space handling.

---

## Validation & tests

**Gotcha (f) — the corpus-diff gate. Run before shipping.** Capture `normalize_name`
output for every name in a real stream-name corpus **and** every channel name across all
of Channel-Maparr's `*_channels.json` (US/UK/CA/AU/IN/NL/BR/DE/ES/FR/MX), with the OLD
code and the NEW code, then diff. The assertion is **0 harmful changes**: the only deltas
permitted are the intended ones (emoji-as-letter substitutions, dropped pure-decoration
tokens, removed resolution markers). Any change to a plain-ASCII name is a regression —
because all three fix functions short-circuit on `name.isascii()`, an ASCII name must come
out byte-identical. If an ASCII name changes, stop and investigate before shipping.

**Port the regression tests too.** Bring over the bug-048 / bug-051 / bug-055 cases from
Stream-Mapparr's suite (e.g. small-cap/superscript decoration dropped; `SP⚽RTS` → matches
`sports`; `1080p`/`3840P` stripped while `Channel 4` and `Volume 100 I` are preserved) into
`C:\Users\User\docker\Channel-Maparr\tests\test_matching.py` (or a sibling). Then run the
full suite:

```
pip install -r requirements-dev.txt
python -m pytest -q
```

All existing tests plus the new regression locks must pass. Per the workspace convention,
add a regression test whenever you port a matcher fix.

---

## References

Stream-Mapparr design specs (`Stream-Mapparr/docs/specs/`):

- `2026-06-13-unicode-normalization-design.md` (Fix 1, bug-048)
- `2026-06-13-emoji-letter-normalization-design.md` (Fix 2, bug-051)
- `2026-06-13-resolution-marker-normalization-design.md` (Fix 3, bug-055)

Shipped in **Stream-Mapparr v1.26.1650009**. Bugs: **048** (stylized-Unicode), **051**
(emoji-as-letter), **055** (resolution markers).

Canonical source: `C:\Users\User\docker\Stream-Mapparr\Stream-Mapparr\fuzzy_matcher.py`.

Per workspace-root `CLAUDE.md`: port matcher fixes + their regression tests to all
`fuzzy_matcher.py` copies until the shared-core refactor lands (DEV-WORKFLOW.md §7.1).
