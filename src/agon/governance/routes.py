"""Route audit (metric M16): no approval channel may produce an approval without `verifier.verify`.

Static half, run over every `.py` file in the channels folder, selected by PATH (never by searching
file text) and counted, so a refactored or renamed adapter cannot fall out of scope:

* Adapter modules (no leading underscore) must define `CHANNEL`, naming one of
  `verifier.CHANNELS`, and a `handle` function whose body calls `verifier.verify(...)`.
* They must import the verifier module (`from agon.governance import verifier`) and never import
  the `verify` name directly, so a test spy on `verifier.verify` sees every call.
* No channel file may touch a store (`.read`, `.create`, `.replace`), call any verifier function
  other than `verify`, `ApprovalDecision` and `VerifyResult`, call `publish`, use dynamic dispatch (`getattr`,
  `eval`, `exec`, `__import__`, `importlib`), or build a `VerifyResult` unless `ok=False` is
  written literally.

The dynamic half lives in tests/routes: it replaces `verifier.verify` with a spy and asserts every
adapter calls it exactly once for a valid payload and returns exactly what it returned.

Scope: this audits the channel adapters. No HTTP router exists yet; when the API lands, its
approval routes must be enumerated by path here too, or M16 does not cover them.

Usage: python -m agon.governance.routes [--dir PATH]
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from agon.governance import verifier

CHANNELS_DIR = Path(__file__).resolve().parent / "channels"
ALLOWED_VERIFIER_CALLS = frozenset({"verify", "ApprovalDecision", "VerifyResult"})
STORE_METHODS = frozenset({"read", "create", "replace"})
DYNAMIC_NAMES = frozenset({"getattr", "eval", "exec", "__import__", "compile"})
VERIFIER_MODULE = "agon.governance.verifier"


@dataclass
class AuditReport:
    scanned: list[str] = field(default_factory=list)
    adapters: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.scanned) and bool(self.adapters) and not self.problems


def _is_verifier_verify(call: ast.Call) -> bool:
    f = call.func
    return isinstance(f, ast.Attribute) and f.attr == "verify" and isinstance(f.value, ast.Name) and f.value.id == "verifier"


def _literal_refusal(call: ast.Call) -> bool:
    """True only for VerifyResult(..., ok=False, ...) with no positional arguments."""
    ok_kw = [k for k in call.keywords if k.arg == "ok"]
    return not call.args and len(ok_kw) == 1 and isinstance(ok_kw[0].value, ast.Constant) and ok_kw[0].value.value is False


def _check_forbidden(tree: ast.AST, name: str, problems: list[str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == VERIFIER_MODULE:
                problems.append(f"{name}: imports names from {VERIFIER_MODULE}; import the module instead")
            if node.module and node.module.startswith("importlib"):
                problems.append(f"{name}: uses importlib")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("importlib"):
                    problems.append(f"{name}: uses importlib")
                if alias.name == VERIFIER_MODULE and alias.asname != "verifier":
                    problems.append(f"{name}: imports the verifier under another name")
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        built = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if built == "VerifyResult" and not _literal_refusal(node):
            problems.append(f"{name}: builds a VerifyResult without a literal ok=False at line {node.lineno}")
        if isinstance(f, ast.Name):
            if f.id in DYNAMIC_NAMES:
                problems.append(f"{name}: dynamic call {f.id}() at line {node.lineno}")
            if f.id == "publish":
                problems.append(f"{name}: calls publish() at line {node.lineno}")
        if isinstance(f, ast.Attribute):
            if f.attr in STORE_METHODS:
                problems.append(f"{name}: calls .{f.attr}() at line {node.lineno}; channels never touch a store")
            if f.attr == "publish":
                problems.append(f"{name}: calls .publish() at line {node.lineno}")
            if isinstance(f.value, ast.Name) and f.value.id == "verifier" and f.attr not in ALLOWED_VERIFIER_CALLS:
                problems.append(f"{name}: calls verifier.{f.attr}() at line {node.lineno}")


def _check_adapter(tree: ast.Module, name: str, problems: list[str]) -> None:
    channel_value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CHANNEL" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                channel_value = node.value.value
    if channel_value is None:
        problems.append(f"{name}: no CHANNEL string constant")
    elif channel_value not in verifier.CHANNELS:
        problems.append(f"{name}: CHANNEL {channel_value!r} is not in verifier.CHANNELS")
    imports_module = any(
        isinstance(n, ast.ImportFrom) and n.module == "agon.governance" and any(a.name == "verifier" and a.asname is None for a in n.names)
        for n in tree.body
    )
    if not imports_module:
        problems.append(f"{name}: does not import the verifier module as 'verifier'")
    handles = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "handle"]
    if len(handles) != 1:
        problems.append(f"{name}: must define exactly one handle()")
        return
    if not any(isinstance(n, ast.Call) and _is_verifier_verify(n) for n in ast.walk(handles[0])):
        problems.append(f"{name}: handle() never calls verifier.verify()")


def audit_channels(directory: Path = CHANNELS_DIR) -> AuditReport:
    report = AuditReport()
    for path in sorted(Path(directory).glob("*.py")):
        name = path.name
        report.scanned.append(name)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            report.problems.append(f"{name}: cannot be parsed: {exc}")
            continue
        _check_forbidden(tree, name, report.problems)
        if not name.startswith("_"):
            report.adapters.append(path.stem)
            _check_adapter(tree, name, report.problems)
    if not report.scanned:
        report.problems.append(f"no channel files found under {directory}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if any approval channel can skip verifier.verify().")
    parser.add_argument("--dir", type=Path, default=CHANNELS_DIR)
    args = parser.parse_args(argv)
    report = audit_channels(args.dir)
    for problem in report.problems:
        print(f"FAIL {problem}")
    print(
        f"channel files scanned: {len(report.scanned)}; adapters: {len(report.adapters)} "
        f"({', '.join(report.adapters)}); routes that skip verify(): {len(report.problems)} "
        f"(channel adapters only; no HTTP router exists yet)"
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
