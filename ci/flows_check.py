"""Contract between the approval Logic Apps (infra/flows.bicep) and the decision intake.

Static checks on the Bicep source, standard library only, so a drift fails at pull request time
instead of as a silent refusal in Azure:

* every queue the flows send to is a route in `agon.intake.ROUTES`, and every route has a flow;
* the Teams flow forwards the card's `responder` and `data` unchanged (what channels.teams reads);
* the Outlook flow offers exactly `channels.outlook.OPTIONS`, in that order;
* the gate T connection `agon-teams` is only ever referenced with `existing` anywhere in infra/,
  so no deploy can reset the owner's consent on it;
* no team, channel, tenant or object id is committed in the flow files (no GUID, no thread id);
* both flow triggers carry the Entra caller policy (accessControl).

Usage: python ci/flows_check.py [--infra DIR]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"
FLOWS = "flows.bicep"
# The files that configure Teams and Outlook. Built-in role definition GUIDs in rbac.bicep and
# cosmos-access.bicep are public constants, not ids of the owner's tenant.
ID_FREE = frozenset({"flows.bicep", "connections.bicep"})

_QUEUE_SEND = re.compile(r"\$\{queueBase\}([a-z0-9-]+)/messages")
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_THREAD = re.compile(r"19:[A-Za-z0-9_-]+@thread", re.IGNORECASE)
_CONNECTION_DECL = re.compile(r"resource\s+\w+\s+'Microsoft\.Web/connections@[^']+'\s+(existing\s+)?=\s*\{(.*?)\n\}", re.DOTALL)
TEAMS_FORWARDS = (
    "responder: '@body(\\'Post_card_and_wait\\')?[\\'responder\\']'",
    "data: '@body(\\'Post_card_and_wait\\')?[\\'data\\']'",
)


def _routes() -> set[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from agon import intake

    return set(intake.ROUTES)


def _outlook_options() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from agon.governance.channels import outlook

    return ", ".join(outlook.OPTIONS)


def check(sources: dict[str, str], routes: set[str], outlook_options: str) -> list[str]:
    """`sources` maps a path under infra/ to its text. Returns the problems found."""
    problems: list[str] = []
    flows = sources.get(FLOWS)
    if flows is None:
        return [f"{FLOWS} not found"]
    sent = set(_QUEUE_SEND.findall(flows))
    if sent != routes:
        problems.append(f"flows send to {sorted(sent)} but the intake routes are {sorted(routes)}")
    for needle in TEAMS_FORWARDS:
        if needle not in flows:
            problems.append(f"the Teams flow no longer forwards {needle.split(':')[0]} unchanged from Post_card_and_wait")
    options = re.findall(r"Options:\s*'([^']*)'", flows)
    if options != [outlook_options]:
        problems.append(f"the Outlook flow offers {options}, the adapter accepts [{outlook_options!r}]")
    if flows.count("accessControl: accessControl") != 2 or "openAuthenticationPolicies" not in flows:
        problems.append("both flow triggers must carry the Entra caller policy (accessControl)")
    for name, text in sorted(sources.items()):
        if name in ID_FREE and _GUID.search(text):
            problems.append(f"{name}: a GUID literal is committed; team, channel, tenant and object ids are parameters")
        if name in ID_FREE and _THREAD.search(text):
            problems.append(f"{name}: a Teams thread id is committed; channel ids are parameters")
        for match in _CONNECTION_DECL.finditer(text):
            if "name: 'agon-teams'" in match.group(2) and not match.group(1):
                problems.append(f"{name}: declares the agon-teams connection; reference it with existing only")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--infra", type=Path, default=INFRA)
    args = parser.parse_args(argv)
    sources = {str(p.relative_to(args.infra)).replace("\\", "/"): p.read_text(encoding="utf-8") for p in sorted(args.infra.rglob("*.bicep"))}
    problems = check(sources, _routes(), _outlook_options())
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"bicep files scanned: {len(sources)}; problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
