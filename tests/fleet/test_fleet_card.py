"""The approval card payloads against the Logic App trigger schemas in infra/flows.bicep, offline.

The trigger schemas are parsed from the Bicep source, so a key added to or dropped from a flow
fails here at pull request time instead of as a silently empty card in Teams.

On secrets: the nonce IS on the card, by design (spec 7.2). It is the single-use capability the
officer's tap sends back and the verifier consumes once; it is not a credential to anything else.
What a payload must never carry is a bearer token, a SAS signature or a key, and the poster must
never call a trigger URL that carries SAS parameters.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path

import pytest
from fleet_reference import run_to_request
from fleet_support import OFFICER, REPO, run, run_input

from agon.fleet import card
from agon.fleet.local import fake_deps
from agon.governance import verifier

FLOWS = REPO / "infra" / "flows.bicep"
CONTEXT = card.CardContext(
    feed_base_url="https://agonexample.blob.core.windows.net/feed/",
    sandbox_base_url="https://agonexample.blob.core.windows.net/sandbox/",
    site_url="https://agon-example.azurestaticapps.net/",
)
FLOW_URL = "https://prod-00.swedencentral.logic.azure.com/workflows/abc123/triggers/manual/paths/invoke?api-version=2016-10-01"


# ----------------------------------------------------------------- the Bicep trigger schemas


def trigger_schema(text: str, resource: str) -> tuple[set[str], set[str]]:
    """(properties, required) of the HTTP trigger schema of one Logic App resource in flows.bicep."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"resource {resource} "))
    schema = next(i for i in range(start, len(lines)) if lines[i].strip() == "schema: {")
    indent = len(lines[schema]) - len(lines[schema].lstrip())
    required: set[str] = set()
    properties: set[str] = set()
    section = None
    prop_indent = None
    for line in lines[schema + 1:]:
        stripped = line.strip()
        depth = len(line) - len(line.lstrip())
        if depth <= indent and stripped:
            break
        if depth == indent + 2 and stripped == "required: [":
            section = "required"
            continue
        if depth == indent + 2 and stripped == "properties: {":
            section, prop_indent = "properties", depth + 2
            continue
        if section == "required":
            if stripped == "]":
                section = None
            else:
                required.add(stripped.strip("'"))
        elif section == "properties" and depth == prop_indent:
            match = re.match(r"^(\w+): \{$", stripped)
            if match:
                properties.add(match.group(1))
    if not properties or not required:
        raise AssertionError(f"no trigger schema parsed for {resource}")
    return properties, required


def schema_problems(keys: set[str], required_keys: set[str], schema: tuple[set[str], set[str]]) -> list[str]:
    properties, required = schema
    problems = []
    if keys != properties:
        problems.append(f"payload keys {sorted(keys ^ properties)} differ from the trigger properties")
    if required_keys != required:
        problems.append(f"required keys {sorted(required_keys ^ required)} differ from the trigger schema")
    return problems


def test_teams_keys_match_the_flows_bicep_trigger_schema():
    schema = trigger_schema(FLOWS.read_text(encoding="utf-8"), "teamsFlow")
    assert schema_problems(set(card.TEAMS_KEYS), set(card.TEAMS_REQUIRED), schema) == []


def test_outlook_keys_match_the_flows_bicep_trigger_schema():
    schema = trigger_schema(FLOWS.read_text(encoding="utf-8"), "outlookFlow")
    assert schema_problems(set(card.OUTLOOK_KEYS), set(card.OUTLOOK_REQUIRED), schema) == []


def test_negative_control_a_mutated_schema_is_caught():
    text = FLOWS.read_text(encoding="utf-8")
    dropped = text.replace("                match_line: {\n                  type: 'string'\n                }\n", "", 1)
    assert dropped != text, "the mutation must hit the Teams trigger schema"
    assert schema_problems(set(card.TEAMS_KEYS), set(card.TEAMS_REQUIRED), trigger_schema(dropped, "teamsFlow"))
    renamed = text.replace("'published_url'\n", "'published_link'\n", 1)
    assert renamed != text
    assert schema_problems(set(card.TEAMS_KEYS), set(card.TEAMS_REQUIRED), trigger_schema(renamed, "teamsFlow"))


