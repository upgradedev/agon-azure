Agon live cues: the timed overlay stream the console and a broadcast graphics system read
=========================================================================================

What this folder is
-------------------
One file per synthetic match, <seed>.json, format agon.cues/1. Each cue is one
graphic that can sit on screen alongside the match: when it happens on the
match clock, when it is on screen, which overlay it is (lower third, player
tag or stat card), its caption in English, Greek, Spanish and French, the
same facts as integers, the event ids it comes from, the charter template it
fills and the hash of the approved Match-Day Charter it runs under.

No cue exists without an approved charter. Before kickoff the club media
officer approves, once, in Microsoft Teams, the rules the live cues may use
(agon.charter: the templates in four languages, their thresholds, the held
claim classes, the Rights Guard lists by hash and the voices). Deterministic
code (agon.cues, never a language model) then fills only those templates from
the event log. With no approved charter agon.cues produces nothing and the
export refuses to write.

The JSON Schema is agon.cues-1.schema.json in this folder. The Python
validator agon.cues.validate enforces the same rules and the ones a schema
cannot state; tests/test_cues.py keeps the two in step.

How a file is made
------------------
After a charter is approved in Teams and published to the public feed:

    PYTHONPATH=src python -m agon.cues \
        --feed-base-url https://<storage account>.blob.core.windows.net/feed/ \
        --charter <approved charter bundle sha256> \
        --approved-at <the approval time from the Published card, ISO 8601 with a zone> \
        --out web/cues

It reads <feed>/<sha>/manifest.json and charter.json, refuses them unless the
manifest hashes to the given sha, every gate passed, the bundle kind is
charter and charter.json matches its sha256; it re-runs the charter gate
against the fictional roster of the seed, regenerates the match from the
seed, writes <seed>.json and validates the folder. A charter publishes no
destination, so no public file carries the approval time; that is why
--approved-at is required. A later charter replaces a file only with
--replace. Check the folder:

    PYTHONPATH=src python -m agon.cues --check web/cues

Where the page reads it
-----------------------
The console serves this folder as cues/<seed>.json beside runs/ (the copy into
the built site belongs to the console build). Every file is UTF-8 JSON in
canonical form (sorted keys, no whitespace) with one final LF.

The file
--------
    {"format": "agon.cues/1",
     "schema": "agon.cues-1.schema.json",
     "seed": int, "match_index": int, "match_id": "s<seed>-m<index>",
     "generator": "synth-v0.2",
     "fixture": {"league": str, "home": {"club_id", "name"}, "away": {"club_id", "name"}},
     "languages": ["en", "el", "es", "fr"],
     "charter": {"bundle_sha256": str, "charter_json_sha256": str, "approved_at": "YYYY-MM-DDTHH:MM:SSZ"},
     "kinematics": null or {"format": "agon.kinematics-triggers/1", "source": str},
     "held": [{"event_id", "held_class", "clock"}],
     "cues": [cue, ...]}

    cue = {"cue_id": 16 hex characters, unique in the file,
           "template_id": "goal" | "red_card" | "substitution" | "big_chance" |
                          "pass_quality" | "player_tag" | "sprint" | "top_speed" | "distance",
           "overlay": "lower_third" | "player_tag" | "stat_card",
           "clock": {"period": 1 | 2, "ms": int, "label": "71'"},
           "window": {"in_ms": int, "out_ms": int},
           "text": {"en": str, "el": str, "es": str, "fr": str},
           "data": {name: int},
           "source_event_ids": [16-hex event id, ...],
           "charter_sha256": the approved charter bundle sha256,
           "team": "home" | "away", "club_id": str, "player_id": str or null}

The clock. clock.ms is the match clock a broadcast shows, in milliseconds. The
second half kicks off at 45:00, that is 2700000 ms, and first-half added time
runs past 45:00 inside period 1, so the stream is ordered by (period, ms) and
a renderer keys on both. clock.label is the display minute (71', 45+2').

The window. window.in_ms and window.out_ms are on the same clock and period:
show the graphic from in_ms until out_ms. The display time comes from the
charter template (lower third 8 or 10 s, stat card 7 s, player tag 5 s). A
lower third and the player tag for the same moment share in_ms; they are
different screen regions.

The data. The facts the text shows, as integers, for a system that draws its
own text: home_goals and away_goals (the score after a goal), xg_ppm
(interpret xG in parts per million), distance_m and difficulty (a notable
pass; difficulty is agon.interpret pass difficulty, 0 to 100), speed_kmh_x10
(tenths of km/h) and distance_km (tracked running).

What fires a cue (the approved charter's thresholds decide)
-----------------------------------------------------------
    goal           lower third   a shot whose outcome is goal
    red_card       lower third   a red card or a second yellow; the cue states
                                 who was sent off and when, nothing about the
                                 decision (judgements on refereeing are held)
    substitution   lower third   a substitution; an injury replacement is held
                                 (injury_medical) and listed under "held"
    big_chance     stat card     a shot that is not a goal with interpret xG at
                                 or above big_chance_xg_ppm (200000)
    pass_quality   stat card     a completed pass of at least 30 m with pass
                                 difficulty of at least 50 out of 100
    player_tag     player tag    the player at the centre of each cue above,
                                 and the player coming on; name and club, no
                                 number
    sprint         player tag    a tracked sprint at 25.2 km/h or more
    top_speed      player tag    a tracked speed at 30 km/h or more
    distance       player tag    every 2 km of tracked running

Speed and distance need tracking data. Without it (kinematics is null) a name
tag carries no number and no sprint, top_speed or distance cue exists; the
events-only carry proxy in agon.interpret.kinematics is never shown as a
running speed. With it, pass --kinematics FILE in this form:

    {"format": "agon.kinematics-triggers/1",
     "source": "what produced the tracking data",
     "triggers": [
       {"type": "sprint",    "player_id": "C1P10", "period": 2, "ms": 3925000, "kmh_x10": 268},
       {"type": "top_speed", "player_id": "C1P10", "period": 2, "ms": 3926000, "kmh_x10": 312},
       {"type": "distance",  "player_id": "C6P04", "period": 2, "ms": 4100000, "km": 6}]}

ms is on the same match clock. A trigger below the charter's threshold, a
distance that is not a whole 2 km step, or a player outside the charter's
squads is dropped, never shown.

Every name in a cue comes from the charter's squads of the fictional fixture,
which the charter gate checked against the roster and the Rights Guard.
