"""Microsoft Foundry behind `ModelPort`: prompt agents for analyst, writer and localiser-editor;
structured-output calls for triage and desk-editor.

Built against agent-framework-foundry 1.15.0 (read at tag python-1.22.0 of microsoft/agent-framework):

* `FoundryAgent(project_endpoint=, agent_name=, agent_version=, credential=)` connects to a
  versioned PromptAgent that lives in the Foundry project (``_agent.py``). Its instructions and
  output format are the ones published from our prompt file, so a model call sends only data.
* `FoundryChatClient(project_endpoint=, model=, credential=)` with an `Agent` carrying the
  prompt as instructions, and ``options={"response_format": <json_schema envelope>}``, for the
  two structured roles (``_chat_client.py``; the envelope is converted by
  ``agent_framework_openai._chat_client._convert_response_format``).
* `to_prompt_agent(agent)` plus ``AIProjectClient.agents.create_version(...)`` publishes a prompt
  file as a new agent version (foundry README, "Publishing an agent as a Foundry prompt agent").
  The version description carries the prompt's sha256, so the deployed agent names its source.
  `agon.agents.publish` finds that version, or creates it when none exists.

Prompt provenance (why the bundle's prompt hashes are true): for the three prompt-agent roles the
instructions that run live in the Foundry agent version, not in the local file. So the worker
calls `FoundryModelPort.verify_deployed` at startup. It reads each configured version with
``AIProjectClient.agents.get_version(agent_name=, agent_version=)`` (azure-ai-projects 2.7.0,
returns ``AgentVersionDetails`` whose ``definition`` is a ``PromptAgentDefinition`` with
``instructions: Optional[str]``) and compares the sha256 of the deployed instructions with the
local prompt pinned in the manifest. Any mismatch, missing instructions or wrong version fails
closed, and until verification has passed `complete` refuses every prompt-agent role. The
verified hashes are what `deployment_ids()` hands to the seal as ``prompt.<role>``.

Live since 2026-10-19: the deployed fleet job runs this port. Each recorded run's manifest in
web/runs/run-*/manifest.json names ports "foundry", the three agent versions and their verified
prompt hashes. Every Azure import is lazy, so this module imports with the standard library alone."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from agon.agents.ports import PROMPT_AGENT_ROLES, ModelUnavailable, Prompt
from agon.agents.prompts import load_prompt

DEFAULT_AGENT_NAMES: dict[str, str] = {
    "analyst": "agon-analyst",
    "writer": "agon-writer",
    "localiser_editor": "agon-localiser-editor",
}


@dataclass(frozen=True)
class FoundrySettings:
    project_endpoint: str
    agent_versions: dict[str, str]
    structured_model: str
    agent_names: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_AGENT_NAMES))

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "FoundrySettings":
        """AGON_FOUNDRY_PROJECT_ENDPOINT, AGON_FOUNDRY_AGENT_VERSIONS (JSON role to version),
        AGON_FOUNDRY_STRUCTURED_MODEL (deployment name for triage and desk-editor)."""
        env = dict(os.environ if env is None else env)
        missing = [k for k in ("AGON_FOUNDRY_PROJECT_ENDPOINT", "AGON_FOUNDRY_AGENT_VERSIONS", "AGON_FOUNDRY_STRUCTURED_MODEL") if not env.get(k)]
        if missing:
            raise ValueError(f"missing Foundry settings: {missing}")
        versions = json.loads(env["AGON_FOUNDRY_AGENT_VERSIONS"])
        if not isinstance(versions, dict) or set(versions) != set(PROMPT_AGENT_ROLES):
            raise ValueError(f"AGON_FOUNDRY_AGENT_VERSIONS must map exactly {list(PROMPT_AGENT_ROLES)}")
        return cls(
            project_endpoint=env["AGON_FOUNDRY_PROJECT_ENDPOINT"],
            agent_versions={k: str(v) for k, v in versions.items()},
            structured_model=env["AGON_FOUNDRY_STRUCTURED_MODEL"],
        )

    def deployment_ids(self) -> dict[str, str]:
        ids = {f"agent.{role}": f"{self.agent_names[role]}:{self.agent_versions[role]}" for role in PROMPT_AGENT_ROLES}
        ids["model.structured"] = self.structured_model
        ids["ports"] = "foundry"
        return ids


class PromptProvenanceError(RuntimeError):
    """A deployed prompt agent does not run the prompt file the manifest pins."""


def _field(obj: Any, name: str) -> Any:
    """Read a model attribute or a mapping key (the SDK models support both)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