# ----------------------------------------------------------------- a real payload from a fake run


@pytest.fixture(scope="module")
def sealed_run():
    deps = fake_deps()
    verifier.grant_role(deps.store, OFFICER, [verifier.APPROVER_ROLE])
    ri = run_input()
    ref = run(run_to_request(deps, ri))
    assert ref.request is not None
    inputs = card.load_inputs(deps.store, deps.artifacts, deps.matches.load(ri.match_ref), ref.request.bundle_sha256)
    return deps, ri, ref, inputs


def test_the_teams_payload_is_filled_from_the_sealed_bundle(sealed_run):
    deps, ri, ref, inputs = sealed_run
    payload = card.teams_payload(ref.request, inputs, ri.match_ref, CONTEXT)
    properties, required = trigger_schema(FLOWS.read_text(encoding="utf-8"), "teamsFlow")
    assert required <= set(payload) <= properties
    record = deps.matches.load(ri.match_ref)
    home = record.roster.clubs[record.meta["home"]]
    away = record.roster.clubs[record.meta["away"]]
    score = record.meta["score"]
    assert f"{home} {score['home']}-{score['away']} {away}" in payload["title"]
    assert record.roster.league_name in payload["match_line"] and "synthetic match" in payload["match_line"]
    assert payload["bundle_sha256"] == ref.request.bundle_sha256 and payload["nonce"] == ref.request.nonce
    assert payload["expires_at"] == ref.request.expiry
    assert payload["editions"] == ["English", "Greek", "Spanish", "French", "English easy read"]
    english = {row["sentence_id"]: row["text"] for row in inputs.report_en["sentences"]}
    assert [row["id"] for row in payload["confirm_rows"]] == list(ref.request.confirm_rows)
    assert all(row["text"] == english[row["id"]] for row in payload["confirm_rows"])
    assert payload["published_url"] == f"{CONTEXT.feed_base_url}{ref.request.bundle_sha256}/manifest.json"
    assert payload["destinations"] == []
    assert len(json.dumps(payload).encode("utf-8")) < card.MAX_PAYLOAD_BYTES


def test_the_outlook_payload_matches_its_schema(sealed_run):
    _, ri, ref, inputs = sealed_run
    payload = card.outlook_payload(ref.request, inputs, ri.match_ref, CONTEXT)
    properties, required = trigger_schema(FLOWS.read_text(encoding="utf-8"), "outlookFlow")
    assert required <= set(payload) <= properties
    assert payload["confirm_rows"] and all(set(r) == {"id", "text"} for r in payload["confirm_rows"])


def test_no_payload_carries_a_secret(sealed_run):
    _, ri, ref, inputs = sealed_run
    for payload in (card.teams_payload(ref.request, inputs, ri.match_ref, CONTEXT), card.outlook_payload(ref.request, inputs, ri.match_ref, CONTEXT)):
        assert card.secret_problems(payload) == []
        text = json.dumps(payload)
        assert "Bearer" not in text and "sig=" not in text and "sv=" not in text and "sp=" not in text
        assert text.count(ref.request.nonce) == 1, "the nonce appears once, in its own field"


def test_negative_control_secret_shaped_values_are_refused(sealed_run):
    _, ri, ref, inputs = sealed_run
    good = card.teams_payload(ref.request, inputs, ri.match_ref, CONTEXT)
    for bad in (
        {**good, "evidence_url": FLOW_URL + "&sp=%2Ftriggers&sv=1.0&sig=abc"},
        {**good, "title": "Bearer abc.def"},
        {**good, "match_line": "eyJhbGciOiJSUzI1NiJ9.eyJhdWQiOiJ4In0.sig"},
    ):
        with pytest.raises(card.CardError):
            card.check_payload(bad, card.TEAMS_KEYS, card.TEAMS_REQUIRED)
    with pytest.raises(card.CardError, match="unknown"):
        card.check_payload({**good, "authorization": "x"}, card.TEAMS_KEYS, card.TEAMS_REQUIRED)
    missing = dict(good)
    del missing["published_url"]
    with pytest.raises(card.CardError, match="missing"):
        card.check_payload(missing, card.TEAMS_KEYS, card.TEAMS_REQUIRED)


