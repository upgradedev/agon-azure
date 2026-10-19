"""A fictional six-club league derived entirely from one integer seed.

Why a syllable table and not a list of names: names built from invented syllables are unlikely
to be anyone's trademark, and every one of them is still screened by `blocklist`. The syllables
are deliberately neutral and non-Greek, because Greek-flavoured invented names kept landing on
real Greek clubs.

Style vectors and attributes are integers (per-mille and 1..99) so the engine never needs floats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agon.synth.blocklist import blocked_reason
from agon.synth.rng import Stream, StreamId

CLUB_COUNT = 6
SQUAD_SHAPE: tuple[tuple[str, int], ...] = (("GK", 2), ("DF", 6), ("MF", 6), ("FW", 4))
STARTING_SHAPE: dict[str, int] = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
MAX_NAME_ATTEMPTS = 64
WORLD_MATCH_KEY = 0  # world draws use match key 0; match draws use match_index + 1

CLUB_STEMS: tuple[str, ...] = (
    "Tarn", "Quel", "Brev", "Lusk", "Corv", "Hask", "Mirl", "Fenn", "Drav", "Pell",
    "Sorr", "Tull", "Wend", "Yarr", "Zenn", "Oster", "Grel", "Kesh", "Morv", "Vask",
)
CLUB_ENDINGS: tuple[str, ...] = (
    "holt", "mark", "vale", "moor", "wick", "stead", "haven", "reach", "gate", "field",
    "combe", "brook",
)
CLUB_SUFFIXES: tuple[str, ...] = (
    "Athletic", "Rovers", "Wanderers", "Albion", "Harriers", "Town", "United", "Borough",
)
LEAGUE_FORMS: tuple[str, ...] = ("League", "Premier Division", "First Division", "Championship")
KIT_COLOURS: tuple[str, ...] = (
    "amber", "teal", "claret", "slate", "emerald", "ochre", "navy", "coral", "ivory", "plum",
    "sky", "charcoal",
)

GIVEN_NAMES: tuple[str, ...] = (
    "Tavin", "Rell", "Oren", "Kael", "Doran", "Jory", "Liro", "Bram", "Esko", "Faro", "Holt",
    "Ivo", "Joss", "Kit", "Lenn", "Maro", "Nils", "Odo", "Pax", "Quin", "Rafe", "Sten", "Teo",
    "Ulf", "Vane", "Wim", "Yori", "Zev", "Arno", "Bex", "Cato", "Deln", "Emry", "Fitz",
)
SURNAME_HEADS: tuple[str, ...] = (
    "Ash", "Bel", "Cor", "Dun", "Ell", "Far", "Gar", "Hal", "Ire", "Jen", "Kel", "Lor", "Mar",
    "Nor", "Orr", "Pen", "Quen", "Rad", "Sel", "Tor", "Ulm", "Var", "Wex", "Yen",
)
SURNAME_TAILS: tuple[str, ...] = (
    "by", "croft", "den", "dale", "ford", "grave", "ley", "low", "more", "rick", "ton", "wood",
    "well", "sey", "thorne", "ridge",
)

# Attribute baselines by position: (passing, finishing, tackling, pace, discipline, keeping, stamina).
_BASELINES: dict[str, tuple[int, int, int, int, int, int, int]] = {
    "GK": (45, 10, 20, 40, 70, 70, 60),
    "DF": (55, 25, 68, 58, 55, 5, 66),
    "MF": (66, 45, 55, 60, 60, 5, 70),
    "FW": (58, 66, 30, 68, 58, 5, 62),
}


@dataclass(frozen=True)
class Style:
    """How a club plays, per-mille. Each knob has a reader in engine.py (see the module docstring there)."""

    press_height: int
    directness: int
    width: int

    def to_dict(self) -> dict[str, int]:
        return {"press_height": self.press_height, "directness": self.directness, "width": self.width}


@dataclass(frozen=True)
class Player:
    player_id: str
    club_id: str
    name: str
    shirt: int
    position: str
    passing: int
    finishing: int
    tackling: int
    pace: int
    discipline: int
    keeping: int
    stamina: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "club_id": self.club_id,
            "name": self.name,
            "shirt": self.shirt,
            "position": self.position,
            "passing": self.passing,
            "finishing": self.finishing,
            "tackling": self.tackling,
            "pace": self.pace,
            "discipline": self.discipline,
            "keeping": self.keeping,
            "stamina": self.stamina,
        }


@dataclass(frozen=True)
class Club:
    club_id: str
    name: str
    short: str
    kit_primary: str
    kit_secondary: str
    style: Style
    players: tuple[Player, ...]

    def starting_eleven(self) -> list[Player]:
        """First GK, four DF, three MF, three FW in squad order: a fixed 4-3-3."""
        chosen: list[Player] = []
        need = dict(STARTING_SHAPE)
        for p in self.players:
            if need.get(p.position, 0) > 0:
                chosen.append(p)
                need[p.position] -= 1
        return chosen

    def bench(self) -> list[Player]:
        starters = {p.player_id for p in self.starting_eleven()}
        return [p for p in self.players if p.player_id not in starters]

    def to_dict(self) -> dict[str, Any]:
        return {
            "club_id": self.club_id,
            "name": self.name,
            "short": self.short,
            "kit_primary": self.kit_primary,
            "kit_secondary": self.kit_secondary,
            "style": self.style.to_dict(),
            "players": [p.to_dict() for p in self.players],
        }


@dataclass(frozen=True)
class League:
    seed: int
    name: str
    clubs: tuple[Club, ...]

    def club(self, club_id: str) -> Club:
        for c in self.clubs:
            if c.club_id == club_id:
                return c
        raise KeyError(club_id)

    def all_names(self) -> list[str]:
        """Every human-readable name in the world, for screening tests."""
        names = [self.name]
        for c in self.clubs:
            names.append(c.name)
            names.extend(p.name for p in c.players)
        return names

    def to_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "name": self.name, "clubs": [c.to_dict() for c in self.clubs]}


class NameExhaustedError(RuntimeError):
    """Raised when no unblocked, unused name was found within MAX_NAME_ATTEMPTS. Never silently fall back."""


def _screened(stream: Stream, build: Any, used: set[str], what: str) -> str:
    for _ in range(MAX_NAME_ATTEMPTS):
        candidate = build(stream)
        if candidate in used:
            continue
        if blocked_reason(candidate) is not None:
            continue
        used.add(candidate)
        return candidate
    raise NameExhaustedError(f"no screened {what} name after {MAX_NAME_ATTEMPTS} attempts")


def _club_core(stream: Stream) -> str:
    stem = str(stream.pick(CLUB_STEMS))
    ending = str(stream.pick(CLUB_ENDINGS))
    if stem[-1] == ending[0]:
        ending = ending[1:]
    return stem + ending


def _club_name(stream: Stream) -> str:
    return f"{_club_core(stream)} {stream.pick(CLUB_SUFFIXES)}"


def _league_name(stream: Stream) -> str:
    return f"{_club_core(stream)} {stream.pick(LEAGUE_FORMS)}"


def _player_name(stream: Stream) -> str:
    head = str(stream.pick(SURNAME_HEADS))
    tail = str(stream.pick(SURNAME_TAILS))
    if head[-1] == tail[0]:
        tail = tail[1:]
    return f"{stream.pick(GIVEN_NAMES)} {head}{tail}"


def _attribute(stream: Stream, base: int) -> int:
    """Base plus a triangular-ish integer spread of about +-18, clamped to 1..99."""
    spread = stream.between(-9, 9) + stream.between(-9, 9)
    return min(99, max(1, base + spread))


def _short_code(name: str, taken: set[str]) -> str:
    letters = [ch for ch in name.upper() if ch.isalpha()]
    for i in range(1, len(letters)):
        for j in range(i + 1, len(letters)):
            code = letters[0] + letters[i] + letters[j]
            if code not in taken:
                taken.add(code)
                return code
    raise NameExhaustedError(f"no unique short code for {name}")


def build_world(seed: int) -> League:
    """Build the whole league for one seed. Pure function of the seed."""
    if seed < 0:
        raise ValueError("seed must be non-negative")
    used: set[str] = set()
    name_stream = Stream(seed, WORLD_MATCH_KEY, StreamId.WORLD_CLUBS)
    league_name = _screened(name_stream, _league_name, used, "league")

    kit_stream = Stream(seed, WORLD_MATCH_KEY, StreamId.WORLD_CLUBS, possession=1)
    primaries = kit_stream.shuffled(KIT_COLOURS)[:CLUB_COUNT]

    clubs: list[Club] = []
    cores: set[str] = set()
    shorts: set[str] = set()
    for index in range(CLUB_COUNT):
        club_id = f"C{index + 1}"
        for _ in range(MAX_NAME_ATTEMPTS):
            name = _screened(name_stream, _club_name, used, "club")
            core = name.split(" ")[0]
            if core not in cores:
                cores.add(core)
                break
        else:
            raise NameExhaustedError(f"no club name with an unused core after {MAX_NAME_ATTEMPTS} attempts")
        secondary_choices = [c for c in KIT_COLOURS if c != primaries[index]]
        kit_secondary = str(kit_stream.pick(secondary_choices))

        style_stream = Stream(seed, WORLD_MATCH_KEY, StreamId.WORLD_STYLE, possession=index)
        style = Style(
            press_height=style_stream.between(200, 800),
            directness=style_stream.between(200, 800),
            width=style_stream.between(200, 800),
        )

        player_stream = Stream(seed, WORLD_MATCH_KEY, StreamId.WORLD_PLAYERS, possession=index)
        players: list[Player] = []
        shirt = 1
        for position, count in SQUAD_SHAPE:
            base = _BASELINES[position]
            for _ in range(count):
                pname = _screened(player_stream, _player_name, used, "player")
                players.append(
                    Player(
                        player_id=f"{club_id}P{shirt:02d}",
                        club_id=club_id,
                        name=pname,
                        shirt=shirt,
                        position=position,
                        passing=_attribute(player_stream, base[0]),
                        finishing=_attribute(player_stream, base[1]),
                        tackling=_attribute(player_stream, base[2]),
                        pace=_attribute(player_stream, base[3]),
                        discipline=_attribute(player_stream, base[4]),
                        keeping=_attribute(player_stream, base[5]),
                        stamina=_attribute(player_stream, base[6]),
                    )
                )
                shirt += 1
        clubs.append(
            Club(
                club_id=club_id,
                name=name,
                short=_short_code(name, shorts),
                kit_primary=str(primaries[index]),
                kit_secondary=kit_secondary,
                style=style,
                players=tuple(players),
            )
        )
    return League(seed=seed, name=league_name, clubs=tuple(clubs))


def fixture_list(club_count: int = CLUB_COUNT) -> list[tuple[int, str, str]]:
    """Double round robin by the circle method: (matchday, home_id, away_id), 30 matches for 6 clubs.

    The calendar is fictional (matchdays, not dates) so fixtures never expire.
    """
    ids = [f"C{i + 1}" for i in range(club_count)]
    rounds: list[list[tuple[str, str]]] = []
    rotating = ids[1:]
    for r in range(club_count - 1):
        order = [ids[0]] + rotating
        pairs = []
        for k in range(club_count // 2):
            a, b = order[k], order[club_count - 1 - k]
            pairs.append((a, b) if (r + k) % 2 == 0 else (b, a))
        rounds.append(pairs)
        rotating = rotating[-1:] + rotating[:-1]
    out: list[tuple[int, str, str]] = []
    for md, pairs in enumerate(rounds, start=1):
        for home, away in pairs:
            out.append((md, home, away))
    second_leg_start = len(rounds) + 1
    for md, pairs in enumerate(rounds, start=second_leg_start):
        for home, away in pairs:
            out.append((md, away, home))
    return out
