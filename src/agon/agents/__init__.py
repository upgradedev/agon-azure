"""Agon agents: the model, translation and speech ports the full-time workflow calls.

Every model-backed step reaches a model only through `ports.ModelPort`; translation only through
`ports.TranslatorPort`; speech only through `ports.SpeechPort`. Three kinds of implementation sit
behind each port:

* Microsoft Foundry, Azure AI Translator and Azure AI Speech adapters (`foundry`, `translator`,
  `speech`). They import their Azure packages lazily, so this package imports with the standard
  library alone.
* Deterministic fakes (`fakes`) for tests. A bundle made with them says so in its manifest
  (`deployment_ids["ports"] == "fake"`), so a fake run can never pass for a real one.

Prompts are versioned files under `prompts/`, each pinned by sha256 in `prompts/manifest.json`.
"""
