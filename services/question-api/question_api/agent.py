"""The question agent: Claude with two tools that reach the KPI API, and nothing else.

Flow: the model may call list_kpis / get_kpi a few times, then answers. Every
number in the answer is checked against what the tools returned (grounding.py).
If any number is unsupported the model gets one chance to fix it; after that the
agent refuses rather than return an ungrounded figure.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from . import grounding

SYSTEM = """You answer questions about a B2B software company's revenue pipeline and product usage, using only the KPI tools.

Rules:
- Every number you state must appear in a tool result. You may round it or show a ratio as a percentage.
- Never compute new figures: no sums, averages, differences, growth rates or ratios of your own. If a question needs one, give the KPI rows that bear on it, or say the KPI layer doesn't provide it.
- For each figure, name the KPI and the period it covers.
- If no KPI answers the question, say so plainly and list the closest KPIs.
- The CRM is a real public dataset for a fictional hardware company, reframed as subscriptions (won deal = 12-month subscription). Product usage is synthetic, and the link between the expansion signal and expansion was planted on purpose: say so whenever you discuss that link.
- Be brief: a short paragraph or a few bullets."""

TOOLS = [
    {
        "name": "list_kpis",
        "description": "List the governed KPIs: name, definition, grain (keys), value columns, time key, and which backend serves each.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_kpi",
        "description": ("Get rows of one KPI. Filter by time with start/end (ISO dates, inclusive; quarters are matched by "
                        "the quarter the dates fall in) and by any key column with filters, e.g. {\"product\": \"GTX Pro\"}. "
                        "Ask for only the rows you need."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "start": {"type": "string", "description": "YYYY-MM-DD"},
                "end": {"type": "string", "description": "YYYY-MM-DD"},
                "filters": {"type": "object", "additionalProperties": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["name"],
        },
    },
]

# Numbers that appear in KPI definitions and may be restated (windows, thresholds, term).
DEFINITION_NUMBERS = {28.0, 56.0, 10.0, 4.0, 6.0, 12.0, 365.0}


class LLM(Protocol):
    def create(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """Return {"stop_reason": str, "content": [blocks as plain dicts]}."""


class AnthropicLLM:
    def __init__(self, model: str | None = None, max_tokens: int = 1500):
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    def create(self, system, messages, tools):
        r = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                        messages=messages, tools=tools)
        content = []
        for b in r.content:
            if b.type == "text":
                content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        return {"stop_reason": r.stop_reason, "content": content}


class KpiClient:
    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=base_url or os.environ.get("KPI_API_URL", "http://kpi-api:8000"),
                                         timeout=60)

    def list_kpis(self) -> dict:
        r = self.http.get("/kpis")
        r.raise_for_status()
        return r.json()

    def get_kpi(self, name: str, start=None, end=None, filters=None, limit=50) -> dict:
        params = {k: v for k, v in {"start": start, "end": end, "limit": min(int(limit or 50), 200)}.items() if v}
        params.update(filters or {})
        r = self.http.get(f"/kpis/{name}", params=params)
        if r.status_code >= 400:
            return {"error": r.json().get("detail", r.text), "status": r.status_code}
        return r.json()


@dataclass
class Answer:
    answer: str | None
    refused: bool
    reason: str | None = None
    kpi_calls: list[dict] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)


def _text(content: list[dict]) -> str:
    return "\n".join(b["text"] for b in content if b["type"] == "text").strip()


def ask(question: str, llm: LLM, kpis: KpiClient, max_tool_rounds: int = 6) -> Answer:
    messages: list[dict] = [{"role": "user", "content": question}]
    pool = grounding.pool_from(question) | DEFINITION_NUMBERS
    calls: list[dict] = []
    retried = False

    for _ in range(max_tool_rounds + 2):
        resp = llm.create(SYSTEM, messages, TOOLS)
        messages.append({"role": "assistant", "content": resp["content"]})
        tool_uses = [b for b in resp["content"] if b["type"] == "tool_use"]

        if tool_uses:
            if len(calls) >= max_tool_rounds * 3:
                return Answer(None, True, "too many KPI lookups for one question", calls)
            results = []
            for tu in tool_uses:
                if tu["name"] == "list_kpis":
                    out = kpis.list_kpis()
                elif tu["name"] == "get_kpi":
                    out = kpis.get_kpi(**tu["input"])
                    calls.append({"kpi": tu["input"].get("name"), "params": tu["input"],
                                  "row_count": out.get("row_count"), "served_by": out.get("served_by"),
                                  "error": out.get("error")})
                else:
                    out = {"error": f"unknown tool {tu['name']}"}
                pool_from_result = grounding.pool_from(out.get("rows", out) if isinstance(out, dict) else out)
                pool |= pool_from_result
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})
            continue

        text = _text(resp["content"])
        bad = grounding.unsupported(text, pool)
        if not bad:
            return Answer(text, False, kpi_calls=calls)
        if retried:
            return Answer(None, True, "the answer contained numbers the KPI layer did not return", calls, bad)
        retried = True
        messages.append({"role": "user", "content": (
            f"These numbers are not in any KPI result: {', '.join(bad)}. Rewrite the answer using only figures the "
            "tools returned (rounding is fine). If the question needs a figure you would have to compute, say the "
            "KPI layer doesn't provide it.")})

    return Answer(None, True, "no answer within the tool-call budget", calls)
