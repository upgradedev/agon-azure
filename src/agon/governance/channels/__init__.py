"""Approval channel adapters. Each one only translates its payload and then calls `verifier.verify`.

Rules every module in this package follows, enforced by `agon.governance.routes.audit_channels`
and by the M16 test (tests/routes):

* exposes `CHANNEL` (its name in `verifier.CHANNELS`) and a `handle(...)` entry point;
* reaches the verifier through the module attribute, `verifier.verify(...)`, so a test can
  intercept every call, never through `from ...verifier import verify`;
* sets `channel` and `received_at` itself, never from the payload;
* never touches the store directly and never builds a successful `VerifyResult` itself. It may
  refuse a payload it cannot translate, but it can never approve.

The Teams payload shape is confirmed: gate T read it from a live response on 2026-10-19, and
teams.handle has translated the live decision behind every recorded run in web/runs. Each of those
bundles is on the public feed, which the Publisher writes only after verify() has approved, and
Teams is the only decision route deployed (the Outlook flow is not; the intake job executions of
three of the runs are in evidence/run-*.executions.json). The approval channel in a web/runs record
is an export argument of agon.webrecord, not read from the approval record. The Outlook payload
shape is still an ASSUMPTION taken from the Logic Apps connector documentation: the Outlook
approval flow is not deployed.
"""
