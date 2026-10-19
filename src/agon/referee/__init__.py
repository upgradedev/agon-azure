"""The referee: ground truth that no agent may see.

This package plans storyline interventions, runs the engine with them, and derives turning-point
labels by two independent code paths (the engine's own ledger, and a scan of the event log alone).
It is used only by CI evaluations and evidence badges.

Isolation rule, enforced by tests/test_referee_isolation.py: no module under src/agon outside this
package imports `agon.referee`. The dependency runs one way only: the referee imports
`agon.synth`, never the reverse, and no agent or MCP tool can reach this package.
"""
