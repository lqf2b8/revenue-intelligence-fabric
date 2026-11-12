"""question-api: natural-language questions answered only from governed KPIs.

Run: uvicorn question_api.main:create_app --factory
Env: ANTHROPIC_API_KEY, ANTHROPIC_MODEL (default claude-sonnet-5-5), KPI_API_URL

POST /ask {"question": "..."} -> answer, or a refusal when the answer would need
a number the KPI API didn't return.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import agent


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def create_app(llm: agent.LLM | None = None, kpis: agent.KpiClient | None = None) -> FastAPI:
    app = FastAPI(title="Revenue Intelligence Question API", version="0.1.0")
    state = {"llm": llm, "kpis": kpis}

    def deps():
        if state["llm"] is None:
            state["llm"] = agent.AnthropicLLM()
        if state["kpis"] is None:
            state["kpis"] = agent.KpiClient()
        return state["llm"], state["kpis"]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/ask")
    def ask(req: AskRequest):
        try:
            llm, kpis = deps()
        except Exception as e:  # noqa: BLE001 - e.g. no API key configured
            raise HTTPException(503, f"question layer not configured: {e}") from None
        return asdict(agent.ask(req.question, llm, kpis))

    return app