def test_load_inputs_refuses_another_match_and_a_tampered_manifest(sealed_run):
    deps, ri, ref, _ = sealed_run
    other = deps.matches.load({"source": "synth", "seed": 2, "match_index": 0})
    with pytest.raises(card.CardError, match="not the one this bundle sealed"):
        card.load_inputs(deps.store, deps.artifacts, other, ref.request.bundle_sha256)
    with pytest.raises(card.CardError, match="not registered"):
        card.load_inputs(deps.store, deps.artifacts, deps.matches.load(ri.match_ref), "0" * 64)


def test_a_long_confirm_row_is_shortened_to_fit(sealed_run):
    deps, ri, ref, inputs = sealed_run
    long_report = {**inputs.report_en, "sentences": [{**s, "text": "x" * 30000} for s in inputs.report_en["sentences"]]}
    payload = card.teams_payload(ref.request, card.CardInputs(inputs.manifest, inputs.record, long_report), ri.match_ref, CONTEXT)
    assert len(json.dumps(payload).encode("utf-8")) <= card.MAX_PAYLOAD_BYTES
    assert all(row["text"].endswith("...") for row in payload["confirm_rows"])


# ----------------------------------------------------------------- the trigger URL and the poster


def test_the_flow_url_must_be_the_base_path_with_api_version_only():
    assert card.validate_flow_url(FLOW_URL) == FLOW_URL
    for bad in (
        FLOW_URL + "&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=abc",
        FLOW_URL.replace("?api-version=2016-10-01", ""),
        FLOW_URL + "&extra=1",
        FLOW_URL.replace("https://", "http://"),
    ):
        with pytest.raises(ValueError):
            card.validate_flow_url(bad)


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def getcode(self):
        return self.status


def test_the_poster_sends_a_bearer_token_to_the_base_url():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return _Response(202)

    poster = card.FlowPoster("teams", FLOW_URL, lambda: "token-value", opener=opener)
    assert poster.post({"run_id": "r"}) == 202
    request = seen[0]
    assert request.full_url == FLOW_URL and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer token-value"
    assert json.loads(request.data) == {"run_id": "r"}


def test_a_refused_post_names_the_status_and_nothing_else():
    def opener(request, timeout):
        raise urllib.error.HTTPError(FLOW_URL, 401, "Unauthorized", {}, io.BytesIO(b"details"))

    poster = card.FlowPoster("teams", FLOW_URL, lambda: "token-value", opener=opener)
    with pytest.raises(card.FlowPostError) as caught:
        poster.post({"run_id": "r"})
    message = str(caught.value)
    assert "401" in message and "token-value" not in message and "abc123" not in message


def test_the_poster_refuses_a_sas_url_at_construction():
    with pytest.raises(ValueError, match="SAS"):
        card.FlowPoster("teams", FLOW_URL + "&sig=abc", lambda: "t")


def test_url_fingerprint_hides_the_path():
    assert "abc123" not in card.url_fingerprint(FLOW_URL)


def test_offered_destinations_become_card_toggles_in_every_shape(sealed_run):
    """Lane C6 offers destinations on the request or in the manifest; the card renders {id, label}."""
    import dataclasses

    _, ri, ref, inputs = sealed_run
    as_list = {**inputs.manifest, "destinations": ["club_site", {"id": "feeds", "label": "RSS and JSON Feed"}]}
    as_table = {**inputs.manifest, "destinations": {"feeds": {"artifact": "destination-feeds.json"}, "club_site": {}}}
    for manifest, expected in (
        (as_list, [{"id": "club_site", "label": "Club site"}, {"id": "feeds", "label": "RSS and JSON Feed"}]),
        (as_table, [{"id": "club_site", "label": "Club site"}, {"id": "feeds", "label": "Feeds"}]),
    ):
        payload = card.teams_payload(ref.request, card.CardInputs(manifest, inputs.record, inputs.report_en), ri.match_ref, CONTEXT)
        assert payload["destinations"] == expected

    @dataclasses.dataclass
    class WithDestinations:
        destinations: list

    assert card._destinations(WithDestinations([{"id": "youtube", "label": "YouTube"}]), as_list) == [{"id": "youtube", "label": "YouTube"}]
