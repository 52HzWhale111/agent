"""Run the team's 20 public fixtures against the live local knowledge API.

HTTP success is recorded separately from semantic grading. No provider key is
read here; configure and start the knowledge service first.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import httpx


def main():
    root = Path(__file__).resolve().parents[2]
    cases_dir = root / "agents/knowledge/02_测试案例集"
    cases = []
    qa = next(cases_dir.glob("概念问答*.md")).read_text(encoding="utf-8")
    for case_id, question in re.findall(r"^## (QA-\d+) (.+)$", qa, re.M):
        cases.append((case_id, "/qa", {"question": question}))
    requirements = next(cases_dir.glob("需求拆解*.md")).read_text(encoding="utf-8")
    for block in re.split(r"(?=^## RA-)", requirements, flags=re.M)[1:]:
        case_id = re.match(r"## (RA-\d+)", block)[1]
        description = re.search(r'- \*\*输入\*\*："(.+)"', block)[1]
        cases.append((case_id, "/analyze_requirement", {"description": description}))
    defects = root / "knowledge-base/05_埋雷文档"
    for number, fixture, kind in [(1, 1, "srs"), (2, 3, "srs"), (3, 4, "srs"),
                                   (4, 5, "hld"), (5, 6, "hld"), (6, 10, "test_report")]:
        path = next(defects.glob(f"DEF-{fixture:02d}_埋雷版_*.md"))
        cases.append((f"DR-{number:02d}", "/review_document",
                      {"content": path.read_text(encoding="utf-8"), "doc_type": kind}))
    assert len(cases) == 20
    report = {"started_at": datetime.now(timezone.utc).isoformat(),
              "model_alias": "deepseek-flash", "semantic_grading": "not_automatically_graded",
              "results": []}
    destination = root / "data/local/knowledge-cases.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8100", trust_env=False, timeout=60) as client:
        ready = client.get("/health")
        ready.raise_for_status()
        assert ready.json()["model_configured"]
        for case_id, route, payload in cases:
            started = time.monotonic()
            response = client.post(route, json=payload)
            elapsed = round(time.monotonic() - started, 2)
            report["results"].append({"case_id": case_id, "route": route,
                "input_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                    sort_keys=True).encode()).hexdigest(), "http_status": response.status_code,
                "elapsed_seconds": elapsed, "request_id": response.headers.get("X-Request-ID"),
                "response": response.json()})
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{case_id}: HTTP {response.status_code}, {elapsed}s", flush=True)
    print("20 responses saved locally. Compare with source rubrics before reporting semantic pass rates.")


if __name__ == "__main__":
    main()
