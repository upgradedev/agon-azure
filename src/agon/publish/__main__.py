"""Run the Publisher job against Azure: `python -m agon.publish --target feed|sandbox [--once | --idle-exit SECONDS]`.

Configuration (environment, set by the Container Apps job in infra):

* `AGON_PUBLISH_TARGET`      `feed` or `sandbox` (the `--target` flag wins); picks the queue and
                             the one container this job writes
* `AGON_QUEUE_ACCOUNT_URL`   https://<storage account>.queue.core.windows.net
* `AGON_BLOB_ACCOUNT_URL`    https://<storage account>.blob.core.windows.net
* `AGON_COSMOS_ENDPOINT`     https://<cosmos account>.documents.azure.com:443/
* `AGON_COSMOS_DATABASE`     the database name, `agon` by default
* `AGON_ARTIFACT_CONTAINER`  the private artifact container, `artifacts` by default
* `AGON_PUBLIC_BASE_URL`     the public base for links in feeds and outbox messages, for example
                             https://<storage account>.blob.core.windows.net; links are
                             site-relative when unset
* `AZURE_CLIENT_ID`          client id of id-publisher (feed) or id-publisher-sandbox (sandbox)

Each message prints one JSON line (queue, message id, status, code, detail, bundle hash) to stdout,
which Container Apps ships to Log Analytics. Nothing secret exists in a publish request.
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

from agon.governance.verifier import TARGETS
from agon.publish.job import JobLine, drain_once


def _log(line: JobLine) -> None:
    print(json.dumps({"agon": "publish", **asdict(line)}, sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drain a publish queue into the governed Publisher.")
    parser.add_argument("--target", choices=TARGETS, default=os.environ.get("AGON_PUBLISH_TARGET") or None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="drain the queue once and exit")
    mode.add_argument("--idle-exit", type=int, default=0, help="loop, and exit after this many seconds with no message")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    required = ("AGON_QUEUE_ACCOUNT_URL", "AGON_BLOB_ACCOUNT_URL", "AGON_COSMOS_ENDPOINT")
    missing = [name for name in required if not os.environ.get(name)]
    if args.target is None or missing:
        print(f"set --target (or AGON_PUBLISH_TARGET) and {', '.join(required)}; missing: {', '.join(missing) or 'target'}", file=sys.stderr)
        return 2

    from agon.governance.publisher import Publisher
    from agon.governance.store_cosmos import connect
    from agon.intake.azure_queues import AzureQueuePort, managed_identity_credential
    from agon.publish.adapters import default_adapters
    from agon.publish.azure_blob import BlobArtifactSource, BlobSink, blob_service
    from agon.publish.sources import ARTIFACT_CONTAINER

    credential = managed_identity_credential(os.environ.get("AZURE_CLIENT_ID") or None)
    store = connect(os.environ["AGON_COSMOS_ENDPOINT"], os.environ.get("AGON_COSMOS_DATABASE", "agon"), credential)
    service = blob_service(os.environ["AGON_BLOB_ACCOUNT_URL"], credential)
    source = BlobArtifactSource(service, os.environ.get("AGON_ARTIFACT_CONTAINER") or ARTIFACT_CONTAINER)
    publisher = Publisher(store, BlobSink(service, args.target), args.target, default_adapters(os.environ.get("AGON_PUBLIC_BASE_URL", "")))
    port = AzureQueuePort(os.environ["AGON_QUEUE_ACCOUNT_URL"], credential)

    def clock() -> datetime:
        return datetime.now(timezone.utc)

    def once() -> bool:
        return bool(drain_once(port, target=args.target, store=store, source=source, publisher=publisher, clock=clock, log=_log))

    if args.once:
        once()
        return 0
    idle_since = time.monotonic()
    while True:
        if once():
            idle_since = time.monotonic()
        elif args.idle_exit and time.monotonic() - idle_since >= args.idle_exit:
            return 0
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
