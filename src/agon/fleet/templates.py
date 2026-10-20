"""Deterministic language: the Writer's brief, the template recap, and claim-preserving template lines.

Three uses, all code, no model:

* `writer_brief` is the data the Writer (and the template recap) works from: every value comes
  with the event ids it was counted from, through `agon.governance.recompute`, so the number a
  model is shown and the number the Fact Gate checks come from the same code.
* `template_recap` is what the card gets when the Writer is unavailable: a short recap built
  from the brief, every line tagged ``template`` and flagged. Never silent (spec section 4).
* `render_claims` writes exactly a sentence's claims in a given language. It is the fallback for
  a sentence the Fact Gate, the easy-read rules or the translation check refused, and because it
  carries the same claims the per-language Fact Gate can still check it.
"""

from __future__ import annotations

from typing import Any, Iterable

from agon.fleet.contracts import Draft
from agon.fleet.sources import MatchRecord
from agon.governance import recompute
from agon.governance.claims import Claim, Sentence
from agon.governance.factgate import check_sentence
from agon.governance.recompute import MatchFacts, display_minute, events_of_kind, score_after, stat_events

BRIEF_STATS: tuple[str, ...] = ("shots", "shots_on_target", "corners", "fouls", "yellow_cards", "red_cards", "saves")
MAX_TEMPLATE_GOAL_LINES = 6

STAT_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "goals": "goals", "shots": "shots", "shots_on_target": "shots on target", "penalties": "penalties",
        "yellow_cards": "yellow cards", "red_cards": "red cards", "substitutions": "substitutions",
        "fouls": "fouls", "corners": "corners", "tackles_won": "tackles won", "passes": "passes",
        "passes_completed": "completed passes", "saves": "saves", "pass_accuracy_pct": "pass accuracy in per cent",
        "progressive_passes": "progressive passes", "progressive_carries": "progressive carries",
        "passes_into_box": "passes into the box", "crosses_into_box": "crosses into the box",
        "carries_into_box": "carries into the box", "box_entries": "box entries",
        "deep_completions": "completed passes near goal", "key_passes": "key passes",
        "field_tilt_pct": "share of final-third passes in per cent",
        "counter_press_recovery_pct": "quick ball recoveries after losing it in per cent",
    },
    "el": {
        "goals": "γκολ", "shots": "τελικές", "shots_on_target": "τελικές στην εστία", "penalties": "πέναλτι",
        "yellow_cards": "κίτρινες κάρτες", "red_cards": "κόκκινες κάρτες", "substitutions": "αλλαγές",
        "fouls": "φάουλ", "corners": "κόρνερ", "tackles_won": "κερδισμένα τάκλιν", "passes": "πάσες",
        "passes_completed": "επιτυχημένες πάσες", "saves": "αποκρούσεις",
        "pass_accuracy_pct": "ποσοστό επιτυχίας πασών",
        "progressive_passes": "προωθητικές πάσες", "progressive_carries": "προωθητικές κινήσεις με την μπάλα",
        "passes_into_box": "πάσες μέσα στην περιοχή", "crosses_into_box": "σέντρες μέσα στην περιοχή",
        "carries_into_box": "είσοδοι στην περιοχή με την μπάλα", "box_entries": "είσοδοι στην περιοχή",
        "deep_completions": "επιτυχημένες πάσες κοντά στην εστία", "key_passes": "καθοριστικές πάσες",
        "field_tilt_pct": "ποσοστό πασών στο τελευταίο τρίτο",
        "counter_press_recovery_pct": "ποσοστό γρήγορων ανακτήσεων μετά από απώλεια",
    },
    "es": {
        "goals": "goles", "shots": "tiros", "shots_on_target": "tiros a puerta", "penalties": "penaltis",
        "yellow_cards": "tarjetas amarillas", "red_cards": "tarjetas rojas", "substitutions": "cambios",
        "fouls": "faltas", "corners": "córneres", "tackles_won": "entradas ganadas", "passes": "pases",
        "passes_completed": "pases completados", "saves": "paradas", "pass_accuracy_pct": "precisión de pase en porcentaje",
        "progressive_passes": "pases progresivos", "progressive_carries": "conducciones progresivas",
        "passes_into_box": "pases al área", "crosses_into_box": "centros al área",
        "carries_into_box": "conducciones al área", "box_entries": "llegadas al área",
        "deep_completions": "pases completados cerca de la portería", "key_passes": "pases clave",
        "field_tilt_pct": "porcentaje de pases en el último tercio",
        "counter_press_recovery_pct": "porcentaje de recuperaciones rápidas tras pérdida",
    },
    "fr": {
        "goals": "buts", "shots": "tirs", "shots_on_target": "tirs cadrés", "penalties": "penalties",
        "yellow_cards": "cartons jaunes", "red_cards": "cartons rouges", "substitutions": "remplacements",
        "fouls": "fautes", "corners": "corners", "tackles_won": "tacles réussis", "passes": "passes",
        "passes_completed": "passes réussies", "saves": "arrêts", "pass_accuracy_pct": "précision des passes en pourcentage",
        "progressive_passes": "passes progressives", "progressive_carries": "conduites progressives",
        "passes_into_box": "passes dans la surface", "crosses_into_box": "centres dans la surface",
        "carries_into_box": "conduites dans la surface", "box_entries": "entrées dans la surface",
        "deep_completions": "passes réussies près du but", "key_passes": "passes clés",
        "field_tilt_pct": "part des passes dans le dernier tiers en pourcentage",
        "counter_press_recovery_pct": "récupérations rapides après la perte en pourcentage",
    },
}