class FoundryModelPort:
    """`ModelPort` over Microsoft Foundry. One client per role, created on first use.

    Prompt-agent roles are refused until `verify_deployed` has proved that every configured
    agent version runs the local prompt bytes.
    """

    def __init__(self, settings: FoundrySettings, credential: Any) -> None:
        self.settings = settings
        self._credential = credential
        self._agents: dict[str, Any] = {}
        self._verified: dict[str, str] = {}

    @property
    def verified_prompts(self) -> dict[str, str]:
        return dict(self._verified)

    async def verify_deployed(self, project_client: Any = None) -> dict[str, str]:
        """Compare each deployed agent version's instructions with the pinned prompt. All or nothing.

        Returns role to the verified sha256. Raises `PromptProvenanceError` naming every problem;
        on any problem nothing is marked verified.
        """
        owned = None
        if project_client is None:
            from azure.ai.projects.aio import AIProjectClient

            project_client = owned = AIProjectClient(endpoint=self.settings.project_endpoint, credential=self._credential)
        problems: list[str] = []
        verified: dict[str, str] = {}
        try:
            for role in PROMPT_AGENT_ROLES:
                name = self.settings.agent_names[role]
                version = self.settings.agent_versions[role]
                prompt = load_prompt(role)
                try:
                    details = await project_client.agents.get_version(agent_name=name, agent_version=version)
                except Exception as exc:  # noqa: BLE001 - an unreadable version is unverified
                    problems.append(f"{role}: cannot read {name}:{version} ({type(exc).__name__}: {exc})")
                    continue
                deployed_version = _field(details, "version")
                if str(deployed_version) != version:
                    problems.append(f"{role}: asked for version {version}, Foundry returned {deployed_version}")
                    continue
                instructions = _field(_field(details, "definition"), "instructions")
                if not isinstance(instructions, str) or not instructions:
                    problems.append(f"{role}: {name}:{version} has no readable instructions")
                    continue
                deployed_sha = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
                if deployed_sha != prompt.sha256:
                    problems.append(
                        f"{role}: {name}:{version} instructions hash to {deployed_sha}, "
                        f"the manifest pins {prompt.sha256}"
                    )
                    continue
                verified[role] = deployed_sha
        finally:
            if owned is not None:
                await owned.close()
        if problems:
            raise PromptProvenanceError("; ".join(problems))
        self._verified = verified
        return dict(verified)

    def deployment_ids(self) -> dict[str, str]:
        """What the seal records for this port: agent versions plus the VERIFIED prompt hashes."""
        if set(self._verified) != set(PROMPT_AGENT_ROLES):
            raise PromptProvenanceError("prompt provenance not verified; call verify_deployed first")
        ids = self.settings.deployment_ids()
        ids.update({f"prompt.{role}": sha for role, sha in sorted(self._verified.items())})
        return ids

    def _agent(self, role: str, prompt: Prompt) -> Any:
        if role in self._agents:
            return self._agents[role]
        from agent_framework.foundry import FoundryAgent, FoundryChatClient

        agent: Any
        if role in PROMPT_AGENT_ROLES:
            agent = FoundryAgent(
                project_endpoint=self.settings.project_endpoint,
                agent_name=self.settings.agent_names[role],
                agent_version=self.settings.agent_versions[role],
                credential=self._credential,
            )
        else:
            from agent_framework import Agent

            agent = Agent(
                client=FoundryChatClient(
                    project_endpoint=self.settings.project_endpoint,
                    model=self.settings.structured_model,
                    credential=self._credential,
                ),
                name=f"agon-{role.replace('_', '-')}",
                instructions=prompt.text,
            )
        self._agents[role] = agent
        return agent

    async def complete(self, role: str, prompt: Prompt, payload: dict[str, Any], schema: dict[str, Any]) -> str:
        if role in PROMPT_AGENT_ROLES and role not in self._verified:
            raise ModelUnavailable(f"Foundry {role}: prompt provenance not verified; the deployed agent is not used")
        message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            agent = self._agent(role, prompt)
            if role in PROMPT_AGENT_ROLES:
                response = await agent.run(message)
            else:
                response = await agent.run(message, options={"response_format": schema})
        except Exception as exc:  # noqa: BLE001 - any client failure is an outage for the card
            raise ModelUnavailable(f"Foundry {role}: {type(exc).__name__}: {exc}") from exc
        text = response.text
        if not text or not text.strip():
            raise ModelUnavailable(f"Foundry {role}: empty answer")
        return text
