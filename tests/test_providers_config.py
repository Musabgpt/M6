"""Provider fallback tiers, key handling, config validation and token creation."""

import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error

from newal_mobile.config import ConfigError, load_or_create_token, load_providers
from newal_mobile.providers import FallbackChain, OpenAICompatibleProvider, ProviderError


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok_urlopen(message):
    payload = json.dumps({"choices": [{"message": message}]}).encode()

    def opener(request, timeout):
        return FakeResponse(payload)
    return opener


def failing_urlopen(code):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "fail", hdrs=None, fp=None)
    return opener


class FallbackTests(unittest.TestCase):
    def provider(self, name, tier, opener):
        return OpenAICompatibleProvider(name, "https://example.invalid/v1", "m", privacy_tier=tier, urlopen=opener)

    def test_falls_back_within_the_same_tier(self):
        chain = FallbackChain([
            self.provider("first", "free-shared", failing_urlopen(429)),
            self.provider("second", "free-shared", ok_urlopen({"role": "assistant", "content": "hi"})),
        ])
        message, name = chain.complete([], [])
        self.assertEqual(name, "second")
        self.assertEqual(message["content"], "hi")

    def test_does_not_silently_cross_privacy_tiers(self):
        chain = FallbackChain([
            self.provider("private", "local", failing_urlopen(503)),
            self.provider("public", "free-shared", ok_urlopen({"role": "assistant", "content": "leak"})),
        ])
        with self.assertRaises(ProviderError):
            chain.complete([], [])

    def test_cross_tier_fallback_works_only_when_allowed(self):
        chain = FallbackChain([
            self.provider("private", "local", failing_urlopen(503)),
            self.provider("public", "free-shared", ok_urlopen({"role": "assistant", "content": "ok"})),
        ], allow_cross_tier=True)
        _, name = chain.complete([], [])
        self.assertEqual(name, "public")

    def test_error_messages_never_contain_the_api_key(self):
        secret = "SUPER-SECRET-KEY-123"
        provider = OpenAICompatibleProvider("keyed", "https://example.invalid/v1", "m",
                                            api_key=secret, urlopen=failing_urlopen(401))
        with self.assertRaises(ProviderError) as ctx:
            provider.chat([], [])
        self.assertNotIn(secret, str(ctx.exception))
        self.assertNotIn(secret, repr(provider))

    def test_malformed_response_is_an_error(self):
        def opener(request, timeout):
            return FakeResponse(b'{"unexpected": true}')
        provider = OpenAICompatibleProvider("x", "https://example.invalid/v1", "m", urlopen=opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.chat([], [])
        self.assertIn("malformed", str(ctx.exception))


class ConfigTests(unittest.TestCase):
    def test_default_providers_are_the_keyless_routes_from_the_repos(self):
        providers = load_providers(None, env={})
        self.assertEqual([p.name for p in providers],
                         ["kilo-auto-free", "llm7-free", "pollinations-free", "ovhcloud-free"])
        self.assertEqual(providers[0].base_url, "https://api.kilo.ai/api/gateway")

    def test_plain_http_to_a_remote_host_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "providers.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"name": "bad", "base_url": "http://example.com/v1", "model": "m",
                            "privacy_tier": "x"}], handle)
            with self.assertRaises(ConfigError):
                load_providers(path, env={})

    def test_provider_with_missing_key_is_skipped_not_called(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "providers.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"name": "keyed", "base_url": "https://example.invalid/v1", "model": "m",
                            "privacy_tier": "x", "api_key_env": "MISSING_KEY_VAR"}], handle)
            self.assertEqual(load_providers(path, env={}), [])
            keyed = load_providers(path, env={"MISSING_KEY_VAR": "abc"})
            self.assertEqual(len(keyed), 1)

    def test_token_is_created_once_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            first = load_or_create_token(d, env={})
            second = load_or_create_token(d, env={})
            self.assertEqual(first, second)
            mode = stat.S_IMODE(os.stat(os.path.join(d, "token")).st_mode)
            self.assertEqual(mode, 0o600)
            self.assertGreaterEqual(len(first), 32)


if __name__ == "__main__":
    unittest.main()
