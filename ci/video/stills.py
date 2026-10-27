"""Build the HTML of the video's still beats, from committed files and the live run, and check their facts.

Three stills, each 1920 x 930 (the picture area above the caption band):

  opening       video/stills/opening.html as committed, animated with CSS only: the title,
                the problem and the flow of one run. Its facts: it names the Microsoft
                parts the run uses, says the clubs are fictional, claims no check of
                "every number" (the Fact Gate recomputes the claims and refuses digits no
                claim carries), uses no superlative, and loads nothing: no script, link,
                image, font file, import or URL.
  architecture  video/stills/architecture.html as committed: the load-bearing sentence,
                the components, the live URL and the repo URL. Its facts: both URLs are
                the sheet's site and the public repo.
  executions    a timeline drawn from evidence/<run>.executions.json (the Container Apps
                job executions of the recorded run, read from Azure with the command the
                file names). Its facts are the narration's: the first worker process ended
                before the approval was recorded, a new worker process started after the
                intake began, and exactly one Publisher execution ran, after it. The file
                must name the run the live site shows and the bundle hash that run sealed.

The run is the one the sheet names, or with run_id "default" the first run in the live
site's runs/index.json, which must be a pipeline run. Before anything is built, the live
page's data-commit must equal --release-sha.

For each still it writes <out>/still-<name>.html and <out>/still-<name>.meta.json. A still
whose input is missing, or whose facts fail, fails the run with --facts enforce (the
video workflow) and is written as a labelled placeholder with --facts report (the pull
request rehearsal). console/e2e/video-capture.ts screenshots each HTML to a PNG, or,
for a still part marked "animate": true in the sheet (the opening), records it as a clip.

Usage:
  python ci/video/stills.py --beats video/beats.json --release-sha SHA --out DIR \
      [--only BEATS] [--facts enforce|report] [--repo ROOT]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import beatsheet as bs  # noqa: E402

REPO_URL = "https://github.com/upgradedev/agon-azure"
LOAD_BEARING = ("Take Foundry out and no report exists. Take the checkpoint out and an approval cannot survive a restart. "
                "Take Teams out and nothing publishes.")
STILL_WIDTH, STILL_HEIGHT = bs.WIDTH, bs.VISUAL_HEIGHT


class StillError(RuntimeError):
    """A still cannot be built truthfully; the message says why."""


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "agon-video"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def resolve_run(sheet: bs.Sheet, fetch: Callable[[str], str]) -> dict[str, Any]:
    """The run file of the run the video shows, from the live site."""
    run_id = sheet.run_id
    if run_id == "default":
        index = json.loads(fetch(f"{sheet.site}/runs/index.json"))
        first = (index.get("runs") or [{}])[0]
        if first.get("provenance") != "pipeline":
            raise StillError(f"the live site's default run {first.get('run_id')} is not a pipeline run")
        run_id = first["run_id"]
    run = json.loads(fetch(f"{sheet.site}/runs/{run_id}.json"))
    if run.get("run_id") != run_id:
        raise StillError(f"runs/{run_id}.json names run {run.get('run_id')}")
    return run


def live_commit(sheet: bs.Sheet, fetch: Callable[[str], str]) -> str:
    m = re.search(r'data-commit="([0-9a-f]{40})"', fetch(f"{sheet.site}/"))
    if not m:
        raise StillError(f"{sheet.site} serves no 40-hex data-commit")
    return m.group(1)


# --------------------------------------------------------------------- executions


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def execution_facts(evidence: dict[str, Any], run: dict[str, Any]) -> list[tuple[str, bool]]:
    """(fact, holds) for every sentence of the narration this still stands behind."""
    runs = evidence.get("executions") or []
    fleet = sorted((e for e in runs if e.get("job") == "job-agon-fleet"), key=lambda e: e["start"])
    intake = [e for e in runs if e.get("job") == "job-agon-intake"]
    publisher = [e for e in runs if e.get("job") == "job-agon-publisher"]
    approved = (run.get("approval") or {}).get("approved_at")
    facts = [
        (f"the evidence names the recorded run {run.get('run_id')}", evidence.get("recorded_run") == run.get("run_id")),
        ("the evidence names the bundle hash the run sealed",
         evidence.get("bundle_sha256") == (run.get("bundle") or {}).get("sha256")),
        ("every execution succeeded", bool(runs) and all(e.get("status") == "Succeeded" for e in runs)),
        ("two worker executions, one intake execution", len(fleet) == 2 and len(intake) == 1),
    ]
    if len(fleet) == 2 and len(intake) == 1 and approved:
        facts += [
            ("the first worker process ended before the approval was recorded", _when(fleet[0]["end"]) < _when(approved)),
            ("the first worker process ended before the intake started", _when(fleet[0]["end"]) < _when(intake[0]["start"])),
            ("a new worker process started after the intake began", _when(fleet[1]["start"]) >= _when(intake[0]["start"])),
        ]
    else:
        facts.append(("the run carries an approval time", bool(approved)))
    facts.append(("exactly one Publisher execution", len(publisher) == 1))
    if len(publisher) == 1 and len(fleet) == 2:
        facts.append(("the Publisher ran after the new worker started", _when(publisher[0]["start"]) >= _when(fleet[1]["start"])))
    return facts


ROW_NAMES = {
    "job-agon-fleet": ("Fleet worker", "#3fb68b"),
    "job-agon-intake": ("Decision intake", "#6ea8fe"),
    "job-agon-publisher": ("Publisher", "#e3b341"),
}


def executions_html(evidence: dict[str, Any], run: dict[str, Any]) -> str:
    rows = sorted(evidence["executions"], key=lambda e: e["start"])
    starts = [_when(e["start"]) for e in rows]
    ends = [_when(e["end"]) for e in rows]
    approved = _when(run["approval"]["approved_at"])
    t0 = min(starts).replace(second=0) - timedelta(minutes=1)
    t1 = max(ends).replace(second=0) + timedelta(minutes=2)
    span = (t1 - t0).total_seconds()
    left, width = 470, 1330  # pixel geometry of the time axis

    def x(moment: datetime) -> float:
        return left + width * (moment - t0).total_seconds() / span

    fleet_seen = 0
    bars = []
    for n, (row, start, end) in enumerate(zip(rows, starts, ends)):
        name, colour = ROW_NAMES.get(row["job"], (row["job"], "#8b949e"))
        if row["job"] == "job-agon-fleet":
            fleet_seen += 1
            name = f"{name}, process {fleet_seen}"
        top = 250 + n * 118
        bars.append(
            f'<div class="label" style="top:{top}px"><b>{html.escape(name)}</b><span>{html.escape(row["execution"])}</span></div>'
            f'<div class="bar" style="top:{top + 6}px;left:{x(start):.1f}px;width:{max(x(end) - x(start), 6):.1f}px;background:{colour}"></div>'
            f'<div class="times" style="top:{top + 56}px">{start:%H:%M:%S} to {end:%H:%M:%S} UTC: '
            f'{html.escape(row.get("role", ""))}</div>'
        )
    ticks = []
    minute = t0
    while minute <= t1:
        ticks.append(f'<div class="tick" style="left:{x(minute):.1f}px"><span>{minute:%H:%M}</span></div>')
        minute += timedelta(minutes=1)
    fleet = [e for e in rows if e["job"] == "job-agon-fleet"]
    intake = next(e for e in rows if e["job"] == "job-agon-intake")
    gap_a, gap_b = _when(fleet[0]["end"]), _when(intake["start"])
    short = run["bundle"]["sha256"][:12]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Executions of the recorded run</title>
<style>
  html, body {{ margin: 0; width: {STILL_WIDTH}px; height: {STILL_HEIGHT}px; background: #0b0f14; color: #e6edf3;
    font-family: "DejaVu Sans", "Segoe UI", Arial, sans-serif; overflow: hidden; }}
  .eyebrow {{ position: absolute; left: 120px; top: 56px; font-size: 22px; letter-spacing: .08em; color: #3fb68b; text-transform: uppercase; }}
  h1 {{ position: absolute; left: 120px; top: 92px; margin: 0; font-size: 46px; font-weight: 700; }}
  .sub {{ position: absolute; left: 120px; top: 160px; font-size: 24px; color: #9aa7b4; }}
  .label {{ position: absolute; left: 120px; width: 330px; font-size: 25px; }}
  .label span {{ display: block; font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 18px; color: #8b949e; margin-top: 6px; }}
  .bar {{ position: absolute; height: 40px; border-radius: 8px; }}
  .times {{ position: absolute; left: {left}px; max-width: {width}px; font-size: 19px; color: #c9d1d9; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; background: #0b0f14; padding: 2px 8px 2px 0; z-index: 3; }}
  .bar, .label {{ z-index: 2; }}
  .tick {{ position: absolute; top: 236px; height: 500px; border-left: 1px dashed #26303b; }}
  .tick span {{ position: absolute; top: 506px; left: -30px; font-size: 18px; color: #8b949e; }}
  .gap {{ position: absolute; top: 236px; height: 500px; background: rgba(110, 168, 254, .07); border-left: 2px solid #26303b; border-right: 2px solid #26303b; }}
  .gap span {{ position: absolute; top: 4px; left: 10px; right: 10px; font-size: 18px; color: #9aa7b4; }}
  .mark {{ position: absolute; top: 220px; height: 530px; border-left: 3px solid #e6edf3; }}
  .mark span {{ position: absolute; top: -34px; left: 8px; font-size: 19px; white-space: nowrap; }}
  .source {{ position: absolute; left: 120px; right: 120px; bottom: 34px; font-size: 17px; color: #8b949e; line-height: 1.5; }}
  .source code {{ font-family: "DejaVu Sans Mono", Consolas, monospace; color: #c9d1d9; }}
</style></head>
<body>
  <div class="eyebrow">{html.escape(run.get("label", ""))}</div>
  <h1>While the card waited, the worker process ended</h1>
  <div class="sub">Container Apps job executions of bundle {short}, read from Azure</div>
  <div class="gap" style="left:{x(gap_a):.1f}px;width:{x(gap_b) - x(gap_a):.1f}px"><span>No execution of this run from {gap_a:%H:%M:%S} to {gap_b:%H:%M:%S}</span></div>
  {''.join(ticks)}
  <div class="mark" style="left:{x(approved):.1f}px"><span>Approval recorded {approved:%H:%M:%S} UTC</span></div>
  {''.join(bars)}
  <div class="source">Source: evidence/{html.escape(run["run_id"])}.executions.json, captured {html.escape(str(evidence.get("captured_at", "")))} with
    <code>{html.escape(evidence.get("source_command", ""))}</code></div>
</body></html>
"""


