"""Resolve a broadcast station from a market city and a virtual channel number.

The FCC station table indexed by ``fuzzy_matcher.FuzzyMatcher`` is keyed on
callsign, so a provider name that prints only a market, such as
"US: ABC 9 HD [SYRACUSE]", matches nothing. This module reads that market
reference and resolves it against the same station records.

Resolution runs in two stages, in this order:

1. The market city is the community the station is licensed to. Candidates are
   the stations in that city carrying the stated network, narrowed by the
   channel number and by the state when the name gives one.
2. The market city is served by a station licensed to a neighbouring community,
   which is common: KCPQ serves Seattle from Tacoma, KTVU serves San Francisco
   from Oakland. The city then supplies only the state, and the channel number
   picks the station within it. This stage requires a channel number, because
   without one there is nothing to choose between the stations of one state.

Both stages return a station only when exactly one candidate survives. A wrong
station is worse than no station: it produces a channel named after the wrong
market, and nothing downstream questions it.

Everything here is pure and Django-free, so it can be unit tested outside the
Dispatcharr runtime.
"""
import collections
import re

MarketReference = collections.namedtuple("MarketReference", "number city state")
MarketIndex = collections.namedtuple("MarketIndex", "by_city city_states")

# Words a provider appends to a market that are not part of the city name.
_NOISE_TOKENS = frozenset({
    "HD", "SD", "FHD", "UHD", "4K", "RAW", "NET", "TV", "DT",
})

# Words that mark a name as a secondary service rather than the market's main
# station. "FOX 9 PLUS" in Minneapolis is WFTC, a different station from KMSP,
# which is "FOX 9". Treating these as noise resolves both names to the main
# station and produces two channels claiming to be the same one, so a name
# carrying one of them is refused instead.
_SECONDARY_SERVICE_TOKENS = frozenset({
    "PLUS", "XTRA", "EXTRA", "MAS", "MORE",
})

# Words that are never a place name on their own. A name whose market reads as
# nothing but these is a national or cable feed, not a market: "ABC NEWS LIVE",
# "ABC (EAST)", "FOX SPORTS SOUTHWEST". The test is that EVERY word is in this
# set, so a real city keeps working: Live Oak in Florida and Texas survives
# because "OAK" is not here.
_NON_PLACE_TOKENS = frozenset({
    "NEWS", "LIVE", "NOW", "SPORT", "SPORTS", "CHANNEL", "NETWORK",
    "EAST", "WEST", "INTERNATIONAL", "BUSINESS", "WEATHER", "SOCCER",
    "DEPORTES", "MOVIES", "KIDS", "MUSIC", "KIDZ",
})

# A market may only contain letters, digits, spaces and the punctuation that
# appears in real place names. This rejects the separator rows the provider
# inserts between groups, such as "##### ABC HD #####".
_PLACE_CHARACTERS = re.compile(r"^[A-Z0-9 .'\-/]+$")

# Two-letter tokens that are real state abbreviations. build_market_index fills
# this from the station table so the module carries no copy of the state list.
# Until an index is built, a trailing two-letter token stays part of the city.
_KNOWN_STATES = set()

# Market names that are not the community served city of any station, so no
# amount of inference reaches them. Each entry maps the provider market to the
# (city, state) the station table uses. Keep this list short: a market whose
# station is merely licensed to a neighbouring community is handled by stage 2
# and needs no entry here. Every entry is checked against the shipped station
# table by tests/test_market_index.py.
MARKET_ALIASES = {
    # The Norfolk, Virginia Beach and Newport News market. No station is
    # licensed to a community called "Hampton Roads"; WVEC is licensed to
    # Hampton.
    "HAMPTON ROADS": ("HAMPTON", "VA"),
    # The provider abbreviates Lake Charles to its second word.
    "CHARLES": ("LAKE CHARLES", "LA"),
    # The Fort Myers market station is licensed to Naples and neither city has
    # an ABC record carrying a channel number, so stage 2 cannot reach it.
    "FORT MYERS": ("NAPLES", "FL"),
}


def set_known_states(states):
    """Record the state abbreviations that appear in the station table.

    ``build_market_index`` calls this. It exists so ``parse_market_reference``
    can tell a trailing state code ("ABILENE TX") from a city whose last word
    happens to be two letters long.
    """
    _KNOWN_STATES.clear()
    _KNOWN_STATES.update(s.upper() for s in states if s)


def _strip_noise(text):
    return " ".join(t for t in text.split() if t.upper() not in _NOISE_TOKENS)


