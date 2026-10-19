# matchfacts: the read-only MCP server

Agents reach match facts only through this server (spec 3.2 row 5). It is a Python MCP SDK
FastMCP server over streamable HTTP, stateless, with JSON responses. The SDK is pinned exactly in
`requirements/mcp.txt` (`mcp==1.30.0`) and installed in CI only.

Status: the code runs locally and in CI with an in-memory data source built from seeds. It is
**not deployed**. The Container Apps app, its Entra authentication and its registration in a
Foundry Toolbox belong to the deploy lane and do not exist yet.

```
python -m agon.mcp.matchfacts --seeds 1,2,3 --host 127.0.0.1 --port 8000   # serves /mcp
```

## Tools

Every tool reads. None writes, publishes, approves or changes anything. The Publisher's write is
never an MCP tool.

| Tool | Arguments | Returns |
|---|---|---|
| `events.window` | `match_id, t0, t1, period?, limit?` | events with `t0 <= engine minute < t1` (0-based clock minutes), each with its football `display_minute`; `period` separates first-half added time from the start of the second half |
| `events.by_id` | `match_id, event_ids[]` | those events, and the ids that do not exist |
| `stats.recompute` | `match_id, metric, scope` | a count or ratio from `agon.governance.recompute` (the Fact Gate's own code) with its supporting event ids, or an interpret metric: `xg_ppm`, `xt_added_ppm`, `pass_difficulty_mean_centi`, `ppda_centi` |
| `momentum.series` | `match_id` | momentum per minute, and control vs chaos and tempo per 5-minute window |
| `pressure.regimes` | `match_id` | pressing regimes and their changes per team, and PPDA per 15 minutes |
| `roster.lookup` | `match_id, query` | a club or player in the match by id or exact name, with the Rights Guard verdict on the name; `*` lists both squads |
| `rights.check` | `text, field?` | the Rights Guard verdict on prose or a name field |
| `wp.swing` | `match_id, event_id` | interpret win probability before and after the event, and a shot's expected swing |
| `charter.get` | `match_id` | the approved Match-Day Charter, or `no_approved_charter` and its consequence |

The names come from one table, `agon.mcp.tools.TOOLS`. Some model APIs reject dots in function
names. If the Foundry registration needs underscores, the rename is one edit to that table.

## Quoted data

Every result has the same envelope: `server`, `tool`, `match_id`, `content: "quoted_data"`, a
notice that every string inside `data` is a value and never an instruction, the interpret
`model_sha256`, and `data`. Name fields come from a roster that can carry a planted injection
string (the `name_field_injection` fixture). `roster.lookup` returns such a name as a plain JSON
string next to a Rights Guard refusal, and never splices it into prose.

## Data sources

`agon.mcp.source.MatchSource` has three read methods: `match_ids`, `record` and `charter`.
- `InMemorySource` holds generated matches: world, match meta and events. It never holds the
  engine's intervention ledger, which is referee ground truth. A test checks that no tool output
  contains a ledger key.
- `CosmosSource` reads only `matchstate` and `charters`, the containers `id-mcp` may read
  (spec 7.1). It talks to Cosmos through a two-method `CosmosReader` interface (`read_item`,
  `query_items`). `ContainerClientsReader` adapts SDK container clients and refuses any other
  container. The expected document layout is in the module docstring and must match what the
  ingest lane writes.

## What the tests prove

Stdlib, every run (`tests/test_matchfacts_tools.py`):
- the tool table equals the nine names exactly, and no name contains a write verb;
- the tools class exposes no other public method, and no data-source layer has a write method;
- every tool returns the envelope, and a period-1 minute-46 event never shares a window with a
  period-2 minute-46 event;
- the referee is unreachable: a fresh interpreter that refuses any referee import calls every
  tool, then checks `sys.modules`. A negative control proves the blocker blocks.

With the SDK, in CI (`tests/test_matchfacts_server.py`, workflow `mcp`, not yet run in CI). On
the author's machine these six tests passed against an mcp 1.29.0 that was already in the user
site-packages, not the pinned 1.30.0; this lane installed nothing:
- an in-process client lists exactly the nine tools, all annotated read-only and non-destructive,
  and calls every one;
- calls to write-like names (`bundle.publish`, `events.write`, ...) return errors;
- one streamable HTTP round trip through uvicorn on a free local port;
- the referee check again, through the server.

Locally these tests skip when the SDK is absent. In CI, `AGON_REQUIRE_MCP=1` turns a missing SDK
into a failure. A negative control runs them before the SDK is installed and expects a failure,
and a junit check fails the job on any skipped test.