# --------------------------------------------------------------------- builders


def _visible_words(text: str) -> str:
    """The text a viewer reads: comments, the style block and tags removed, entities decoded, spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->|<style.*?</style>", " ", text, flags=re.S))).split())


def build_architecture(repo: Path, sheet: bs.Sheet, run: dict[str, Any]) -> tuple[str, list[tuple[str, bool]]]:
    path = repo / "video" / "stills" / "architecture.html"
    if not path.is_file():
        raise StillError(f"{path} is missing")
    text = path.read_text(encoding="utf-8")
    words = _visible_words(text)
    return text, [
        (f"the still shows the live URL {sheet.site}", sheet.site in words),
        (f"the still shows the repo URL {REPO_URL}", REPO_URL in words),
        ("the still states the load-bearing sentence", LOAD_BEARING in words),
    ]


OPENING_PARTS = ("Microsoft Foundry", "Microsoft Agent Framework", "Azure Container Apps", "Microsoft Teams", "Azure Logic Apps")
SUPERLATIVES = re.compile(r"\b(first|best|novel|revolutionary|sota|state of the art)\b", re.I)
LOADS = re.compile(r"<\s*(script|link|img|iframe|object|embed|video|audio|source)\b|@import|@font-face|url\s*\(|https?:", re.I)


def build_opening(repo: Path, sheet: bs.Sheet, run: dict[str, Any]) -> tuple[str, list[tuple[str, bool]]]:
    path = repo / "video" / "stills" / "opening.html"
    if not path.is_file():
        raise StillError(f"{path} is missing")
    text = path.read_text(encoding="utf-8")
    words = _visible_words(text)
    return text, [
        ("the still shows the title Agon", re.search(r"\bAgon\b", words) is not None),
        *((f"the still names {part}", part in words) for part in OPENING_PARTS),
        ("the still says the clubs are fictional", "fictional clubs" in words.lower()),
        ("the still claims no check of every number, only of the claims", "every number" not in words.lower()),
        ("the still uses no superlative", SUPERLATIVES.search(words) is None),
        ("the still has no em or en dash", not any(d in text for d in bs.DASHES)),
        ("the still animates with CSS keyframes", "@keyframes" in text),
        ("the still loads nothing: no script, link, image, font file, import or URL", LOADS.search(text) is None),
    ]


def build_executions(repo: Path, sheet: bs.Sheet, run: dict[str, Any]) -> tuple[str, list[tuple[str, bool]]]:
    path = repo / "evidence" / f"{run['run_id']}.executions.json"
    if not path.is_file():
        raise StillError(f"no evidence file evidence/{run['run_id']}.executions.json in the repository")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema") != "agon.run-executions/1":
        raise StillError(f"{path.name} is not an agon.run-executions/1 file")
    facts = execution_facts(evidence, run)
    if not all(ok for _, ok in facts):
        return "", facts
    return executions_html(evidence, run), facts


BUILDERS: dict[str, Callable[[Path, bs.Sheet, dict[str, Any]], tuple[str, list[tuple[str, bool]]]]] = {
    "opening": build_opening,
    "architecture": build_architecture,
    "executions": build_executions,
}


def main(argv: list[str] | None = None, fetch: Callable[[str], str] = fetch_text) -> int:
    parser = argparse.ArgumentParser(description="Build the still beats' HTML and check their facts.")
    parser.add_argument("--beats", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="all")
    parser.add_argument("--facts", choices=("enforce", "report"), default="enforce")
    parser.add_argument("--repo", type=Path, default=HERE.parent.parent)
    args = parser.parse_args(argv)

    sheet = bs.load_sheet(args.beats)
    stills = [(b, p) for b in sheet.select(args.only) for p in b.parts if p.kind == "still"]
    args.out.mkdir(parents=True, exist_ok=True)
    if not stills:
        print("no still beats selected")
        return 0
    commit = live_commit(sheet, fetch)
    if commit != args.release_sha:
        print(f"FAIL: the live site serves {commit}, not the release {args.release_sha}")
        return 1
    run = resolve_run(sheet, fetch)
    failed_any = False
    for beat, part in stills:
        builder = BUILDERS.get(part.target)
        meta: dict[str, Any] = {"beat": beat.id, "still": part.target, "run_id": run["run_id"], "release_sha": args.release_sha,
                                "data_commit": commit, "facts_mode": args.facts, "facts_checked": [], "facts_failed": [],
                                "placeholder": None}
        try:
            if builder is None:
                raise StillError(f"no builder for still {part.target}")
            page, facts = builder(args.repo, sheet, run)
            meta["facts_checked"] = [f for f, ok in facts if ok]
            meta["facts_failed"] = [f for f, ok in facts if not ok]
            if meta["facts_failed"]:
                raise StillError("facts do not hold: " + "; ".join(meta["facts_failed"]))
            (args.out / f"still-{part.target}.html").write_text(page, encoding="utf-8")
            print(f"still {part.target} for beat {beat.id}: {len(meta['facts_checked'])} facts hold")
        except (StillError, OSError, json.JSONDecodeError, KeyError) as exc:
            failed_any = True
            meta["placeholder"] = f"{part.target} still not built: {exc}"
            print(f"{'FAIL' if args.facts == 'enforce' else 'REPORTED'}: beat {beat.id}: {exc}")
        (args.out / f"still-{part.target}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return 1 if failed_any and args.facts == "enforce" else 0


if __name__ == "__main__":
    sys.exit(main())
