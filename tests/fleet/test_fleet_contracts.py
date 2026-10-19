"""The persisted contract of W-fulltime, pinned by literal (spec trap 1).

Changing any value here changes what an existing checkpoint can be resumed by. That is allowed
only together with a deliberate migration, which is why each value is written out in full.
"""

from __future__ import annotations

import dataclasses
import subprocess

from fleet_support import PYTHON, subprocess_env

from agon.fleet import contracts

PINNED_EXECUTOR_IDS = (
    "ingest",
    "interpret",
    "analyst",
    "writer",
    "triage",
    "factgate",
    "desk_editor",
    "localise_el",
    "localise_es",
    "localise_fr",
    "easy_read",
    "factgate_lang",
    "rights",
    "audio",
    "seal",
    "approval",
    "publish_request",
)

PINNED_ALLOWED_CHECKPOINT_TYPES = [
    "agon.fleet.contracts:RunInput",
    "agon.fleet.contracts:Draft",
    "agon.fleet.contracts:Ingested",
    "agon.fleet.contracts:Interpreted",
    "agon.fleet.contracts:Analysed",
    "agon.fleet.contracts:WriterRetry",
    "agon.fleet.contracts:Written",
    "agon.fleet.contracts:Triaged",
    "agon.fleet.contracts:Checked",
    "agon.fleet.contracts:Edited",
    "agon.fleet.contracts:Localised",
    "agon.fleet.contracts:LanguageChecked",
    "agon.fleet.contracts:RightsChecked",
    "agon.fleet.contracts:Voiced",
    "agon.fleet.contracts:SealedDraft",
    "agon.fleet.contracts:ApprovalRequest",
    "agon.fleet.contracts:DecisionEnvelope",
    "agon.fleet.contracts:PublishRequest",
    "agon.fleet.contracts:RunOutcome",
]

# Field annotations a checkpointed message may use: JSON-native values, or another pinned message.
JSON_NATIVE_ANNOTATIONS = {
    "str",
    "int",
    "bool",
    "str | None",
    "Draft",
    "dict[str, Any]",
    "dict[str, str]",
    "dict[str, list[str]]",
    "dict[str, dict[str, Any]]",
    "dict[str, list[dict[str, Any]]]",
    "list[str]",
    "list[dict[str, Any]]",
}


def test_workflow_name_is_pinned() -> None:
    assert contracts.WORKFLOW_NAME == "agon_w_fulltime"


def test_executor_ids_are_pinned() -> None:
    assert contracts.EXECUTOR_IDS == PINNED_EXECUTOR_IDS
    assert len(set(contracts.EXECUTOR_IDS)) == len(contracts.EXECUTOR_IDS)


def test_allowed_checkpoint_types_are_pinned() -> None:
    assert contracts.ALLOWED_CHECKPOINT_TYPES == PINNED_ALLOWED_CHECKPOINT_TYPES


def test_every_checkpoint_type_is_a_dataclass_in_the_contract_module() -> None:
    for cls in contracts.CHECKPOINT_TYPES:
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__module__ == "agon.fleet.contracts"


def test_every_message_field_is_json_native() -> None:
    """A field of any other type (datetime, bytes, a governance object) would grow the allow-list
    and the checkpoint; this fails at authoring time instead of at a resume."""
    bad = [
        f"{cls.__name__}.{f.name}: {f.type}"
        for cls in contracts.CHECKPOINT_TYPES
        for f in dataclasses.fields(cls)
        if f.type not in JSON_NATIVE_ANNOTATIONS
    ]
    assert bad == []


def test_bounded_retry_is_one_retry() -> None:
    assert contracts.MAX_WRITER_ATTEMPTS == 2


def test_fleet_and_agents_import_without_agent_framework() -> None:
    """The gates and steps must stay testable on a machine without Agent Framework."""
    code = (
        "import sys\n"
        "for name in ('agent_framework', 'agent_framework_azure_cosmos', 'azure', 'azure.identity'):\n"
        "    sys.modules[name] = None\n"
        "import agon.fleet, agon.fleet.contracts, agon.fleet.steps, agon.fleet.local, agon.fleet.stores\n"
        "import agon.agents.foundry, agon.agents.translator, agon.agents.speech, agon.agents.fakes\n"
        "print('ok')\n"
    )
    out = subprocess.run([PYTHON, "-c", code], capture_output=True, text=True, env=subprocess_env(), timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
