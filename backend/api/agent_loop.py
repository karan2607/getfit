"""
The tool-calling loop behind chat.

Replaces a single free-form text call with: give the model tools, let it ask
for data, hand back results, repeat until it answers. The previous chat had
NO tools at all -- it was told about a plan in its system prompt and asked to
describe it, which is why it could talk about a plan but never read one, and
why "what did I lift last week" was answered from a prompt blob rather than
from the database.

Contract
--------
`run` is a GENERATOR yielding text chunks, matching `stream_gemini_chat`, so
`chat_message_stream`'s SSE framing is untouched. It may also yield a dict
`{'status': ...}`, which the view emits as its own frame and does NOT append
to the saved reply -- progress is not part of the answer. Everything the view needs
afterwards -- tool calls made, a proposed change -- is collected on the
`AgentRun` the caller passes in, because a generator cannot return a value
the caller can see.

Why not the SDK's tool runner
-----------------------------
`client.beta.messages.tool_runner` would drive this loop, but it owns the
turn: tools execute inside it and text arrives at the end. Chat has to stream
text as it is produced and record every tool call in `ToolCallLog` with its
own timing, so the loop is written out here.
"""

import json
import logging

from . import agent_tools, claude

logger = logging.getLogger(__name__)

#: A turn that has not answered after this many rounds of tool calls is
#: looping. Each round is a billed request, so this is a cost ceiling as much
#: as a correctness one. Four is comfortably above the 1-2 a real answer
#: takes.
MAX_ROUNDS = 4

#: Tool results are small (a plan week, a handful of logs). The ceiling is
#: here so a pathological result cannot push the request over Haiku's 100K
#: price cliff, where the WHOLE request reprices.
MAX_RESULT_CHARS = 20000


class AgentRun:
    """Everything the view needs after the generator is exhausted."""

    def __init__(self):
        self.tool_calls = []
        self.rounds = 0
        self.stopped_early = False
        self.model = ''
        #: The draft a `propose_plan_change` call produced, if any. Lifted out
        #: of the tool result so the view can put it in the `done` payload:
        #: the frontend renders a confirm card from it, and the user approves
        #: the exact draft they were shown rather than a paraphrase of it in
        #: the reply text.
        self.proposed_change = None

    def record(self, name, arguments, summary, result=None):
        self.tool_calls.append({
            'tool': name,
            'arguments': arguments,
            'result_summary': summary,
        })
        if name == 'propose_plan_change' and isinstance(result, dict):
            if result.get('change_id'):
                self.proposed_change = result


#: What each tool is doing, in the user's terms. The tool name is a poor
#: status line -- "list_workout_plans" tells someone waiting nothing about
#: whether anything is happening.
_DOING = {
    'get_plan_week': 'Reading week {week_number} of your plan',
    'get_workout_plan': 'Reading your plan',
    'list_workout_plans': 'Finding your plan',
    'get_todays_workout': "Looking up today's workout",
    'get_exercise_history': 'Checking your history for {exercise_name}',
    'list_workout_sessions': 'Looking through your recent sessions',
    'get_workout_session': 'Reading that session',
    'get_profile': 'Checking your profile',
    'get_health_summary': 'Reading your health data',
    'get_health_series': 'Reading your health trend',
    'propose_plan_change': 'Drafting the change for you to confirm',
    'log_set': 'Logging that set',
    'log_meal': 'Logging that meal',
    'set_body_weight': 'Saving your weight',
    'update_personal_notes': 'Updating your notes',
    'update_exercise_notes': 'Updating the exercise notes',
    'get_meal_logs': 'Reading your meal log',
    'list_diet_plans': 'Finding your diet plan',
    'get_diet_plan': 'Reading your diet plan',
}


def describe(name, arguments):
    """
    A human line for one tool call.

    Falls back to the tool name rather than inventing a description: a
    status line that guesses is worse than one that is merely terse.
    """
    template = _DOING.get(name)
    if not template:
        return name.replace('_', ' ')
    try:
        return template.format(**{k: v for k, v in (arguments or {}).items()
                                  if v not in (None, '')})
    except (KeyError, IndexError):
        return template.split(' {')[0]


