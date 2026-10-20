intel/: the intelligence files the console draws
==================================================

Written at deploy by ci/stamp_web.py (agon.webintel.write_intel_web), then re-checked by
agon.webintel.verify_intel_dir. Where the numbers come from:
  * metrics, passes and rhythm: the exact data/<seed>.ndjson and data/<seed>.world.json files
    the stamp has just verified, read by agon.interpret with the Fact Gate's definitions;
  * milestones: agon.governance.recompute over the simulated season up to the match's matchday
    (agon.synth.season: every fixture of those matchdays, not only the served match), after
    checking that the served log is that season's match byte for byte.
Reproduce locally:

    python -m agon.webdata --seeds 1,2,3 --out build/data
    python -m agon.webintel --data build/data --seeds 1,2,3 --out build/intel

Every file is canonical JSON: sorted keys, ASCII, integers, strings, booleans and null only
(no floats), one final LF. The validators in src/agon/webintel.py are the contract; this file
describes them. A test (tests/test_webintel.py) checks one direction only: every key the code
writes into a metrics or milestones file, sub-keys included, is named here. It does not check
that every word below is a key.

Synthetic data only: fictional clubs and players, nothing about real football. Units:
  ppm         parts per million (xG 0.42 is 420000)
  per mille   parts per thousand (a share of 56.1% is 561)
  dm          decimetres (12.3 m is 123)
  centi       value times 100 (PPDA 4.68 is 468)
A value of null means "nothing to divide by" (for example PPDA in a window with no defensive
action). Draw it as a gap, never as zero.


manifest.json
-------------
  schema          "agon.webintel-manifest/1"
  generator       generator version, for example "synth-v0.2"
  interpret       "interpret-v1"
  pack            metrics pack version, "advanced-v1"
  model_sha256    sha256 of the frozen interpret model the numbers come from
  files           one entry per file:
                    name           bare file name in intel/
                    kind           "metrics", "milestones" or "whatif"
                    seed           the published seed (null for milestones)
                    sha256         sha256 of the exact file bytes
                    events_sha256  sha256 of data/<seed>.ndjson the file describes (null for
                                   milestones); refuse a file whose events_sha256 differs from
                                   the sha256 the data manifest lists for that seed
Fetch only the files the manifest lists. Any other file in intel/ fails the deploy.


metrics-<seed>.json (kind "metrics", schema "agon.webintel.metrics/1")
-----------------------------------------------------------------------
Header: kind, schema, seed, match_id, generator, interpret, pack, model_sha256, events_sha256,
label (show it near the numbers), home, away (club ids), clubs ([{club_id, name, short}], home
first).

definitions   list of {key, label, unit, scope, better, description, rule}, one per metric.
              description is one plain sentence for a reader; rule is the exact definition;
              better is "higher", "lower" or "context". Use label and description on
              scorecards and tooltips instead of writing new copy.

teams         {club_id: {...}} with these integer keys (null where noted):
                xg_ppm, shots, xa_ppm, key_passes,
                xg_open_play_ppm, xg_set_piece_ppm, xg_corner_ppm, xg_free_kick_ppm,
                xg_penalty_ppm   (set piece = corner + free kick + penalty; open play + set
                                  piece = xg_ppm)
                xt_added_ppm     (can be negative)
                progressive_passes, progressive_carries,
                passes_into_box, crosses_into_box (proxy), carries_into_box, box_entries
                                  (= passes_into_box + carries_into_box), deep_completions,
                final_third_passes, field_tilt_permille (null when neither club passed from
                                  its final third; the two clubs sum to 1000 within 1),
                ppda_centi (null), ppda_opponent_passes, ppda_defensive_actions,
                defensive_actions, defensive_action_height_dm (null; a proxy, not a line),
                counter_press_losses, counter_press_recoveries,
                counter_press_rate_permille (null),
                passes, passes_completed, pass_accuracy_permille (null),
                mean_pass_distance_dm (null), mean_pass_difficulty_centi (null)

