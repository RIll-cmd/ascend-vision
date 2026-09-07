import json

import httpx
from google import genai
from google.genai import types

from config import FeedbackConfig
from feedback import GeminiRoaster, RoastContext


def test_real_sdk_serializes_only_text_metadata():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP',
            'content': {'role': 'model', 'parts': [{'text': 'Your phone can wait.'}]}}]})
    with genai.Client(api_key='test-only', vertexai=False, http_options=types.HttpOptions(
            client_args={'transport': httpx.MockTransport(handler)},
            retry_options=types.HttpRetryOptions(attempts=1))) as client:
        text = GeminiRoaster(FeedbackConfig(), client=client).generate(RoastContext(3, 1, 10., '14:20'))
    assert text == 'Your phone can wait.'
    assert len(requests) == 1
    assert requests[0].url.host == 'generativelanguage.googleapis.com'
    assert requests[0].url.path.endswith('/models/gemini-3.6-flash:generateContent')
    body = json.loads(requests[0].content)
    assert set(body) == {'contents', 'systemInstruction', 'generationConfig'}
    assert len(body['contents']) == 1
    parts = body['contents'][0]['parts']
    assert len(parts) == 1 and set(parts[0]) == {'text'}
    assert set(json.loads(parts[0]['text'])) == {'pickups_today', 'pickups_this_session',
                                             'session_duration_minutes', 'time_of_day'}
