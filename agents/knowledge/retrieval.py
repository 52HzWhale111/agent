"""Enrich the preserved team retriever with source fields and two sourced gaps."""
import json
import os
from pathlib import Path
import re

from .knowledge_retriever import KNOWLEDGE_BASE, retrieve as original_retrieve

ROOT = Path(__file__).resolve().parent
KNOWLEDGE_ROOT = Path(os.getenv("KNOWLEDGE_BASE_DIR", str(ROOT.parents[1] / "knowledge-base")))
SUPPLEMENTS = json.loads((ROOT / "supplemental_knowledge.json").read_text(encoding="utf-8"))
SOURCE_FIELDS = {}
for path in (KNOWLEDGE_ROOT / "01_知识点").glob("*.md"):
    for kp_id, body in re.findall(r"^## (KP-\d+) [^\n]+\n(.*?)(?=^## |\Z)",
                                  path.read_text(encoding="utf-8"), re.M | re.S):
        SOURCE_FIELDS[kp_id] = dict(re.findall(r"^- \*\*(.+?)\*\*：([^\n]+)", body, re.M))
if set(SOURCE_FIELDS) != {point["kp_id"] for point in KNOWLEDGE_BASE}:
    raise ValueError("Knowledge source files must contain the team's 60 knowledge points")


def retrieve(question, top_k=5):
    # The two explicit topics cover absent facts without letting generic '软件' crowd them out.
    supplements = [point for point in SUPPLEMENTS
                   if any(word.casefold() in question.casefold() for word in point["keywords"])]
    points = []
    for point in original_retrieve(question, top_k=top_k):
        fields = SOURCE_FIELDS[point["kp_id"]]
        points.append({**point, "source": fields["出处"], "common_mistake": fields.get("学生常犯的错", "")})
    return (supplements + points)[:top_k]


def format_for_prompt(points):
    return "\n".join(f'【{p["kp_id"]} {p["title"]}】\n定义：{p["definition"]}\n'
                     f'例子：{p["example"]}\n易错点：{p.get("common_mistake", "")}\n'
                     f'资料分组：{p["chapter"]}\n原文出处标注：{p["source"]}\n' for p in points)
