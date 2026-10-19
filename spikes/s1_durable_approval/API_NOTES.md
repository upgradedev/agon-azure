# Spike S1: Agent Framework API notes

Read on 2026-10-19 by a read-only GET of the source. Nothing was installed locally. Everything
below is a paraphrase of the source, cited by path and line, so a reader can check it.

## Version and source

| Fact | Value | How it was read |
|---|---|---|
| PyPI version exists | `agent-framework-core` 1.22.0, wheel and sdist uploaded 2026-10-15 | `curl -s https://pypi.org/pypi/agent-framework-core/json`, field `releases["1.22.0"]` |
| Latest on PyPI that day | 1.22.0 | same JSON, `info.version` |
| Requires | `msgspec>=0.20.0,<0.23`, `typing-extensions>=4.15.0,<5`, `pydantic>=2,<3`, `python-dotenv>=1,<2`, `opentelemetry-api>=1.39.0,<2`, `pyyaml>=6.0,<7.0`, `regex>=2024.11.6`; Python `>=3.10`. Every other package is behind the `all` extra, which this spike does not install | same JSON, `info.requires_dist` and `info.requires_python` |
| Matching tag | `python-1.22.0`, a lightweight tag on commit `14229b083a7ef95eeaadf472e1da0367a9f46457`, release published 2026-10-15 09:53 UTC | `gh api repos/microsoft/agent-framework/git/ref/tags/python-1.22.0` (the annotated-tag lookup returns 404 because the tag is lightweight) |
| Source root | `python/packages/core/agent_framework/_workflows/` | `gh api "repos/microsoft/agent-framework/git/trees/python-1.22.0?recursive=1"` |

Paths below are relative to `python/packages/core/agent_framework/_workflows/` at that commit.
File hashes of the copies that were read (sha256): `_workflow.py` 8ec38721...cf42c9,
`_runner.py` 81f9994e...cc51c1, `_checkpoint.py` b00f0624...0d4d, `_checkpoint_encoding.py`
314af918...4ed4, `_workflow_context.py` 99ae50a9...c24b, `_request_info_mixin.py`
c232ba32...18ee, `_runner_context.py` 3d9ce081...fe9fe.

## What the spike relies on

### Building the graph

- `_workflow_builder.py:53` `class WorkflowBuilder`; constructor at line 91. `name` (line 94) is
  the identifier for all instances built from the builder; when it is omitted a unique name is
  generated, so the spike always passes a fixed name. `start_executor` and `checkpoint_storage`
  are keyword-only (lines 97 and 98). `output_from` (line 99) lists the executors whose
  `yield_output` becomes a workflow `output` event.
- `_workflow_builder.py:230` `add_edge`, `:727` `build`.
- `_validation.py:378` an executor named in `output_from` must declare workflow output types.
  `_executor.py:475` `workflow_output_types` collects them from both `@handler` and
  `@response_handler` specs, so declaring the output on the response handler is enough.
- `_executor.py:36` `class Executor`; `__init__` at line 179 takes a non-empty `id`. The spike uses
  the fixed ids `draft`, `seal`, `approval`.
- `_executor.py:590` `handler` decorator. Message and output types are inferred from the handler's
  `WorkflowContext[OutT, W_OutT]` annotation (`_workflow_context.py` lines 40 to 50 list the
  rules: one argument is the message type; the second is the workflow output type; `Never` means
  none).
- `_executor.py:536` and `:551` `on_checkpoint_save` / `on_checkpoint_restore` hooks for executor
  instance state. The spike keeps no instance state that matters after the pause; the sealed bytes
  go through workflow state instead.

### Asking a human

- `_workflow_context.py:404` `WorkflowContext.request_info(request_data, response_type, *,
  request_id=None)`. It emits a `request_info` event carrying the data and the expected response
  type; the id defaults to a new uuid4. The executor must have a matching `@response_handler`.
- `_request_info_mixin.py:140` `response_handler` decorator. `_request_info_mixin.py:326`
  validates the signature: exactly `(self, original_request, response, ctx)` (line 352), and the
  `original_request` parameter must be annotated.
- `_workflow_context.py:437` `get_state`, `:441` `set_state`. State is committed at the superstep
  boundary (`_runner.py`, inside `run_until_convergence`, before the checkpoint at line 174).

### Checkpoints

- `_runner.py:174` a checkpoint is created after every superstep. The superstep in which
  `approval` calls `request_info` therefore ends with a checkpoint that holds the pending request.
- `_checkpoint.py:35` `WorkflowCheckpoint` fields: `workflow_name`, `graph_signature_hash`,
  `checkpoint_id`, `previous_checkpoint_id`, `timestamp`, `messages`, `state`,
  `pending_request_info_events` (line 96), `iteration_count`, `metadata`, `version` (line 103,
  default `"1.0"`).
- `_checkpoint.py:505` `FileCheckpointStorage(storage_path, *, allowed_checkpoint_types=None)`.
  One file per checkpoint, named `<checkpoint_id>.json` (line 569). The file is JSON; values that
  are not JSON native are pickled and embedded as base64 (class docstring, and
  `_checkpoint_encoding.py:84` marker `__pickled__`). Writes go to a temp file and `os.replace`
  (line 617); there is no fsync, so a killed process is covered and a power cut is not.
- `_checkpoint.py:551` the allow-list is always a frozenset, so file storage always decodes with
  the restricted unpickler (`_checkpoint_encoding.py:162`). Allowed: a built-in safe set (line
  115), anything under `agent_framework.` or `agent_framework_orchestrations.` (line 95), OpenAI
  SDK types, types passed in `allowed_checkpoint_types` as `"module:qualname"`, and types
  registered process-wide with `register_checkpoint_type` (`_checkpoint_encoding.py:65`, merged at
  line 308).