series        per 15-minute bin, for line or bar charts:
                bins   ["0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+"]
                keys   the metric keys present for each club
                <club_id>: {key: [6 values]} for keys
                  xg_ppm, xa_ppm, xt_added_ppm, progressive_passes, progressive_carries,
                  box_entries, deep_completions, field_tilt_permille, ppda_centi,
                  defensive_action_height_dm, counter_press_losses,
                  counter_press_rate_permille
              "30-45+" and "75-90+" include the added time of that half.

players       one row per player with at least one event, sorted by player_id:
                player_id, name, shirt, position, club_id,
                xg_ppm, shots, xa_ppm, key_passes, xt_added_ppm,
                progressive_passes, progressive_carries, passes_into_box, crosses_into_box,
                carries_into_box, box_entries, deep_completions, tackles_and_fouls,
                passes, passes_completed, pass_accuracy_permille, mean_pass_distance_dm,
                mean_pass_difficulty_centi
              Interceptions carry no player in the event log, so tackles_and_fouls is the
              player-level defensive count.

passes        every pass, column-oriented to keep the file small:
                frame    "home_attacks_high_x_both_periods": coordinates are already
                         converted so the home club attacks towards x = 1050 in both halves;
                         draw them directly, do not rotate again
                columns  event_id, club_id, player_id, receiver_id (null unless complete),
                         period, minute (engine minute, 0-based), second, x_dm, y_dm,
                         end_x_dm, end_y_dm, outcome ("complete", "incomplete", "out"),
                         under_pressure, distance_m, completion_ppm (modelled),
                         difficulty (0 to 100), progressive, into_box, cross_into_box,
                         deep_completion, key_pass, xa_ppm
                rows     one list per pass, in that column order
              Pass quality in the brief's three terms: distance_m, accuracy (completed over
              attempted, from outcome), difficulty.

rhythm        why moments matter, in the rules' words:
                explain           one sentence each for control_vs_chaos, pressure, tempo
                control_vs_chaos  windows: [{window, period, start_minute, index_permille,
                                  band}], changes: [{window, from, to, index_from_permille,
                                  index_to_permille, sentence}]
                pressure          {club_id: {windows: [{window, pressures, band}],
                                  changes: [{window, from, to, pressures, sentence}]}}
                tempo             windows: [{window, on_ball_actions, band}],
                                  changes: [{window, from, to, on_ball_actions,
                                  on_ball_actions_before, sentence}]
              A window label is "p<period> <start>-<end>" in engine minutes; "p1 45-50" is
              first-half added time. Every change carries one plain sentence to show as is.

milestones    citable season facts for this match: list of {kind, subject, matchday,
              match_id, sentence, claims}. kind is first_goal, season_goals, first_assist,
              season_assists, table or club_streak. claims are Fact Gate claims
              {type, stat, subject, value, event_ids}, type player_season_total,
              season_streak or table_position, event_ids empty, whose values the
              sentence writes in digits. The published matches are matchday 1 of their
              season, so their facts are first goals, first assists and table positions.

milestones_provenance  one sentence saying where the milestones come from (the season, not
              this log alone); show it with them.

milestones_note  null when the served log is the whole season match. When it is only the
              first part (a truncated replay such as the development fixture), milestones is
              empty and this string says why; show it instead of the list.

citable       {claim_type: "stat", stats: [{stat, unit, subject}], note}: the statistics a
              writer may cite as Fact Gate `stat` claims (agon.governance.recompute.STAT_KEYS):
              progressive_passes, progressive_carries, passes_into_box, crosses_into_box,
              carries_into_box, box_entries, deep_completions, key_passes,
              pass_accuracy_pct, field_tilt_pct, counter_press_recovery_pct.
              xG, xA, xT and PPDA are never citable (decimals; the gate reads whole numbers).


milestones-season-<n>.json (kind "milestones", schema "agon.webintel.milestones/1")
-----------------------------------------------------------------------------------
  kind, schema, season, generator, label, rules (list of sentences stating how facts are
  chosen), matchdays: [{matchday, matches: [{match_id, home, away, score, facts}]}], where
  each fact has the milestones shape above.


