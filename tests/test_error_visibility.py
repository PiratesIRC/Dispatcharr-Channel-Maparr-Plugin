"""Dispatcharr's plugin card renders `message` (transient green toast), `error`
(persistent red) and `file`. `status` renders NOWHERE. A failure return that
sets no `error` key looks exactly like success.
"""
import ast
from pathlib import Path

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Channel-Maparr" / "plugin.py"


def _error_returns_without_error_key(source):
    """Line numbers of `return {... "status": "error" ...}` with no "error" key."""
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        pairs = {}
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant):
                pairs[key.value] = value
        status = pairs.get("status")
        if (isinstance(status, ast.Constant) and status.value == "error"
                and "error" not in pairs):
            offenders.append(node.lineno)
    return offenders


def test_every_literal_error_return_sets_the_error_key():
    offenders = _error_returns_without_error_key(
        PLUGIN_PY.read_text(encoding="utf-8"))
    assert not offenders, (
        f"status='error' returns with no `error` key (invisible to the user) "
        f"at lines: {offenders}"
    )


# --- the detector must BITE: an AST guard with no positive fixture is inert ---

BAD = '''
def f():
    return {"status": "error", "message": "boom"}
'''

GOOD_ERROR_KEY = '''
def f():
    return {"status": "error", "error": "boom"}
'''

GOOD_NOT_AN_ERROR = '''
def f():
    return {"status": "success", "message": "fine"}
'''


def test_detector_flags_an_error_return_without_the_key():
    assert _error_returns_without_error_key(BAD) == [3]


def test_detector_accepts_a_visible_error():
    assert _error_returns_without_error_key(GOOD_ERROR_KEY) == []


def test_detector_ignores_success_returns():
    assert _error_returns_without_error_key(GOOD_NOT_AN_ERROR) == []


def test_validate_settings_surfaces_a_red_error(plugin_instance, logger, fake_groups):
    """The computed-status return is invisible to the AST guard, so pin it here."""
    fake_groups([{"id": 10, "name": "Sports"}])
    result = plugin_instance.validate_settings_action({"channel_databases": ""}, logger)
    assert result["status"] == "error"
    assert result.get("error"), "validation failure rendered as a green toast"
    assert "message" not in result
