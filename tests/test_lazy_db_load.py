"""Regression: constructing FuzzyMatcher must NOT eager-load the channel DBs.

Eager-loading all ~42k channel records in ``FuzzyMatcher.__init__`` meant every
Dispatcharr plugin *discovery* — which re-instantiates every Plugin and cascades
across all uWSGI/Celery workers via ``/data/plugins/.reload_token`` — burned CPU
loading 42k channels on each worker's single gevent thread. Under channel-zapping
plus a reload cascade this pinned the workers and wedged all UI + streaming (ops
incident 2026-06-27: two autoheal restarts in 7 min). The channel data is only
needed when a mapping run calls ``reload_databases()`` (plugin.py:756), which
clears and reloads the user-selected countries anyway — so the constructor's
eager load was always discarded. Keep construction cheap; load on demand.
"""


def test_construction_does_not_eager_load_databases(fuzzy_module, plugin_dir):
    """Building the matcher must not touch the channel JSON databases."""
    fm = fuzzy_module.FuzzyMatcher(plugin_dir=str(plugin_dir), match_threshold=80)
    assert fm.premium_channels == []
    assert fm.premium_channels_full == []
    assert fm.broadcast_channels == []


def test_reload_databases_still_loads_on_demand(fuzzy_module, plugin_dir):
    """The explicit run-path load (reload_databases) must still populate data."""
    fm = fuzzy_module.FuzzyMatcher(plugin_dir=str(plugin_dir), match_threshold=80)
    assert fm.reload_databases(["US"]) is True
    assert len(fm.premium_channels) > 0
    assert len(fm.broadcast_channels) > 0
