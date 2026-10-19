"""Approval channel adapters. Each one only translates its payload and then calls `verifier.verify`.

Rules every module in this package follows, enforced by `agon.governance.routes.audit_channels`
and by the M16 test (tests/routes):

* exposes `CHANNEL` (its name in `verifier.CHANNELS`) and a `handle(...)` entry point;
* reaches the verifier through the module attribute, `verifier.verify(...)`, so a test can
  intercept every call, never through `from ...verifier import verify`;
* sets `channel` and `received_at` itself, never from the payload;
* never touches the store directly and never builds a successful `VerifyResult` itself. It may
  refuse a payload it cannot translate, but it can never approve.

The payload shapes for Teams and Outlook are ASSUMPTIONS taken from the Logic Apps connector
documentation until gate T has run end to end.
"""
