# Agon synthetic match data: dataset card

Generator version `synth-v0.2`. Everything here is synthetic and fictional. No real league, club,
player or match data was used to build, seed or calibrate it.

Sections that describe parts not built yet say so. Nothing below is a claim about real football:
the realism bands in section 4 are ranges we chose as sanity checks, and every number is
self-measured with the command printed beside it.

## 1. Motivation

A transparent simulator can say what caused a result. That is what the later evaluation needs,
and a generator trained on real tracking data could not say it (and the contest rules exclude real
data anyway).

## 2. Composition

**Entities.** One fictional six-club league per seed (`agon.synth.world`). Club and player names
are built from an invented syllable table and screened against `agon.synth.blocklist`. Each club
has a style vector (press height, directness, width, in per-mille) and each player has integer
attributes from 1 to 99.

**Event types.** pass, carry, shot, tackle, pressure, foul, set_piece, sub, card,
possession_change. The fields, allowed outcomes and qualifiers are defined once in
`agon.synth.schema` (`Event`, `OUTCOMES`, `QUALIFIERS`) and checked by `validate_event`.

**Units and quantisation.** Every value is an integer, so canonical JSON is byte-identical on
every platform. Locations are in decimetres on a 105 m by 68 m pitch:
`0 <= x_dm <= 1050` and `0 <= y_dm <= 680`. Time is `minute` and `second` of the match clock,
plus `period` (1 or 2).

**Event id.** The first 16 hex characters of sha256 over the canonical JSON of the event without
its `event_id`. Anyone can recompute it, and editing any field changes it.

### Coordinate frames (read this before drawing anything)

Stored coordinates use the **acting team's attacking frame**. The match meta records this as
`"coordinate_frame": "acting_team_attacks_high_x"`, and so does `manifest.json`.

- The team named in `event.team` always attacks towards `x_dm = 1050`, in both periods.
- So an away-team event and a home-team event at the same stored `(x_dm, y_dm)` are at opposite
  ends of the pitch.
- Defensive events (pressure, tackle, foul, and the card that follows a foul) belong to the
  defending team. They are stored in that team's frame, which is the 180 degree rotation of the
  ball carrier's frame in the same possession chain.
- Restart points (the possession_change and set_piece that open a chain) are stored in the frame
  of the team that takes the restart (on a free kick, the fouled team keeps the ball).

To get one pitch picture, call `agon.synth.schema.to_fixed_frame(event, home_id)`. It returns
`(x, y, end_x, end_y)` in the frame `home_attacks_high_x_both_periods`:

- home events come back unchanged;
- every other event is rotated 180 degrees: `x -> 1050 - x` and `y -> 680 - y` (not an x-only
  flip), and a missing end point stays missing;
- the generator **never swaps ends at half time**, so do not flip again by period.

Worked example, home club `C1`, away club `C2`:

| Stored event | Stored `(x_dm, y_dm)` | Fixed frame `(x, y)` |
|---|---|---|
| `C2` carry ends at | (820, 150) | (230, 530) |
| `C1` pressure on that carrier | (230, 530) | (230, 530) |
| `C1` pass from | (300, 200) | (300, 200) |

The carrier and the presser land on the same spot only after conversion. `tests/test_synth_frame.py`
pins this example. It also checks all 1198 pressure, tackle and foul events of generated seeds 11
to 15 (match index 0, generator `synth-v0.2`) against the carrier's spot, with none skipped, and every interception
restart against the end of the intercepted pass.

Known quirk: a `sub` event is stored at `(525, 0)`, a fixed marker on the `y_dm = 0` touchline of the
substituting team's frame. In the fixed frame the two clubs' substitutions therefore appear on
opposite touchlines. It is a marker, not a location of play.

A planned substitution has no qualifier. An injury replacement (only in storyline matches, section
5) has `qualifier` `injury`; `validate_event` allows exactly these two.

**Brief-field mapping table and tracking data.** Not built yet.

## 3. Collection process

