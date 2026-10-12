"""Shared test doubles. ScriptedProvider replays a fixed list of assistant messages.

It exercises the agent loop and tool handling; it says nothing about real model quality.
"""

import itertools
import json

from newal_mobile.providers import ProviderError

_ids = itertools.count(1)


class ScriptedProvider:
    def __init__(self, script, name="scripted", tier="test"):
        self.script = list(script)
        self.name = name
        self.privacy_tier = tier
        self.requests = 0

    def chat(self, messages, tools):
        self.requests += 1
        if not self.script:
            raise ProviderError("script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def call(name, args, call_id=None):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": call_id or f"call_{next(_ids)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}


def text(content):
    return {"role": "assistant", "content": content}
