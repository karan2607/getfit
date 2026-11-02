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
`chat_message_stream`'s SSE framing is untouched. Everything the view needs
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
            payload, error = agent_tools.run_tool(
                user, call.name, arguments, session=session, model=chosen)
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
