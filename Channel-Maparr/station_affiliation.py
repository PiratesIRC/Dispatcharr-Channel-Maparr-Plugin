"""Read the free-text network_affiliation field of an FCC station record.

The field is not a single network. It can name several, in several formats,
sometimes with subchannel numbers and sometimes prefixed by a callsign. Every
question about which networks a station carries goes through here so that a
substring test never decides it.
"""
import re

# Tokens that are structure, not networks.
_DROP_TOKENS = frozenset({"CH", "DT", "TV", "AND", "ON"})


def station_networks(affiliation):
    """Return the networks named in an affiliation field, in order.

    Uppercase, duplicates removed, subchannel numbers and parenthetical
    annotations stripped.
    """
    if not affiliation:
        return []

    text = str(affiliation)
    # Drop parenthetical annotations such as "(9.1)" or "(main)".
    text = re.sub(r"\([^)]*\)", " ", text)
    # Drop "Ch 3.1" style subchannel markers and bare decimals.
    text = re.sub(r"\bCh\.?\s*\d+(?:\.\d+)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+\.\d+\b", " ", text)

    # "&" is ambiguous: in "CBS & FOX" it joins two networks, but in
    # "HEROES & ICONS" it is part of one network's own name. Treat it as a
    # delimiter only when it is the sole separator in the string; once a
    # slash is already doing the joining, "&" belongs to the name it sits
    # inside.
    if "/" in text:
        delimiter = r"[,/]|\s+\band\b\s+|\.\s+"
    else:
        delimiter = r"[,/&]|\s+\band\b\s+|\.\s+"
    parts = re.split(delimiter, text, flags=re.IGNORECASE)

    networks = []
    for index, part in enumerate(parts):
        token = part.strip().strip(".").upper()
        if not token or token in _DROP_TOKENS:
            continue
        # A leading callsign followed by a network ("KALB/NBC"): the first
        # element is four letters starting with K or W and is not a known
        # network word, so drop it when something follows.
        if index == 0 and len(parts) > 1 and re.fullmatch(r"[KW][A-Z]{2,3}", token):
            continue
        token = re.sub(r"\s+", " ", token)
        if token not in networks:
            networks.append(token)
    return networks


def primary_network(affiliation):
    """Return the first network named, or None."""
    networks = station_networks(affiliation)
    return networks[0] if networks else None


def carries_network(station, network):
    """True when the station names ``network`` anywhere in its affiliation."""
    if not network:
        return False
    return network.strip().upper() in station_networks(station.get("network_affiliation"))


def is_primary_network(station, network):
    """True only when ``network`` is the FIRST network the station names.

    Use this, not ``carries_network``, when deciding what a station IS. A
    station that carries Telemundo on a subchannel is not a Telemundo station.
    """
    if not network:
        return False
    return primary_network(station.get("network_affiliation")) == network.strip().upper()
