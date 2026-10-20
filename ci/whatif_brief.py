"""Analyst briefs for the What-If scenario set: one model call per scenario, every number checked by code.

Runs only on main with the CI identity (workflow whatif-brief.yml), which holds Cognitive Services
OpenAI User on the Foundry account. For each scenario of each committed What-If file it sends the
prompt from agon.whatif.brief (the scenario's numbers only) to the deployment, then checks the draft
with agon.whatif.brief.check_brief: every number written must be one of the scenario's own numbers,
written as given, in exactly three sentences. A refused draft is recorded with its reasons and never
published. The output, brief-<seed>.json per seed, names the What-If file sha256 it was drafted
from, the deployment, the model the service reported and the prompt sha256; agon.webintel re-checks
every number before it ships a committed brief.

HTTP, host checks and error hints come from ci/foundry_smoke.py. The token comes from the
environment variable named by --token-env and is never printed.

Usage (CI):
    AGON_AI_TOKEN=... python ci/whatif_brief.py --endpoint https://<name>.openai.azure.com/ \\
        --deployment gpt-5.6-luna --out build/briefs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any

from foundry_smoke import Transport, chat_url, hint, request_body, urllib_transport

from agon.synth.schema import canonical_bytes, sha256_hex
from agon.whatif.brief import BRIEF_LABEL, check_brief, prompt
from agon.whatif.scenarios import SET_DIR

BRIEF_SCHEMA = "agon.webintel.brief/1"


def draft(endpoint: str, deployment: str, token: str, text: str, transport: Transport, timeout: float) -> tuple[str, str, int]:
    """(reply text, model reported by the service, latency ms). Raises RuntimeError on anything but a 200 reply."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
    started = time.perf_counter()
    response = transport(chat_url(endpoint), headers, request_body(deployment, text, 4000), timeout)
    latency = round((time.perf_counter() - started) * 1000)
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}: {hint(response.status, response.body)}")
    data = json.loads(response.body)
    content = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return content, str(data.get("model") or ""), latency


def briefs_for(seed: int, endpoint: str, deployment: str, token: str, transport: Transport, timeout: float,
               set_dir: str = SET_DIR) -> dict[str, Any]:
    raw = open(os.path.join(set_dir, f"whatif-{seed}.json"), "rb").read()
    body = json.loads(raw)
    clubs = {c["club_id"]: c["name"] for c in body["clubs"]}
    rows = []
    for sc in body["scenarios"]:
        text = prompt(sc, clubs)
        reply, model, latency = draft(endpoint, deployment, token, text, transport, timeout)
        reasons = check_brief(reply, sc)
        rows.append({
            "scenario_id": sc["scenario_id"],
            "status": "accepted" if not reasons else "refused",
            "text": reply if not reasons else None,
            "refused_draft": reply if reasons else None,
            "reasons": reasons,
            "model": model,
            "latency_ms": latency,
            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    return {
        "kind": "brief",
        "schema": BRIEF_SCHEMA,
        "seed": seed,
        "whatif_sha256": sha256_hex(raw),
        "deployment": deployment,
        "label": BRIEF_LABEL,
        "briefs": rows,
    }


def main(argv: list[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--deployment", default="gpt-5.6-luna")
    parser.add_argument("--token-env", default="AGON_AI_TOKEN")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--summary", default="", help="file to append a markdown summary to")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"FAIL: environment variable {args.token_env} is empty; no token, no call")
        return 1
    started = time.perf_counter()
    os.makedirs(args.out, exist_ok=True)
    lines = ["| seed | scenario | status | reasons |", "|---|---|---|---|"]
    accepted = refused = 0
    try:
        for seed in [int(s) for s in args.seeds.split(",") if s.strip()]:
            out = briefs_for(seed, args.endpoint, args.deployment, token, transport, args.timeout)
            with open(os.path.join(args.out, f"brief-{seed}.json"), "wb") as fh:
                fh.write(canonical_bytes(out) + b"\n")
            for row in out["briefs"]:
                accepted += row["status"] == "accepted"
                refused += row["status"] == "refused"
                lines.append(f"| {seed} | {row['scenario_id']} | {row['status']} | {'; '.join(row['reasons'])} |")
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"FAIL: {str(exc).replace(token, '***')}")
        return 1
    print("\n".join(lines))
    print(f"accepted {accepted}, refused {refused}")
    sys.stderr.write(f"wall time {int((time.perf_counter() - started) * 1000)} ms total\n")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write("### What-If analyst briefs\n\n" + "\n".join(lines) + f"\n\naccepted {accepted}, refused {refused}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