KIND_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "goal": "Goal", "shot": "Shot", "shot_on_target": "Shot on target", "save": "Save", "penalty": "Penalty",
        "yellow_card": "Yellow card", "red_card": "Red card", "substitution": "Substitution", "foul": "Foul",
        "corner": "Corner", "tackle_won": "Tackle won", "pass": "Pass", "pass_completed": "Completed pass",
        "progressive_pass": "Progressive pass", "progressive_carry": "Progressive carry",
        "pass_into_box": "Pass into the box", "cross_into_box": "Cross into the box",
        "carry_into_box": "Carry into the box", "box_entry": "Box entry", "deep_completion": "Completed pass near goal",
    },
    "el": {
        "goal": "Γκολ", "shot": "Τελική", "shot_on_target": "Τελική στην εστία", "save": "Απόκρουση",
        "penalty": "Πέναλτι", "yellow_card": "Κίτρινη κάρτα", "red_card": "Κόκκινη κάρτα", "substitution": "Αλλαγή",
        "foul": "Φάουλ", "corner": "Κόρνερ", "tackle_won": "Κερδισμένο τάκλιν", "pass": "Πάσα",
        "pass_completed": "Επιτυχημένη πάσα",
        "progressive_pass": "Προωθητική πάσα", "progressive_carry": "Προωθητική κίνηση με την μπάλα",
        "pass_into_box": "Πάσα μέσα στην περιοχή", "cross_into_box": "Σέντρα μέσα στην περιοχή",
        "carry_into_box": "Είσοδος στην περιοχή με την μπάλα", "box_entry": "Είσοδος στην περιοχή",
        "deep_completion": "Επιτυχημένη πάσα κοντά στην εστία",
    },
    "es": {
        "goal": "Gol", "shot": "Tiro", "shot_on_target": "Tiro a puerta", "save": "Parada", "penalty": "Penalti",
        "yellow_card": "Tarjeta amarilla", "red_card": "Tarjeta roja", "substitution": "Cambio", "foul": "Falta",
        "corner": "Córner", "tackle_won": "Entrada ganada", "pass": "Pase", "pass_completed": "Pase completado",
        "progressive_pass": "Pase progresivo", "progressive_carry": "Conducción progresiva",
        "pass_into_box": "Pase al área", "cross_into_box": "Centro al área",
        "carry_into_box": "Conducción al área", "box_entry": "Llegada al área",
        "deep_completion": "Pase completado cerca de la portería",
    },
    "fr": {
        "goal": "But", "shot": "Tir", "shot_on_target": "Tir cadré", "save": "Arrêt", "penalty": "Penalty",
        "yellow_card": "Carton jaune", "red_card": "Carton rouge", "substitution": "Remplacement", "foul": "Faute",
        "corner": "Corner", "tackle_won": "Tacle réussi", "pass": "Passe", "pass_completed": "Passe réussie",
        "progressive_pass": "Passe progressive", "progressive_carry": "Conduite progressive",
        "pass_into_box": "Passe dans la surface", "cross_into_box": "Centre dans la surface",
        "carry_into_box": "Conduite dans la surface", "box_entry": "Entrée dans la surface",
        "deep_completion": "Passe réussie près du but",
    },
}

WORDS: dict[str, dict[str, str]] = {
    "en": {"score": "Score", "minute": "Minute"},
    "el": {"score": "Σκορ", "minute": "Λεπτό"},
    "es": {"score": "Marcador", "minute": "Minuto"},
    "fr": {"score": "Score", "minute": "Minute"},
}

