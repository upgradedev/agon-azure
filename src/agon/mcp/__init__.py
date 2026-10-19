"""matchfacts: the read-only MCP server the agents use to reach match facts (spec 3.2 row 5).

Layout:
- `source`: the read-only data source protocol, an in-memory implementation and a Cosmos one
  behind a small reader interface. No write method exists at any layer.
- `tools`: the nine tools as plain functions returning quoted-data envelopes. Stdlib only.
- `matchfacts`: registers `tools.TOOLS` on a FastMCP server (Python MCP SDK, streamable HTTP).
  The SDK is pinned in requirements/mcp.txt and installed in CI only.

The referee package (ground truth) is unreachable from here; tests import every module of this
package in a fresh interpreter that refuses any referee import, then call every tool.
"""
