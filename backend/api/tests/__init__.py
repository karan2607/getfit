"""
Test package for the api app.

Hard network guard: blank the Gemini key for the whole test run. Several views
import `call_gemini_json` *inside* the function body, so patching
`api.views.call_gemini_json` does not reach them -- patch `api.gemini` instead.
With no key, any call that slips past a patch raises GeminiError immediately
rather than making a real request and hanging the suite.
"""

import os

os.environ['GEMINI_API_KEY'] = ''
os.environ['SENTRY_DSN'] = ''
