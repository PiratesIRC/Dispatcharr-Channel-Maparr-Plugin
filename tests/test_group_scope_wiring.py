"""AST guard: every channel fetch takes its scope from the resolver.

Modelled on a sibling plugin's read-only mutation guard. The synthetic self-tests
at the bottom are mandatory - an AST guard with no positive fixture is inert and
returns exit 0 for months while proving nothing.
"""
import ast
from pathlib import Path

import pytest

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Channel-Maparr" / "plugin.py"

EXPECTED_FETCH_SITES = 5

# Methods allowed to touch Channel.objects directly. Everything else must go
# through _get_all_channels so the scope cannot be bypassed.
CHANNEL_OBJECTS_ALLOWLIST = {
    "_get_all_channels",
    "_bulk_update_channels",
    "_get_next_channel_number",
    "_import_matched_streams",
    "validate_settings_action",
    # Reads every channel NAME so Create Channels From Streams can tell which
    # names are already used. It must see channels in ignored groups too:
    # narrowing it to the scope would let the action create a second channel
    # with a name an ignored group already carries, which is the duplicate the
    # check exists to prevent. It reads names only and writes nothing.
    "_existing_channel_names",
    # Reads every channel NUMBER, which is unique across the whole
    # installation rather than per group, so a scoped read would hand out a
    # number already in use. Its one write creates a channel in the target
    # group, and that group is checked against the ignore list by
    # _check_group_destinations_not_ignored before the action starts.
    "_create_seed_channels",
}


def _methods(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _calls_named(node, attr):
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == attr):
            yield sub


def _resolver_bound_names(fn):
    """Names bound from any self._resolve_*_scope(...) call inside fn."""
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr in {
                    "_resolve_group_scope", "_resolve_process_scope",
                    "_resolve_category_scope"}):
            for target in node.targets:
                elts = target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
                for el in elts:
                    if isinstance(el, ast.Name):
                        out.add(el.id)
    return out


def check_wiring(source, expected_sites):
    tree = ast.parse(source)
    sites = [(fn, call) for fn in _methods(tree)
             for call in _calls_named(fn, "_get_all_channels")]
    assert len(sites) == expected_sites, (
        f"expected {expected_sites} _get_all_channels call sites, found "
        f"{len(sites)} - a new fetch site must be wired to the resolver too")

    for fn, call in sites:
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        bound = _resolver_bound_names(fn)

        assert "group_ids" in kwargs, f"{fn.name}: group_ids not passed by keyword"
        value = kwargs["group_ids"]
        assert not (isinstance(value, ast.Constant) and value.value is None), \
            f"{fn.name}: passes a literal None (means EVERY channel)"
        assert not isinstance(value, ast.IfExp), (
            f"{fn.name}: a conditional group_ids can still degrade to None - "
            f"this is the shape that shipped bug-044")
        assert isinstance(value, ast.Attribute) or isinstance(value, ast.Name), \
            f"{fn.name}: group_ids should come from the resolved scope"
        if isinstance(value, ast.Attribute):
            assert value.attr == "group_ids", (
                f"{fn.name}: group_ids kwarg reads .{value.attr}, not .group_ids - "
                f"provenance must be pinned to the FIELD, not just the resolved object")
        root = value.value.id if isinstance(value, ast.Attribute) and isinstance(
            value.value, ast.Name) else getattr(value, "id", None)
        assert root in bound, (
            f"{fn.name}: group_ids does not trace to a _resolve_*_scope() result "
            f"(bound names: {sorted(bound)})")

        # A site that passes group_ids but omits include_ungrouped silently
        # evicts every NULL-group channel from a bulk rename/logo/move run -
        # exactly the defect class this slice exists to prevent.
        assert "include_ungrouped" in kwargs, (
            f"{fn.name}: include_ungrouped not passed (would evict ungrouped channels)")
        ug = kwargs["include_ungrouped"]
        assert isinstance(ug, ast.Attribute) and getattr(ug.value, "id", None) in bound, (
            f"{fn.name}: include_ungrouped does not trace to a _resolve_*_scope() result")
        assert ug.attr == "include_ungrouped", (
            f"{fn.name}: include_ungrouped kwarg reads .{ug.attr}, not "
            f".include_ungrouped - provenance must be pinned to the FIELD")


def check_channel_objects_confined(source):
    tree = ast.parse(source)
    offenders = []
    for fn in _methods(tree):
        if fn.name in CHANNEL_OBJECTS_ALLOWLIST:
            continue
        for sub in ast.walk(fn):
            if (isinstance(sub, ast.Attribute) and sub.attr == "objects"
                    and isinstance(sub.value, ast.Name) and sub.value.id == "Channel"):
                offenders.append((fn.name, sub.lineno))
    assert not offenders, (
        f"Channel.objects used outside the allowlist (bypasses the group "
        f"scope): {offenders}")


def test_all_fetch_sites_are_resolver_wired():
    check_wiring(PLUGIN_PY.read_text(encoding="utf-8"), EXPECTED_FETCH_SITES)


def test_channel_objects_stays_inside_the_helpers():
    check_channel_objects_confined(PLUGIN_PY.read_text(encoding="utf-8"))


# --- the detector must BITE ---------------------------------------------

BAD_LITERAL_NONE = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(l, group_ids=None)
'''

BAD_IFEXP = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(l, group_ids=scope.group_ids if s else None)
'''

BAD_UNWIRED = '''
class P:
    def a(self, s, l):
        ids = {g["id"] for g in self._get_all_groups(l)}
        return self._get_all_channels(l, group_ids=ids)
'''

BAD_MISSING_UNGROUPED = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(l, group_ids=scope.group_ids)
'''

BAD_WRONG_FIELD = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(
            l, group_ids=scope.ignored_names, include_ungrouped=scope.include_ungrouped)
'''

GOOD = '''
class P:
    def a(self, s, l):
        scope = self._resolve_process_scope(s, l)
        return self._get_all_channels(
            l, group_ids=scope.group_ids, include_ungrouped=scope.include_ungrouped)
'''


@pytest.mark.parametrize("src", [
    BAD_LITERAL_NONE, BAD_IFEXP, BAD_UNWIRED, BAD_MISSING_UNGROUPED, BAD_WRONG_FIELD,
])
def test_detector_rejects_unwired_shapes(src):
    with pytest.raises(AssertionError):
        check_wiring(src, expected_sites=1)


def test_detector_accepts_the_wired_shape():
    check_wiring(GOOD, expected_sites=1)


BAD_DIRECT_OBJECTS = '''
class P:
    def some_action(self, s, l):
        return list(Channel.objects.all().values("id"))
'''


def test_detector_flags_direct_channel_objects():
    with pytest.raises(AssertionError):
        check_channel_objects_confined(BAD_DIRECT_OBJECTS)
