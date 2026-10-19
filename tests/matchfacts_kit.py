"""Shared helpers for the matchfacts tests: one valid call per tool, and the referee import blocker."""

from __future__ import annotations

import os
import re
from typing import Any

from agon.mcp.source import InMemorySource

SEED = 1
EXPECTED_TOOLS = frozenset({
    "events.window",
    "events.by_id",
    "stats.recompute",
    "momentum.series",
    "pressure.regimes",
    "roster.lookup",
    "rights.check",
    "wp.swing",
    "charter.get",
})
# Secondary guard; the primary one is exact equality with EXPECTED_TOOLS. A name is split into
# words on dots and underscores and no word may be a write verb.
WRITE_VERBS = frozenset({
    "write", "set", "update", "delete", "remove", "publish", "approve", "create", "insert", "upsert",
    "put", "post", "patch", "replace", "seal", "send", "enqueue", "commit", "save", "store", "add",
    "append", "upload", "sign",
})


def write_like(name: str) -> bool:
    return any(word.lower() in WRITE_VERBS for word in re.split(r"[._]", name))


# Keys of the engine's intervention ledger (ground truth). None may appear in any tool output.
LEDGER_KEYS = ("anchor_event_id", "planned_minute", "goals_for_after", "press_by_minute", "opp_onball_by_minute")

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TESTS = os.path.dirname(os.path.abspath(__file__))

BLOCKER = '''
import sys
class _RefuseReferee:
    def find_spec(self, name, path=None, target=None):
        if name == "agon." + "referee" or name.startswith("agon." + "referee."):
            raise ImportError("referee import refused: " + name)
        return None
sys.meta_path.insert(0, _RefuseReferee())
'''


def subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    parts = [SRC, TESTS] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def source() -> InMemorySource:
    return InMemorySource.from_seeds([SEED])


def calls(src: InMemorySource) -> list[tuple[str, dict[str, Any]]]:
    """One valid call for every tool, against the first match in the source."""
    match_id = src.match_ids()[0]
    rec = src.record(match_id)
    goal = next(e for e in rec.events if e["type"] == "shot" and e["outcome"] == "goal")
    first = rec.events[0]
    home = rec.meta["home"]
    player = goal["player"]
    return [
        ("events.window", {"match_id": match_id, "t0": 0, "t1": 5}),
        ("events.by_id", {"match_id": match_id, "event_ids": [first["event_id"], goal["event_id"]]}),
        ("stats.recompute", {"match_id": match_id, "metric": "goals", "scope": home}),
        ("momentum.series", {"match_id": match_id}),
        ("pressure.regimes", {"match_id": match_id}),
        ("roster.lookup", {"match_id": match_id, "query": player}),
        ("rights.check", {"text": "The home side pressed high after the break.", "field": "prose"}),
        ("wp.swing", {"match_id": match_id, "event_id": goal["event_id"]}),
        ("charter.get", {"match_id": match_id}),
    ]
