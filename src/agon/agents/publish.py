"""Find or create the Foundry prompt agent version for each pinned prompt: `python -m agon.agents.publish`.

For each prompt-agent role (analyst, writer, localiser_editor) this lists the versions of the
agent (``AIProjectClient.agents.list_versions``, azure-ai-projects 2.7.0) and picks the newest one
whose description carries the pinned prompt's sha256 and whose definition names the wanted model
deployment. When none does, it creates one from the pinned prompt file: an Agent Framework
`Agent` over `FoundryChatClient` with the prompt as instructions, turned into a
`PromptAgentDefinition` by `to_prompt_agent` and published with ``agents.create_version`` (the
path the agon.agents.foundry docstring names). Each role prints one JSON line: role, name,
version, model, prompt sha256 and whether the version was found or created.

Afterwards `FoundryModelPort.verify_deployed` reads every chosen version back and compares the
sha256 of its instructions with the local prompt bytes. That check is never skipped: a version
whose description names the right hash but whose instructions differ fails closed (exit 3).

Runs as id-fleet (Foundry User on the project), either alone or at worker startup with
``python -m agon.fleet.worker --ensure-agents``.

Configuration: AGON_FOUNDRY_PROJECT_ENDPOINT; AGON_FOUNDRY_AGENT_MODELS (JSON, role to model
deployment, for example {"analyst": "gpt-5.6-luna", "writer": "gpt-5.6-terra",
"localiser_editor": "gpt-5.6-luna"}); AGON_FOUNDRY_STRUCTURED_MODEL (only to build the port for
the verification); AZURE_CLIENT_ID (the user-assigned identity, empty in CI).

Exit codes: 0 found or created and verified; 2 bad configuration; 3 provenance not verified or a
version could not be found (with --no-create) or created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from agon.agents.foundry import DEFAULT_AGENT_NAMES, PromptProvenanceError
from agon.agents.ports import PROMPT_AGENT_ROLES, Prompt
from agon.agents.prompts import load_prompt
from agon.agents.schemas import SCHEMAS


class PublishError(RuntimeError):
    """A prompt agent version could not be found or created."""


@dataclass(frozen=True)
class AgentRef:
    role: str
    name: str
    version: str
    model: str
    prompt_sha256: str
    action: str  # "found" or "created"


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def description_for(role: str, prompt: Prompt, model: str) -> str:
    return f"Agon {role} prompt {prompt.version}, sha256 {prompt.sha256}, model {model}"


def carries(details: Any, prompt: Prompt, model: str) -> bool:
    """The version names the pinned prompt hash in its description and runs the wanted model."""
    description = _field(details, "description")
    if not isinstance(description, str) or prompt.sha256 not in description:
        return False
    deployed_model = _field(_field(details, "definition"), "model")
    return deployed_model is None or deployed_model == model


def _version_key(details: Any) -> tuple[int, str]:
    version = str(_field(details, "version"))
    return (int(version), version) if version.isdigit() else (-1, version)


async def list_versions(project: Any, name: str) -> list[Any]:
    """Every version of one agent; an agent that does not exist yet has none."""
    try:
        return [details async for details in project.agents.list_versions(agent_name=name)]
    except Exception as exc:
        if _status(exc) == 404:
            return []
        raise


def prompt_agent_definition(endpoint: str, credential: Any, role: str, prompt: Prompt, model: str) -> Any:
    """The PromptAgentDefinition for one pinned prompt (agent-framework-foundry 1.15.0)."""
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient, to_prompt_agent

    agent = Agent(
        client=FoundryChatClient(project_endpoint=endpoint, model=model, credential=credential),
        name=DEFAULT_AGENT_NAMES[role],
        instructions=prompt.text,
        default_options={"response_format": SCHEMAS[role]},
    )
    return to_prompt_agent(agent)


async def ensure_prompt_agents(
    project: Any,
    endpoint: str,
    credential: Any,
    models: Mapping[str, str],
    *,
    create: bool = True,
    definition: Callable[[str, Any, str, Prompt, str], Any] = prompt_agent_definition,
) -> list[AgentRef]:
    """One AgentRef per prompt-agent role, found or created. Raises PublishError."""
    if set(models) != set(PROMPT_AGENT_ROLES):
        raise PublishError(f"models must map exactly {list(PROMPT_AGENT_ROLES)}")
    refs: list[AgentRef] = []
    for role in PROMPT_AGENT_ROLES:
        prompt = load_prompt(role)
        name = DEFAULT_AGENT_NAMES[role]
        model = models[role]
        found = sorted((d for d in await list_versions(project, name) if carries(d, prompt, model)), key=_version_key)
        if found:
            refs.append(AgentRef(role, name, str(_field(found[-1], "version")), model, prompt.sha256, "found"))
            continue
        if not create:
            raise PublishError(f"{role}: no version of {name} carries prompt sha256 {prompt.sha256} on model {model}")
        try:
            created = await project.agents.create_version(
                agent_name=name,
                definition=definition(endpoint, credential, role, prompt, model),
                description=description_for(role, prompt, model),
                metadata={"agon_role": role, "agon_prompt_sha256": prompt.sha256, "agon_model": model},
            )
        except Exception as exc:  # noqa: BLE001 - the reason is reported, the run stops
            raise PublishError(f"{role}: create_version for {name} failed ({type(exc).__name__}: {exc})") from exc
        version = _field(created, "version")
        if version is None:
            raise PublishError(f"{role}: Foundry returned no version for {name}")
        refs.append(AgentRef(role, name, str(version), model, prompt.sha256, "created"))
    return refs


async def _run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    from azure.ai.projects.aio import AIProjectClient

    from agon.agents.foundry import FoundryModelPort, FoundrySettings
    from agon.fleet.azure_ports import async_credential

    endpoint = env["AGON_FOUNDRY_PROJECT_ENDPOINT"]
    models = json.loads(env["AGON_FOUNDRY_AGENT_MODELS"])
    credential = async_credential(env.get("AZURE_CLIENT_ID") or None)
    try:
        project = AIProjectClient(endpoint=endpoint, credential=credential)
        try:
            refs = await ensure_prompt_agents(project, endpoint, credential, models, create=not args.no_create)
        finally:
            await project.close()
        for ref in refs:
            print(json.dumps(asdict(ref), sort_keys=True), flush=True)
        settings = FoundrySettings(
            project_endpoint=endpoint,
            agent_versions={ref.role: ref.version for ref in refs},
            structured_model=env.get("AGON_FOUNDRY_STRUCTURED_MODEL") or models["analyst"],
        )
        verified = await FoundryModelPort(settings, credential).verify_deployed()
        print(json.dumps({"verified": verified}, sort_keys=True), flush=True)
        return 0
    except (PublishError, PromptProvenanceError) as exc:
        print(json.dumps({"refused": type(exc).__name__, "detail": str(exc)[:600]}, sort_keys=True), flush=True)
        return 3
    finally:
        await credential.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find or create the Foundry prompt agent versions for the pinned prompts.")
    parser.add_argument("--no-create", action="store_true", help="only find; fail if a role has no matching version")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    missing = [k for k in ("AGON_FOUNDRY_PROJECT_ENDPOINT", "AGON_FOUNDRY_AGENT_MODELS") if not env.get(k)]
    if missing:
        print(f"agon.agents.publish: missing settings {missing}", file=sys.stderr)
        return 2
    try:
        models = json.loads(env["AGON_FOUNDRY_AGENT_MODELS"])
    except ValueError:
        models = None
    if not isinstance(models, dict) or set(models) != set(PROMPT_AGENT_ROLES):
        print(f"agon.agents.publish: AGON_FOUNDRY_AGENT_MODELS must map exactly {list(PROMPT_AGENT_ROLES)}", file=sys.stderr)
        return 2
    return asyncio.run(_run(args, env))


if __name__ == "__main__":
    sys.exit(main())