def _signature(name, arguments):
    """A stable key for one tool call, so a repeat is recognisable."""
    return name + '|' + json.dumps(arguments, sort_keys=True, default=str)


def _result_block(tool_use_id, payload, error):
    """One tool_result. A failure is returned, never dropped."""
    if error:
        body, is_error = error, True
    else:
        body = json.dumps(payload, default=str)
        if len(body) > MAX_RESULT_CHARS:
            body = body[:MAX_RESULT_CHARS] + '\n...[truncated]'
        is_error = False
    return {
        'type': 'tool_result',
        'tool_use_id': tool_use_id,
        'content': body,
        'is_error': is_error,
    }


def run(*, user, system_prompt, history, domain, run_state,
        session=None, model=None):
    """
    Stream one agent turn. Yields text chunks.

    `history` is the same shape `stream_gemini_chat` takes -- a list of
    {'role', 'parts'} -- and is translated here so the view does not have to
    know which provider it is talking to.
    """
    chosen = claude._model(model)
    run_state.model = chosen
    tools = agent_tools.anthropic_tools(domain)

    messages = []
    for entry in history:
        text = ''.join(p.get('text', '') for p in entry.get('parts', []))
        if not text:
            continue
        messages.append({
            'role': 'user' if entry.get('role') == 'user' else 'assistant',
            'content': text,
        })

    client = claude._client()
    #: Read-tool results already fetched this turn, keyed by call signature.
    seen = {}

    for round_number in range(MAX_ROUNDS):
        run_state.rounds = round_number + 1
        try:
            with client.messages.stream(
                model=chosen,
                max_tokens=8000,
                output_config={'effort': 'low'},
                system=system_prompt,
                tools=tools,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield text
                message = stream.get_final_message()
        except Exception as exc:                        # noqa: BLE001
            logger.error('agent stream failed: %s', exc, exc_info=True)
            raise claude.ClaudeError('AI request failed') from exc

        claude._log_usage('agent', chosen, message.usage)

        if message.stop_reason == 'refusal':
            raise claude.ClaudeError('AI declined this request')

        calls = [b for b in message.content
                 if getattr(b, 'type', '') == 'tool_use']
        if not calls:
            return                                  # the model answered

        messages.append({'role': 'assistant', 'content': message.content})

        # All results go back in ONE user message. Splitting them across
        # several silently teaches the model to stop making parallel calls.
        results = []
        for call in calls:
            arguments = dict(call.input or {})
            signature = _signature(call.name, arguments)
            # A status frame, not text: the view must not fold it into the
            # saved reply. Measured on staging, 30.7s of a 31.9s turn passed
            # before the first character -- all of it in rounds like this
            # one, with nothing on screen. Saying what is happening does not
            # make it faster, but it makes the wait legible instead of
            # looking like a hang.
            yield {'status': describe(call.name, arguments)}

            # A repeat of a call already made THIS turn is served from what
            # it returned the first time. Measured on staging:
            # `list_workout_plans` ran twice in one turn, and because text
            # only streams in the final round, that repeat cost a whole
            # round of silence. Re-running it could also return something
            # different mid-turn, which is worse than slow.
            #
            # Only read tools are cached. A write is never replayed from a
            # cache: the model asking to propose the same change twice means
            # two drafts, and quietly returning the first one would hide
            # that it tried.
            if signature in seen and call.name not in agent_tools.WRITE_TOOLS:
                payload, error = seen[signature]
                run_state.record(call.name, arguments,
                                 'repeat of an earlier call this turn',
                                 result=payload)
            else:
                payload, error = agent_tools.run_tool(
                    user, call.name, arguments, session=session, model=chosen)
                seen[signature] = (payload, error)
                run_state.record(call.name, arguments,
                                 error or agent_tools._summarise(payload),
                                 result=payload)
            results.append(_result_block(call.id, payload, error))
        messages.append({'role': 'user', 'content': results})

    # Out of rounds. Say so rather than stopping mid-thought: a turn that
    # just stops looks identical to a crash from the user's side.
    run_state.stopped_early = True
    yield ('\n\nI stopped after several lookups without reaching an answer. '
           'Could you narrow the question?')
