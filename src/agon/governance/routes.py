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

Queue half (`audit_intake`), run over every `.py` file in `agon/intake`, selected by PATH:

* `agon.intake.ROUTES` maps each inbound queue to a channel adapter. The queue name must be
  `decisions-<CHANNEL>` and the channel must be an officer channel fed by a Logic App (`teams`,
  `outlook`); the console and the signed link are never queue routes.
* `handle_message` reaches the verifier only through `adapter.handle(...)`. No intake file may call
  `verifier.verify` directly, build a `VerifyResult`, write a store (`.create`, `.replace`), or use
  dynamic dispatch; its one store read is `store.read(verifier.APPROVALS, ...)` inside `_replay`.

Scope: channel adapters and queue intake routes. No HTTP router exists yet; when the api's HTTP
routes land (console stand-in, signed link), they must be enumerated by path here too, or M16
does not cover them.

Usage: python -m agon.governance.routes [--dir PATH] [--intake-dir PATH]
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


INTAKE_DIR = Path(__file__).resolve().parent.parent / "intake"
QUEUE_CHANNELS = frozenset({"teams", "outlook"})


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _check_intake_file(tree: ast.Module, name: str, problems: list[str]) -> None:
    replay = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_replay"]
    replay_calls = {id(c) for f in replay for c in _calls_in(f)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else None
            names = [a.name for a in node.names]
            if (mod or "").startswith("importlib") or any(n.startswith("importlib") for n in names):
                problems.append(f"intake/{name}: uses importlib")
            if mod == VERIFIER_MODULE:
                problems.append(f"intake/{name}: imports names from {VERIFIER_MODULE}; import the module instead")
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        built = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if built == "VerifyResult":
            problems.append(f"intake/{name}: builds a VerifyResult at line {node.lineno}")
        if isinstance(f, ast.Name) and f.id in DYNAMIC_NAMES:
            problems.append(f"intake/{name}: dynamic call {f.id}() at line {node.lineno}")
        if isinstance(f, ast.Attribute):
            if f.attr == "verify":
                problems.append(f"intake/{name}: calls .verify() at line {node.lineno}; go through the channel adapter")
            if f.attr in {"create", "replace", "publish"}:
                problems.append(f"intake/{name}: calls .{f.attr}() at line {node.lineno}; intake never writes a store")
            if f.attr == "read":
                first = node.args[0] if node.args else None
                approvals = isinstance(first, ast.Attribute) and first.attr == "APPROVALS" and isinstance(first.value, ast.Name) and first.value.id == "verifier"
                if id(node) not in replay_calls or not approvals:
                    problems.append(f"intake/{name}: store read at line {node.lineno} outside _replay or not on verifier.APPROVALS")


def audit_intake(directory: Path = INTAKE_DIR) -> AuditReport:
    """Queue half of M16. `report.adapters` lists the queue routes, as `queue->channel`."""
    report = AuditReport()
    for path in sorted(Path(directory).glob("*.py")):
        report.scanned.append(path.name)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            report.problems.append(f"intake/{path.name}: cannot be parsed: {exc}")
            continue
        _check_intake_file(tree, path.name, report.problems)
        if path.name == "__init__.py":
            handlers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "handle_message"]
            through_adapter = any(
                isinstance(c.func, ast.Attribute) and c.func.attr == "handle" and isinstance(c.func.value, ast.Name) and c.func.value.id == "adapter"
                for h in handlers for c in _calls_in(h)
            )
            if len(handlers) != 1 or not through_adapter:
                report.problems.append("intake/__init__.py: handle_message must call adapter.handle(...)")
    if not report.scanned:
        report.problems.append(f"no intake files found under {directory}")
        return report
    from agon import intake  # the live table, after the static pass

    for queue, adapter in sorted(intake.ROUTES.items()):
        channel = getattr(adapter, "CHANNEL", None)
        report.adapters.append(f"{queue}->{channel}")
        if channel not in QUEUE_CHANNELS:
            report.problems.append(f"intake route {queue}: channel {channel!r} is not a Logic App officer channel {sorted(QUEUE_CHANNELS)}")
        if queue != f"decisions-{channel}":
            report.problems.append(f"intake route {queue}: the queue must be named decisions-{channel}")
        if not callable(getattr(adapter, "handle", None)) or not callable(getattr(adapter, "parse", None)):
            report.problems.append(f"intake route {queue}: adapter has no handle() and parse()")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if any approval channel can skip verifier.verify().")
    parser.add_argument("--dir", type=Path, default=CHANNELS_DIR)
    parser.add_argument("--intake-dir", type=Path, default=INTAKE_DIR)
    args = parser.parse_args(argv)
    report = audit_channels(args.dir)
    intake_report = audit_intake(args.intake_dir)
    problems = report.problems + intake_report.problems
    for problem in problems:
        print(f"FAIL {problem}")
    print(
        f"channel files scanned: {len(report.scanned)}; adapters: {len(report.adapters)} "
        f"({', '.join(report.adapters)}); intake files scanned: {len(intake_report.scanned)}; "
        f"queue routes: {len(intake_report.adapters)} ({', '.join(intake_report.adapters)}); "
        f"routes that skip verify(): {len(problems)} (channel adapters and queue routes; no HTTP router exists yet)"
    )
    return 0 if report.ok and intake_report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