- `_checkpoint.py:599` and `:605` `save` round-trips the encoded checkpoint through the restricted
  decoder and refuses to save if it would not decode.
- `_runner.py:250` `create_checkpoint_if_enabled` catches that refusal and only logs a warning;
  the log text says the failure does not fail the workflow run (line 281). **Consequence for the
  spike:** process A proves the pause checkpoint exists and holds the request id before it writes
  the handoff, and exits 3 otherwise.
- `_checkpoint.py:765` `load` raises `WorkflowCheckpointException` with the message "No checkpoint
  found with ID ..." when the file is missing. The negative control asserts that exact reason.
- `_checkpoint_encoding.py:20` the module docstring states that checkpoint storage is treated as a
  trusted source, because the format uses pickle; the allow-list is defence in depth, not a
  security boundary. For Agon this means the checkpoint container needs the same access control as
  a secret store.

### Graph signature

- `_workflow.py:1319` `_compute_graph_signature`: the start executor id, every executor id mapped
  to `"<class module>.<class name>"` (line 1331), and the edge groups. `:1382` sha256 of the
  canonical JSON. Stored on the workflow as `graph_signature_hash` (line 376).
- `_runner.py:288` `restore_from_checkpoint` refuses a checkpoint whose graph signature hash
  differs ("Workflow graph has changed since the checkpoint was created", line 325).
- **Consequence:** executor classes must live in an importable module that both processes import
  under the same name. A class defined in an entry script would be `__main__.X` in one process and
  could differ in the other. The same holds for every pickled checkpoint type.

### Resuming in a new process with the human's answer

- `_workflow.py:801` `Workflow.run(message=None, *, stream=False, responses=None,
  checkpoint_id=None, checkpoint_storage=None, ...)`. The docstring (lines 830 to 834) says
  `responses` can be combined with `checkpoint_id` to restore a checkpoint and send responses in a
  single call.
- `_workflow.py:1063` `_validate_run_params`: `message` excludes both `responses` and
  `checkpoint_id`; `responses` with `checkpoint_id` is allowed (line 1074).
- `_workflow.py:1088` `_resolve_execution_mode`: with both set it binds
  `_restore_and_send_responses` (line 1099).
- `_workflow.py:1109` `_restore_and_send_responses`: restore from the checkpoint (line 1130), then
  `_send_responses_internal` (line 1131).
- `_runner.py:288` restore: load, check the graph signature, clear and import workflow state,
  restore executor states (line 337), restore edge state, then `apply_checkpoint` (line 341).
- `_runner_context.py:516` `apply_checkpoint` restores messages and re-registers every pending
  request from the checkpoint (line 530).
- `_workflow.py:1133` to 1151 `_validate_responses_internal`: each response must name a pending
  request id ("No pending requests found", line 1139; "Response provided for unknown request
  ID", line 1145) and is coerced to the recorded response type (`_coerce_request_info_response`,
  line 68).
- `_workflow.py:1153` `_send_responses_internal` delivers the responses to the requesting
  executors, then records a response-entry checkpoint (comment at line 1171) so a crash right
  after delivery is itself replayable.
- `_workflow.py:1004` a `request_info` event whose id is answered in the same call is not
  re-emitted to the caller.
- `_workflow.py:1421` `resolve_pause_checkpoint_id(request_ids, *, checkpoint_storage=None, ...)`
  returns the persisted checkpoint whose pending requests cover the given ids, or `None`. Process
  A uses it, then loads the checkpoint to double check.
- `_events.py:88` `WorkflowRunState.IDLE_WITH_PENDING_REQUESTS`; `_workflow.py:422` `status`.
  The upstream test notes that the final status can still read IDLE_WITH_PENDING_REQUESTS after a
  restore with responses, so the spike judges by the outputs and the publish file, not by status.

### Upstream evidence that the combined call works in one process

- Sample `python/samples/03-workflows/checkpoint/checkpoint_with_human_in_the_loop.py` (sha256
  38955ecef5ac...89fc8): file storage, `request_info`, `@response_handler`, resume of a
  fresh workflow instance from a checkpoint id.
- Test `python/packages/core/tests/workflow/test_request_info_event_rehydrate.py` line 312,
  `test_checkpoint_restore_with_responses_does_not_reemit_handled_requests`: a second workflow
  instance, `run(checkpoint_id=..., responses=...)`, FileCheckpointStorage with an explicit
  allow-list. It runs in a single interpreter. **The cross-process, killed-process case is what
  this spike adds; it is not shown upstream.**

## Findings that shape Agon

1. **A checkpoint is not consumed by a resume.** Nothing in the restore path marks a checkpoint
   used or deletes it, so the same pause checkpoint can be resumed with the same answer again.
   Single publish must come from our side: in the spike a create-only file, in Agon the
   etag-conditional `approvals` write and the Publisher's `If-None-Match: *`. The driver's replay
   step tests exactly this.
2. **A silent save failure is possible** (runner warns, run continues). Any worker that reports
   "awaiting approval" must first load the checkpoint and see the request in it.
3. **Pin types and module names.** The allow-list, the executor ids, the workflow name and the
   module path of every executor and checkpoint type are part of the persisted contract. They are
   pinned as literals in `tests/test_spike_s1_bundle.py`.
4. **Fallback (our own CheckpointStorage plus re-emission) is not implemented.** The source
   supports the framework path, so the fallback is built only if the CI driver shows the native
   path failing across processes.
