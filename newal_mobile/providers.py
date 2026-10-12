"""OpenAI-compatible chat providers and a fallback chain that never silently changes privacy tier."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class ProviderError(Exception):
    """A provider could not produce a usable answer. The message never contains API keys."""


class OpenAICompatibleProvider:
    def __init__(self, name, base_url, model, *, api_key="", privacy_tier="free-shared",
                 timeout_s=60, urlopen=urllib.request.urlopen):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.privacy_tier = privacy_tier
        self.timeout_s = timeout_s
        self._api_key = api_key
        self._urlopen = urlopen

    def __repr__(self) -> str:  # never print the key
        return f"<provider {self.name} tier={self.privacy_tier}>"

    def chat(self, messages, tools) -> dict:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                         headers=headers, method="POST")
        try:
            with self._urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as exc:  # must come before URLError (it is a subclass)
            raise ProviderError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise ProviderError(f"network error ({type(exc).__name__})") from None
        try:
            return json.loads(raw)["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("malformed response") from None


class FallbackChain:
    """Tries providers in order. Fallback stays inside the primary's privacy tier unless explicitly allowed."""

    def __init__(self, providers, *, allow_cross_tier=False):
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = list(providers)
        self.allow_cross_tier = allow_cross_tier

    def complete(self, messages, tools):
        primary = self.providers[0]
        failures = []
        for provider in self.providers:
            if (provider is not primary and not self.allow_cross_tier
                    and provider.privacy_tier != primary.privacy_tier):
                continue
            try:
                return provider.chat(messages, tools), provider.name
            except ProviderError as exc:
                failures.append(f"{provider.name}: {exc}")
        raise ProviderError("; ".join(failures) or "no eligible provider")
