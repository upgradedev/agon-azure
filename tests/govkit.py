"""Shared builders for the governance tests: a seeded match, sealed bundles, nonces and payloads.

Importable from every test folder because pytest puts tests/ on sys.path for tests/conftest.py.
Everything here is synthetic. Signing keys are derived at run time, never written as literals.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from agon.governance import verifier
from agon.governance.claims import Claim, Sentence
from agon.governance.recompute import MatchFacts, Roster, display_minute, events_of_kind, score_after, stat_events
from agon.governance.seal import Artifact, SealedBundle, event_log_sha256, seal, seed_sha256
from agon.governance.store import InMemoryStore
from agon.synth.engine import GENERATOR_VERSION, simulate_match
from agon.synth.world import build_world

T0 = datetime(2026, 10, 19, 15, 0, 0, tzinfo=timezone.utc)
MAIN_SEED = 9  # 2-1, goals at 55, 70 and 79 (display minutes), several cards
OFFICER = "officer@media.example"
DEPUTY = "deputy@media.example"
INTERN = "intern@media.example"  # holds no role
VISITOR = "console-visitor:session-1"
PASS_VERDICTS = {"factgate": "pass", "factgate_lang": "pass", "rights": "pass"}


@lru_cache(maxsize=None)
def match(seed: int = MAIN_SEED) -> tuple[Any, Any, MatchFacts]:
    league = build_world(seed)
    result = simulate_match(league, 0)
    facts = MatchFacts(
        events=tuple(result.events),
        home_id=result.meta["home"],
        away_id=result.meta["away"],
        roster=Roster.from_league(league),
    )
    return league, result, facts


def first_goal(facts: MatchFacts):
    return events_of_kind(facts.events, "goal")[0]


def sot_claim(facts: MatchFacts, club_id: str, value: int | None = None, ids: tuple[str, ...] | None = None) -> Claim:
    counted = tuple(e.event_id for e in stat_events(facts, "shots_on_target", club_id))
    return Claim(
        type="stat",
        value=len(counted) if value is None else value,
        event_ids=counted if ids is None else ids,
        stat="shots_on_target",
        subject=club_id,
    )


def home_name(facts: MatchFacts) -> str:
    return facts.roster.clubs[facts.home_id]


def sot_sentences(facts: MatchFacts, written: int | None = None, claimed: int | None = None) -> dict[str, Sentence]:
    """One shots-on-target sentence per language. `written` plants a wrong digit in the prose,
    `claimed` plants a wrong value in the claim; None means the true count."""
    true = len(stat_events(facts, "shots_on_target", facts.home_id))
    n = true if written is None else written
    name = home_name(facts)
    claims = (sot_claim(facts, facts.home_id, value=claimed), Claim(type="name", value=name, subject=facts.home_id))
    texts = {
        "en": f"{name} had {n} shots on target.",
        "el": f"Η {name} είχε {n} τελικές στην εστία.",
        "es": f"El {name} tuvo {n} tiros a puerta.",
        "fr": f"{name} a cadré {n} tirs.",
    }
    return {lang: Sentence(sentence_id=f"s1-{lang}", text=t, language=lang, claims=claims) for lang, t in texts.items()}


def goal_sentence(facts: MatchFacts, language: str = "en") -> Sentence:
    goal = first_goal(facts)
    scorer = facts.roster.players[goal.player]
    minute = display_minute(goal.period, goal.minute)
    score = score_after(facts, goal.seq)
    claims = (
        Claim(type="name", value=scorer, event_ids=(goal.event_id,), subject=goal.player),
        Claim(type="minute", value=minute, event_ids=(goal.event_id,)),
        Claim(type="event_ref", value="goal", event_ids=(goal.event_id,)),
        Claim(type="score", value=score, event_ids=(goal.event_id,)),
    )
    texts = {
        "en": f"{scorer} scored in the {minute}th minute to make it {score['home']}-{score['away']}.",
        "el": f"Ο {scorer} σκόραρε στο {minute}ο λεπτό για το {score['home']}-{score['away']}.",
    }
    return Sentence(sentence_id=f"g1-{language}", text=texts[language], language=language, claims=claims)


# ----------------------------------------------------------------- sealed bundles


def report_bytes(facts: MatchFacts, tag: str = "") -> dict[str, bytes]:
    sents = sot_sentences(facts)
    return {
        "report-en.txt": (sents["en"].text + tag).encode("utf-8"),
        "report-el.txt": (sents["el"].text + tag).encode("utf-8"),
        "audio-el.mp3": b"ID3-synthetic-audio" + tag.encode("utf-8"),
    }


def make_bundle(
    *,
    kind: str = "fulltime",
    verdicts: dict[str, str] | None = None,
    parent: str | None = None,
    tag: str = "",
    seed: int = MAIN_SEED,
) -> tuple[SealedBundle, dict[str, bytes]]:
    league, result, facts = match(seed)
    files = report_bytes(facts, tag)
    media = {"report-en.txt": "text/plain", "report-el.txt": "text/plain", "audio-el.mp3": "audio/mpeg"}
    langs = {"report-en.txt": "en", "report-el.txt": "el", "audio-el.mp3": "el"}
    roles = {"report-en.txt": "edition", "report-el.txt": "edition", "audio-el.mp3": "audio"}
    sealed = seal(
        kind=kind,
        match_id=result.meta["match_id"],
        seed_hash=seed_sha256(seed, 0, GENERATOR_VERSION),
        event_log_hash=event_log_sha256(result.events),
        charter_hash=hashlib.sha256(b"synthetic charter v1").hexdigest(),
        artifacts=[Artifact(n, d, media[n], roles[n], langs[n]) for n, d in files.items()],
        gate_verdicts=verdicts or PASS_VERDICTS,
        author_tags={"s1-en": "model", "s1-el": "model"},
        attested=["report-en.txt"],
        gate_cleared=["report-el.txt", "audio-el.mp3"],
        parent_bundle_sha256=parent,
    )
    return sealed, files


def hand_built(mutate, **bundle_kwargs: Any) -> tuple[bytes, str, dict[str, bytes]]:
    """A canonical manifest built by hand, not by seal(), so it can hold what seal() refuses.

    `mutate(manifest, files)` edits a deep copy in place and returns the files to publish.
    Returns (manifest bytes, their sha256, files).
    """
    import copy

    from agon.governance.seal import canonical_utf8, sha256_hex

    sealed, files = make_bundle(**bundle_kwargs)
    manifest = copy.deepcopy(sealed.manifest)
    files = mutate(manifest, dict(files))
    data = canonical_utf8(manifest)
    return data, sha256_hex(data), files


def drop_gate(gate: str):
    def mutate(manifest: dict[str, Any], files: dict[str, bytes]) -> dict[str, bytes]:
        del manifest["gate_verdicts"][gate]
        return files

    mutate.__name__ = f"drop_{gate}"
    return mutate


def plant_nonce(store: InMemoryStore, bundle_sha256: str, *, key: str = "planted", target: str = "feed", role: str = "approver") -> str:
    """A nonce document written straight into the store, skipping issue_nonce() and its checks."""
    store.create(verifier.NONCES, key, {
        "bundle_sha256": bundle_sha256, "purpose": "fulltime", "target": target, "role": role,
        "issued_at": 0, "expires_at": 4_102_444_800, "confirm_rows": [], "status": "pending",
    })
    return key


@dataclass
class Env:
    store: InMemoryStore
    bundle: SealedBundle
    files: dict[str, bytes]
    nonce: str


def grant_default_roles(store: InMemoryStore) -> None:
    verifier.grant_role(store, OFFICER, [verifier.APPROVER_ROLE])
    verifier.grant_role(store, DEPUTY, [verifier.APPROVER_ROLE])
    verifier.grant_role(store, VISITOR, [verifier.SANDBOX_ROLE])


def make_env(
    *,
    target: str = "feed",
    confirm_rows: tuple[str, ...] = (),
    ttl: int = 1800,
    store: InMemoryStore | None = None,
    **bundle_kwargs: Any,
) -> Env:
    store = store if store is not None else InMemoryStore()
    if store.read(verifier.APPROVERS, OFFICER) is None:
        grant_default_roles(store)
    bundle, files = make_bundle(**bundle_kwargs)
    verifier.register_sealed_bundle(store, bundle.manifest_bytes)
    nonce = verifier.issue_nonce(store, bundle.bundle_sha256, target=target, now=T0, ttl_seconds=ttl, confirm_rows=confirm_rows)
    return Env(store=store, bundle=bundle, files=files, nonce=nonce)


def later(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


# ----------------------------------------------------------------- channel payloads


def teams_payload(env: Env, decision: str = "approve", upn: str = OFFICER, confirmed: tuple[str, ...] = ()) -> dict[str, Any]:
    data: dict[str, Any] = {"decision": decision, "bundle_sha256": env.bundle.bundle_sha256, "nonce": env.nonce}
    for row in confirmed:
        data[f"confirm:{row}"] = "true"
    return {"responder": {"userPrincipalName": upn, "objectId": "00000000-0000-0000-0000-000000000001"}, "data": data}


def outlook_payload(env: Env, option: str = "Approve", email: str = OFFICER, confirm_rows: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "bundle_sha256": env.bundle.bundle_sha256,
        "nonce": env.nonce,
        "confirm_rows": list(confirm_rows),
        "response": {"SelectedOption": option, "UserEmailAddress": email},
    }


def console_body(env: Env, decision: str = "approve", confirmed: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"bundle_sha256": env.bundle.bundle_sha256, "nonce": env.nonce, "decision": decision, "confirmed_rows": list(confirmed)}


class TestSecrets:
    """Secret provider for tests. The key is derived at run time, so no key literal is committed."""

    __test__ = False

    def __init__(self) -> None:
        self.keys = {"k1": hashlib.sha256(b"agon-test-signing-key-1").digest()}

    def signing_key(self, key_id: str) -> bytes:
        return self.keys[key_id]
