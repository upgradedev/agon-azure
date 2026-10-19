# Spike S1: durable human approval across process death

The question: can a Microsoft Agent Framework workflow pause on a human approval, lose the
process that asked, and finish correctly in a new process? Agon's design depends on it (build spec
sections 3.4 steps 3 and 6, and 11.2 S1). Until the `spike-s1` CI job is green on both runners,
treat it as an assumption.

## What runs

| File | Role |
|---|---|
| `s1_bundle.py` | Stdlib only: message types, canonical JSON and sha256 seal, the decision check, create-only publish |
| `s1_workflow.py` | The graph: `draft` then `seal` then `approval`, fixed ids, file checkpoint storage with an explicit allow-list |
| `process_a.py` | Runs until `approval` has called `ctx.request_info(ApprovalRequest{bundle_sha256, nonce})`, proves the pause checkpoint holds that request, writes `handoff.json`, then exits or blocks to be killed |
| `process_b.py` | A fresh interpreter: rebuilds the graph and calls `workflow.run(checkpoint_id=..., responses={request_id: ApprovalDecision{approve, bundle_sha256, nonce}})` |
| `driver.py` | Runs the scenarios below and prints PASS or FAIL per check, wall time per scenario and in total |
| `API_NOTES.md` | The framework API this relies on, cited by file and line at tag `python-1.22.0` |

The response handler recomputes sha256 from the sealed bytes kept in workflow state, compares it
with the request and the decision, checks the nonce, and only then writes `publish.json` with
`O_CREAT | O_EXCL`. A second write raises and leaves the first file untouched.

## Scenarios

1. `exit_then_resume`: A exits normally while the request is pending; B approves; one publish.
2. `kill_then_resume`: A is killed (SIGKILL on Linux, TerminateProcess on Windows) while
   pending; B approves; a second fresh B replays the same decision and is refused by the
   create-only write; the file bytes do not change.
3. `tampered_hash`: B sends a decision naming a different hash; refused; nothing published.
4. `deleted_checkpoint` (negative control): the pause checkpoint file is deleted; B's resume
   fails with "No checkpoint found"; nothing published; the response handler never ran.

Every scenario also checks, from a per-step trace, that `draft` and `seal` ran only in A and the
response was handled only in B.

## Run it

CI: `.github/workflows/spike-s1.yml` on `ubuntu-latest` and `windows-latest`, Python 3.12,
installs `requirements.txt` and `pytest==9.0.2`, prints `pip freeze`, runs
`tests/test_spike_s1_bundle.py` and the driver, and uploads the run directories.

Locally, where the pins are installed:

```
python spikes/s1_durable_approval/driver.py --work-dir <dir>
```

The driver refuses to run unless `agent-framework-core` is exactly 1.22.0.

## Not covered here

File storage only. The Cosmos checkpoint store, a queue-triggered worker, and the Teams leg are
later spikes. File writes are not fsynced by the framework, so a killed process is covered and a
machine power cut is not.
