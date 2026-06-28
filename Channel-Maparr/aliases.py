"""
Built-in channel alias table for Channel-Maparr.

Maps official channel-database names to common IPTV stream name variants.
Stage 0 of the matching pipeline — an O(1) exact-or-near-exact lookup that
runs before the fuzzy stages. A successful alias hit is faster, more
reviewable, and safer than a fuzzy score on noisy provider strings.

Sourced from Lineuparr. Entries whose key doesn't match any Channel-Maparr
database name are simply unused (no harm). User aliases merged on top via
the FuzzyMatcher constructor.
"""

CHANNEL_ALIASES = {
    # --- News ---
    "ABC News Live": ["ABC News", "ABC News Live"],
    "AccuWeather": ["AccuWeather", "Accu Weather"],
    "BBC News": ["BBC News", "BBC World News"],
    "Bloomberg TV": ["Bloomberg", "Bloomberg Television", "Bloomberg TV"],
    "Bloomberg Television": ["Bloomberg", "Bloomberg TV", "Bloomberg Television"],
    "CNN": ["CNN", "CNN US", "CNN USA"],
    "CNN En Español": ["CNN Espanol", "CNN en Espanol", "CNN Spanish"],
    "CNNi": ["CNN International", "CNNi"],
    "CNBC": ["CNBC", "CNBC US"],
    "CNBC World": ["CNBC World"],
    "C-SPAN": ["C-SPAN", "CSPAN", "C SPAN"],
    "C-SPAN2": ["C-SPAN 2", "CSPAN 2", "C SPAN 2", "C-SPAN2"],
    "FOX Business Network": ["Fox Business", "FBN", "FOX Business"],
    "FOX News Channel": ["Fox News", "FNC", "FOX NEWS", "Fox News Channel"],
    "E! Entertainment Television": ["E!", "E Entertainment", "E! Entertainment", "E! Entertainment Television"],
    "FOX Weather": ["Fox Weather"],
    "HLN": ["HLN", "Headline News", "Headline News Network", "HLN Headline News", "HLN Headline News Network", "CNN HLN", "CNN Headline News"],
    "HLN Headline News Network": ["HLN", "Headline News", "Headline News Network", "CNN HLN", "CNN Headline News"],
    "MS Now": ["MSNBC", "MSNBC Now", "MS Now"],
    "MSNBC": ["MSNBC", "MS Now", "MSNBC Now"],
    "Newsmax": ["Newsmax", "Newsmax TV"],
    "NewsNation": ["NewsNation", "News Nation"],
    "Weather Channel": ["Weather Channel", "TWC", "The Weather Channel"],

    # --- Sports ---
    "ACC Network": ["ACC Network", "ACCN"],
    "Big Ten Network": ["Big Ten Network", "BTN", "Big 10 Network", "Big Ten"],
    "Big 10 Network": ["Big Ten Network", "BTN", "Big 10 Network", "Big Ten"],
    "CBS Sports Network": ["CBS Sports Network", "CBSSN", "CBS Sports"],
    "ESPN": ["ESPN", "ESPN US", "ESPN USA"],
    "ESPN2": ["ESPN 2", "ESPN2"],
    "ESPNEWS": ["ESPN News", "ESPNEWS", "ESPNews"],
    "ESPNU": ["ESPNU"],
    "FanDuel TV": ["FanDuel TV", "FanDuel", "TVG"],
    # NOTE: Do NOT alias regional FanDuel Sports feeds (Cincinnati, Detroit,
    # Florida, Midwest, North, Ohio, Oklahoma, SoCal, South, Southeast,
    # Southwest, Sun, West, Wisconsin) to "FanDuel TV Extra". That fallback
    # gave every regional sports channel the same generic FanDuel EPG when
    # no regional EPG existed — worse than NO MATCH. Regions with a real
    # regional EPG (e.g. Midwest, Southwest, West) match by direct name.
    "FS1": ["Fox Sports 1", "FS1", "FS 1", "Fox Sport 1"],
    "Fox Sports 1": ["Fox Sports 1", "FS1", "FS 1", "Fox Sport 1"],
    "FS2": ["Fox Sports 2", "FS2", "FS 2", "Fox Sport 2"],
    "Fox Sports 2": ["Fox Sports 2", "FS2", "FS 2", "Fox Sport 2"],
    "FOX Sports": ["FOX Sports", "Fox Sports"],
    "GOLF Channel": ["Golf Channel", "Golf Ch", "GOLF", "NBC Golf Channel", "NBC GOLF", "US GOLF"],
    "Golf Channel": ["Golf Channel", "Golf Ch", "GOLF", "NBC Golf Channel", "NBC GOLF", "US GOLF"],
    "MLB Network": ["MLB Network", "MLB Net", "MLBN"],
    "NBA TV": ["NBA TV", "NBATV"],
    "NFL Network": ["NFL Network", "NFL Net", "NFLN"],
    "NHL Network": ["NHL Network", "NHL Net", "NHLN"],
    "SEC Network": ["SEC Network", "SECN"],
    "Tennis Channel HD": ["Tennis Channel", "Tennis Ch"],
    "TUDN": ["TUDN", "Univision Deportes", "Univision Deportes Network", "UDN"],
    "Univision Deportes": ["TUDN", "Univision Deportes", "Univision Deportes Network"],

    # --- Movies ---
    "Cinemax": ["Cinemax", "Cinemax US"],
    "HBO East": ["HBO East", "HBO (East)", "HBO"],
    "HBO Comedy East HD": ["HBO Comedy East", "HBO Comedy (East)", "HBO Comedy"],
    "HBO Drama HD East": ["HBO Drama East", "HBO Drama (East)", "HBO Drama", "HBO Signature"],
    "HBO Hits HD East": ["HBO Hits East", "HBO Hits (East)", "HBO Hits", "HBO 2", "HBO2"],
    "HBO Latino": ["HBO Latino"],
    "HBO Movies HD": ["HBO Movies", "HBO Movies HD", "HBO Movies East", "HBO Movies (East)", "HBO Zone"],
    "Paramount+ with SHOWTIME EAST": ["Showtime East", "Showtime (East)", "SHOWTIME EAST", "Showtime"],
    "Showtime (E)": ["Paramount+ with Showtime", "Paramount+ with Showtime HD", "Showtime East", "Showtime"],
    "Showtime (W)": ["Paramount+ with Showtime (Pacific)", "Paramount+ with Showtime HD (Pacific)", "Showtime West"],
    "Showtime 2": ["Showtime 2 East", "Showtime 2 (East)", "Showtime 2", "SHOWTIME 2"],
    "SHOWTIME 2 East": ["Showtime 2 East", "Showtime 2 (East)", "Showtime 2"],
    "STARZ Cinema East HD": ["Starz Cinema East", "STARZ CINEMA EAST"],
    "STARZ Comedy East HD": ["Starz Comedy East", "STARZ COMEDY EAST"],
    "STARZ Edge East HD": ["Starz Edge East", "STARZ EDGE EAST"],
    "STARZ ENCORE East": ["Starz Encore East", "STARZ ENCORE EAST", "Starz Encore"],
    "STARZ ENCORE West": ["Starz Encore West", "STARZ ENCORE WEST"],
    "STARZ ENCORE Westerns": ["Starz Encore Westerns", "STARZ ENCORE WESTERNS", "StarzenCore Westerns"],
    "STARZ In Black East HD": ["Starz In Black East", "STARZ IN BLACK EAST"],
    "SHOWTIME EXTREME": ["Showtime Extreme", "SHO Extreme"],
    "STARZ": ["Starz", "STARZ"],
    "STARZ Kids & Family": ["Starz Kids", "Starz Kids HD", "STARZ Kids & Family"],
    "SundanceTV": ["Sundance", "SundanceTV", "Sundance TV"],
    "TCM": ["TCM", "Turner Classic Movies"],

    # --- Kids ---
    "Cartoon Network": ["Cartoon Network", "Cartoon Network HD", "CN", "Cartoon Net HD", "Cartoon Netwrk"],
    "Cartoon Network East": ["Cartoon Network", "Cartoon Network East", "CN"],
    "Disney Channel East": ["Disney Channel", "Disney Channel East", "Disney Ch"],
    "Disney Junior": ["Disney Junior", "Disney Jr"],
    "Disney Jr HD": ["Disney Junior HD", "Disney Junior", "Disney Jr"],
    "Nick Jr.": ["Nick Jr", "Nick Junior"],
    "Nick/Nick at Nite (E)": ["Nickelodeon", "Nickelodeon East", "Nick", "Nick at Nite"],
    "Nick/Nick at Nite (W)": ["Nickelodeon West", "Nick West", "Nick at Nite West"],
    "Nickelodeon East": ["Nickelodeon", "Nickelodeon East", "Nick", "Nickelodeon US"],

    # --- Entertainment ---
    "A&E": ["A&E", "A and E", "AE"],
    "AMC": ["AMC", "AMC US"],
    "BBC America": ["BBC America", "BBCA"],
    "CleoTV": ["Cleo TV", "CleoTV"],
    "BET": ["BET", "Black Entertainment Television"],
    "E!": ["E!", "E Entertainment", "E! Entertainment", "E! Entertainment Television"],
    "Freeform": ["Freeform", "ABC Family"],
    "FX": ["FX", "FX US"],
    "FXX": ["FXX", "FX X"],
    "HISTORY Channel, The": ["History", "History Channel", "HISTORY"],
    "Heroes & Icons (H&I)": ["Heroes & Icons", "Heroes and Icons", "H&I", "Heros & Icons"],
    "ION East HD": ["ION", "ION East", "ION Television"],
    "Lifetime": ["Lifetime", "Lifetime US"],
    "LMN": ["LMN", "Lifetime Movie Network", "LMN HD"],
    "Lifetime Movie Network": ["LMN", "Lifetime Movie Network", "LMN HD"],
    "Paramount Network": ["Paramount", "Paramount Network"],
    "Syfy": ["Syfy", "Sci-Fi", "SciFi"],
    "TBS": ["TBS", "TBS US"],
    "TNT": ["TNT", "TNT US", "TNT USA"],
    "ShortsTV": ["Shorts TV", "ShortsTV"],
    "USA Network": ["USA Network", "USA"],
    "UPTV": ["UP TV", "UPTV"],
    "truTV": ["truTV", "tru TV"],

    # --- Home & Garden ---
    "DIY": ["DIY Network", "Magnolia Network", "Magnolia"],

    # --- Reality & Lifestyle ---
    "Bravo": ["Bravo", "Bravo US"],
    "Bravo Vault": ["Bravo Vault"],
    "HGTV": ["HGTV", "HGTV US", "Home & Garden Television", "Home and Garden Television"],
    "OWN": ["OWN", "Oprah Winfrey Network"],
    "OWN: Oprah Winfrey Network": ["OWN", "Oprah Winfrey Network"],
    "TLC": ["TLC", "TLC US"],

    # --- Comedy ---
    "Comedy Central": ["Comedy Central", "CC", "ComedyCentral", "ComedyCentHD", "Comedy Central HD"],

    # --- Discovery ---
    "Animal Planet": ["Animal Planet", "Animal Planet US"],
    "Discovery": ["Discovery", "Discovery Channel"],
    "Investigation Discovery": ["Investigation Discovery", "ID"],
    "National Geographic": ["National Geographic", "National Geographic HD", "Nat Geo", "Nat Geo HD", "NatGeo"],
    "National Geographic Channel": ["Nat Geo", "National Geographic", "NatGeo"],
    "Nat Geo WILD": ["Nat Geo Wild", "NatGeo Wild", "National Geographic Wild"],
    "Nat Geo Wild": ["Nat Geo Wild", "NatGeo Wild", "National Geographic Wild"],
    "Science": ["Discovery Science", "Science Channel"],
    "Smithsonian Channel": ["Smithsonian", "Smithsonian Channel"],

    # --- Crime ---
    "Oxygen": ["Oxygen True Crime", "Oxygen True Crime HD"],
    "Oxygen True Crime": ["Oxygen", "Oxygen True Crime"],
    "Oxygen True Crime Archives": ["Oxygen True Crime Archives"],

    # --- Music ---
    "CMT": ["CMT", "Country Music Television"],
    "MTV": ["MTV", "MTV US"],
    "MTV2": ["MTV2", "MTV 2", "MTV2: Music Television", "MTV2: Music Television HD"],
    "VH1": ["VH1", "VH 1"],

    # --- Food & Travel ---
    "Cooking Channel": ["Cooking Channel", "Cooking Ch"],
    "Food Network": ["Food Network", "Food Net"],
    "Recipe TV": ["RecipeTV", "Recipe TV"],
    "Tastemade Home": ["Tastemade"],
    "Tastemade Travel": ["Tastemade Travel"],

    # --- Premium channels ---
    "EPIX 1": ["EPIX", "Epix", "EPIX 1", "MGM+", "MGM+ East", "MGM+ HD"],
    "EPIX 2": ["EPIX 2", "MGM 2", "MGM+ 2"],
    "EPIX Hits": ["EPIX Hits", "MGM+ Hits", "MGM Hits"],
    "EPIX Drive-In": ["EPIX Drive-In", "MGM+ Drive-In", "MGM Drive-In"],

    # Rebrand bridges surfaced by the channel<->stream alias miner (ported from
    # Lineuparr's proven entries, 2026-06-28): new<->old brand names the normalizer
    # cannot derive (Fox Movie Channel->FXM, EPIX->MGM+, DIY->Magnolia, the HBO
    # multiplex renames, MoreMax<->Cinemax Hits). NOTE: unlike Lineuparr (which iterates
    # the map by lineup name), Channel-Maparr inverts it to a variant->one-canonical
    # reverse index, so a redundant "FXM" canonical would win the "fxm" key and shadow
    # the real channel-DB name. Keep only the real name "FX Movie Channel".
    "FX Movie Channel": ["FXM", "FX Movie Channel", "FXMovie", "Fox Movie Channel", "FXM Retro"],
    "MGM+": ["MGM+", "MGM Plus", "MGM+ East", "EPIX", "Epix", "EPIX 1"],
    "Magnolia Network": ["Magnolia Network", "Magnolia", "DIY Network", "DIY"],
    "Cinemax Hits HD": ["Cinemax Hits HD", "Cinemax Hits", "MoreMax", "More Max"],
    "More Max": ["More Max", "MoreMax", "Cinemax Hits"],
    "HBO Signature": ["HBO Signature", "HBO Drama", "HBO Drama HD"],
    "HBO Zone": ["HBO Zone", "HBO Movies", "HBO Movies HD"],
    "HBO 2": ["HBO 2", "HBO2", "HBO Hits"],
    "The Movie Channel (E)": ["The Movie Channel", "Movie Channel East", "TMC", "TMC East"],
    "The Movie Channel (W)": ["The Movie Channel West", "Movie Channel West", "TMC West"],
    "The Movie Channel Xtra": ["TMC Xtra", "Movie Channel Xtra", "The Movie Channel Extra"],
    "The Movie Channel Xtra (E)": ["TMC Xtra", "Movie Channel Xtra", "The Movie Channel Extra East"],

    # --- Additional aliases for DISH lineup ---
    "American Heroes Channel": ["AHC", "American Heroes Channel", "American Heroes"],
    "BabyFirstTV": ["Baby First", "BabyFirst", "BabyFirstTV", "Baby First TV"],
    "getTV": ["Get TV", "getTV"],
    "GSN": ["GSN", "Game Show Network"],
    "Pop": ["Pop TV", "Pop TV East"],
    "ReelzChannel": ["Reelz", "ReelzChannel"],
    "Telemundo": ["Telemundo"],

    # --- Faith & Family ---
    "FETV": ["FETV", "Family Entertainment Television", "Family Entertainment TV"],

    # --- Other ---
    "MeTV": ["ME TV", "MeTV"],
    "Mythbusters": ["Mythbusters", "MYTHBUSTERS"],
    "Hallmark Channel": ["Hallmark", "Hallmark Channel"],
    "Hallmark Mystery": ["Hallmark Movies", "Hallmark Mystery", "Hallmark Movies & Mysteries"],
    "Hallmark Movies & Mysteries": ["Hallmark Mystery", "Hallmark Movies & More", "Hallmark Mystery HD"],
    "MotorTrend": ["MotorTrend", "Motor Trend", "Velocity"],
    "Travel Channel": ["Travel Channel", "Travel Ch"],

    # --- Faith (rebrands) ---
    # Hillsong Channel rebranded to TBN Inspire on 2022-01-01 in the US.
    "Hillsong Channel": ["Hillsong Channel", "TBN Inspire", "Hillsong", "The Church Channel"],

    # --- Movies (rebrands / discontinued) ---
    # Showtime Beyond was rebranded SHO×BET on 2020-07-15.
    "Showtime Beyond": ["Showtime Beyond", "SHO×BET", "SHO BET", "SHOxBET", "Showtime BET", "SHO X BET"],

    # --- UK: News ---
    "Al Jazeera English": ["Al Jazeera English", "Al Jazeera English HD", "Al Jazeera HD"],
    "NDTV World": ["NDTV 24x7", "UKSD NDTV 24x7", "UK: NDTV 24X7 SD"],

    # --- UK: Sky Sports ---
    "Sky Sports +": ["SkySp+", "SkySp+HD", "HEVC HD Sky Sports Plus"],
    "Sky Sports Premier League": ["SkySp PL HD", "UKHD Sky Sports Premier League HD"],
    "Sky Sports Football": ["SkySp Fball", "SkySp Fball HD", "Sky Sports Football HD", "UKHD Sky Sports Football HD"],
    "Sky Sports Cricket": ["SkySp Cricket", "SkySpCricket HD"],
    "Sky Sports Golf": ["SkySp Golf", "SkySp Golf HD"],
    "Sky Sports F1": ["SkySp F1", "SkySp F1 HD"],
    "Sky Sports Action": ["SkySp Action", "SkySp ActionHD", "UK: SKY SPORTS ARENA HD"],
    "Sky Sports Mix": ["SkySp Mix HD"],
    "Sky Sports News": ["SkySp News HD", "Sky Sports News HD"],
    "Sky Sports Racing": ["SkySp Racing", "SkySp Racing HD"],
    "Sky Sports Main Event": ["SkySpMainEvHD", "Sky Sports Main Event HD"],
    "Sky Sports Tennis": ["SkySp Tennis HD", "UKSD Sky Sports Tennis"],
    "Sky Sports Box Office HD": ["SkySpBoxOff", "UK: SKY SPORTS BOX OFFICE SD"],

    # --- UK: Sky Cinema ---
    "Sky Cinema Premiere": ["Sky Premiere", "SkyPremiereHD", "HEVC HD SKY CINEMA PREMIER"],
    "Sky Cinema Family": ["Sky Family HD", "HEVC HD SKY CINEMA FAMILY"],
    "Sky Cinema Action": ["Sky Cinema Action HD", "HEVC HD SKY CINEMA ACTION", "Sky Action", "Sky Action HD"],
    "Sky Cinema Greats": ["Sky Greats", "Sky Greats HD", "HEVC HD SKY CINEMA GREATS"],
    "Sky Cinema Thriller": ["Sky Thriller HD", "HEVC HD SKY CINEMA THRILLER"],
    "Sky Cinema Drama": ["Sky Drama HD", "HEVC HD SKY CINEMA DRAMA/CHRISTMAS"],
    "Sky Cinema SciFi/Horror": ["Sky ScFi/HorHD", "Sky Sci-Fi HD", "HEVC HD SKY CINEMA SCIFI/HORROR"],
    "Sky Cinema Animation": ["Sky Cinema Animation HD", "HEVC HD SKY CINEMA ANIMATION", "SkyAnimationHD"],

    # --- UK: Kids ---
    "Baby TV": ["Baby Tv", "Baby TV", "BabyTV"],

    # --- UK: UKTV / Entertainment ---
    # UKTV rebranded all channels under the "U" masterbrand on 16 July 2024
    # (Dave/Drama/Yesterday/W) and 7 November 2024 (Gold/alibi). Reverse
    # aliases let lineups using the pre-rebrand short names match the
    # current EPG entries (U&Dave, U&Drama, etc.).
    "BBC Scotland": ["BBCScotlandHD", "UKHD BBC SCOTLAND", "UKSD BBC SCOTLAND"],
    "Sky Showcase": ["NEW UKHD Sky Showcase", "UKSD: Sky Showcase +1"],
    "U&Dave": ["HEVC HD U&Dave", "U and Dave HD", "UK: Dave", "Dave"],
    "U&GOLD": ["HEVC HD U&GOLD", "U and GOLD HD", "UK: Gold", "Gold"],
    "U&W": ["U and W HD", "U and W+1", "UK: U&W (Watch)"],
    "U&YESTERDAY": ["U and YESTERDAY", "UK FHD YESTERDAY", "Yesterday"],
    "U&alibi": ["U and alibi HD", "alibi+1", "UK: alibi", "Alibi"],
    "U&Drama": ["U and Drama", "U and Drama HD", "U and Drama +1"],
    # Reverse aliases — lineup uses pre-rebrand short name, EPG has U& prefix.
    "Dave": ["U&Dave", "U&Dave HD", "Dave"],
    "Drama": ["U&Drama", "U&Drama HD", "Drama"],
    "Drama +1": ["U&Drama+1", "U&Drama +1", "Drama +1"],
    "Yesterday": ["U&Yesterday", "U&Yesterday HD", "U&YESTERDAY", "Yesterday"],
    "W": ["U&W", "U&W HD", "W"],
    "Gold": ["U&GOLD", "U&GOLD HD", "U&Gold", "Gold"],
    "alibi": ["U&alibi", "U&alibi HD", "Alibi"],
    "Eden": ["U&Eden", "U&Eden HD", "Eden"],
    "Really": ["U&Really", "U&Really HD", "Really"],
    "Really +1": ["U&Really+1", "U&Really +1", "Really +1"],
    "Home": ["U&Home", "U&Home HD", "Home"],
    "Dave ja vu": ["Dave ja vu", "U&Dave ja vu"],

    # --- UK: Factual ---
    "Discovery History": ["Disc.History", "Disc.History+1", "UK: Discovery History"],
    "Discovery Turbo": ["Disc.Turbo", "Disc.Turbo+1", "UK: DISCOVERY TURBO", "DISCOVERY TURBO"],
    "Discovery Science": ["Disc.Science", "Disc.Sci+1", "Discovery Science", "UK: DISCOVERY SCIENCE"],
    "Crime+Investigation": ["Crime+Inv HD", "Crime+Inv+1", "Crime + Investigation", "Crime+Investigation"],

    # --- Canada: French sports ---
    # The CA database stores the bare RDS channel under its full French name
    # "Réseau des Sports (RDS) HD" — fuzzy matching can't reach it from
    # provider strings like "RDS" or "RDS 1" because the (RDS) parenthetical
    # is stripped during normalization, leaving only "Réseau des Sports".
    # Aliases bridge the gap. "RDS 1" is a provider-side name for the single
    # main RDS feed — there is no separate RDS-1 channel; RDS 2 is RDS2.
    "Réseau des Sports (RDS) HD": [
        "RDS", "RDS HD", "RDS 1", "RDS 1 HD",
        "CA RDS", "CA RDS HD", "CA RDS 1", "CA RDS 1 HD",
    ],
    "Réseau des Sports (RDS)": ["RDS", "RDS HD", "RDS 1"],
    "Réseau des Sports Info HD": ["RDS Info", "RDS INFO", "CA RDS INFO"],
    "Réseau des Sports Info": ["RDS Info", "RDS INFO"],
    "RDS2 HD": ["RDS 2", "RDS2", "RDS 2 HD"],
    "RDS2": ["RDS 2", "RDS2", "RDS 2 HD"],

    # --- Canada: TVA Sports ---
    "TVA Sports": ["TVA Sports", "TVA SPORTS", "TVA Sport"],
    "TVA Sports 2": ["TVA Sports 2", "TVA SPORTS 2"],

    # --- Canada: TSN (carrier "RAW" / blackout-feed variants) ---
    # Providers often suffix TSN feeds with "RAW" (raw broadcast feed, no
    # regional overlay) or "BK" (blackout). The HD entries in the DB don't
    # include those tags — these aliases route the variants to the HD feed.
    "TSN 1 HD": ["TSN 1 RAW", "TSN 1 BK", "TSN 1 BK RAW"],
    "TSN 2 HD": ["TSN 2 RAW", "TSN 2 BK", "TSN 2 BK RAW"],
    "TSN 3 HD": ["TSN 3 RAW", "TSN 3 BK", "TSN 3 BK RAW"],
    "TSN 4 HD": ["TSN 4 RAW", "TSN 4 BK", "TSN 4 BK RAW"],
    "TSN 5 HD": ["TSN 5 RAW", "TSN 5 BK", "TSN 5 BK RAW"],
}