WITHHELD_LINE: dict[str, str] = {
    "en": "One line of this report was withheld by the Fact Gate.",
    "el": "Μία πρόταση της αναφοράς αποσύρθηκε από τον έλεγχο στοιχείων.",
    "es": "Una frase de este informe fue retirada por el control de datos.",
    "fr": "Une phrase de ce compte rendu a été retirée par le contrôle des faits.",
}

AUDIO_DISCLOSURE: dict[str, str] = {
    "en": "This audio report was generated by AI from a synthetic match.",
    "el": "Αυτή η ηχητική αναφορά δημιουργήθηκε από τεχνητή νοημοσύνη για έναν συνθετικό αγώνα.",
    "es": "Este informe de audio fue generado por IA a partir de un partido sintético.",
    "fr": "Ce compte rendu audio a été généré par IA à partir d'un match synthétique.",
}

EDITION_LABEL = (
    "AI-drafted from a synthetic match. Numbers and names checked by code. "
    "Approval time and approver are added by the Publisher's receipt."
)


# ----------------------------------------------------------------- brief


def _name_of(record: MatchRecord, entity_id: str | None) -> str | None:
    if entity_id is None:
        return None
    return record.roster.clubs.get(entity_id) or record.roster.players.get(entity_id)


def writer_brief(record: MatchRecord, draft: Draft) -> dict[str, Any]:
    facts = record.facts()
    home, away = facts.home_id, facts.away_id
    goals = []
    for e in events_of_kind(facts.events, "goal"):
        goals.append(
            {
                "event_id": e.event_id,
                "minute": display_minute(e.period, e.minute),
                "club_id": e.team,
                "club": record.roster.clubs[e.team],
                "scorer_id": e.player,
                "scorer": _name_of(record, e.player),
                "score_after": score_after(facts, e.seq),
            }
        )
    cards = []
    for kind in ("red_card", "yellow_card"):
        for e in events_of_kind(facts.events, kind):
            cards.append(
                {
                    "event_id": e.event_id,
                    "kind": kind,
                    "minute": display_minute(e.period, e.minute),
                    "club_id": e.team,
                    "player_id": e.player,
                    "player": _name_of(record, e.player),
                }
            )
    stats = {}
    for club in (home, away):
        stats[club] = {
            stat: {
                "value": recompute.recompute_stat(facts, stat, club),
                "event_ids": [ev.event_id for ev in stat_events(facts, stat, club)],
            }
            for stat in BRIEF_STATS
        }
    return {
        "match": {
            "match_id": record.match_id,
            "league": record.roster.league_name,
            "home": {"id": home, "name": record.roster.clubs[home]},
            "away": {"id": away, "name": record.roster.clubs[away]},
            "final_score": score_after(facts, None),
        },
        "goals": goals,
        "cards": cards,
        "stats": stats,
        "turning_points": draft.turning_points,
    }


# ----------------------------------------------------------------- template recap


def template_recap(record: MatchRecord) -> list[Sentence]:
    """A short English recap from the facts alone. Every line passes the Fact Gate by construction."""
    facts = record.facts()
    home, away = facts.home_id, facts.away_id
    hn, an = record.roster.clubs[home], record.roster.clubs[away]
    final = score_after(facts, None)
    out = [
        Sentence(
            sentence_id="t1",
            text=f"Full time: {hn} {final['home']}-{final['away']} {an}.",
            language="en",
            claims=(
                Claim(type="score", value=final),
                Claim(type="name", value=hn, subject=home),
                Claim(type="name", value=an, subject=away),
            ),
            author="template",
        )
    ]
    for n, e in enumerate(events_of_kind(facts.events, "goal")[:MAX_TEMPLATE_GOAL_LINES]):
        scorer = _name_of(record, e.player)
        minute = display_minute(e.period, e.minute)
        club = record.roster.clubs[e.team]
        claims = [
            Claim(type="minute", value=minute, event_ids=(e.event_id,)),
            Claim(type="event_ref", value="goal", event_ids=(e.event_id,)),
            Claim(type="name", value=club, event_ids=(e.event_id,), subject=e.team),
        ]
        if scorer:
            claims.insert(0, Claim(type="name", value=scorer, event_ids=(e.event_id,), subject=e.player))
            text = f"{scorer} scored for {club} in minute {minute}."
        else:
            text = f"{club} scored in minute {minute}."
        out.append(Sentence(sentence_id=f"t{n + 2}", text=text, language="en", claims=tuple(claims), author="template"))
    for club, name in ((home, hn), (away, an)):
        counted = stat_events(facts, "shots_on_target", club)
        out.append(
            Sentence(
                sentence_id=f"t{len(out) + 1}",
                text=f"{name} had {len(counted)} shots on target.",
                language="en",
                claims=(
                    Claim(
                        type="stat",
                        value=len(counted),
                        event_ids=tuple(ev.event_id for ev in counted),
                        stat="shots_on_target",
                        subject=club,
                    ),
                ),
                author="template",
            )
        )
    return out