The engine (`agon.synth.engine`, `synth-v0.2`) is a semi-Markov possession-chain simulator on a 16 by 12
zone grid. Club style vectors, player attributes, score effects and a simple momentum term drive
the choice between pass, carry and shot, the pressure rate and the foul rate.

Randomness comes from a counter-based integer RNG (`agon.synth.rng`, splitmix64 finaliser) keyed by
(seed, match, stream, possession, draw, replicate). Each concern has its own named stream
(`StreamId`), so adding draws to one concern never shifts another. Sampling uses integer
thresholds in parts per million, with no `exp`, `log` or `pow` in the sampling path.

**Knobs and their readers.** Each style knob is read in the engine and moves its metric in the
stated direction, checked by `python -m agon.synth.sensitivity` (metric M4, all clubs set to 200
then 800 on the same seeds): press height raises pressures per on-ball action; directness raises
the share of passes gaining 13 m or more and lowers pass completion; width raises the mean
lateral pass distance. Self-measured on seeds 1 to 8, `synth-v0.2`: 4 of 4 checks pass, about 11 s
total. A test cuts every reader and requires all four checks to fail.

**Streams added in v0.2.** `STORYLINE` (20) draws storyline plans and the injured player;
`FIXTURES` (21) draws fixture choices. Both use match keys outside the fixture range 1 to 30, so a
plain match draws exactly what it drew before; a test pins that a match with an empty intervention
list is byte-identical to a plain one.

## 4. Calibration

