import os
import json
import logging
import urllib.request
import urllib.error
from typing import Generator

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta/models'

# Chosen from this repo's own eval harness, not a leaderboard.
#
# Measured on freshly generated 8-week plans (manage.py eval_plans --live):
#
#   gemini-2.5-flash        0.970   28/515 rep-scheme, 15/481 type  <- previous
#   gemini-3.5-flash        0.977   0, 0
#   gemini-3.8-flash        0.978   0, 8/206
#   gemini-4-argon          0.986   0, 0
#   gemini-3.1-pro-preview  0.988   0, 4/424
#
# 2.5-flash was the only model emitting unconventional rep schemes at all, and
# the worst on measurement-type agreement. The 0.986/0.988 difference between
# the top two is inside the noise at these sample sizes; gemini-4-argon is
# preferred because -preview models can change or be withdrawn under us, and
# because it posted zero failures on every rule except exercise naming.
#
# Naming is the top failure for EVERY model (32-115 occurrences). No model
# fixes it -- that is what the canonical registry in exercise_names.py is for.
#
# Override per deployment with GEMINI_MODEL; re-measure with
# `manage.py eval_plans --live --model=a,b --n=3`.
DEFAULT_MODEL = 'gemini-4-argon'

# CHAT runs on a cheaper model than generation, deliberately.
#
# The table above measures PLAN GENERATION, and it is why this is a separate
# constant rather than a change to DEFAULT_MODEL: 2.5-flash scored lowest of
# the five (0.970), was the only model emitting unconventional rep schemes at
# all, and was worst on measurement-type agreement -- the exact two faults
# upgrade steps 4 and 6 exist to clean up afterwards. Reverting generation to
# it would re-introduce them to save money on a call that runs once a week.
#
# Chat is the opposite shape: short turns, many of them, and no structured
# plan JSON to get wrong. $0.30/$2.50 per million against Argon's $2/$10, and
# free-tier eligible within rate limits.
#
# Override per deployment with GEMINI_CHAT_MODEL.
CHAT_MODEL = 'gemini-2.5-flash'

# Kept so existing imports and log greps keep working; the model used per call
# now comes from _model() so it can be overridden without editing this file.
BASE_URL = '%s/%s' % (API_ROOT, DEFAULT_MODEL)


class GeminiError(Exception):
    pass


def _api_key() -> str:
    key = os.getenv('GEMINI_API_KEY', GEMINI_API_KEY)
    if not key:
        raise GeminiError('GEMINI_API_KEY not configured')
    return key


def _model(override: str = None) -> str:
    """
    Which model to call.

    Resolved per call rather than at import, so GEMINI_MODEL can be set in the
    environment (or overridden per call by the eval harness) without a reload.
    The model used to be baked into the URL path, which meant comparing two
    models required editing this module -- so "do we need a better model?" was
    unanswerable by construction.
    """
    return override or os.getenv('GEMINI_MODEL') or DEFAULT_MODEL


def _chat_model(override: str = None) -> str:
    """
    Which model to call for CHAT, which is not the generation model.

    Falls back to DEFAULT_MODEL only if CHAT_MODEL is somehow empty, so a
    misconfiguration degrades to the better model rather than to none.
    """
    return (override or os.getenv('GEMINI_CHAT_MODEL') or CHAT_MODEL
            or DEFAULT_MODEL)



# Per-million USD, for the log line only. Rough by design: it exists so a cost
# question has an answer in the logs, not to bill anyone.
_PRICES = {
    'gemini-4-argon': (2.00, 10.00),
    'gemini-2.5-flash': (0.30, 2.50),
    'gemini-3.5-flash-lite': (0.30, 2.50),
    'gemini-3.8-flash': (0.75, 3.75),
}


def _log_usage(action: str, model: str, data: dict):
    """
    Record what a call actually cost.

    Nothing logged usage before this: every log line in the module was an
    error path, so "where did the spend go?" was unanswerable from the logs
    and had to be reconstructed by reading call sites and guessing.

    It was worth knowing. Chat ran on the generation model with the WHOLE
    plan in the system prompt -- ~32,000 tokens on every turn, about $0.07 an
    exchange just to ask a question, and ~$0.38 if the model re-emitted the
    plan. None of that was visible anywhere.

    Never raises: a logging failure must not fail the call it describes.
    """
    try:
        usage = (data or {}).get('usageMetadata') or {}
        prompt = usage.get('promptTokenCount') or 0
        total = usage.get('totalTokenCount') or 0
        output = max(total - prompt, 0)
        price_in, price_out = _PRICES.get(model, (0.0, 0.0))
        cost = prompt / 1e6 * price_in + output / 1e6 * price_out
        logger.info('gemini usage action=%s model=%s in=%d out=%d ~$%.4f',
                    action, model, prompt, output, cost)
    except Exception:                                   # noqa: BLE001
        pass


def _endpoint(action: str, model: str = None) -> str:
    return '%s/%s:%s?key=%s' % (API_ROOT, _model(model), action, _api_key())


def _generation_config(temperature: float = None, json_output: bool = True) -> dict:
    config = {}
    if json_output:
        config['response_mime_type'] = 'application/json'
    if temperature is not None:
        config['temperature'] = temperature
    return config


