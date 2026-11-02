"""
The tool surface the chat agent sees.

Built ON TOP of `mcp.TOOLS` rather than beside it. That registry already
holds a description, a JSON Schema and a handler for all 22 tools, every
handler is user-scoped, and it is covered by 87 tests. A second
implementation for the agent would be a second set of scoping bugs, and the
two would drift the first time one gained a field.

So this module does three things and no more:

  1. translates an MCP tool definition into the provider's tool format
  2. decides which tools a given domain may see
  3. dispatches, and records the call in `ToolCallLog`

What a domain can reach
-----------------------
The router sends workouts to Claude and food to Gemini, so each side is given
only the tools it can act on. That is not cosmetic: a model offered
`log_meal` on a workout turn will eventually use it, and the cheapest way to
prevent a wrong tool call is for the tool not to be on the list.
"""

import time

from . import mcp

WORKOUT = 'workout'
FOOD = 'food'

# Which domain each MCP tool belongs to. Explicit rather than inferred from
# the name: `set_body_weight` reads as a workout tool and is body composition,
# and `get_profile` is needed by both.
_DOMAIN = {
    'get_profile': 'both',
    'update_personal_notes': 'both',

    'list_workout_plans': WORKOUT,
    'get_workout_plan': WORKOUT,
    'get_todays_workout': WORKOUT,
    'list_workout_sessions': WORKOUT,
    'get_workout_session': WORKOUT,
    'get_exercise_history': WORKOUT,
    'log_set': WORKOUT,
    'update_exercise_notes': WORKOUT,
    'get_health_summary': WORKOUT,
    'get_health_series': WORKOUT,
    'log_health_day': WORKOUT,
    'update_health_day': WORKOUT,
    'delete_health_day': WORKOUT,
    'log_health_workout': WORKOUT,
    'delete_health_workout': WORKOUT,

    'list_diet_plans': FOOD,
    'get_diet_plan': FOOD,
    'get_meal_logs': FOOD,
    'log_meal': FOOD,
    'set_body_weight': FOOD,
}


def tools_for(domain):
    """The MCP tool names this domain may call."""
    return sorted(
        name for name, assigned in _DOMAIN.items()
        if name in mcp.TOOLS and assigned in (domain, 'both'))


def anthropic_tools(domain):
    """
    MCP definitions in Anthropic's tool format.

    `strict: True` on every tool, with `additionalProperties: false`: it is
    what guarantees the arguments validate against the schema, and it is how
    determinism is obtained now that `temperature` is rejected outright.
    """
    out = []
    for name in tools_for(domain):
        spec = mcp.TOOLS[name]
        schema = dict(spec['schema'])
        schema['additionalProperties'] = False
        schema.setdefault('required', [])
        out.append({
            'name': name,
            'description': spec['description'],
            'input_schema': schema,
            'strict': True,
        })
    return out


def _summarise(result):
    """
    A short description of a result, never the result itself.

    Storing full payloads would duplicate the database into a log table and
    grow without bound; the arguments are what answer "what did it do".
    """
    if isinstance(result, dict):
        for key in ('detail', 'status', 'message'):
            if isinstance(result.get(key), str):
                return result[key][:500]
        return ('keys: %s' % ', '.join(sorted(result)[:8]))[:500]
    if isinstance(result, list):
        return '%d item(s)' % len(result)
    return str(result)[:500]


def run_tool(user, name, arguments, session=None, model=''):
    """
    Dispatch one tool call and record it.

    Returns (result, error). A refusal or failure comes back as an error
    string rather than an exception, because the agent loop has to hand the
    model a `tool_result` either way -- dropping a failed call silently
    teaches it the call succeeded.
    """
    from .models import ToolCallLog

    spec = mcp.TOOLS.get(name)
    started = time.monotonic()
    result = None
    error = ''

    if spec is None:
        error = 'unknown tool: %s' % name
    else:
        try:
            result = spec['handler'](user, arguments or {})
        except mcp.McpError as exc:
            error = str(exc)
        except Exception as exc:                        # noqa: BLE001
            error = 'tool failed: %s' % exc

    try:
        ToolCallLog.objects.create(
            user=user,
            session=session,
            tool=name[:64],
            arguments=arguments or {},
            result_summary='' if error else _summarise(result),
            model=(model or '')[:64],
            ok=not error,
            error=error[:300],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:                                   # noqa: BLE001
        # An audit-log failure must not fail the call it describes.
        pass

    return result, error
