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
