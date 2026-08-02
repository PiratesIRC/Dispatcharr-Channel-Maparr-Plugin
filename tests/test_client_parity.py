"""The vendored Newsflasharr client must never drift from its pin.

Hand-editing the vendored copy is how a caller silently stops matching the
service contract. The pin makes drift a build failure rather than a runtime
surprise. The workflow when the shared client changes is to re-copy the whole
file and update the pin, never to patch the vendored copy in place.

The pin lives in scripts/client_manifest.json and NOT in scripts/core_manifest.json.
scripts/sync_core.py rebuilds core_manifest.json from scratch on every re-vendor of
the shared matcher core, so a pin placed there is deleted silently, and
tests/test_core_parity.py parametrizes over that manifest's own keys, so the check
would vanish with a fully green test run.
"""
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDORED = ROOT / "Channel-Maparr" / "notify_client.py"
MANIFEST = ROOT / "scripts" / "client_manifest.json"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_vendored_client_exists():
    assert VENDORED.is_file(), (
        "Channel-Maparr/notify_client.py is missing. Copy it from "
        "<workspace>/_shared/notify_client.py without modification."
    )


def test_the_vendored_client_matches_its_pin():
    pinned = json.loads(MANIFEST.read_text(encoding="utf-8"))["notify_client.py"]
    assert _sha256(VENDORED) == pinned, (
        "Channel-Maparr/notify_client.py drifted from scripts/client_manifest.json. "
        "Re-copy the whole file from the shared source, do not patch it in place, "
        "then update the pin."
    )


def test_the_vendored_client_matches_the_shared_source_when_present():
    """Skipped where the sibling workspace directory is absent, for example on a
    continuous integration runner that checks out this repository alone."""
    shared = ROOT.parent / "_shared" / "notify_client.py"
    if not shared.exists():
        return
    assert _sha256(VENDORED) == _sha256(shared), (
        "The vendored copy no longer matches <workspace>/_shared/notify_client.py."
    )


def test_the_pin_is_not_in_the_matcher_core_manifest():
    """scripts/sync_core.py wholesale-overwrites core_manifest.json, so a client
    pin placed there is deleted on the next matcher-core re-vendor without any
    failure. This test fails if someone moves it back."""
    core_manifest = ROOT / "scripts" / "core_manifest.json"
    pins = json.loads(core_manifest.read_text(encoding="utf-8"))
    assert "notify_client.py" not in pins, (
        "notify_client.py is pinned in scripts/core_manifest.json, which "
        "scripts/sync_core.py rebuilds from scratch. Move the pin to "
        "scripts/client_manifest.json."
    )
