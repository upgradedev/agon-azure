"""Proof that the Bicep gate fails on errors, on warnings and on an empty run.

The compiler is replaced by a fake runner, so these tests need no Bicep binary.
CI runs the real compiler on infra/ and on a fixture with a known warning.
"""

from __future__ import annotations

from pathlib import Path

import bicep_check

WARNING = ("C:\\repo\\infra\\main.bicep(12,7) : Warning no-unused-params: Parameter \"x\" is "
           "declared but never used. [https://aka.ms/bicep/linter-diagnostics#no-unused-params]")
TYPE_WARNING = ("/repo/infra/modules/cosmos.bicep(82,5) : Warning BCP037: The property \"typo\" "
                "is not allowed on objects of type \"X\".")
ERROR = "/repo/infra/main.bicep(3,1) : Error BCP018: Expected the \"=\" character at this location."


def fake(result: bicep_check.BuildResult) -> bicep_check.Runner:
    return lambda path: result


def bicep_file(tmp_path: Path) -> Path:
    path = tmp_path / "x.bicep"
    path.write_text("output x string = 'x'\n", encoding="utf-8")
    return path


def test_clean_build_passes(tmp_path: Path) -> None:
    text, ok = bicep_check.check([bicep_file(tmp_path)], fake(bicep_check.BuildResult(0, "")), False)
    assert ok and "verdict: PASS" in text


def test_warning_with_exit_zero_fails(tmp_path: Path) -> None:
    for line in (WARNING, TYPE_WARNING):
        text, ok = bicep_check.check([bicep_file(tmp_path)], fake(bicep_check.BuildResult(0, line + "\n")), False)
        assert not ok
        assert "Warning" in text and "verdict: FAIL" in text


def test_error_fails(tmp_path: Path) -> None:
    text, ok = bicep_check.check([bicep_file(tmp_path)], fake(bicep_check.BuildResult(1, ERROR + "\n")), False)
    assert not ok and "BCP018" in text and "exit code 1" in text


def test_nonzero_exit_without_diagnostic_fails(tmp_path: Path) -> None:
    text, ok = bicep_check.check([bicep_file(tmp_path)], fake(bicep_check.BuildResult(2, "boom\n")), False)
    assert not ok and "exit code 2: boom" in text


def test_zero_files_fails() -> None:
    text, ok = bicep_check.check([], fake(bicep_check.BuildResult(0, "")), False)
    assert not ok and "zero files given" in text


def test_missing_file_fails(tmp_path: Path) -> None:
    text, ok = bicep_check.check([tmp_path / "absent.bicep"], fake(bicep_check.BuildResult(0, "")), False)
    assert not ok and "not a file" in text


def test_expect_fail_passes_only_when_the_file_fails(tmp_path: Path) -> None:
    path = bicep_file(tmp_path)
    _, caught = bicep_check.check([path], fake(bicep_check.BuildResult(0, WARNING)), True)
    assert caught
    text, toothless = bicep_check.check([path], fake(bicep_check.BuildResult(0, "")), True)
    assert not toothless and "the gate has no teeth" in text


def test_shipped_fixture_holds_an_unused_param() -> None:
    fixture = Path(__file__).resolve().parents[1] / "ci" / "fixtures" / "bicep_warning.bicep"
    text = fixture.read_text(encoding="utf-8")
    assert "param unusedOnPurpose" in text
    assert text.count("unusedOnPurpose") == 1
