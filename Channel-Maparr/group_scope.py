"""Pure, Django-free channel-group scope resolution.

Lives outside plugin.py so the whole include/exclude behaviour table can be
unit-tested with zero mocks. plugin.py supplies the ORM rows and formats the
returns; every rule lives here.

The contract is section 4 of
docs/superpowers/specs/2026-07-26-ignore-groups-design.md.
"""
import re
from dataclasses import dataclass

try:
    from .wildcard_match import expand_patterns
except ImportError:                      # loaded standalone (tests, or a non-package path)
    from wildcard_match import expand_patterns

_SPLIT = re.compile(r'[,\n]+')


class GroupScopeError(Exception):
    """The configured scope cannot be honoured; the action must refuse to run.

    Fail-closed is right here because the scope is the operator's PRIMARY input,
    not a defence-in-depth backstop: "I could not resolve your exclusion" must
    never authorize touching the channels it was meant to protect.
    """


@dataclass(frozen=True)
class GroupScope:
    """The resolved include/exclude outcome for one action run.

    ignored_names lists every group name matched by the ignore patterns
    ANYWHERE, which is a SUPERSET of out_of_scope_names (the subset that was
    already outside the include scope, so removing them changed nothing).
    A consumer that sums the two counts, or reports len(ignored_names) as
    "groups excluded from this run", will over-count.
    """
    group_ids: frozenset[int]
    include_ungrouped: bool
    ignored_names: tuple[str, ...] = ()
    out_of_scope_names: tuple[str, ...] = ()
    info: str = ""


def parse_tokens(raw):
    """Split a comma/newline separated setting into non-empty stripped tokens.

    Empty tokens are dropped BEFORE any emptiness test, so a stray comma reads
    as "no list" rather than "a list that matched nothing" - which, under
    fail-closed resolution, would hard-error every action.
    """
    if not raw:
        return []
    return [tok.strip() for tok in _SPLIT.split(raw) if tok.strip()]


def build_name_to_ids(rows):
    """Map group name -> SET of ids.

    A set, not a scalar: Dispatcharr permits two groups with the same name, and
    a scalar map silently drops one of them - leaving it unprotected by an
    exclusion that names it.
    """
    mapping = {}
    for row in rows:
        name, gid = row.get('name'), row.get('id')
        if name is None or gid is None:
            continue
        mapping.setdefault(name, set()).add(gid)
    return mapping


def resolve_group_scope(include_value, ignore_value, group_name_to_ids, *, include_label):
    """Resolve the include filter, then subtract the exclusion.

    Returns a GroupScope whose group_ids is always explicit. Raises
    GroupScopeError for every refusal case in the spec's section 4 table.
    """
    include_tokens = parse_tokens(include_value)
    ignore_tokens = parse_tokens(ignore_value)

    # --- include ---------------------------------------------------------
    if include_tokens:
        # Exact, case-sensitive: unchanged from the pre-existing behaviour.
        missing = [t for t in include_tokens if t not in group_name_to_ids]
        target = set()
        for tok in include_tokens:
            target |= group_name_to_ids.get(tok, set())
        if not target:
            raise GroupScopeError(
                f"None of the groups named in '{include_label}' could be found: "
                f"{', '.join(missing)}"
            )
        include_ungrouped = False
    else:
        target = set()
        for ids in group_name_to_ids.values():
            target |= ids
        include_ungrouped = True

    # --- exclude ---------------------------------------------------------
    ignored_names, out_of_scope = (), ()
    if ignore_tokens:
        if not group_name_to_ids:
            raise GroupScopeError(
                "'Channel Groups to Ignore' is set, but Dispatcharr has no "
                "channel groups to match it against."
            )
        matched, unmatched = expand_patterns(
            ignore_tokens, list(group_name_to_ids), ci_plain=True)
        if unmatched:
            raise GroupScopeError(
                f"These entries in 'Channel Groups to Ignore' match no channel "
                f"group: {', '.join(unmatched)}. Check the spelling, or use a "
                f"wildcard (a group name containing a comma cannot be written "
                f"literally, because the setting splits on commas)."
            )
        ignored_ids = set()
        for name in matched:
            ignored_ids |= group_name_to_ids[name]
        ignored_names = tuple(matched)
        # A real group that the include filter had already excluded: a no-op,
        # NOT a typo. Reported, never fatal.
        out_of_scope = tuple(
            n for n in matched if not (group_name_to_ids[n] & target))
        target -= ignored_ids
        if not target:
            raise GroupScopeError(
                f"'Channel Groups to Ignore' excluded every group that "
                f"'{include_label}' selected, so there is nothing left to "
                f"process. Narrow the exclusion or widen the selection."
            )

    return GroupScope(
        group_ids=frozenset(target),
        include_ungrouped=include_ungrouped,
        ignored_names=ignored_names,
        out_of_scope_names=out_of_scope,
        info=_describe(include_tokens, ignored_names, include_label),
    )


def split_rows_by_ignore(rows, ignore_value, *, group_key='channel_group'):
    """Partition persisted result rows into (kept, dropped) by group NAME.

    The rename/tag actions replay a results file and never fetch channels, so
    the exclusion has to be applied here too. Matching on the stored NAME rather
    than an id means a stale file is still filtered after the group has been
    renamed or deleted.

    Deliberately does not refuse on an unmatched token: the results file may
    legitimately contain no rows from a named group, and refusing a rename for
    a group absent from *this file* would be wrong. The typo case is already
    caught at scan time by resolve_group_scope.
    """
    rows = list(rows)
    tokens = parse_tokens(ignore_value)
    if not tokens:
        return rows, []

    present = sorted({r.get(group_key) for r in rows if r.get(group_key)})
    matched, _ = expand_patterns(tokens, present, ci_plain=True)
    ignored = set(matched)

    kept, dropped = [], []
    for row in rows:
        (dropped if row.get(group_key) in ignored else kept).append(row)
    return kept, dropped


def _describe(include_tokens, ignored_names, include_label):
    parts = []
    if include_tokens:
        parts.append(f"{include_label}: {', '.join(include_tokens)}")
    else:
        parts.append(f"{include_label}: all groups")
    if ignored_names:
        parts.append(f"ignoring {len(ignored_names)} group(s): "
                     f"{', '.join(ignored_names)}")
    return "; ".join(parts)
