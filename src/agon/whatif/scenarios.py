"""The fixed scenario set precomputed for the published demo seeds, and the What-If web file per seed.

Every published seed gets the same seven scenarios, told from the side they change:

    red_home_30            a red card for the home side from 30:00
    red_away_30            a red card for the away side from 30:00
    press_up_home_60       the home side presses more often from 60:00 (press-height knob +300 per mille)
    press_down_home_60     the home side presses less often from 60:00 (press-height knob -300 per mille)
    collapse_home_60       the home side's line drops after its next goal from 60:00 (late_collapse)
    heroics_away_60        the away goalkeeper concedes half the usual chance from 60:00
    red_press_down_home_55 a home red card at 55:00 and the press knob -300 from 55:00

`whatif_payload(seed, k)` is a pure function of (seed, k): the committed files under
`agon/whatif/scenario_set/` are its output for the published seeds, regenerated on ubuntu and windows
by the determinism workflow and compared byte for byte (`python -m agon.whatif --set`).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from agon.interpret import INTERPRET_VERSION, model_sha256
from agon.synth.engine import GENERATOR_VERSION
from agon.synth.interventions import Intervention
from agon.synth.schema import canonical_bytes, sha256_hex
from agon.whatif.lab import LABEL, TIMELINE_MARKS, Lab, Scenario
from agon.whatif.replay import Change

WHATIF_SCHEMA = "agon.webintel.whatif/1"
SET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenario_set")
BRIEF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefs")
AGON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The code whose behaviour the numbers in a What-If file depend on, beyond the generator version and the
# frozen interpret model that the file names separately: the lab itself, the engine paths it drives, and
# the analyst-facing definitions it reads every replay with (xG, box entries, PPDA).
CODE_FILES: tuple[str, ...] = (
    "whatif/lab.py",
    "whatif/stats.py",
    "whatif/replay.py",
    "governance/recompute.py",
    "interpret/features.py",
    "interpret/metrics.py",
    "synth/engine.py",
    "synth/interventions.py",
)
REGENERATE = "python -m agon.whatif --set --seeds 1,2,3 --out src/agon/whatif/scenario_set --hash-out src/agon/whatif/scenario_set/hash.txt"
DEFAULT_K = 200
PRESS_SHIFT = 300
COLLAPSE_MAGNITUDE = 2000
HEROICS_MAGNITUDE = 500
SCENARIO_IDS: tuple[str, ...] = (
    "red_home_30", "red_away_30", "press_up_home_60", "press_down_home_60",
    "collapse_home_60", "heroics_away_60", "red_press_down_home_55",
)

METHOD: tuple[str, ...] = (
    "Each scenario replays the published match once up to the scenario minute, then forks K pairs of replays from that exact moment: one without the change (baseline) and one with it.",
    "Replay r of both arms draws the same random numbers, possession by possession (common random numbers), so the two arms differ only by the change and by where it leads the play.",
    "The play before the scenario minute is the published match itself in every replay; prefix.differing_events counts any difference and is 0.",
    "Shared by every replay: the added time of each half and the planned substitution times, both drawn at kick-off, and the random draw behind each substitution (keyed by how many substitutions the side has made). What can still differ between the two replays of a pair: a substitution happens at the first possession after its planned time, so its minute follows that replay's own clock, and the draw chooses among the outfield players on the pitch at that moment, so after a red card it can take off a different player.",
    "The press-height knob sets how often a side presses (the engine's pressure chance); the engine has no defensive line, so no scenario moves a line.",
    "Win, draw and loss shares carry Wilson 95% intervals. Differences are paired means (with the change minus without) with normal-approximation 95% intervals; unpaired_half is the half-width the same replays would give without the pairing.",
    "xG comes from the frozen interpret model, box entries from the Fact Gate's definitions and PPDA from agon.interpret; box entries and PPDA count the play after the scenario minute.",
    "Everything is computed with integers, so the file is the same bytes on every operating system.",
)


def scenario_set(home: str, away: str) -> list[Scenario]:
    """The seven fixed scenarios, worded with the two club names."""
    def red(side: str, period: int, minute: int) -> Intervention:
        return Intervention("red_card", side, period, minute)

    red_note = ("The engine sends off whichever outfield player makes {club}'s next defensive duel after the minute; "
                "it cannot choose a position, and the foul can give away a penalty.")
    return [
        Scenario(
            "red_home_30", "home", 1, 30, Change(interventions=(red("home", 1, 30),)),
            title=f"{home} down to ten men from 30:00",
            question=f"What if {home} had a player sent off just after 30:00?",
            engine_note=red_note.format(club=home),
        ),
        Scenario(
            "red_away_30", "away", 1, 30, Change(interventions=(red("away", 1, 30),)),
            title=f"{away} down to ten men from 30:00",
            question=f"What if {away} had a player sent off just after 30:00?",
            engine_note=red_note.format(club=away),
        ),
        Scenario(
            "press_up_home_60", "home", 2, 60, Change(press_side="home", press_shift=PRESS_SHIFT),
            title=f"{home} press more often from 60:00",
            question=f"What if {home} pressed more often for the last half hour?",
            engine_note=(f"{home}'s press-height knob, which sets how often the engine has a side press, rises by "
                         f"{PRESS_SHIFT} per mille from 60:00 and stays within 50 to 950."),
        ),
        Scenario(
            "press_down_home_60", "home", 2, 60, Change(press_side="home", press_shift=-PRESS_SHIFT),
            title=f"{home} press less often from 60:00",
            question=f"What if {home} pressed less often for the last half hour?",
            engine_note=(f"{home}'s press-height knob falls by {PRESS_SHIFT} per mille from 60:00 and stays within 50 to 950."),
        ),
        Scenario(
            "collapse_home_60", "home", 2, 60, Change(collapse_side="home", collapse_magnitude=COLLAPSE_MAGNITUDE),
            title=f"{home}'s line drops after they score",
            question=f"What if {home}'s line dropped after their next goal from 60:00?",
            engine_note=(f"From the first {home} goal after 60:00, the engine's late collapse applies to {home}: the opponent's "
                         f"chances against them are multiplied by {COLLAPSE_MAGNITUDE // 1000} and the opponent shoots more often. "
                         f"Replays in which {home} do not score are unchanged; `triggered` counts the others."),
        ),
        Scenario(
            "heroics_away_60", "away", 2, 60,
            Change(interventions=(Intervention("goalkeeper_heroics", "away", 2, 60, HEROICS_MAGNITUDE),)),
            title=f"{away}'s goalkeeper stands tall from 60:00",
            question=f"What if {away}'s goalkeeper had an inspired last half hour?",
            engine_note=(f"The engine's goalkeeper heroics at {HEROICS_MAGNITUDE} per mille: {away}'s keeper concedes half the "
                         "usual scoring chance, and the chance taken away becomes a save."),
        ),
        Scenario(
            "red_press_down_home_55", "home", 2, 55,
            Change(interventions=(red("home", 2, 55),), press_side="home", press_shift=-PRESS_SHIFT),
            title=f"{home} lose a player at 55:00 and press less often",
            question=f"What if {home} had a player sent off at 55:00 and then pressed less often?",
            engine_note=red_note.format(club=home) + f" From 55:00 {home}'s press-height knob also falls by {PRESS_SHIFT} per mille.",
        ),
    ]


def code_sha256(root: str = AGON_DIR) -> str:
    """sha256 over CODE_FILES: each path and the sha256 of its bytes with CRLF read as LF, one line each.

    Line endings are normalised so a checkout on windows and one on ubuntu give the same fingerprint.
    """
    lines = []
    for rel in CODE_FILES:
        with open(os.path.join(root, *rel.split("/")), "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")
        lines.append(f"{rel} {hashlib.sha256(data).hexdigest()}\n")
    return hashlib.sha256("".join(lines).encode("ascii")).hexdigest()


def command_for(seed: int, scenario_id: str, k: int) -> str:
    return f"python -m agon.whatif --seed {seed} --scenario {scenario_id} --k {k}"


def published_events_sha256(lab: Lab) -> str:
    return sha256_hex(b"".join(canonical_bytes(e.to_dict()) + b"\n" for e in lab.published.events))


def whatif_payload(seed: int, k: int = DEFAULT_K, scenario_ids: tuple[str, ...] = SCENARIO_IDS) -> dict[str, Any]:
    """The What-If file for one published seed: the scenario set run with K paired replays each."""
    lab = Lab(seed, k)
    clubs = {c.club_id: c for c in lab.league.clubs}
    meta = lab.published.meta
    home, away = clubs[meta["home"]], clubs[meta["away"]]
    chosen = [s for s in scenario_set(home.name, away.name) if s.scenario_id in scenario_ids]
    results = []
    for sc in chosen:
        out = lab.run(sc)
        out["command"] = command_for(seed, sc.scenario_id, k)
        results.append(out)
    return {
        "kind": "whatif",
        "schema": WHATIF_SCHEMA,
        "seed": seed,
        "match_id": meta["match_id"],
        "generator": GENERATOR_VERSION,
        "interpret": INTERPRET_VERSION,
        "model_sha256": model_sha256(),
        "code_sha256": code_sha256(),
        "code_files": list(CODE_FILES),
        "events_sha256": published_events_sha256(lab),
        "label": LABEL,
        "method": list(METHOD),
        "k": k,
        "home": meta["home"],
        "away": meta["away"],
        "clubs": [{"club_id": c.club_id, "name": c.name, "short": c.short} for c in (home, away)],
        "actual_score": dict(meta["score"]),
        "timeline_marks": [m[0] for m in TIMELINE_MARKS],
        "scenarios": results,
    }
