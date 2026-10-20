"""What-If Lab: counterfactual replays of a published synthetic match with common random numbers.

`python -m agon.whatif` reproduces any scenario from its parameters (see `__main__`). The pieces:
`replay` (the engine with a pause, a fork and replicate keying), `lab` (K paired replays and their
reading), `stats` (integer-only intervals) and `scenarios` (the fixed set precomputed for the
published seeds and committed under `scenario_set/`).

Isolation: this package imports the engine and the analyst-facing interpret code, never the
referee, and publishes only aggregates over replays. Synthetic engine, paired replays, not a
prediction about real football.
"""
