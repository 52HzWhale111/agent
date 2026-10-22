"""The trial never forwards more calls after restart or concurrent reservation."""
import asyncio

import httpx

from infra.cloud_knowledge import TrialTransport


def test_trial_reserves_before_forwarding_and_survives_restart(tmp_path):
    forwarded = []

    async def upstream(request):
        forwarded.append(request)
        if len(forwarded) == 1:
            raise httpx.ConnectError("Simulated connection loss after reservation")
        return httpx.Response(200, json={"ok": True})

    async def check():
        body = {"model": "deepseek-flash", "max_tokens": 8192,
                "messages": [{"role": "user", "content": "test"}]}
        def request(payload):
            return httpx.Request("POST", "https://api.deepseek.com/chat/completions", json=payload)
        transport = TrialTransport(tmp_path, httpx.MockTransport(upstream))
        for invalid in ({**body, "model": "deepseek-v4-pro"},
                        {**body, "max_tokens": 8193},
                        {**body, "tools": []},
                        {**body, "messages": [{"role": "user", "content": "x" * 20_000}]}):
            assert (await transport.handle_async_request(request(invalid))).status_code == 413
        assert not list(tmp_path.iterdir())
        results = await asyncio.gather(*(transport.handle_async_request(request(body)) for _ in range(12)),
                                       return_exceptions=True)
        assert sum(isinstance(r, httpx.ConnectError) for r in results) == 1
        assert sum(isinstance(r, httpx.Response) and r.status_code == 200 for r in results) == 7
        assert sum(isinstance(r, httpx.Response) and r.status_code == 402 for r in results) == 4
        restarted = TrialTransport(tmp_path, httpx.MockTransport(upstream))
        assert (await restarted.handle_async_request(request(body))).status_code == 402
        assert len(forwarded) == len(list(tmp_path.glob("call-*.reserved"))) == 8
        await transport.aclose()
        await restarted.aclose()

    asyncio.run(check())
