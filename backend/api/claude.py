"""
Claude client wrapper, shaped like `gemini.py` so the two are comparable.

Deliberately mirrors that module's contract -- same function shape, same
error type, same usage logging -- so a call site can be routed to either
provider without knowing which it got, and so the eval harness can score them
against each other on identical prompts.

Model choice
------------
`claude-haiku-5-5` is the cheap, fast tier: $0.10/$0.50 per million up to
100K input tokens, then $0.50/$2.50 for the WHOLE request beyond that. The
cliff is why callers send one week rather than a whole plan -- crossing it
multiplies the entire request, not just the overage.

API notes that are easy to get wrong
------------------------------------
* No `temperature` / `top_p` / `top_k`. Haiku 5.5 rejects non-default values
  with a 400, and from 2026-10-21 the API rejects them on every model.
  Determinism comes from `strict: true` tool schemas and structured outputs.
* Thinking is adaptive and on by default; `effort` is the control, and its
  default is `medium`. Chat and extraction run at `low`.
* No assistant prefill -- it 400s. Response shape is steered by the prompt or
  by structured outputs.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-haiku-5-5'

# Per-million USD (input, output) for prompts up to 100K tokens. For the log
# line only; see `gemini._log_usage` for why this exists at all.
_PRICES = {
    'claude-haiku-5-5': (0.10, 0.50),
    'claude-sonnet-5-5': (2.00, 10.00),
    'claude-opus-5-5': (4.00, 20.00),
}


class ClaudeError(Exception):
    """Mirrors GeminiError so call sites can catch one shape."""


def _model(override=None):
    return override or os.getenv('ANTHROPIC_MODEL') or DEFAULT_MODEL


def _client():
    import anthropic

    if not os.getenv('ANTHROPIC_API_KEY'):
        # Workload identity federation also satisfies the SDK, so an absent
        # key is not proof of no credentials -- let the SDK decide and report.
        logger.debug('ANTHROPIC_API_KEY unset; relying on the SDK credential chain')
    return anthropic.Anthropic()


def _log_usage(action, model, usage):
    """What the call cost. Never raises."""
    try:
        prompt = getattr(usage, 'input_tokens', 0) or 0
        output = getattr(usage, 'output_tokens', 0) or 0
        price_in, price_out = _PRICES.get(model, (0.0, 0.0))
        cost = prompt / 1e6 * price_in + output / 1e6 * price_out
        logger.info('claude usage action=%s model=%s in=%d out=%d ~$%.4f',
                    action, model, prompt, output, cost)
    except Exception:                                   # noqa: BLE001
        pass


def _text_of(message):
    return ''.join(b.text for b in message.content if getattr(b, 'type', '') == 'text')


def _strip_fence(text):
    """
    Unwrap a ```json fence if the model added one.

    Gemini is told `response_mime_type: application/json` and cannot fence;
    Claude has no equivalent on a plain call, so this keeps the two
    comparable instead of scoring one of them down for punctuation.
    """
    stripped = text.strip()
    if not stripped.startswith('```'):
        return stripped
    body = stripped.split('\n', 1)[1] if '\n' in stripped else ''
    if body.rstrip().endswith('```'):
        body = body.rstrip()[:-3]
    return body.strip()


def call_claude_json(*, system_prompt, user_prompt, model=None,
                     max_tokens=16000, effort='low'):
    """
    One JSON-returning call. The twin of `gemini.call_gemini_json`.

    Raises ClaudeError on anything the caller cannot act on, so call sites
    handle one failure shape regardless of provider.
    """
    chosen = _model(model)
    try:
        message = _client().messages.create(
            model=chosen,
            max_tokens=max_tokens,
            output_config={'effort': effort},
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
    except Exception as exc:                            # noqa: BLE001
        logger.error('Claude request failed: %s', exc, exc_info=True)
        raise ClaudeError('AI request failed') from exc

    _log_usage('messages.create', chosen, message.usage)

    if message.stop_reason == 'refusal':
        raise ClaudeError('AI declined this request')
    if message.stop_reason == 'max_tokens':
        # Truncated JSON parses as garbage; say what actually happened rather
        # than reporting it as a malformed response.
        raise ClaudeError('AI response was cut off (max_tokens)')

    try:
        return json.loads(_strip_fence(_text_of(message)))
    except json.JSONDecodeError as exc:
        logger.error('Claude returned non-JSON (%d chars)',
                     len(_text_of(message)))
        raise ClaudeError('AI returned non-JSON response') from exc
