"""ci/flows_check.py: the approval Logic Apps stay in contract with the decision intake.

The real infra/ must pass untouched; each mutation breaks one rule on a copy and must be caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import flows_check
from agon import intake
from agon.governance.channels import outlook

INFRA = Path(__file__).resolve().parent.parent / "infra"
ROUTES = set(intake.ROUTES)
OPTIONS = ", ".join(outlook.OPTIONS)


def _sources() -> dict[str, str]:
    return {str(p.relative_to(INFRA)).replace("\\", "/"): p.read_text(encoding="utf-8") for p in sorted(INFRA.rglob("*.bicep"))}


def test_the_real_infra_passes():
    assert flows_check.check(_sources(), ROUTES, OPTIONS) == []
    assert flows_check.main([]) == 0


def test_the_flows_send_to_exactly_the_intake_queues():
    sent = set(flows_check._QUEUE_SEND.findall(_sources()["flows.bicep"]))
    assert sent == ROUTES == {"decisions-teams", "decisions-outlook"}


MUTATIONS = [
    ("decisions-outlook/messages", "decisions-email/messages", "but the intake routes are"),
    ("responder: '@body(\\'Post_card_and_wait\\')?[\\'responder\\']'", "responder: '@triggerBody()?[\\'responder\\']'", "no longer forwards responder"),
    ("data: '@body(\\'Post_card_and_wait\\')?[\\'data\\']'", "data: '@triggerBody()'", "no longer forwards data"),
    ("Options: 'Approve and confirm flagged sentences, Approve, Request changes'", "Options: 'Approve, Reject'", "the Outlook flow offers"),
    ("    accessControl: accessControl\n", "", "Entra caller policy"),
    ("    accessControl: postAccessControl\n", "", "Entra caller policy"),
    ("param teamsGroupId string\n", "param teamsGroupId string = '00000000-1111-2222-3333-444444444444'\n", "a GUID literal is committed"),
    ("param teamsChannelId string\n", "param teamsChannelId string = '19:abcdef0123@thread.tacv2'\n", "a Teams thread id is committed"),
    ("resource teamsConnection 'Microsoft.Web/connections@2016-06-01' existing = {", "resource teamsConnection 'Microsoft.Web/connections@2016-06-01' = {", "declares the agon-teams connection"),
]


@pytest.mark.parametrize(("old", "new", "problem"), MUTATIONS, ids=[m[2] for m in MUTATIONS])
def test_each_rule_fails_on_a_broken_copy(old, new, problem):
    sources = _sources()
    text = sources["flows.bicep"]
    assert old in text, f"mutation anchor {old!r} went stale"
    sources["flows.bicep"] = text.replace(old, new, 1)
    problems = flows_check.check(sources, ROUTES, OPTIONS)
    assert any(problem in p for p in problems), problems


def test_a_route_without_a_flow_fails():
    problems = flows_check.check(_sources(), ROUTES | {"decisions-sms"}, OPTIONS)
    assert any("but the intake routes are" in p for p in problems)


def test_missing_flows_file_fails():
    assert flows_check.check({}, ROUTES, OPTIONS) == ["flows.bicep not found"]
