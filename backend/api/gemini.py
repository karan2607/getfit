import os
import json
import logging
import urllib.request
import urllib.error
from typing import Generator

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta/models'
DEFAULT_MODEL = 'gemini-2.5-flash'

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
            text = data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiError('AI returned non-JSON response') from exc
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
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': history,
        'generationConfig': {'temperature': temperature},
    }).encode()

    url = _endpoint('streamGenerateContent', model) + '&alt=sse'
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
