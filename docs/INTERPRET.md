# Interpret: football state from the event log

`src/agon/interpret` turns the synthetic event log into the quantities the brief names: xG, xT,
PPDA, pass distance and difficulty, speed and distance triggers, a control vs chaos index,
pressure regimes, tempo bands, momentum and a win probability with `wp.swing(event_id)`.
Everything is a pure function of the events. No model call, no randomness, no clock reads.

All data is synthetic. Club and player ids such as `C1` are fictional; nothing here describes a
real league.

## Isolation

Interpret reads the event contract (`agon.synth.schema`) and the minute convention
(`agon.governance.recompute.display_minute`) and nothing else from the repo. It never imports the
engine, its constants, the storyline interventions or the referee. A test
(`tests/test_interpret.py::test_inference_modules_read_only_the_event_contract`) parses every
inference module and fails on any other `agon` import. The one exception is the fitter
(`agon.interpret.fit`), which generates training matches through `agon.synth.manifest`, offline.

Why it matters: the win probability is later compared with generator outcomes (M6) and used as a
baseline for the Analyst (M7). If interpret could read the engine's internal scoring chances, both
comparisons would be circular.

## Determinism and units

Every value that can reach a hash is an integer:

| Quantity | Unit |
|---|---|
| probabilities, xG, xT, win probability | ppm (parts per million) |
| shares, entropy, control vs chaos index | per mille |
| PPDA | centi-units (PPDA x 100), `null` when the side made no defensive action |
| logistic coefficients | integer micro-units per feature unit |
| goal rates | integer nano-goals per second |
| speed | tenths of km/h |
| distance | decimetres (pass distance in whole metres) |

The transcendental functions (logistic, Poisson, entropy) use `decimal`, whose `exp` and `ln` are
correctly rounded in integer software, rather than the platform libm, whose last bit may differ
between Linux and Windows. Distances use `math.isqrt`. The test
`test_interpret_output_hash_is_pinned` pins the sha256 of the full output for seed 1, computed on
Windows with Python 3.11; the CI test job and the `mcp` workflow recompute it on ubuntu and
windows with Python 3.12 (not yet run).

## The frozen model

`src/agon/interpret/model.json` holds every fitted number, as canonical integer JSON.
`model.lock` holds its sha256. Loading refuses a file that does not hash to the lock, and a test
pins the same hash as a literal.

Regenerate (about two minutes, wall time printed to stderr):

```
python -m agon.interpret.fit --seeds 1-500
python -m agon.interpret.fit --seeds 1-500 --check   # refit and compare bytes with the committed file
```

The model records its provenance: the command, seeds 1 to 500, match index 0, the generator
version and the combined sha256 of the 500 training bundles. A generator bump fails
`test_model_was_fitted_on_the_current_generator` until the model is refitted. That refit is one
command, and it changes the pinned hashes in `tests/test_interpret.py`.

The fitter uses floats, but its transcendental calls go through `decimal`, every float sum of
more than two terms is `math.fsum` (the builtin `sum` over floats changed algorithm in Python
3.12), and the rows are visited in one fixed key order. A refit on another OS or Python version is
designed to produce the same bytes. The `mcp` workflow checks that with `--check` on ubuntu and
windows under Python 3.12, after a negative control (`--seeds 1-3 --check` must report a
mismatch); that workflow has not run yet. Locally, on Windows with Python 3.11, `--check` on seeds
1 to 500 reports OK and `--seeds 1-3 --check` exits 1 with a mismatch.

## Each quantity

**xG.** Logistic regression of goal on shot features in the acting team's frame (it attacks
towards x = 1050 dm): distance to the goal centre in metres, lateral offset in metres, inside the
penalty area, under pressure, header, direct free kick, penalty, home side, and minutes past 45.
Fitted by Newton-Raphson with step halving and a ridge of 0.1 on the slopes, over the shots of
training seeds 1 to 500 (counts are in `model.json` under `xg`).

**Pass distance and difficulty.** Distance is the straight line from start to end in whole
metres. Difficulty is `round(100 x (1 - p))` where `p` is the MODELLED completion probability from
a second logistic regression (distance, forward distance, under pressure, ending in the final
third, starting in the own third, home side). It is learnt from outcomes in the event log. It is
not the generator's internal success probability, which interpret never sees.

**xT.** Expected threat on the 16 by 12 grid. For each zone the fitter counts shots, goals and
move attempts (passes and carries), and the end zones of completed moves. Value iteration
`xT(z) = shots/actions x goals/shots + moves/actions x sum(T(z to z') x xT(z'))` runs until
convergence. An action's xT added is `xT(end) - xT(start)` for a completed pass or carry.

**PPDA.** Opponent passes in the opponent's own 60% of the pitch (x < 630 dm in the opponent's
frame) divided by the side's tackles, fouls and interceptions in the same area (x >= 420 dm in the
side's own frame). Reported for the match and per 15-minute bin (0-15, 15-30, 30-45 plus added
time, 45-60, 60-75, 75-90 plus added time). Lower means a more intense press.

