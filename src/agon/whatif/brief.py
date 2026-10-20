"""Analyst brief for a What-If scenario: drafted by a model from the numbers only, every number checked by code.

The model sees one scenario's numbers, already formatted as the strings it may use (`numbers`), and
writes three plain sentences. `check_brief` then reads every number written in the draft, digits
with an optional decimal part and an optional percent sign, and refuses the draft unless each one is
a string from that allowed set. A refused draft is never shown; an accepted one is labelled
`BRIEF_LABEL`.

The allowed set is built here from the committed scenario result with integer arithmetic, so the
check is deterministic and runs again wherever a brief is published (agon.webintel re-checks it).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from agon.whatif.stats import fixed, round_div

BRIEF_LABEL = "AI-drafted from the numbers above; every number checked by code."
BRIEF_SENTENCES = 3
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.:])(?:[0-9]+:[0-9]{2}|-?[0-9]+(?:\.[0-9]+)?%?)")
_SCALES = {"points": (1000, 2), "xg": (1_000_000, 2), "shots": (1000, 1), "box_entries": (1000, 1), "ppda": (100, 2)}


def pct(ppm: int) -> int:
    return round_div(ppm, 10_000)


def numbers(scenario: Mapping[str, Any]) -> dict[str, str]:
    """The numbers a brief may use, by name, each already formatted as it must be written."""
    minute = scenario["params"]["minute"]
    out: dict[str, str] = {
        "replays_per_arm": str(scenario["k"]),
        "scenario_minute": str(minute),
        "scenario_clock": f"{minute}:00",
        "interval_level": "95%",
    }
    for arm in ("baseline", "scenario"):
        for kind, share in scenario["outcomes"][arm].items():
            out[f"{arm}_{kind}_pct"] = f"{pct(share['ppm'])}%"
            out[f"{arm}_{kind}_low_pct"] = f"{pct(share['lo_ppm'])}%"
            out[f"{arm}_{kind}_high_pct"] = f"{pct(share['hi_ppm'])}%"
    for d in scenario["deltas"]:
        if d["delta_mean"] is None:
            continue
        scale, places = _SCALES[d["metric"]]
        name = f"{d['metric']}_{d['side']}"
        for key in ("baseline_mean", "scenario_mean", "delta_mean", "delta_lo", "delta_hi"):
            out[f"{name}_{key}"] = fixed(d[key], scale, places)
    if scenario.get("triggered") is not None:
        out["replays_where_the_change_fired"] = str(scenario["triggered"])
    return out


def allowed(scenario: Mapping[str, Any]) -> set[str]:
    """Every string a brief may write as a number: the formatted values, and each percent without its sign.

    A minus sign may not be dropped: "fell by 0.42" for -0.42 could as well be written "rose by 0.42",
    and only the sign the code computed is checked.
    """
    values = set(numbers(scenario).values())
    values |= {v[:-1] for v in values if v.endswith("%")}
    return values


def written_numbers(text: str) -> list[str]:
    return _NUMBER.findall(text)


def check_brief(text: str, scenario: Mapping[str, Any]) -> list[str]:
    """Reasons to refuse a draft; empty means every number in it is one of the scenario's own numbers."""
    reasons = []
    ok = allowed(scenario)
    for token in written_numbers(text):
        if token not in ok and token.rstrip("%") not in ok:
            reasons.append(f"the number {token} is not one of the scenario's numbers")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if len(sentences) != BRIEF_SENTENCES:
        reasons.append(f"a brief is {BRIEF_SENTENCES} sentences, this draft has {len(sentences)}")
    if any(ch in text for ch in (chr(0x2013), chr(0x2014))):
        reasons.append("a brief may not use long dashes")
    return reasons


def prompt(scenario: Mapping[str, Any], club_names: Mapping[str, str]) -> str:
    """The whole instruction and the numbers; the model sees nothing else."""
    facts = {
        "question": scenario["question"],
        "what_the_engine_changes": scenario["engine_note"],
        "side": club_names[scenario["club_id"]],
        "numbers": numbers(scenario),
        "meaning": {
            "baseline": "replays without the change",
            "scenario": "replays with the change",
            "delta": "with the change minus without, paired replay by replay",
            "low/high or delta_lo/delta_hi": "the 95% interval",
        },
    }
    return (
        "You write for a football club's analysts. The data is synthetic: a simulation engine replaying a "
        "fictional match, not a prediction about real football.\n"
        f"Write exactly {BRIEF_SENTENCES} plain sentences about the scenario below. Use only numbers from "
        "`numbers`, written exactly as given, minus signs included (write 'changed by -0.42', never "
        "'fell by 0.42'). Write no other number, no dates and no long dashes. Say the result is from "
        "paired replays of the engine.\n"
        + json.dumps(facts, sort_keys=True, ensure_ascii=True)
    )
