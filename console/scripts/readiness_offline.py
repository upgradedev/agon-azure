"""Write an offline fixture so ci/readiness.py can judge the built console before it deploys.

Why: the readiness gate (ci/readiness.py) only runs live, after a deploy to main.
A React build can trip its rules in ways the old hand-written page never did: a
bundled library with a dash in a string, a minified "12/10", a word on the
real-names list. This script maps every URL the gate would fetch to a file of the
stamped build, so the console workflow runs the unchanged gate, with the
unchanged ci/readiness.json, over the exact bytes that would ship:

    python ci/readiness.py --offline <out> --set-host agon.example --add-marker demo=<sha>

Mapped: the five targets of ci/readiness.json (the page, data/manifest.json, the
sample run, its sealed manifest and the README) and every .js file of the site,
so the gate's own asset crawl finds each script it follows. A URL with no entry
answers status 0 and fails, so nothing is skipped silently.

Stdlib only. Usage: python scripts/readiness_offline.py <site_dir> <out_dir> <readme>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOST = "https://agon.example"
README_URL = "https://raw.githubusercontent.com/upgradedev/agon-azure/main/README.md"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    site, out, readme = (Path(a).resolve() for a in argv)
    index: dict[str, dict[str, object]] = {
        f"{HOST}/": {"status": 200, "body_file": str(site / "index.html")},
        README_URL: {"status": 200, "body_file": str(readme)},
    }
    for rel in ("data/manifest.json", "runs/sample-s1.json", "runs/sample-s1/manifest.json"):
        path = site / rel
        if not path.is_file():
            print(f"readiness_offline: FAIL: {rel} is missing from {site}")
            return 1
        index[f"{HOST}/{rel}"] = {"status": 200, "body_file": str(path)}
    scripts = sorted(p for p in site.rglob("*.js") if p.is_file())
    if not scripts:
        print("readiness_offline: FAIL: the site holds no script; the asset crawl would prove nothing")
        return 1
    for path in scripts:
        index[f"{HOST}/{path.relative_to(site).as_posix()}"] = {"status": 200, "body_file": str(path)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"readiness_offline: mapped {len(index)} URLs ({len(scripts)} scripts) into {out / 'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
