"""Read the free-text network_affiliation field of an FCC station record.

The field is not a single network. It can name several, in several formats,
sometimes with subchannel numbers and sometimes prefixed by a callsign. Every
question about which networks a station carries goes through here so that a
substring test never decides it.
"""
import re

# Tokens that are structure, not networks.
_DROP_TOKENS = frozenset({"CH", "DT", "TV", "AND", "ON"})

# Real network names that use an ampersand as part of the name itself, rather
# than as a separator between two networks. Whether "&" separates or belongs
# is not decidable from punctuation alone: "This TV & Start" and "Cozi &
# Court TV" have the same shape as "Heroes & Icons" but name two networks
# apiece, not one. This list is the only thing that can tell them apart, so
# it must be checked against a whole comma/slash-delimited segment, never a
# substring. "H&I" and "H & I" are both the same network, Heroes and Icons,
# written with and without spaces around the ampersand.
_AMPERSAND_NETWORK_NAMES = frozenset({"HEROES & ICONS", "H&I", "H & I"})

# A record whose affiliation field is this literal placeholder names no
# network at all. It must not be split into one-letter tokens "N" and "A".
_NOT_APPLICABLE_RE = re.compile(r"^\s*N\s*/\s*A\s*$", re.IGNORECASE)


def station_networks(affiliation):
    """Return the networks named in an affiliation field, in order.

    Uppercase, duplicates removed, subchannel numbers and parenthetical
    annotations stripped.
    """
    if not affiliation:
        return []

    text = str(affiliation)
    if _NOT_APPLICABLE_RE.match(text):
        return []

    # Drop parenthetical annotations such as "(9.1)" or "(main)".
    text = re.sub(r"\([^)]*\)", " ", text)
    # Drop "Ch 3.1" style subchannel markers and bare decimals.
    text = re.sub(r"\bCh\.?\s*\d+(?:\.\d+)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+\.\d+\b", " ", text)

    # Split on comma, semicolon, slash, "and" and ". " first, but never on
    # "&" at this stage: whether "&" is a separator or part of one network's
    # own name can only be judged per segment, against the whole segment,
    # below.
    raw_segments = re.split(r"[,;/]|\s+\band\b\s+|\.\s+", text, flags=re.IGNORECASE)

    parts = []
    for segment in raw_segments:
        cleaned = re.sub(r"\s+", " ", segment.strip().strip(".")).upper()
        if "&" in cleaned and cleaned not in _AMPERSAND_NETWORK_NAMES:
            parts.extend(re.split(r"\s*&\s*", segment))
        else:
            parts.append(segment)

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
