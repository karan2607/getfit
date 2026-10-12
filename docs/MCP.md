# MCP server

Exposes GetFit to external AI agents over the Model Context Protocol, so another
agent can read your plans and training history and write results back.

- **Endpoint:** `POST /api/mcp/`
- **Transport:** JSON-RPC 2.0 over HTTP POST
- **Auth:** the same DRF token the app uses — `Authorization: Token <key>`
- **Implementation:** [`backend/api/mcp.py`](../backend/api/mcp.py) (tool registry)
  and `mcp_endpoint` in [`backend/api/views.py`](../backend/api/views.py)

## Transport caveat

This is the POST half of MCP's Streamable HTTP transport: request/response only,
no SSE stream and no session resumption. That covers every tool call, because all
tools here are plain request/response — but a client that *requires* an SSE
channel needs a bridge such as `mcp-remote`:

```jsonc
// Claude Desktop / Claude Code MCP config
{
  "mcpServers": {
    "getfit": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://<your-backend>.onrender.com/api/mcp/",
        "--header", "Authorization: Token ${GETFIT_TOKEN}"
      ],
      "env": { "GETFIT_TOKEN": "<your-40-char-token>" }
    }
  }
}
```

Get a token by logging in (`POST /api/auth/login/`) — tokens don't expire.

## Methods

| Method | Notes |
|---|---|
| `initialize` | Returns protocol version and `serverInfo` |
| `notifications/*` | Accepted and ignored |
| `ping` | Health check |
| `tools/list` | Generated from the registry, so it never drifts from the handlers |
| `tools/call` | `params: { name, arguments }` |

## Tools

Read-only:

| Tool | Purpose |
|---|---|
| `get_profile` | Body stats, goal, experience, dietary preference, personal notes |
| `list_workout_plans` | All plans, newest first |
| `get_workout_plan` | One plan with days and exercises (defaults to the active one) |
| `list_workout_sessions` | Training logs |
| `get_workout_session` | One session with all set logs |
| `get_exercise_history` | Full history for one exercise by name |
| `list_diet_plans` / `get_diet_plan` | Diet plans with meals and macros |
| `get_health_summary` | Recent steps, calories, resting HR, sleep |

Writes:

| Tool | Purpose |
|---|---|
| `log_set` | Record a set result in an active session |
| `update_exercise_notes` | Replace one exercise's coaching notes |
| `update_personal_notes` | Replace the user's remembered context |

## Two things an agent must understand

**1. Units are explicit.** Weights cross this boundary in the user's preferred
unit, with the unit named in the payload (`weight`, `weight_unit`). Storage is
always kilograms; conversion happens at the boundary. Never assume kg.

**2. Exercises are typed.** Every exercise carries a `measurement_type` and a
`loggable_fields` list. Only those fields mean anything:

| `measurement_type` | Loggable |
|---|---|
| `weight_reps` | `weight`, `reps_completed` |
| `weight_duration` | `weight`, `duration_seconds` |
| `duration` | `duration_seconds` |
| `bodyweight_reps` | `reps_completed` |
| `warmup` | none — completion only |

`log_set` **silently drops** fields that don't apply and reports them in
`ignored_fields`. So sending `weight` for a plank is not an error, but it is also
not recorded — read `ignored_fields` to notice. This is deliberate: the same
contract is enforced for the web app, and it exists because seconds used to be
written into the weight column, which made a 180-second plank show up as a
396.8 lb personal best.

## Errors

- Unknown method or unknown tool → JSON-RPC `error` (`-32601`)
- `tools/call` without `name` → JSON-RPC `error` (`-32602`)
- Tool failures (bad arguments, missing or not-yours object) → a **successful**
  response with `isError: true` and the message in `content[0].text`, so the
  agent can read and recover

Every tool scopes its queries to the authenticated user. Another user's data
returns "not found" rather than existing-but-forbidden, and there is no
cross-user access of any kind. This is covered by
`CrossUserIsolationTests` in [`backend/api/tests/test_mcp.py`](../backend/api/tests/test_mcp.py).

## Capacity

Render runs gunicorn with `--workers 1 --threads 4`, so agent traffic competes
with real app requests. Raise the thread count in
[`backend/render.yaml`](../backend/render.yaml) before pointing anything chatty
at this.

## Quick check

```bash
TOKEN=<your-token>
BASE=https://<your-backend>.onrender.com

curl -s -X POST "$BASE/api/mcp/" \
  -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools[].name'

curl -s -X POST "$BASE/api/mcp/" \
  -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_workout_plan","arguments":{}}}' | jq '.result.structuredContent'
```
