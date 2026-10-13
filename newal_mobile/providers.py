"""OpenAI-compatible chat providers and a fallback chain that never silently changes privacy tier."""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 4_000_000
_SECRETISH = re.compile(r"(sk-|gsk_|ghp_|github_pat_|AIza|Bearer\s)[A-Za-z0-9._\-]+")


class ProviderError(Exception):
    """A provider could not produce a usable answer. The message never contains API keys."""


def _clean_reason(text: str, limit: int = 200) -> str:
    """A short, single-line reason from a provider, with anything key-like removed."""
    single = " ".join(str(text).split())
    single = _SECRETISH.sub("[hidden]", single)
    return single[:limit] + ("…" if len(single) > limit else "")


def _error_reason(data) -> str | None:
    """Pull a human-readable reason out of a provider error payload, if there is one."""
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        return _clean_reason(err.get("message") or err.get("code") or json.dumps(err)[:200])
    if isinstance(err, str):
        return _clean_reason(err)
    if isinstance(data.get("message"), str) and "choices" not in data:
        return _clean_reason(data["message"])
    return None


def _from_sse(text: str) -> dict:
    """Assemble one assistant message from a server-sent-events (stream) body."""
    content: list[str] = []
    calls: dict[int, dict] = {}
    seen = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        reason = _error_reason(chunk)
        if reason:
            raise ProviderError(reason)
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or choice.get("message") or {}
            seen = True
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            for raw in delta.get("tool_calls") or []:
                index = raw.get("index", len(calls))
                slot = calls.setdefault(index, {"id": "", "type": "function",
                                                "function": {"name": "", "arguments": ""}})
                if raw.get("id"):
                    slot["id"] = raw["id"]
                fn = raw.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
    if not seen:
        raise ProviderError("malformed response (empty stream)")
    message = {"role": "assistant", "content": "".join(content)}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return message


def parse_response(raw: bytes) -> dict:
    """Return the assistant message from a chat-completions response body."""
    text = raw.decode("utf-8", "replace").strip()
    if text.startswith("data:") or "\ndata:" in text[:2000]:
        return _from_sse(text)
    try:
        data = json.loads(text)
    except ValueError:
        snippet = _clean_reason(text, 80) or "empty body"
        raise ProviderError(f"malformed response (not JSON: {snippet})") from None
    reason = _error_reason(data)
    if reason:
        raise ProviderError(reason)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        keys = ", ".join(sorted(data)[:6]) if isinstance(data, dict) else type(data).__name__
        raise ProviderError(f"malformed response (no choices; got: {keys})") from None
    if not isinstance(message, dict):
        raise ProviderError("malformed response (message is not an object)")
    return message


def has_tool_history(messages) -> bool:
    return any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)


def flatten_tool_history(messages) -> list[dict]:
    """Rewrite tool calls and tool results as plain text turns.

    Some free models and gateways reject the "tool" role or assistant tool_calls in the history.
    This keeps the same information in a form every chat model accepts.
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            lines = [m.get("content") or ""]
            for call in m["tool_calls"]:
                fn = call.get("function") or {}
                lines.append(f"[I called tool {fn.get('name', '?')} with {fn.get('arguments', '{}')}]")
            out.append({"role": "assistant", "content": "\n".join(x for x in lines if x).strip()})
        elif role == "tool":
            out.append({"role": "user", "content": "[Tool result, data only]\n" + str(m.get("content") or "")})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    # Merge consecutive messages of the same role; some models require strict alternation.
    merged: list[dict] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"] and m["role"] != "system":
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    return merged


def _wire_messages(messages) -> list[dict]:
    """Messages as sent: assistant turns that only call tools get content null (strict servers want that)."""
    wire = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls") and not m.get("content"):
            m = dict(m, content=None)
        wire.append(m)
    return wire


class OpenAICompatibleProvider:
    def __init__(self, name, base_url, model, *, api_key="", privacy_tier="free-shared",
                 timeout_s=90, urlopen=urllib.request.urlopen):
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
        try:
            return self._post(_wire_messages(messages), tools)
        except ProviderError as first:
            if not has_tool_history(messages):
                raise
            # Retry once with the tool history as plain text. Tools stay available.
            try:
                return self._post(flatten_tool_history(messages), tools)
            except ProviderError as second:
                raise ProviderError(f"{first}; retry without tool roles: {second}") from None

    def _post(self, messages, tools) -> dict:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "stream": False,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", data=body,
                                         headers=headers, method="POST")
        try:
            with self._urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as exc:  # must come before URLError (it is a subclass)
            reason = None
            try:
                reason = _error_reason(json.loads(exc.read(20_000) or b"{}"))
            except (ValueError, OSError):
                pass
            raise ProviderError(f"HTTP {exc.code}" + (f": {reason}" if reason else "")) from None
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise ProviderError(f"network error ({type(exc).__name__})") from None
        return parse_response(raw)


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