def call_gemini_json(*, system_prompt: str, user_prompt: str,
                     model: str = None, temperature: float = None) -> dict:
    """
    `model` and `temperature` are for the eval harness sweeping candidates.
    Both default to the configured behaviour, so every existing caller is
    unaffected and no request shape changes unless a value is passed.
    """
    import time
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': _generation_config(temperature),
    }).encode()

    url = _endpoint('generateContent', model)
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})

    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            _log_usage('generateContent', _model(model), data)
            text = data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # RETRY, do not give up.
            #
            # This used to raise on the first occurrence, inside the retry
            # loop, so only HTTP 429/503 ever got a second attempt. A single
            # malformed response therefore became a 503 for the user -- which
            # is what "Generate plan is throwing request fail" was.
            #
            # It is not rare at this output size: an eval sweep of four models
            # generating 8-week plans (~25k output tokens each) hit 2 failures
            # in 14 attempts, ~14%. The likely mechanism is the response being
            # truncated at the model's output ceiling, which leaves JSON that
            # cannot parse. Retrying is the cheap mitigation; generating one
            # week per call is the real fix.
            logger.error(
                'Gemini returned unparseable JSON (attempt %d/3, %d chars)',
                attempt + 1, len(locals().get('text') or ''))
            last_exc = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise GeminiError(
                'AI returned a malformed response three times. If you are '
                'generating a long plan, try fewer weeks.') from exc
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            logger.error('Gemini HTTP error %s (attempt %d): %s', exc.code, attempt + 1, body)
            if exc.code in (429, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            if exc.code == 429:
                raise GeminiError('AI service busy, please try again shortly')
            raise GeminiError(f'AI request failed ({exc.code})')
        except Exception as exc:
            logger.error('Gemini error: %s', exc, exc_info=True)
            raise GeminiError('AI request failed') from exc
    raise GeminiError('AI service unavailable, please try again') from last_exc


def call_gemini_vision_text(*, system_prompt: str, user_prompt: str, image_bytes: bytes,
                            mime_type: str, model: str = None) -> str:
    """Like call_gemini_vision_json but returns raw text instead of parsed JSON."""
    import base64
    b64 = base64.b64encode(image_bytes).decode()
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [
            {'inline_data': {'mime_type': mime_type, 'data': b64}},
            {'text': user_prompt},
        ]}],
    }).encode()
    url = _endpoint('generateContent', model)
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        _log_usage('vision', _model(model), data)
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as exc:
        raise GeminiError('AI request failed') from exc


def call_gemini_vision_json(*, system_prompt: str, user_prompt: str, image_bytes: bytes,
                            mime_type: str, model: str = None,
                            temperature: float = None) -> dict:
    import base64
    b64 = base64.b64encode(image_bytes).decode()

    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{
            'role': 'user',
            'parts': [
                {'inline_data': {'mime_type': mime_type, 'data': b64}},
                {'text': user_prompt},
            ],
        }],
        'generationConfig': _generation_config(temperature),
    }).encode()

    url = _endpoint('generateContent', model)
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        text = data['candidates'][0]['content']['parts'][0]['text']
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError('AI returned non-JSON response') from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error('Gemini vision HTTP error %s: %s', exc.code, body)
        if exc.code == 429:
            raise GeminiError('AI service busy, please try again shortly')
        raise GeminiError(f'AI request failed ({exc.code})')
    except Exception as exc:
        logger.error('Gemini vision error: %s', exc, exc_info=True)
        raise GeminiError('AI request failed') from exc


def stream_gemini_chat(*, system_prompt: str, history: list[dict],
                       model: str = None,
                       temperature: float = 0.7) -> Generator[str, None, None]:
    """
    Stream a chat reply.

    Defaults to CHAT_MODEL, not DEFAULT_MODEL -- this is the one call site
    that is chat, so the cheaper model is chosen here rather than at every
    caller. `model` still overrides, which is what the eval harness uses.
    """
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': history,
        'generationConfig': {'temperature': temperature},
    }).encode()

    # _chat_model, not _model: chat is the cheap path. See CHAT_MODEL.
    chosen = _chat_model(model)
    url = _endpoint('streamGenerateContent', chosen) + '&alt=sse'
    last_usage = None
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(req) as resp:
            buffer = b''
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                buffer += chunk
                lines = buffer.split(b'\n')
                buffer = lines[-1]
                for line in lines[:-1]:
                    line = line.strip()
                    if line.startswith(b'data: '):
                        try:
                            data = json.loads(line[6:])
                            # usageMetadata rides the final frame, and only
                            # that one carries the totals for the whole turn.
                            if data.get('usageMetadata'):
                                last_usage = data
                            text = data['candidates'][0]['content']['parts'][0].get('text', '')
                            if text:
                                yield text
                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error('Gemini stream HTTP error %s: %s', exc.code, body)
        if exc.code == 429:
            raise GeminiError('AI service busy, please try again shortly')
        raise GeminiError(f'AI stream failed ({exc.code})')
    except Exception as exc:
        logger.error('Gemini stream error: %s', exc, exc_info=True)
        raise GeminiError('AI stream failed') from exc
    finally:
        # In a `finally` so a turn that failed part-way is still costed --
        # those tokens were spent and billed whether or not the user saw them.
        if last_usage is not None:
            _log_usage('streamGenerateContent', chosen, last_usage)