**Control vs chaos.** Per 5-minute window keyed by (period, minute // 5), so first-half added time
never merges with the start of the second half. Two components, each 0 to 1000:
- possession-chain entropy: the normalised Shannon entropy of on-ball actions (pass, carry, shot)
  across possession chains. 0 when one chain holds every action, 1000 when every action is its own
  chain;
- turnover share: open-play turnovers (interceptions and tackles) per chain.

The index is their mean. Bands (control, contested, chaos) are the training terciles frozen in
`model.json`.

**Pressure regimes.** Pressure events per side per 5-minute window, banded low, medium or high by
frozen training terciles. Consecutive windows in one band merge into a regime. Every regime
boundary is listed as a change, which is what the rules call a tactical pressure change.

**Tempo bands.** On-ball actions per 5-minute window, both sides, banded slow, normal or fast by
frozen training terciles. The last window of each half can be partial and is not rescaled.

**Momentum.** Per engine minute with events, each side's threat is its xG plus its positive xT
added. The balance is `previous x 0.8 + home threat - away threat`. A positive balance leans home.

**Win probability.** Each side's remaining goals are an independent Poisson count. The mean is
the sum, over the 15-minute bins still to play, of that side's fitted goal rate times the seconds
left in the bin, with home and away fitted separately. Added time is the training mean per half.
A side with more players has its rate multiplied by the fitted `up` factor and the other side by
`down`. Both factors are observed over expected goals in spells with unequal numbers, shrunk
towards 1 by 5 pseudo-goals because such spells are rare. Score effects are not modelled. Once
the actual added time passes the expected added time, no time is left and the probability is the
current result.

`wp.swing(event_id)` returns the probability just before and just after the event, the change for
the home side and for the acting side. For a shot it also returns `wp_if_goal` and
`expected_swing_ppm` = xG x (the acting side's probability if it scored minus its probability
before). That leverage lets a saved chance be a candidate moment. `candidate_moments` lists
goals, sendings-off, penalties and shots with xG of at least 0.2, ranked by the larger of the
realised and expected swing.

**Speed and distance triggers.** The brief names a top-speed trigger (30 km/h or more), sprints
(25.2 km/h or more) and 2 km distance milestones. synth-v0.2 generates no tracking data
(`DATASET_CARD.md` section 8). So:
- `triggers_from_samples` implements the triggers on any per-player stream of (ms, x dm, y dm)
  samples. It is tested on hand-built samples and is what a tracking feed would use;
- `carry_proxy` is what the events allow. It divides each carry's start-to-end displacement by
  the whole-second gap to the next event. That is an on-ball displacement proxy, not running
  speed, and the output says so in its `note` field;
- distance milestones cannot be derived from events, because most running happens off the ball.
  The output returns `null` with that reason rather than a number.

## In the fleet

The W-fulltime `interpret` executor calls this package through `AgonInterpret`
(`src/agon/fleet/interpret_port.py`). Every deps builder uses it: the offline fakes and the Azure
worker alike. Its output is a digest that the Draft carries to the Analyst and the Desk Editor:

- `candidates`: `candidate_moments`, most important first. Each has a `kind` from the Fact Gate's
  claim vocabulary (goal, red_card, penalty, shot), the score before and after from
  `agon.governance.recompute`, and `swing`: the larger of the realised and the expected change in
  the acting club's win probability, in ppm, with both parts and the probabilities before and after;
- `metrics`: xG and xT totals, PPDA, control vs chaos per window (index and band), pressure regimes
  with their changes, and tempo bands;
- `units`: the unit of every field above.

The momentum and win-probability series and the speed proxy stay out of the digest, because the
Draft travels in every checkpointed message. `source` is `agon.interpret/` plus the first 12 hex
digits of the model sha256, and the seal records it as `deployment_ids.interpret`, so a bundle names
the model behind its candidates. A bundle that names `fleet-local-standin/1` came from the earlier
stand-in, whose swing was a score-state measure and not a win probability.

## M6: interpret win probability against final results

```
python -m agon.referee.wp_compare --seeds 3001-3020
```

synth-v0.2 exposes no true win probabilities, so there is no oracle to compare with. The command
scores interpret's three-way probability against the one-hot FINAL RESULT at 18 checkpoints per
match (every 5 minutes of regular time in each half). It also scores a constant baseline, the
training outcome shares. It prints the three-way Brier score and the mean absolute difference for
both. Points inside one match are correlated, so no interval is printed.

Self-measured on 2026-10-19, branch `b/interpret-mcp`, local Windows run with Python 3.11, the
command above, generator synth-v0.2, model sha256
`7ab5e12ab86e08263a6e827412b94ab2ef7b53d0d25de29d2485da2c711b0ba7`, 20 held-out matches and
360 points:

| Predictor | Brier | Mean abs diff |
|---|---|---|
| interpret | 0.5034 | 0.3271 |
| constant training shares | 0.6350 | 0.4229 |

Twenty matches is a small sample. These numbers describe this generator and these seeds only, and
say nothing about real football.