**Bands.** 22 realism bands live in `src/agon/synth/bands.json`. They are ranges we chose from
general football knowledge as sanity checks for a synthetic generator, not measurements of any
real league. They were frozen, and their sha256 written to `src/agon/synth/bands.lock`, in a commit
before any engine constant changed (`git log --follow src/agon/synth/bands.json` shows the order).
The CLI refuses a bands file that does not match its lock, and it also refuses (exit 2, "NOT THE
FROZEN BANDS: not an M2 result", before any match is simulated) any file whose sha256 differs from
`FROZEN_BANDS_SHA256`, pinned in `agon/synth/bands.py`. So an edited band set with a regenerated lock
next to it never prints an M2 line. `tests/test_synth_bands.py` pins the same hash as a literal and
runs that widened-and-relocked attack. Before the bands were written we had read v0.1 aggregates for seeds 1001
to 1040 (goals, shots, passes, cards, corners); the bands were not set to bracket them.

Two bands need tracking data (sprints per outfield player, distance per outfield player). The
generator has none yet, so they print NOT MEASURABLE and count as not passed. With the M2 threshold
at 20 of 22, every other band must pass.

**Fitting.** The engine constants in `agon.synth.engine` (the block under "Calibrated constants")
were moved by hand, not by the evolution strategy the build spec describes, which is not built.
About ten evaluation rounds ran on calibration seeds only: seeds 1 to 200 at match index 0, and for
the last rounds seeds 1 to 200 at match indices 0, 7 and 15 (600 matches) to reduce noise. No band
was changed. The held-out seeds were read once before fitting (the v0.1 column) and once after.

| Constant | v0.1 | v0.2 | Why |
|---|---|---|---|
| `FOUL_BASE_PPM` | 330000 | 480000 | fouls per match were below the band |
| `YELLOW_BASE_PPM` | 60000 | 95000 | yellows per match were below the band |
| `STRAIGHT_RED_BASE_PPM`, `STRAIGHT_RED_PER_POINT_PPM` | 3000, 40 | 1500, 25 | more fouls would have pushed reds above the band |
| `BOOKED_YELLOW_PERMILLE` | 1000 (no caution) | 300 | booked players were sent off too often |
| `SAVED_SHARE_PCT`, `OFF_TARGET_SHARE_PCT`, `BLOCKED_SHARE_PCT` | 33, 42, 25 | 27, 46, 27 | shots on target ratio was above the band |
| `HOME_XG_PERMILLE`, `AWAY_XG_PERMILLE` | 1000, 1000 (no home advantage) | 1050, 860 | home goal share and home win rate were below the band |
| `HOME_PASS_BONUS_PPM` | 0 | 15000 | home advantage in keeping the ball |
| `PASSING_ATTR_PPM` | 3000 | 6000 | possession split was too even |
| `SHORT_GAME_PPM_PER_POINT` (reads `directness`) | 0 | 320 | possession split was too even |
| `PASS_BASE_PPM`, `PASS_DISTANCE_PPM` | 900000, 35000 | 940000, 52000 | long passes cost more; completion kept in the band |
| `PASS_ACTION_WEIGHT`, `SHOT_APPETITE_PERMILLE` | 600, 1000 | 800, 1350 | passes and shots per team stayed in the band together |
| `CLEARED_CORNER_MIN_X`, `CLEARED_CORNER_PPM` | none | 12, 100000 | corners per match were about half the band |
| `SECOND_HALF_XG_PER_MINUTE` | 0 | 12 | second-half goal share was at the lower edge |

**Results (self-measured, Windows 11, Python 3.11, branch `b/synth-realism`).** Command:
`python -m agon.synth.bands --seeds 1001-1200 --bands bands.json` (with `PYTHONPATH=src`).

| Band | lo | hi | v0.1 held-out | v0.2 held-out |
|---|---|---|---|---|
| goals_per_match | 2.30 | 3.10 | 2.5450 PASS | 2.5800 PASS |
| home_win_rate | 0.38 | 0.50 | 0.3350 FAIL | 0.4550 PASS |
| draw_rate | 0.20 | 0.32 | 0.3200 PASS | 0.2500 PASS |
| zero_zero_rate | 0.04 | 0.11 | 0.0750 PASS | 0.0800 PASS |
| home_goal_share | 0.52 | 0.60 | 0.5010 FAIL | 0.5950 PASS |
| comeback_rate | 0.24 | 0.40 | 0.3243 PASS | 0.2935 PASS |
| shots_per_team | 10.0 | 14.5 | 11.4525 PASS | 11.4750 PASS |
| shots_on_target_ratio | 0.30 | 0.40 | 0.4128 FAIL | 0.3534 PASS |
| goals_per_shot | 0.09 | 0.13 | 0.1111 PASS | 0.1124 PASS |
| passes_per_team | 380 | 560 | 424.2150 PASS | 432.3075 PASS |
| pass_completion | 0.75 | 0.87 | 0.7640 PASS | 0.7802 PASS |
| possession_dominant_share | 0.54 | 0.64 | 0.5372 FAIL | 0.5773 PASS |
| fouls_per_match | 19 | 27 | 17.9550 FAIL | 22.2300 PASS |
| yellow_cards_per_match | 3.0 | 5.0 | 2.4400 FAIL | 3.6300 PASS |
| red_cards_per_match | 0.06 | 0.25 | 0.2800 FAIL | 0.1650 PASS |
| corners_per_match | 8.5 | 11.5 | 4.7450 FAIL | 10.3450 PASS |
| penalties_per_match | 0.18 | 0.40 | 0.2100 PASS | 0.3150 PASS |
| set_piece_goal_share | 0.20 | 0.35 | 0.1690 FAIL | 0.3178 PASS |
| second_half_goal_share | 0.52 | 0.60 | 0.5403 PASS | 0.6105 FAIL |
| late_goal_share | 0.18 | 0.30 | 0.2515 PASS | 0.3023 FAIL |
| sprints_per_outfield_player | 20 | 60 | NOT MEASURABLE | NOT MEASURABLE |
| distance_per_outfield_player_km | 9.0 | 12.5 | NOT MEASURABLE | NOT MEASURABLE |
| **M2 passed** | | | **11 of 22** | **18 of 22** |

M2 on the held-out seeds is **18 of 22, below the threshold of 20**, and the CLI exits 1. The two
misses are the goal-timing bands, just above their upper edges. On the 600 calibration matches the
same two bands sat inside (0.5693 and 0.2484). On the like-for-like calibration set (seeds 1 to
200 at match index 0, the index M2 uses) they were already close to their upper edges (0.5774 and
0.2734). So the miss is either noise in 200 matches (one standard error of a share over about 516
goals is about 0.022) or a real overshoot of the fatigue term; the data do not tell the two apart. We did not retune on the held-out seeds, because that would turn them into calibration seeds.
The next fit must run on calibration seeds only, and the result is reported here as it stands.
On the calibration seeds 1 to 200 at match index 0 the same command gives 20 of 22.
Total wall time for the 200 held-out matches: about 41 s on this machine (printed on stderr).

## 5. Labels

**Storyline labels (metric M3).** Five storylines are injected as labelled interventions, one per
storyline match: red card, tactical shift (press height up), late collapse (the opponent's scoring
chance multiplied), goalkeeper heroics (part of each chance becomes a save) and injury stoppage
(a replacement and added time). `agon.referee.storylines.plan_storyline(seed, kind)` picks the side,
minute and size from the seed. The engine applies the plan and keeps a ledger
(`MatchResult.applied`) that never enters the meta, the event log or the manifest; a test checks
that a storyline match's meta and events never name the storyline.

Each label carries the activation clock, an anchor event id (the red card, the first event after
the shift, the first goal conceded in a collapse, the first save, the injury replacement), the
measured effect and a `turning_point` flag set by one written rule (for example a collapse is a
turning point only if a goal followed). Labels are derived twice: from the engine's ledger, and
again from the plan, the meta and the event log alone, with the injury's baseline added time
recomputed from the RNG. `python -m agon.referee.verify_labels --seeds 1,2,3` compares the two;
`--mutate` corrupts every label in one field and must exit 1. Self-measured on seeds 1, 2, 3:
15 of 15 labels re-derived identically; with `--mutate`, 15 of 15 rejected and exit 1; about 2.5 s
each. Not every storyline takes effect: in seed 3 the late collapse produced no goal, and in the
separate seed 3 heroics match the keeper made only 2 saves, so both labels say `turning_point` false.

The referee package is reachable only from CI and evidence tooling: `ci/check_referee_isolation.py`
parses every module under `src/agon` outside `src/agon/referee` and fails on any import of it.

Not built: the coupled counterfactual twins (spec P1, metric M5), which would give causal
turning-point labels for any moment rather than only for injected storylines.

**Planted-error and adversarial fixtures.** `python -m agon.synth.fixtures --seeds 1,2,3
--print-hash` lists, per seed: three wrong-number sentences in EN, EL, ES and FR with the planted
value, the true value and the event ids proving it; a scorer correction (one goal reassigned to an
outfield teammate on the pitch at that moment, `published` for seeds in `PUBLISHED_SEEDS`, which is
provisionally 1, 2, 3); a player name carrying an instruction string (planted only in a world
copy); and a free-text note naming a real club, drawn at run time from the blocklist. Goal minutes
are stated as the minute being played (event `minute` plus one).

## 6. Uses

Supports pipeline testing and evaluation against a known ground truth. It supports no claim about
any real league, club or player.

## 7. Distribution

Seeds are stored, not generated files. To regenerate and hash:

```
python -m agon.synth.manifest --seeds 1,2,3 --print-hash
python -m agon.synth.manifest --seeds 1,2,3 --out build/synth
python -m agon.synth.fixtures --seeds 1,2,3 --out build/fixtures.json
python -m agon.referee.storylines --seeds 1,2,3 --out build/labels.ndjson
```

The fixtures manifest contains a real club name (the bait) and instruction strings on purpose.
It is test input for the gates and must never be copied into a deployed page.

The per-seed hash is sha256 over the canonical JSON of the seed's world, match meta and events. The
CI workflow `.github/workflows/determinism.yml` compares these hashes, the fixtures manifest and the
storyline labels between ubuntu-latest and windows-latest. The hash values of record come from that CI run, not from this card.

## 8. Maintenance and known gaps

- Engine constants fitted by hand on calibration seeds; M2 held-out is 18 of 22 (section 4).
- No tracking data and no video, so the sprint and distance bands cannot be measured.
- Storyline labels cover injected interventions only; counterfactual twins are not built.
- The sub marker location described in section 2.