whatif-<seed>.json (kind "whatif", schema "agon.webintel.whatif/1")
-------------------------------------------------------------------
The What-If Lab for one published match: the same seven scenarios for every seed, each replayed
K times with and without the change from the scenario minute (python -m agon.whatif; the file
is the committed set under src/agon/whatif/scenario_set, regenerated and compared byte for byte on
ubuntu and windows by the determinism workflow). Show the label beside every number.

Header: kind, schema, seed, match_id, generator, interpret, model_sha256, events_sha256 (the
served data/<seed>.ndjson, as for metrics), label ("Synthetic engine, paired replays, not a
prediction about real football."), method (list of plain sentences: how the replays are paired
and what they share), k (replays per arm), home, away, clubs, actual_score,
timeline_marks (["5", "10", ..., "45", "45+", "50", ..., "90", "90+"]: cumulative cut-offs; "45+"
is the end of the first half with its added time, "90+" full time).

scenarios     list, one per scenario, each with:
                scenario_id   red_home_30, red_away_30, press_up_home_60, press_down_home_60,
                              collapse_home_60, heroics_away_60, red_low_block_home_55
                title, question, engine_note   plain text; engine_note says what the engine
                              really changes (show it, it is the honest limit)
                side, club_id the side the scenario changes; every share and delta is told from
                              its point of view ("for" is that side, "against" its opponent)
                params        the exact parameters (side, period, minute, interventions,
                              press_side, press_shift, collapse_side, collapse_magnitude)
                command       the CLI line that reproduces this scenario
                k             replays per arm
                prefix        {events_before_scenario, replays_checked, differing_events}:
                              differing_events is always 0 (the validator refuses anything else)
                triggered     for collapse_home_60, how many scenario replays saw the trigger
                              goal; null for the others
                outcomes      {baseline, scenario}: {win, draw, loss}: {count, ppm, lo_ppm,
                              hi_ppm} with a Wilson 95% interval; counts add up to k
                deltas        list of {metric, side, unit, scale, pairs, dropped,
                              baseline_mean, scenario_mean, delta_mean, delta_lo, delta_hi,
                              unpaired_half}; metric is points (side for only), xg, shots,
                              box_entries or ppda. Every number is the quantity times scale
                              (points 1000, xg 1000000, shots 1000, box_entries 1000,
                              ppda 100). delta = with the change minus without, paired per
                              replay; delta_lo..delta_hi is its 95% interval; unpaired_half is
                              the half-width without pairing, to show what pairing buys.
                              box_entries and ppda count the play after the scenario minute;
                              a pair with no PPDA (no defensive action) is dropped and counted.
                xg_timeline   {marks, baseline: {for, against}, scenario: {for, against}}: mean
                              cumulative xG in ppm at each mark, for a line chart of both arms
                summary       one plain sentence from the numbers, ready to show


brief-<seed>.json (kind "brief", schema "agon.webintel.brief/1"), optional
-------------------------------------------------------------------------
Present only when an analyst brief was drafted for the seed (workflow whatif-brief.yml, by hand on
main: one gpt-5.6-luna call per scenario from that scenario's numbers only) and committed under
src/agon/whatif/briefs/. Every number in every brief is re-checked by code against the What-If file
at deploy; a refused draft is never published.
  kind, schema, seed, events_sha256 (as the What-If file), whatif_sha256 (the What-If file the
  briefs were drafted from), deployment, label ("AI-drafted from the numbers above; every number
  checked by code."), briefs: [{scenario_id, text, model, prompt_sha256}]
Show the label with every brief, under the numbers it was drafted from.


Honest limits (say them on the page where the number appears)
-------------------------------------------------------------
  * Crosses are a geometric proxy: the events carry no cross field.
  * Defensive action height is the mean position of tackles, fouls and interceptions, not a
    measured defensive line; the generator has no 22-player tracking.
  * xA equals xG for a club whose every shot followed a key pass; the engine's chains often
    end that way.
  * Nothing here measures scanning, metabolic load, injury risk or pitch control.
  * The What-If replays come from this synthetic engine; they are not a prediction about any real
    match. A red card falls on whichever player makes the next defensive duel (no position can
    be chosen), and xG does not see the engine's hidden late-collapse multiplier, only the shots.
