"""Deterministic fakes for every port, for tests and the offline CI run. Not a model.

Every answer is a pure function of the payload, so a run with fakes is reproducible byte for
byte and its bundle hash can be compared across processes and against the reference run. A bundle
sealed with these fakes records ``deployment_ids["ports"] = "fake"``; see `FAKE_DEPLOYMENT_IDS`.

The fake Writer reads only the brief, exactly as the real one must, and the knobs below plant the
failures section 4 describes: an outage, a wrong number that a retry fixes (or does not), a
bait sentence for the Rights Guard, and a critic that tries to do more than add flags.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from agon.agents.ports import ModelUnavailable, Prompt, SpeechResult, SpeechUnavailable, TranslatorUnavailable

FAKE_DEPLOYMENT_IDS: dict[str, str] = {
    "ports": "fake",
    "model": "agon-fake-model/1",
    "translator": "agon-fake-translator/1",
    "speech": "agon-fake-speech/1",
}
THEME_CYCLE: tuple[str, ...] = ("game_rhythm", "control_vs_chaos", "tactical_pressure")


def _claim(type_: str, value: Any, event_ids: Sequence[str] = (), stat: str | None = None, subject: str | None = None) -> dict[str, Any]:
    return {"type": type_, "value": value, "event_ids": list(event_ids), "stat": stat, "subject": subject}


@dataclass
class PlantedStat:
    """A stat sentence written with a wrong value on the first attempt.

    `template` holds one ``{n}`` placeholder (the agon.synth.fixtures sentence with the number cut
    out). On a retry whose feedback names this sentence, the fake reads the true value and its
    event ids from the brief, as a real Writer would, unless `fix_on_retry` is False.
    """

    sentence_id: str
    stat: str
    subject: str
    template: str
    planted_value: int
    planted_event_ids: list[str]
    fix_on_retry: bool = True


@dataclass
class FakeModel:
    fail_roles: set[str] = field(default_factory=set)
    invalid_json_roles: set[str] = field(default_factory=set)
    plant: PlantedStat | None = None
    extra_sentences: list[dict[str, Any]] = field(default_factory=list)
    desk_flags: list[dict[str, str]] = field(default_factory=list)
    analyst_invented_answers: int = 0
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def complete(self, role: str, prompt: Prompt, payload: dict[str, Any], schema: dict[str, Any]) -> str:
        self.calls.append((role, json.loads(json.dumps(payload))))
        if role in self.fail_roles:
            raise ModelUnavailable(f"fake outage for {role}")
        if role in self.invalid_json_roles:
            return "this is not JSON"
        answer = getattr(self, f"_{role}")(payload)
        return json.dumps(answer, ensure_ascii=False, sort_keys=True)

    def calls_for(self, role: str) -> list[dict[str, Any]]:
        return [payload for r, payload in self.calls if r == role]

    # --------------------------------------------------------- roles

    def _analyst(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.analyst_invented_answers > 0:
            self.analyst_invented_answers -= 1
            return {"turning_points": [{"event_id": "no-such-event", "why": "Invented.", "theme": "game_rhythm"}]}
        picks = payload.get("candidates", [])[:4]
        return {
            "turning_points": [
                {"event_id": c["event_id"], "why": f"The {c['kind'].replace('_', ' ')} changed the match.", "theme": THEME_CYCLE[n % 3]}
                for n, c in enumerate(picks)
            ]
        }

    def _writer(self, brief: dict[str, Any]) -> dict[str, Any]:
        feedback = {f["sentence_id"]: f for f in brief.get("feedback", [])}
        full = self._write_all(brief)
        if not feedback:
            return {"sentences": full}
        rows = []
        by_id = {s["sentence_id"]: s for s in full}
        for sid in sorted(feedback):
            if self.plant is not None and sid == self.plant.sentence_id and self.plant.fix_on_retry:
                rows.append(self._planted(brief, true_value=True))
            elif sid in by_id:
                rows.append(by_id[sid])
        return {"sentences": rows}

    def _write_all(self, brief: dict[str, Any]) -> list[dict[str, Any]]:
        match = brief["match"]
        home, away, final = match["home"], match["away"], match["final_score"]
        out = [
            {
                "sentence_id": "s1",
                "text": f"{home['name']} and {away['name']} finished {final['home']}-{final['away']}.",
                "claims": [
                    _claim("score", final),
                    _claim("name", home["name"], subject=home["id"]),
                    _claim("name", away["name"], subject=away["id"]),
                ],
            }
        ]
        for goal in brief["goals"][:4]:
            if not goal["scorer"]:
                continue
            ids = [goal["event_id"]]
            after = goal["score_after"]
            out.append(
                {
                    "sentence_id": f"s{len(out) + 1}",
                    "text": f"{goal['scorer']} scored in minute {goal['minute']} to make it {after['home']}-{after['away']}.",
                    "claims": [
                        _claim("name", goal["scorer"], ids, subject=goal["scorer_id"]),
                        _claim("minute", goal["minute"], ids),
                        _claim("event_ref", "goal", ids),
                        _claim("score", after, ids),
                    ],
                }
            )
        cards = [c for c in brief["cards"] if c["player"]]
        if cards:
            card = cards[0]
            ids = [card["event_id"]]
            verb = "was sent off" if card["kind"] == "red_card" else "was booked"
            out.append(
                {
                    "sentence_id": f"s{len(out) + 1}",
                    "text": f"{card['player']} {verb} in minute {card['minute']}.",
                    "claims": [
                        _claim("name", card["player"], ids, subject=card["player_id"]),
                        _claim("minute", card["minute"], ids),
                        _claim("event_ref", card["kind"], ids),
                    ],
                }
            )
        for side in (home, away):
            sot = brief["stats"][side["id"]]["shots_on_target"]
            out.append(
                {
                    "sentence_id": f"s{len(out) + 1}",
                    "text": f"{side['name']} had {sot['value']} shots on target.",
                    "claims": [_claim("stat", sot["value"], sot["event_ids"], "shots_on_target", side["id"])],
                }
            )
        if self.plant is not None:
            out.append(self._planted(brief, true_value=False))
        out.extend(json.loads(json.dumps(self.extra_sentences)))
        return out

    def _planted(self, brief: dict[str, Any], true_value: bool) -> dict[str, Any]:
        p = self.plant
        assert p is not None
        if true_value:
            row = brief["stats"][p.subject][p.stat]
            value, ids = row["value"], row["event_ids"]
        else:
            value, ids = p.planted_value, p.planted_event_ids
        return {
            "sentence_id": p.sentence_id,
            "text": p.template.format(n=value),
            "claims": [_claim("stat", value, ids, p.stat, p.subject)],
        }

    def _triage(self, payload: dict[str, Any]) -> dict[str, Any]:
        items = []
        for s in payload["sentences"]:
            kinds = {c["value"] for c in s["claims"] if c["type"] == "event_ref"}
            numeric = any(c["type"] in ("score", "stat", "minute") for c in s["claims"])
            kind = "disciplinary_referee" if kinds & {"red_card", "yellow_card"} else "match_stat" if numeric else "neutral_colour"
            ids = sorted({e for c in s["claims"] for e in c["event_ids"]})
            items.append({"sentence_id": s["sentence_id"], "type": kind, "grounding_ids": ids})
        return {"items": items}

    def _desk_editor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"flags": [dict(f) for f in self.desk_flags]}

    def _localiser_editor(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"sentences": [{"sentence_id": s["sentence_id"], "text": s["machine_translation"]} for s in payload["sentences"]]}


@dataclass
class FakeTranslator:
    """Prefixes each line with the language code; names and digits pass through untouched.

    `corrupt` maps a language to the index of a line whose first digit is changed, to prove the
    per-language Fact Gate catches a translation that alters a number.
    """

    fail_languages: set[str] = field(default_factory=set)
    corrupt: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, list[str], list[str]]] = field(default_factory=list)

    async def translate(self, texts: Sequence[str], target: str, protected: Sequence[str]) -> list[str]:
        self.calls.append((target, list(texts), list(protected)))
        if target in self.fail_languages:
            raise TranslatorUnavailable(f"fake translator outage for {target}")
        out = [f"({target.upper()}) {t}" for t in texts]
        index = self.corrupt.get(target)
        if index is not None and index < len(out):
            line = out[index]
            for pos, ch in enumerate(line):
                if ch.isdigit():
                    out[index] = line[:pos] + str((int(ch) + 1) % 10) + line[pos + 1 :]
                    break
        return out


@dataclass
class FakeSpeech:
    """Deterministic bytes standing in for audio; word timings are a fixed 350 ms grid, not measured."""

    fail_languages: set[str] = field(default_factory=set)

    async def synthesize(self, text: str, language: str) -> SpeechResult:
        if language in self.fail_languages:
            raise SpeechUnavailable(f"fake speech outage for {language}")
        digest = hashlib.sha256(f"{language}|{text}".encode("utf-8")).hexdigest()
        audio = b"AGON-FAKE-AUDIO/1\n" + digest.encode("ascii") + b"\n"
        timings = [{"text": w, "offset_ms": 350 * i, "duration_ms": 300} for i, w in enumerate(text.split())]
        return SpeechResult(audio=audio, media_type="audio/mpeg", voice=f"fake-voice-{language}", word_timings=timings)
