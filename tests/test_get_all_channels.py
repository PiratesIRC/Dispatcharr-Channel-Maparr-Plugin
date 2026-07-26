"""_get_all_channels scope guards (bug-044).

`if group_ids:` treats an EMPTY set as "no filter", which means EVERY channel.
Two callers can reach that state from a typo, so the empty set must filter to
nothing instead.
"""


def test_empty_group_ids_filters_to_nothing(plugin_instance, logger, fake_channel):
    assert plugin_instance._get_all_channels(logger, group_ids=set()) == []
    # Load-bearing: prove the filter was APPLIED. Without this the assertion
    # above passes against the broken guard too.
    assert fake_channel == [{"channel_group_id__in": set()}]


def test_empty_group_ids_logs_a_warning(plugin_instance, logger, fake_channel):
    """Degrade loudly. A silent empty scope is indistinguishable from 'no channels'."""
    plugin_instance._get_all_channels(logger, group_ids=set())
    assert logger.warning.called


def test_none_group_ids_returns_every_channel(plugin_instance, logger, fake_channel):
    """CONTROL - must keep passing. None means 'no scope', which is all channels."""
    rows = plugin_instance._get_all_channels(logger, group_ids=None)
    assert len(rows) == 3
    assert fake_channel == []


def test_subset_group_ids_filters(plugin_instance, logger, fake_channel):
    """CONTROL - must keep passing."""
    rows = plugin_instance._get_all_channels(logger, group_ids={10})
    assert [r["id"] for r in rows] == [1]


def test_include_ungrouped_keeps_null_group_channels(plugin_instance, logger, fake_channel):
    """A blank include filter historically passed None, which included NULL-group
    channels. An explicit id set would evict them, so the flag preserves them."""
    rows = plugin_instance._get_all_channels(
        logger, group_ids={10}, include_ungrouped=True)
    assert sorted(r["id"] for r in rows) == [1, 3]      # kept group 10 + the orphan


def test_include_ungrouped_false_drops_null_group_channels(plugin_instance, logger, fake_channel):
    rows = plugin_instance._get_all_channels(
        logger, group_ids={10}, include_ungrouped=False)
    assert [r["id"] for r in rows] == [1]


def test_include_ungrouped_with_empty_scope_keeps_only_orphans(plugin_instance, logger, fake_channel):
    """ignore_groups='*' with a blank include: every group excluded, but an
    ungrouped channel is not in any group, so it is not excluded by name."""
    rows = plugin_instance._get_all_channels(
        logger, group_ids=set(), include_ungrouped=True)
    assert [r["id"] for r in rows] == [3]
