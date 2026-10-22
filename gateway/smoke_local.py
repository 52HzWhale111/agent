"""Run against infra/start-local.ps1 -MockAgents; never targets real Agents."""
import argparse
from io import BytesIO
import time
import zipfile

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-check", action="store_true", help="Also wait for the real 30-second QA timeout")
    options = parser.parse_args()
    checks = 0

    def passed(label):
        nonlocal checks
        checks += 1
        print(f"PASS {label}", flush=True)

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=40, trust_env=False) as client:
        for port in (8100, 8200):
            response = client.get(f"http://127.0.0.1:{port}/health")
            assert response.json().get("mock") is True, "Refusing to change a real Agent's state"
        assert client.get("/health/ready").status_code == 200
        passed("gateway and both local Mock Agents reachable")
        response = client.post("/api/qa", json={"question": "cohesion"}, headers={"X-Request-ID": "local-smoke-qa"})
        assert response.status_code == 200 and response.json()["mock"] is True
        assert response.json()["request_id"] == "local-smoke-qa"
        passed("QA forwarding and request ID propagation")
        assert client.post("/api/requirements/analyze", json={"description": "library"}).json()["mock"] is True
        passed("requirement forwarding")
        assert client.post("/api/uploads", files={"file": ("bad.exe", b"bad")}).status_code == 415
        passed("unsupported upload rejected")
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("Main.java", "class Main {}")
        ids = [client.post("/api/uploads", files={"file": ("same.zip", data.getvalue())}).json()["file_id"] for _ in range(2)]
        assert ids[0] != ids[1]
        passed("same-named uploads receive different IDs")

        def finish(task_id):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = client.get("/api/tasks/" + task_id).json()
                if result["status"] in {"succeeded", "failed"}:
                    return result
                time.sleep(2)
            raise AssertionError("Task did not finish")

        review = client.post("/api/reviews", json={"content": "requirements text", "doc_type": "srs"})
        assert review.status_code == 202
        result = finish(review.json()["task_id"])
        assert result["status"] == "succeeded" and result["result"]["received_characters"] == 17
        passed("202 review task, two-second polling, successful completion")
        test = client.post("/api/code-tests", json={"file_id": ids[0]})
        assert test.status_code == 202
        result = finish(test.json()["task_id"])
        assert result["status"] == "succeeded" and result["result"]["archive_received"] is True
        passed("ZIP code task handoff (Mock does not execute code)")
        assert client.post("/api/consistency", json={"code": "class Main {}", "diagram": {}}).json()["mock"] is True
        passed("consistency forwarding")
        try:
            client.post("http://127.0.0.1:8200/__test__/behavior", json={"unavailable": True}).raise_for_status()
            assert client.get("/health/ready").status_code == 503
            test = client.post("/api/code-tests", json={"code": "class Main {}"}).json()
            assert finish(test["task_id"])["error"]["code"] == "AGENT_UNAVAILABLE"
            assert client.post("/api/qa", json={"question": "still available?"}).status_code == 200
            passed("sandbox outage fails its task while QA remains available")
        finally:
            client.post("http://127.0.0.1:8200/__test__/behavior", json={"delay_seconds": 2, "unavailable": False}).raise_for_status()
        if options.timeout_check:
            try:
                client.post("http://127.0.0.1:8100/__test__/behavior", json={"delay_seconds": 35}).raise_for_status()
                started = time.monotonic()
                response = client.post("/api/qa", json={"question": "timeout test"})
                assert response.status_code == 504 and response.json()["error"]["code"] == "AGENT_TIMEOUT"
                assert 29 <= time.monotonic() - started < 34
                passed("real 30-second QA timeout")
            finally:
                client.post("http://127.0.0.1:8100/__test__/behavior", json={"delay_seconds": 2}).raise_for_status()
        assert client.get("/health/ready").status_code == 200
        passed("Mock behavior reset and readiness restored")
    print(f"{checks} live HTTP checks passed. Results are infrastructure checks, not Agent quality evidence.")


if __name__ == "__main__":
    main()
