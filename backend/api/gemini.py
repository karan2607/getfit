import os
import json
import logging
import urllib.request
import urllib.error
from typing import Generator

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash'


class GeminiError(Exception):
    pass


def _api_key() -> str:
    key = os.getenv('GEMINI_API_KEY', GEMINI_API_KEY)
    if not key:
        raise GeminiError('GEMINI_API_KEY not configured')
    return key


def call_gemini_json(*, system_prompt: str, user_prompt: str) -> dict:
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': {'response_mime_type': 'application/json'},
    }).encode()

    url = f'{BASE_URL}:generateContent?key={_api_key()}'
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
        logger.error('Gemini HTTP error %s: %s', exc.code, body)
        if exc.code == 429:
            raise GeminiError('AI service busy, please try again shortly')
        raise GeminiError(f'AI request failed ({exc.code})')
    except Exception as exc:
        logger.error('Gemini error: %s', exc, exc_info=True)
        raise GeminiError('AI request failed') from exc


def call_gemini_vision_json(*, system_prompt: str, user_prompt: str, image_bytes: bytes, mime_type: str) -> dict:
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
        'generationConfig': {'response_mime_type': 'application/json'},
    }).encode()

    url = f'{BASE_URL}:generateContent?key={_api_key()}'
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


def stream_gemini_chat(*, system_prompt: str, history: list[dict]) -> Generator[str, None, None]:
    payload = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': history,
        'generationConfig': {'temperature': 0.7},
    }).encode()

    url = f'{BASE_URL}:streamGenerateContent?alt=sse&key={_api_key()}'
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
