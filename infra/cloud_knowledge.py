"""One-off cloud trial: at most RMB 0.878 at the verified Flash peak rates.

Eight calls * ((20,000 UTF-8 bytes + 2,048 framing tokens) * RMB 2/M
              + 8,192 output tokens * RMB 8/M). Failed calls keep their slot.
Slots persist on the host; there is no automatic quota reset.
"""
import json
import os
from pathlib import Path

import httpx

from agents.knowledge.api import create_app


class TrialTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory=None, upstream=None):
        self.directory = Path(directory or os.environ["LLM_BUDGET_DIR"])
        self.upstream = upstream if upstream is not None else httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        raw = await request.aread()
        body = json.loads(raw)
        allowed = {"model", "messages", "max_tokens", "response_format", "thinking", "reasoning_effort"}
        valid = (request.method == "POST" and str(request.url) == "https://api.deepseek.com/chat/completions"
                 and len(raw) <= 20_000 and not body.keys() - allowed
                 and body.get("model") == "deepseek-flash"
                 and type(body.get("max_tokens")) is int and 1 <= body["max_tokens"] <= 8192
                 and isinstance(body.get("messages"), list) and 1 <= len(body["messages"]) <= 3
                 and all(isinstance(m.get("content"), str) for m in body["messages"]))
        if not valid:
            return httpx.Response(413, json={"error": {"code": "CLOUD_TRIAL_REQUEST_LIMIT"}})
        for number in range(8):
            try:
                # Exclusive creation reserves a slot across concurrent calls and restarts.
                with (self.directory / f"call-{number}.reserved").open("x") as ticket:
                    ticket.write("reserved; never automatically reset\n")
                break
            except FileExistsError:
                continue
        else:
            return httpx.Response(402, json={"error": {"code": "CLOUD_TRIAL_BUDGET_EXHAUSTED"}})
        return await self.upstream.handle_async_request(request)

    async def aclose(self):
        await self.upstream.aclose()


def create_trial_app():
    return create_app(transport=TrialTransport())
