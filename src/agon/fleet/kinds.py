"""Run kinds: which Agent Framework workflow a `RunInput` starts, and which officer channels it uses.

An extensible enum. A new kind (for example a half-time brief) adds one row to each table below,
its own contract module with a pinned workflow name, executor ids and checkpoint types, and one
builder in `agon.fleet.runner.workflow_builders`. A kind that is not listed here is refused by the
worker's message parser before any workflow runs.

Standard library only, like the contract modules, so the worker parses messages without Agent
Framework.
"""

from __future__ import annotations

from agon.fleet import charter_contracts, contracts

FULLTIME = "fulltime"
CHARTER = "charter"
RUN_KINDS: tuple[str, ...] = (FULLTIME, CHARTER)

# kind -> the workflow name its checkpoints are grouped under (pinned in each contract module).
WORKFLOW_NAMES: dict[str, str] = {
    FULLTIME: contracts.WORKFLOW_NAME,
    CHARTER: charter_contracts.WORKFLOW_NAME,
}

# kind -> the officer channels the worker posts the approval card to, when configured. The charter
# goes to Teams only: the Outlook email is written for a full-time report.
CARD_CHANNELS: dict[str, tuple[str, ...]] = {
    FULLTIME: ("teams", "outlook"),
    CHARTER: ("teams",),
}


# One checkpoint storage serves every workflow the worker runs, so it decodes the union of their
# pinned allow-lists, in order and without repeats. Each contract module keeps and pins its own
# list; W-fulltime's is unchanged by W-charter.
STORAGE_ALLOWED_CHECKPOINT_TYPES: list[str] = list(
    dict.fromkeys([*contracts.ALLOWED_CHECKPOINT_TYPES, *charter_contracts.ALLOWED_CHECKPOINT_TYPES])
)


def kind_for_workflow(workflow_name: str) -> str:
    """The run kind whose workflow has this name. Raises ValueError for an unknown name."""
    for kind, name in WORKFLOW_NAMES.items():
        if name == workflow_name:
            return kind
    raise ValueError(f"no run kind runs the workflow {workflow_name!r}")
