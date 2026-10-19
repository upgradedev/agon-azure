"""Run the decision intake against Azure: `python -m agon.intake [--once | --idle-exit SECONDS]`.

Configuration (environment, set by the Container Apps job in infra):

* `AGON_QUEUE_ACCOUNT_URL`  https://<storage account>.queue.core.windows.net
* `AGON_COSMOS_ENDPOINT`    https://<cosmos account>.documents.azure.com:443/
* `AGON_COSMOS_DATABASE`    the database name, `agon` by default
* `AZURE_CLIENT_ID`         client id of the user-assigned identity (id-api); empty in CI

Each drained message prints one JSON line (queue, message id, status, code, detail) to stdout,
which Container Apps ships to Log Analytics. Nothing secret is printed: the message text and the
nonce never appear in a log line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Sequence

from agon.intake.drain import DrainLine, drain_once


def _log(line: DrainLine) -> None:
    print(json.dumps({"agon": "intake", **asdict(line)}, sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain the Teams and Outlook decision queues into the one verifier.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="drain each queue once and exit")
    mode.add_argument("--idle-exit", type=int, default=0, help="loop, and exit after this many seconds with no message")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    queue_url = os.environ.get("AGON_QUEUE_ACCOUNT_URL", "")
    cosmos_endpoint = os.environ.get("AGON_COSMOS_ENDPOINT", "")
    if not queue_url or not cosmos_endpoint:
        print("AGON_QUEUE_ACCOUNT_URL and AGON_COSMOS_ENDPOINT must be set", file=sys.stderr)
        return 2

    from agon.governance.store_cosmos import connect
    from agon.intake.azure_queues import AzureQueuePort, managed_identity_credential

    credential = managed_identity_credential(os.environ.get("AZURE_CLIENT_ID") or None)
    store = connect(cosmos_endpoint, os.environ.get("AGON_COSMOS_DATABASE", "agon"), credential)
    port = AzureQueuePort(queue_url, credential)

    def clock() -> datetime:
        return datetime.now(timezone.utc)

    if args.once:
        drain_once(port, store, clock, log=_log)
        return 0
    idle_since = time.monotonic()
    while True:
        if drain_once(port, store, clock, log=_log):
            idle_since = time.monotonic()
        elif args.idle_exit and time.monotonic() - idle_since >= args.idle_exit:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
