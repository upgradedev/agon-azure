"""Real names the Rights Guard blocks, beyond the club and field names in `agon.synth.blocklist`.

Why this file holds real names: it is a deny list. Like `agon/synth/blocklist.py`, it is the only
place in the repository where these names appear, and any forbidden-string scan of a deployed
bundle must exclude both files by path. Club names and the field names (rival hackathon entries)
are NOT repeated here: they are imported from `agon.synth.blocklist` so there is one source.

Latin-script entries are ASCII with diacritics dropped; the guard strips diacritics from the text
it screens the same way. Greek-script entries are stems (lowercase, no accents, final sigma as
sigma) matched as the start of a word, because Greek club names decline: one stem covers the
nominative, genitive and accusative forms.

This is a screen, not a census. A name passing it is not proof that the name is unused.
"""

from __future__ import annotations

LEAGUES: tuple[str, ...] = (
    "Premier League", "English Premier League", "EPL", "EFL", "La Liga", "LaLiga", "Serie A",
    "Bundesliga", "Ligue 1", "Eredivisie", "Primeira Liga", "Scottish Premiership",
    "Super League Greece", "Stoiximan Super League", "Super League", "Champions League",
    "Europa League", "Conference League", "UEFA", "FIFA", "Copa del Rey", "FA Cup",
    "Coppa Italia", "DFB Pokal", "Coupe de France",
)

BROADCASTERS: tuple[str, ...] = (
    "Sky Sports", "Sky Sport", "BT Sport", "TNT Sports", "BBC", "ITV", "DAZN", "ESPN",
    "beIN Sports", "beIN", "Canal Plus", "NBC Sports", "Peacock", "Prime Video", "Movistar",
    "Cosmote TV", "Nova Sports", "ERT", "Viaplay", "Paramount Plus", "Fox Sports", "Telemundo",
    "RMC Sport", "Ligue1 Plus", "Match of the Day",
)

# Full names of prominent real players, as remembered at authoring time.
PLAYERS_FULL: tuple[str, ...] = (
    "Erling Haaland", "Kylian Mbappe", "Mohamed Salah", "Harry Kane", "Jude Bellingham",
    "Vinicius Junior", "Lamine Yamal", "Kevin De Bruyne", "Bukayo Saka", "Phil Foden",
    "Lionel Messi", "Cristiano Ronaldo", "Robert Lewandowski", "Virgil van Dijk",
    "Martin Odegaard", "Declan Rice", "Bruno Fernandes", "Son Heung-min", "Cole Palmer",
    "Florian Wirtz", "Jamal Musiala", "Alexander Isak", "Ousmane Dembele", "Lautaro Martinez",
    "Victor Osimhen", "Khvicha Kvaratskhelia", "Rafael Leao", "Antoine Griezmann", "Jan Oblak",
    "Thibaut Courtois", "Alisson Becker", "Marc-Andre ter Stegen", "Joshua Kimmich",
    "Thomas Muller", "Manuel Neuer", "Federico Valverde", "Luka Modric", "Toni Kroos",
    "Karim Benzema", "Achraf Hakimi", "Trent Alexander-Arnold", "Marcus Rashford",
    "Jack Grealish", "Bernardo Silva", "Ruben Dias", "William Saliba", "Gabriel Martinelli",
    "Alexis Mac Allister", "Dominik Szoboszlai", "Ollie Watkins", "Kostas Tsimikas",
    "Vangelis Pavlidis", "Anastasios Bakasetas", "Konstantinos Mavropanos", "Christos Tzolis",
    "Giorgos Masouras", "Odysseas Vlachodimos",
)

# Surnames distinctive enough to block on their own. Common words (Kane, Rice, Palmer, Silva,
# Dias, Rodri as a word stem, and so on) are deliberately left to the full-name list above.
PLAYER_SURNAMES: tuple[str, ...] = (
    "Haaland", "Mbappe", "Salah", "Bellingham", "Vinicius", "Yamal", "Saka", "Foden", "Messi",
    "Ronaldo", "Lewandowski", "Odegaard", "Musiala", "Wirtz", "Dembele", "Osimhen",
    "Kvaratskhelia", "Griezmann", "Oblak", "Courtois", "Kimmich", "Neuer", "Modric", "Kroos",
    "Neymar", "Benzema", "Hakimi", "Rashford", "Grealish", "Saliba", "Martinelli", "Szoboszlai",
    "Pedri", "Tsimikas", "Pavlidis", "Bakasetas", "Mavropanos", "Tzolis", "Vlachodimos",
    "Masouras",
)

# Greek-script stems of real Greek clubs, the league and Greek spellings of large foreign clubs.
GREEK_STEMS: tuple[str, ...] = (
    "ολυμπιακ",  # Olympiakos
    "παναθηναικ",  # Panathinaikos
    "παοκ",  # PAOK
    "αεκ",  # AEK
    "ατρομητ",  # Atromitos
    "λεβαδειακ",  # Levadiakos
    "πανσερραικ",  # Panserraikos
    "παναιτωλικ",  # Panetolikos
    "οφη",  # OFI
    "σουπερλιγκ",  # Superleague (one word)
    "λιβερπουλ",  # Liverpool
    "τσελσι",  # Chelsea
    "αρσεναλ",  # Arsenal
    "μπαρτσελον",  # Barcelona
    "γιουβεντουσ",  # Juventus
)

# Single-token blocked names that are also everyday words in English, Greek, Spanish or French
# ("Como resultado", "Nice work", "Hearts sank"). In prose every single-token name is matched only
# when capitalised; these few are also skipped when they open a sentence. In name fields they are
# always matched. Cities that are club names are NOT here: a real city has no place in the
# fictional world either, so it blocks wherever it is capitalised.
PROSE_AMBIGUOUS: frozenset[str] = frozenset({
    "como", "nice", "lens", "angers", "levante", "hearts", "rangers", "celtic",
})
