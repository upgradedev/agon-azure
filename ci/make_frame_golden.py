"""Write tests/js/fixed_frame_golden.json from the committed web fixture.

Usage: PYTHONPATH=src python ci/make_frame_golden.py

Why: the browser's port of to_fixed_frame (web/agon-core.js) is held to
Python by this golden. tests/test_web_data.py checks that the committed golden
equals `golden_text()` now, and tests/js/core.test.js checks the JavaScript
against it. After a generator change, regenerate the fixture first
(python -m agon.webdata --seeds 1 --max-minute 10 --out web/data-fixture),
then run this script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agon.synth.schema import to_fixed_frame

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "web/data-fixture/1.ndjson"
GOLDEN = ROOT / "tests" / "js" / "fixed_frame_golden.json"


def golden_text(root: Path = ROOT, source: str = SOURCE) -> str:
    """The golden as text: the home club and to_fixed_frame of every event, in file order."""
    lines = (root / source).read_text(encoding="ascii").splitlines()
    events = [json.loads(line) for line in lines]
    home = events[0]["team"]
    body = {"source": source, "home": home, "points": [list(to_fixed_frame(e, home)) for e in events]}
    return json.dumps(body, separators=(",", ":")) + "\n"


def main() -> int:
    text = golden_text()
    GOLDEN.write_bytes(text.encode("ascii"))
    sys.stdout.write(f"wrote {GOLDEN.relative_to(ROOT).as_posix()} from {SOURCE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
