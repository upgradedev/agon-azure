"""Agon publish side: per-destination payloads, the destination adapters and the Publisher job.

* `agon.publish.render`: the exact payload of every destination, rendered at seal time from the
  checked English edition and sealed into the bundle (one sha256 per offered destination). The
  Instagram and YouTube renderers validate the platforms' published limits in code.
* `agon.publish.adapters`: one adapter per destination. Each plans only files under its own
  folder, refuses a destination the approval record does not list, and gets no store and no
  writable sink (`agon.governance.publisher` writes what they plan).
* `agon.publish.job` and `python -m agon.publish`: the Publisher job. It drains its target's
  queue, reads the sealed manifest from Cosmos `bundles`, fetches every artifact from the private
  `artifacts` container, recomputes every hash first and writes create-only blobs.
* `agon.publish.webview`: the `destinations` key of the agon.web-run/1 run format.

Standard library only at import time; the Azure SDK is imported where the adapters to it are built.
"""
