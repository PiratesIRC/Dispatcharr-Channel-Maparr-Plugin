"""OTA / broadcast callsign matching.

Regression lock for the bug where the entire OTA pipeline was inert: the
``*_channels.json`` databases carry only ``National``/``Regional`` premium
entries (no ``broadcast`` type, no ``callsign`` field), so ``broadcast_channels``
was always empty and ``ota_attempted`` stayed 0. Local affiliate streams like
``ABC 5 (WEWS) CLEVELAND HD`` then fell through to premium fuzzy matching and got
"No match found".

The fix ships ``networks.json`` (FCC station table: callsign ->
network_affiliation / community_served_city / community_served_state) and loads
it into ``broadcast_channels`` + ``channel_lookup`` whenever the US database is
selected. ``match_broadcast_channel`` + ``Plugin._format_ota_name`` then render
the configured OTA format ``{NETWORK} - {STATE} {CITY} ({CALLSIGN})``.
"""
import pytest


def test_broadcast_stations_loaded(matcher):
    """networks.json must populate the broadcast table when US is loaded."""
    assert len(matcher.broadcast_channels) > 1000, (
        "OTA station table is empty — networks.json was not loaded into "
        "broadcast_channels"
    )


# (stream_name, callsign, city_upper, state, network_prefix)
OTA_STATIONS = [
    ("ABC 5 (WEWS) CLEVELAND HD", "WEWS", "CLEVELAND", "OH", "ABC"),
    ("ABC 7 (KGO) SAN FRANCISCO HD", "KGO", "SAN FRANCISCO", "CA", "ABC"),
    ("ABC WATE - KNOXVILLE", "WATE", "KNOXVILLE", "TN", "ABC"),
    ("FOX (KTVU)", "KTVU", "OAKLAND", "CA", "FOX"),
    ("ABC 9 (WFTV) ORLANDO HD", "WFTV", "ORLANDO", "FL", "ABC"),
]


@pytest.mark.parametrize("stream,callsign,city,state,network", OTA_STATIONS)
def test_broadcast_match(matcher, stream, callsign, city, state, network):
    cs, station = matcher.match_broadcast_channel(stream)
    assert cs == callsign, f"{stream!r} extracted callsign {cs!r}, expected {callsign!r}"
    assert station is not None, f"{stream!r} ({callsign}) not found in networks.json"
    assert station.get("community_served_city", "").upper() == city
    assert station.get("community_served_state", "").upper() == state
    assert station.get("network_affiliation", "").upper().startswith(network)
