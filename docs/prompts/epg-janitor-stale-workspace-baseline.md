# Hand-off: EPG Janitor's workspace matcher baseline is stale

Written 2026-08-12 from Channel Mapparr, after taking the quality-tag fix described in
`EPG-Janitor/docs/prompts/quality-tag-gluing-in-a-partial-subclass.md`. Copy everything below the
line into EPG Janitor's coding agent. It is written to be self-contained, so it repeats context that
agent will not have.

---

## The finding

`<workspace>/tools/baselines/epg-janitor.json`, the frozen matcher output the cross-plugin harness
`<workspace>/tools/matcher_parity_check.py` compares against, **does not match what EPG Janitor's
code now produces**. It was not regenerated when the quality-tag fix landed there.

Measured on 2026-08-12 while regenerating Channel Mapparr's own baseline for the same fix. Running

```
python tools/matcher_parity_check.py --write
```

at the workspace root rewrote **two** files, not one: `channel-maparr.json`, which was expected, and
`epg-janitor.json`, which was not. Channel Mapparr restored `epg-janitor.json` byte for byte rather
than committing someone else's baseline, so nothing was changed on your side and the staleness is
still there.

## Why it matters, and why it is not urgent

The harness has two gates. The **golden gate** compares each plugin against its own committed
baseline and currently reports `OK (matches baseline)` for all four, because each plugin's own
`tests/matcher_golden_baseline.json` is separate from the workspace copy and yours is up to date.
The workspace file feeds the **cross-plugin divergence** report, which is what tells you whether the
four matchers agree with each other. A stale entry there makes that report describe a version of
your matcher that no longer exists, so a divergence it shows may already be fixed and one it hides
may be real.

Nothing fails while it is stale, which is exactly why it can sit unnoticed.

## What to do

1. Confirm it first rather than trusting this note:

   ```
   cd <workspace>
   python -c "import hashlib,pathlib;p=pathlib.Path('tools/baselines/epg-janitor.json');print(hashlib.sha256(p.read_bytes()).hexdigest()[:16])"
   python tools/matcher_parity_check.py --write
   python -c "import hashlib,pathlib;p=pathlib.Path('tools/baselines/epg-janitor.json');print(hashlib.sha256(p.read_bytes()).hexdigest()[:16])"
   ```

   Two different hashes means it was stale.

2. **`--write` rewrites all four plugins' files.** Before running it, copy `tools/baselines/*.json`
   somewhere, and afterwards restore every file except your own, so the other three keep their
   contents and timestamps. Channel Mapparr's baseline was regenerated on 2026-08-12 and is current;
   do not overwrite it with an older one.

3. **Report which entries moved before committing.** If more moved than the quality-tag fix
   explains, stop and find out why. For Channel Mapparr the same fix moved exactly 2 of 284 entries,
   both `TNT UHD RAW`, from `TNTRAW` to `TNT RAW`.

4. Run `python tools/matcher_parity_check.py` with no arguments and confirm all four plugins report
   `OK (matches baseline)`.

## One thing worth checking while you are there

The workspace baseline and your own `tests/matcher_golden_baseline.json` hold the same content in
Channel Mapparr but are formatted differently, so they are not byte-identical. If that also holds
for EPG Janitor, copying one over the other reformats the whole file and produces a large diff for a
two-line change. Apply the moved entries to your own file surgically instead.