def parse_market_reference(stream_name, network):
    """Return the market reference a name states, or None.

    ``network`` is the network the name itself claims, as returned by
    ``Plugin._extract_stream_network``. It is required because the channel
    number is the token that follows it.
    """
    if not stream_name or not network:
        return None

    # The leading prefix is not always a country code. This provider labels
    # four groups with a word (CITY, PRIME, TUBI, NEXT), so accept up to 12
    # letters, the same bound the network token itself uses.
    text = re.sub(r"^\s*[\[(]?[A-Za-z]{2,12}[\])]?\s*[:|]\s*", "", stream_name.strip())

    if any(t.upper() in _SECONDARY_SERVICE_TOKENS for t in re.split(r"[\s\[\]()]+", text)):
        return None

    number = None
    match = re.match(
        r"^" + re.escape(network) + r"\s+(\d{1,3})(?:/\d{1,3})?\b",
        text,
        re.IGNORECASE,
    )
    if match:
        number = match.group(1)

    bracketed = re.search(r"[\[(]([^\])]+)[\])]", text)
    if bracketed:
        city = bracketed.group(1)
        # A trailing "(FL)" is the state, not the city, so read the state from
        # the bracket and the city from the text ahead of it.
        if len(city.strip()) == 2 and city.strip().upper() in _KNOWN_STATES:
            city = text[:bracketed.start()] + " " + city
    else:
        city = text
    city = re.sub(
        r"^" + re.escape(network) + r"\s*(?:\d{1,3}(?:/\d{1,3})?)?\s*",
        "",
        city,
        flags=re.IGNORECASE,
    )
    city = _strip_noise(city).upper().strip(" .,-")

    # Split on the separators rather than matching a lazy prefix against them.
    # The obvious pattern for this, "^(.*?)[\s,]+([A-Z]{2})$", backtracks over
    # every possible split when the tail does not match, which is quadratic in
    # the length of the name: measured at 0.04 s for 2,000 spaces and 0.56 s
    # for 8,000. Stream names come from the provider, so they are not input
    # this code controls, and that shape is what CodeQL reports as a polynomial
    # regular expression on uncontrolled data. Splitting is linear and reads
    # more plainly besides.
    state = None
    tokens = [t for t in re.split(r"[\s,]+", city) if t]
    if len(tokens) > 1 and tokens[-1] in _KNOWN_STATES:
        state = tokens[-1]
        city = " ".join(tokens[:-1]).strip(" .,-")

    if len(city) < 3 or not _PLACE_CHARACTERS.match(city):
        return None
    tokens = city.split()
    if all(t in _NON_PLACE_TOKENS for t in tokens):
        return None
    return MarketReference(number=number, city=city, state=state)


def build_market_index(stations):
    """Index active stations by the community they are licensed to.

    ``city_states`` is built from EVERY station, not only the ones carrying one
    network, because it answers "which state is this market in" for a city that
    no station of the requested network serves directly.
    """
    by_city = collections.defaultdict(list)
    city_states = collections.defaultdict(set)
    states = set()
    for station in stations:
        city = (station.get("community_served_city") or "").upper().strip()
        state = (station.get("community_served_state") or "").upper().strip()
        if state:
            states.add(state)
        if not city:
            continue
        city_states[city].add(state)
        if (station.get("active_ind") or "").upper() == "Y":
            by_city[city].append(station)
    set_known_states(states)
    return MarketIndex(by_city=dict(by_city), city_states=dict(city_states))


def _serves_network(station, network):
    return network.upper() in (station.get("network_affiliation") or "").upper()


def _virtual_channel(station):
    return (station.get("tv_virtual_channel") or "").strip()


def resolve_station(reference, network, index):
    """Return the single station a market reference names, or None.

    Refuses whenever more than one station survives, and whenever none does.
    """
    if reference is None or not network or index is None:
        return None

    city, state = reference.city, reference.state
    alias = MARKET_ALIASES.get(city)
    if alias:
        city = alias[0]
        state = state or alias[1]

    # Stage 1: the market city is the community the station is licensed to.
    in_city = [s for s in index.by_city.get(city, []) if _serves_network(s, network)]
    candidates = in_city
    if state:
        narrowed = [s for s in candidates
                    if (s.get("community_served_state") or "").upper() == state]
        # An empty result here means the state contradicts the city rather than
        # narrowing it, so keep the wider list and let the count decide.
        candidates = narrowed or candidates
    if reference.number:
        narrowed = [s for s in candidates if _virtual_channel(s) == reference.number]
        candidates = narrowed or candidates
    if len(candidates) == 1:
        return candidates[0]

    # Stage 2: the market city is served from a neighbouring community. The
    # city gives only the state. A channel number is required, because it is
    # the only thing that picks one station out of a whole state.
    #
    # This runs ONLY when the market city has no station carrying the network
    # at all. If it has some and they could not be separated, the answer is
    # among them or there is no answer: widening to the whole state then
    # returns a station in a different city, which is a confidently wrong
    # answer rather than no answer. Measured on the live station table, that
    # turned "ABC 4 HD [CHARLESTON]" into a station in Oak Hill.
    if in_city:
        return None
    if not reference.number:
        return None
    states = {state} if state else index.city_states.get(city, set())
    if not states:
        return None
    wider = [s
             for station_list in index.by_city.values()
             for s in station_list
             if _serves_network(s, network)
             and _virtual_channel(s) == reference.number
             and (s.get("community_served_state") or "").upper() in states]
    return wider[0] if len(wider) == 1 else None
