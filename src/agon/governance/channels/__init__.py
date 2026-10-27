"""Approval channel adapters. Each one only translates its payload and then calls `verifier.verify`.

Rules every module in this package follows, enforced by `agon.governance.routes.audit_channels`
and by the M16 test (tests/routes):

* exposes `CHANNEL` (its name in `verifier.CHANNELS`) and a `handle(...)` entry point;
* reaches the verifier through the module attribute, `verifier.verify(...)`, so a test can
  intercept every call, never through `from ...verifier import verify`;
* sets `channel` and `received_at` itself, never from the payload;
* never touches the store directly and never builds a successful `VerifyResult` itself. It may
  refuse a payload it cannot translate, but it can never approve.

The Teams payload shape is confirmed: gate T read it from a live response on 2026-10-19, and every
recorded run in web/runs was approved on the Teams card (approval channel "teams") and verified by
the deployed intake job (job executions of three of them in evidence/run-*.executions.json). The
Outlook payload shape is still an ASSUMPTION taken from the Logic Apps connector documentation: the
Outlook approval flow is not deployed.
"""
