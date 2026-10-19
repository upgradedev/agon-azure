"""Compare two generator outputs (for example ubuntu and windows) and fail on any difference.

Usage:
    python -m agon.synth.crosscheck DIR_A DIR_B

Each directory holds `hash.txt` (from `manifest --hash-out`) and `out/` (from `manifest --out`).
Both hash files are printed. On a mismatch the first differing line of every differing file is
printed, which for events.ndjson names the first diverging event.

Honest scope: this locates the first diverging EVENT. The spec asks for the first diverging RNG
draw; a draw-level trace is not built yet, and the event is the v0 stand-in.

Exit code 0 only when the hash files and every output file are byte-identical. The check is never
relaxed: no normalisation is applied except reading hash.txt, which the CLI writes with LF.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _files_under(root: str) -> list[str]:
    found: list[str] = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def first_difference(a: bytes, b: bytes) -> tuple[int, str, str] | None:
    """Return (1-based line number, line in a, line in b) of the first differing line, or None."""
    lines_a = a.split(b"\n")
    lines_b = b.split(b"\n")
    for i in range(max(len(lines_a), len(lines_b))):
        la = lines_a[i] if i < len(lines_a) else b"<missing>"
        lb = lines_b[i] if i < len(lines_b) else b"<missing>"
        if la != lb:
            return i + 1, la.decode("utf-8", "replace"), lb.decode("utf-8", "replace")
    return None


def compare(dir_a: str, dir_b: str) -> list[str]:
    """Return human-readable problems; empty list means identical."""
    problems: list[str] = []
    hash_a = _read(os.path.join(dir_a, "hash.txt"))
    hash_b = _read(os.path.join(dir_b, "hash.txt"))
    print(f"--- {dir_a}/hash.txt")
    print(hash_a.decode("ascii", "replace"), end="")
    print(f"--- {dir_b}/hash.txt")
    print(hash_b.decode("ascii", "replace"), end="")
    if hash_a != hash_b:
        diff = first_difference(hash_a, hash_b)
        problems.append(f"hash.txt differs at line {diff[0] if diff else '?'}")
    out_a, out_b = os.path.join(dir_a, "out"), os.path.join(dir_b, "out")
    files_a, files_b = _files_under(out_a), _files_under(out_b)
    print(f"scanned {len(files_a)} output file(s) in A and {len(files_b)} in B")
    if not files_a or not files_b:
        problems.append("an output directory is empty or missing")
    if files_a != files_b:
        problems.append(f"file lists differ: only A {sorted(set(files_a) - set(files_b))}, only B {sorted(set(files_b) - set(files_a))}")
    for rel in sorted(set(files_a) & set(files_b)):
        a = _read(os.path.join(out_a, *rel.split("/")))
        b = _read(os.path.join(out_b, *rel.split("/")))
        if a != b:
            diff = first_difference(a, b)
            if diff is None:
                problems.append(f"{rel}: bytes differ")
            else:
                problems.append(f"{rel}: first difference at line {diff[0]}\n  A: {diff[1][:400]}\n  B: {diff[2][:400]}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m agon.synth.crosscheck DIR_A DIR_B", file=sys.stderr)
        return 2
    problems = compare(args[0], args[1])
    if problems:
        print("MISMATCH")
        for p in problems:
            print(p)
        return 1
    print("IDENTICAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
