"""Gate: no module under src/agon outside src/agon/referee imports the referee package.

Usage:
    python ci/check_referee_isolation.py [ROOT]

Why: the referee holds the ground truth (storyline ledgers, turning-point labels). If any agent,
MCP tool or pipeline module could import it, an evaluation against those labels would be circular.

How: files are selected by PATH (every *.py under src/agon, minus src/agon/referee), never by
searching their text, and each one is parsed with `ast`. Flagged:
- `import agon.referee` and `import agon.referee.x`;
- `from agon.referee... import ...` and `from agon import referee`;
- relative imports that resolve to agon.referee (for example `from ..referee import labels`);
- a string literal that is exactly a referee module name (`"agon.referee"`, `"agon.referee.labels"`),
  which is how `importlib.import_module` or `__import__` would reach it by absolute name;
- `import_module(".referee", "agon")` and similar, when the relative name and the package are
  both string literals that resolve to agon.referee.
Not caught: module names assembled at run time (concatenation, variables, `__import__` with a
`level` argument). A reviewer still reads any dynamic import.
The scanned file count is printed, and zero scanned files is itself a failure.
Exit 0 clean, 1 on any violation or an empty scan.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from typing import Sequence

REFEREE = "agon.referee"
_MODULE_STRING = re.compile(r"^agon\.referee(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _package_of(rel_path: str) -> list[str]:
    """Package parts for a file path relative to src, e.g. agon/synth/x.py -> ['agon', 'synth']."""
    # For a module and for a package's __init__.py alike, the package is the containing directory.
    return rel_path.replace(os.sep, "/").split("/")[:-1]


def _is_referee(module: str) -> bool:
    return module == REFEREE or module.startswith(REFEREE + ".")


def _resolve(name: str, package: str) -> str:
    """Resolve a relative module name such as '..referee' against a dotted package name."""
    dots = len(name) - len(name.lstrip("."))
    parts = package.split(".")
    base = parts[: len(parts) - (dots - 1)] if dots > 1 else parts
    rest = name[dots:]
    return ".".join(base + ([rest] if rest else []))


def _relative_dynamic_import(node: ast.Call) -> str | None:
    """The module a literal relative import_module/__import__ call names, or None."""
    func = node.func
    fname = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if fname not in ("import_module", "__import__") or not node.args:
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith(".")):
        return None
    package = node.args[1] if len(node.args) > 1 else None
    for kw in node.keywords:
        if kw.arg == "package":
            package = kw.value
    if isinstance(package, ast.Constant) and isinstance(package.value, str):
        return _resolve(first.value, package.value)
    return None


def violations_in_source(source: str, rel_path: str) -> list[str]:
    tree = ast.parse(source, filename=rel_path)
    found: list[str] = []
    package = _package_of(rel_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_referee(alias.name):
                    found.append(f"{rel_path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else list(package)
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            targets = [module] + [f"{module}.{a.name}" for a in node.names]
            if any(_is_referee(t) for t in targets):
                found.append(f"{rel_path}:{node.lineno}: from {'.' * node.level}{node.module or ''} import ...")
        elif isinstance(node, ast.Call):
            target = _relative_dynamic_import(node)
            if target is not None and _is_referee(target):
                found.append(f"{rel_path}:{node.lineno}: dynamic import resolves to {target}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _MODULE_STRING.match(node.value):
                found.append(f"{rel_path}:{node.lineno}: string names module {node.value!r}")
    return found


def scan(src_root: str) -> tuple[int, list[str]]:
    """Scan every .py under src_root/agon except src_root/agon/referee. Return (files scanned, violations)."""
    agon_dir = os.path.join(src_root, "agon")
    scanned = 0
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(agon_dir):
        dirnames.sort()
        # Exclude exactly the referee package: compare the first path component under agon, so a
        # sibling such as agon/referee_tools is still scanned.
        if dirpath != agon_dir and os.path.relpath(dirpath, agon_dir).split(os.sep)[0] == "referee":
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, src_root).replace(os.sep, "/")
            with open(path, "rb") as fh:
                source = fh.read().decode("utf-8")
            found.extend(violations_in_source(source, rel))
            scanned += 1
    return scanned, found


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    scanned, found = scan(root)
    print(f"referee isolation: scanned {scanned} file(s) under {root}/agon outside agon/referee; violations {len(found)}")
    for line in found:
        print(f"  {line}")
    if scanned == 0:
        print("no files scanned: the gate selected nothing, which is a failure")
        return 1
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
