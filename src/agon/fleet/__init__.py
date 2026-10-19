"""Agon fleet: the W-fulltime Microsoft Agent Framework workflow and the worker that runs it.

Layout:

* `contracts`: workflow name, fixed executor ids, every checkpointed message type (stdlib).
* `steps`: what each executor does, as plain async functions over injected ports (stdlib).
* `workflow`: the Agent Framework `WorkflowBuilder` graph wrapping those steps.
* `checkpoints`: file and Cosmos DB checkpoint storage, with a guard for silent save failures.
* `runner`: start a run to its approval pause, or resume it from a checkpoint in a new process.

Importing this package does not import Agent Framework; only `workflow`, `checkpoints` and
`runner` do, so the gates and steps stay testable on a machine without it.
"""
