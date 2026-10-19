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

Not yet exercised against a live Foundry project: that is gate C2. Every Azure import is lazy, so
this module imports with the standard library alone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from agon.agents.ports import PROMPT_AGENT_ROLES, ModelUnavailable, Prompt
from agon.agents.prompts import load_prompt
from agon.agents.schemas import SCHEMAS

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


class FoundryModelPort:
    """`ModelPort` over Microsoft Foundry. One client per role, created on first use."""

    def __init__(self, settings: FoundrySettings, credential: Any) -> None:
        self.settings = settings
        self._credential = credential
        self._agents: dict[str, Any] = {}

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


async def publish_prompt_agents(settings_endpoint: str, credential: Any, models: dict[str, str]) -> dict[str, str]:
    """Publish each prompt file as a new Foundry prompt agent version. Returns role to version.

    `models` maps each prompt-agent role to its model deployment name (gate C2 records them).
    Run by an operator or a main-only CI job holding the Foundry role; never by the worker.
    """
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient, to_prompt_agent
    from azure.ai.projects.aio import AIProjectClient

    if set(models) != set(PROMPT_AGENT_ROLES):
        raise ValueError(f"models must map exactly {list(PROMPT_AGENT_ROLES)}")
    versions: dict[str, str] = {}
    project = AIProjectClient(endpoint=settings_endpoint, credential=credential)
    try:
        for role in PROMPT_AGENT_ROLES:
            prompt = load_prompt(role)
            agent = Agent(
                client=FoundryChatClient(project_endpoint=settings_endpoint, model=models[role], credential=credential),
                name=DEFAULT_AGENT_NAMES[role],
                instructions=prompt.text,
                default_options={"response_format": SCHEMAS[role]},
            )
            created = await project.agents.create_version(
                agent_name=DEFAULT_AGENT_NAMES[role],
                definition=to_prompt_agent(agent),
                description=f"Agon {role} prompt {prompt.version}, sha256 {prompt.sha256}",
            )
            versions[role] = str(created.version)
    finally:
        await project.close()
    return versions