# ----------------------------------------------------------------- claim rendering


def render_claims(claims: Iterable[Claim], language: str, record: MatchRecord) -> str:
    """One plain sentence that writes exactly these claims, home first, digits only. "" for no claims."""
    claims = list(claims)
    words, stats, kinds = WORDS[language], STAT_LABELS[language], KIND_LABELS[language]
    parts: list[str] = []
    for c in claims:
        if c.type == "score" and isinstance(c.value, dict):
            parts.append(f"{words['score']} {c.value['home']}-{c.value['away']}")
        elif c.type == "stat":
            who = _name_of(record, c.subject) or ""
            label = stats.get(c.stat or "", c.stat or "")
            parts.append(f"{who}, {label}: {c.value}".lstrip(", "))
        elif c.type == "minute":
            parts.append(f"{words['minute']} {c.value}")
        elif c.type == "event_ref":
            parts.append(kinds.get(str(c.value), str(c.value)))
    text = ". ".join(parts)
    names: list[str] = []
    for c in claims:
        if c.type == "name" and isinstance(c.value, str) and c.value.casefold() not in text.casefold() and c.value not in names:
            names.append(c.value)
    text = ". ".join([*names, text] if text else names)
    return f"{text}." if text else ""


def corrected_claims(sentence: Sentence, facts: MatchFacts) -> tuple[Claim, ...]:
    """The sentence's claims with every value recomputed from the log; claims that cannot be fixed are dropped."""
    by_id = facts.by_id()
    out: list[Claim] = []
    for c in sentence.claims:
        ids = tuple(e for e in c.event_ids if e in by_id)
        if c.type == "stat":
            if c.stat not in recompute.STAT_KEYS or not c.subject:
                continue
            try:
                value = recompute.recompute_stat(facts, c.stat, c.subject)
                support = () if c.stat in recompute.RATIO_STATS else tuple(e.event_id for e in stat_events(facts, c.stat, c.subject))
            except recompute.RecomputeError:
                continue
            out.append(Claim(type="stat", value=value, event_ids=support, stat=c.stat, subject=c.subject))
        elif c.type == "score":
            last = max((by_id[e].seq for e in ids), default=None)
            out.append(Claim(type="score", value=score_after(facts, last), event_ids=ids))
        elif c.type == "minute":
            if not ids:
                continue
            first = by_id[ids[0]]
            shown = display_minute(first.period, first.minute)
            keep = tuple(e for e in ids if display_minute(by_id[e].period, by_id[e].minute) == shown)
            out.append(Claim(type="minute", value=shown, event_ids=keep))
        elif c.type == "name":
            if not isinstance(c.value, str) or not facts.roster.ids_for_name(c.value):
                continue
            entity = c.subject if c.subject in facts.roster.ids_for_name(c.value) else None
            targets = [entity] if entity else facts.roster.ids_for_name(c.value)
            keep = tuple(e for e in ids if any(recompute.event_involves(facts, by_id[e], t) for t in targets))
            out.append(Claim(type="name", value=c.value, event_ids=keep, subject=entity))
        elif c.type == "event_ref":
            if c.value not in recompute.EVENT_KINDS:
                continue
            keep = tuple(e for e in ids if recompute.EVENT_KINDS[c.value](by_id[e]))
            if keep:
                out.append(Claim(type="event_ref", value=c.value, event_ids=keep))
    return tuple(out)


def template_line_for(sentence: Sentence, record: MatchRecord) -> Sentence:
    """Replace a refused English sentence by a template line with corrected claims; withheld if none hold."""
    facts = record.facts()
    claims = corrected_claims(sentence, facts)
    text = render_claims(claims, "en", record)
    if text:
        candidate = Sentence(sentence.sentence_id, text, "en", claims, "template")
        if check_sentence(candidate, facts).passed:
            return candidate
    return Sentence(sentence.sentence_id, WITHHELD_LINE["en"], "en", (), "template")
