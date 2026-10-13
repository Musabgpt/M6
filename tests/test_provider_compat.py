"""Provider compatibility: error reasons, stream bodies, and the retry for models that reject tool roles.

Regression for the phone report: after one tool call, kilo-auto/free answered every request with
"malformed response" and the whole session stopped working.
"""

import io
import json
import unittest
import urllib.error

from newal_mobile.providers import (OpenAICompatibleProvider, ProviderError, flatten_tool_history,
                                    parse_response)

TOOL_HISTORY = [
    {"role": "system", "content": "s"},
    {"role": "user", "content": "make a calculator"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"calc.py\"}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true, \"output\": \"1: print(1)\"}"},
]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingOpener:
    """Returns one body per call and records the request bodies it received."""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(json.loads(request.data))
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body if isinstance(body, bytes) else json.dumps(body).encode())


def provider(opener):
    return OpenAICompatibleProvider("p", "https://example.invalid/v1", "m", urlopen=opener)


class ParseTests(unittest.TestCase):
    def test_error_payload_with_status_200_shows_the_reason(self):
        with self.assertRaises(ProviderError) as ctx:
            parse_response(json.dumps({"error": {"message": "model does not support tools"}}).encode())
        self.assertIn("does not support tools", str(ctx.exception))

    def test_stream_body_is_assembled(self):
        sse = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","function":{"name":"list_dir","arguments":"{\\"pa"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\\": \\".\\"}"}}]}}]}\n\n'
            "data: [DONE]\n"
        ).encode()
        message = parse_response(sse)
        self.assertEqual(message["content"], "Hello")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "list_dir")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"path": "."})

    def test_not_json_says_so(self):
        with self.assertRaises(ProviderError) as ctx:
            parse_response(b"<html>Bad gateway</html>")
        self.assertIn("not JSON", str(ctx.exception))

    def test_reasons_never_echo_key_like_text(self):
        with self.assertRaises(ProviderError) as ctx:
            parse_response(json.dumps({"error": "invalid key sk-abcdefghijklmnop"}).encode())
        self.assertNotIn("abcdefghijklmnop", str(ctx.exception))


class RetryTests(unittest.TestCase):
    def test_tool_history_is_retried_as_plain_text_when_rejected(self):
        opener = RecordingOpener({"error": {"message": "invalid role: tool"}},
                                 {"choices": [{"message": {"role": "assistant", "content": "done"}}]})
        message = provider(opener).chat(TOOL_HISTORY, [])
        self.assertEqual(message["content"], "done")
        self.assertEqual(len(opener.requests), 2)
        retried_roles = [m["role"] for m in opener.requests[1]["messages"]]
        self.assertNotIn("tool", retried_roles)
        self.assertIn("calc.py", json.dumps(opener.requests[1]["messages"]))
        self.assertTrue(opener.requests[1]["tools"] is not None)

    def test_no_retry_without_tool_history(self):
        opener = RecordingOpener({"error": {"message": "rate limited"}})
        with self.assertRaises(ProviderError) as ctx:
            provider(opener).chat([{"role": "user", "content": "hi"}], [])
        self.assertEqual(len(opener.requests), 1)
        self.assertIn("rate limited", str(ctx.exception))

    def test_both_failures_are_reported(self):
        opener = RecordingOpener({"error": {"message": "first"}}, {"error": {"message": "second"}})
        with self.assertRaises(ProviderError) as ctx:
            provider(opener).chat(TOOL_HISTORY, [])
        self.assertIn("first", str(ctx.exception))
        self.assertIn("second", str(ctx.exception))

    def test_http_error_body_reason_is_shown(self):
        error = urllib.error.HTTPError("https://x", 400, "bad", hdrs=None,
                                       fp=io.BytesIO(b'{"error":{"message":"context too long"}}'))
        opener = RecordingOpener(error)
        with self.assertRaises(ProviderError) as ctx:
            provider(opener).chat([{"role": "user", "content": "hi"}], [])
        self.assertIn("HTTP 400: context too long", str(ctx.exception))

    def test_tool_only_assistant_turn_is_sent_with_null_content(self):
        opener = RecordingOpener({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        provider(opener).chat(TOOL_HISTORY, [])
        self.assertIsNone(opener.requests[0]["messages"][2]["content"])
        self.assertFalse(opener.requests[0]["stream"])
        self.assertEqual(TOOL_HISTORY[2]["content"], "")  # the stored history is not changed


class FlattenTests(unittest.TestCase):
    def test_flattened_history_alternates_and_keeps_information(self):
        flat = flatten_tool_history(TOOL_HISTORY)
        self.assertEqual([m["role"] for m in flat], ["system", "user", "assistant", "user"])
        self.assertIn("read_file", flat[2]["content"])
        self.assertIn("print(1)", flat[3]["content"])


if __name__ == "__main__":
    unittest.main()
