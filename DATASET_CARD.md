# Agon synthetic match data: dataset card

Generator version `synth-v0.1`. Everything here is synthetic and fictional. No real league, club,
player or match data was used to build, seed or calibrate it.

Sections that describe parts not built yet say so. Nothing below is a measured realism claim.

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
  of the team that gains the ball.

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
pins this example and checks it on every pressure, tackle and foul of generated seeds 11 to 15.

Known quirk: a `sub` event is stored at `(525, 0)`, a fixed marker on the `y_dm = 0` touchline of the
substituting team's frame. In the fixed frame the two clubs' substitutions therefore appear on
opposite touchlines. It is a marker, not a location of play.

**Brief-field mapping table and tracking data.** Not built yet.

## 3. Collection process

The engine (`agon.synth.engine`, v0) is a semi-Markov possession-chain simulator on a 16 by 12
zone grid. Club style vectors, player attributes, score effects and a simple momentum term drive
the choice between pass, carry and shot, the pressure rate and the foul rate.

Randomness comes from a counter-based integer RNG (`agon.synth.rng`, splitmix64 finaliser) keyed by
(seed, match, stream, possession, draw, replicate). Each concern has its own named stream
(`StreamId`), so adding draws to one concern never shifts another. Sampling uses integer
thresholds in parts per million, with no `exp`, `log` or `pow` in the sampling path.

## 4. Calibration

Not built yet. The v0 tests check only wide sanity ranges (goals 0 to 9 and shots 5 to 40 per
match, on seeds 1 to 50). They are not realism bands.

## 5. Labels

Not built yet (turning-point labels, storyline labels, planted-error manifests).

## 6. Uses

Supports pipeline testing and evaluation against a known ground truth. It supports no claim about
any real league, club or player.

## 7. Distribution

Seeds are stored, not generated files. To regenerate and hash:

```
python -m agon.synth.manifest --seeds 1,2,3 --print-hash
python -m agon.synth.manifest --seeds 1,2,3 --out build/synth
```

The per-seed hash is sha256 over the canonical JSON of the seed's world, match meta and events. The
CI workflow `.github/workflows/determinism.yml` compares these hashes between ubuntu-latest and
windows-latest. The hash values of record come from that CI run, not from this card.

## 8. Maintenance and known gaps

- v0 engine, uncalibrated.
- No tracking data and no video.
- The sub marker location described in section 2.
