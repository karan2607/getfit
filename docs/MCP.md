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

`tools/list` is generated from the registry, so it is always authoritative.
The table below is for orientation.

### Read-only

| Tool | Params | Purpose |
|---|---|---|
| `get_profile` | — | Body stats, goal, experience, dietary preference, notes |
| `list_workout_plans` | `active_only` | All plans, newest first |
| `get_workout_plan` | `plan_id`, `week_number` | One plan with days and exercises (defaults to active) |
| `list_workout_sessions` | `completed_only`, `limit` | Training logs |
| `get_workout_session` | **`session_id`** | One session with all set logs |
| `get_exercise_history` | **`exercise_name`**, `limit` | Full history for one exercise |
| `get_todays_workout` | — | Next scheduled day + loggable fields + open session id |
| `list_diet_plans` | `active_only` | Diet plans |
| `get_diet_plan` | `plan_id` | One plan with meals and macro targets |
| `get_health_summary` | `days` | Recent steps, calories, resting HR, sleep |
| `get_meal_logs` | `date`, `days` | Meals logged, with macro totals |

### Writes

| Tool | Params | Purpose |
|---|---|---|
| `log_set` | **`session_id`**, **`exercise_id`**, **`set_number`**, `weight`, `reps_completed`, `duration_seconds`, `is_completed` | Record a set result |
| `log_health_day` | `date`, `steps`, `active_calories`, `resting_calories`, `resting_heart_rate`, `sleep_hours` | Upsert one day of health metrics |
| `log_health_workout` | **`start_time`**, `external_id`, `activity_type`, `duration_seconds`, `calories`, `avg_heart_rate`, `distance_meters` | Upsert a workout from an external source |
| `log_meal` | **`meal_type`**, **`food_name`**, `date`, `calories`, `protein_g`, `carbs_g`, `fat_g`, `notes` | Record something eaten |
| `set_body_weight` | **`weight`**, `unit` | Record body weight |
| `update_exercise_notes` | **`exercise_id`**, **`notes`** | Replace one exercise's coaching notes |
| `update_personal_notes` | **`notes`** | Replace the user's remembered context |

**Bold** = required. Dates are ISO `YYYY-MM-DD` and default to today;
`start_time` is ISO 8601. There are deliberately **no plan-editing tools** — an
agent can record what happened but cannot restructure the training programme.

### Notes for a daily-sync agent

- `log_health_day` is an **upsert that merges**. Calling it twice in a day with
  different fields keeps both; it never nulls a value you set earlier. So you can
  post steps in the morning and sleep at night.
- Values outside plausible ranges (e.g. 5,000,000 steps) are **rejected**, not
  stored, and listed in the response's `problems` array. Read it — a successful
  call can still have skipped a field.
- `log_health_workout` keys on `external_id` per user, so re-sending the same
  workout updates it rather than duplicating. Omit it and one is derived from the
  start time.
- A malformed date is an error, not a silent default. Check for `isError`.

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
