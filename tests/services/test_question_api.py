import pytest
from fastapi.testclient import TestClient

from kpi_api import backends
from kpi_api.main import create_app as create_kpi_app
from question_api import agent, grounding
from question_api.main import create_app as create_question_app


# ---------- grounding ----------

@pytest.mark.parametrize("text,value,percent", [
    ("ARR was $10,005,534", 10005534, False),
    ("win rate 67.1%", 67.1, True),
    ("about $10.0M", 10_000_000, False),
    ("-0.25", -0.25, False),
])
def test_figures_parse(text, value, percent):
    f = grounding.figures(text)[-1]
    assert f.value == pytest.approx(value) and f.percent == percent


def test_quarter_labels_are_not_numbers():
    assert [f.text for f in grounding.figures("In 2017Q3 and Q4")] == ["2017"]


def test_supported_allows_rounding_and_percent():
    pool = {0.671186, 10005534.0}
    assert grounding.unsupported("67% of accounts, ARR $10.0M, or 67.1%", pool) == []
    assert grounding.unsupported("ARR $10,005,534", pool) == []
    assert grounding.unsupported("ARR $10.1M", pool) == ["$10.1M"]
    assert grounding.unsupported("68%", pool) == ["68%"]


def test_pool_reads_dates_inside_strings():
    pool = grounding.pool_from({"rows": [{"quarter": "2017Q4", "date": "2017-12-31", "win_rate": 0.64}]})
    assert {2017.0, 4.0, 12.0, 31.0, 0.64} <= pool


# ---------- agent with a scripted model ----------

class ScriptedLLM:
    """Plays back a fixed list of responses and records what it was sent."""

    def __init__(self, responses):
        self.responses, self.seen = list(responses), []

    def create(self, system, messages, tools):
        self.seen.append([m for m in messages])
        return self.responses.pop(0)


def tool_use(tool, **inp):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": f"t_{tool}", "name": tool, "input": inp}]}


def text(t):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": t}]}


@pytest.fixture(scope="module")
def kpis(reference_tables):
    return agent.KpiClient(http=TestClient(create_kpi_app(backends.ReferenceBackend(reference_tables))))


def _dec_arr(kpis):
    rows = kpis.get_kpi("arr_monthly", start="2017-12-31", end="2017-12-31")["rows"]
    return rows[0]["arr"]


def test_grounded_answer_passes(kpis):
    arr = _dec_arr(kpis)
    llm = ScriptedLLM([
        tool_use("get_kpi", name="arr_monthly", start="2017-12-31", end="2017-12-31"),
        text(f"ARR on 2017-12-31 was ${arr:,.0f} (KPI: ARR, month-end)."),
    ])
    a = agent.ask("What was ARR at the end of December 2017?", llm, kpis)
    assert not a.refused and "ARR" in a.answer
    assert a.kpi_calls[0]["kpi"] == "arr_monthly" and a.kpi_calls[0]["row_count"] == 1


def test_invented_number_gets_one_retry_then_refusal(kpis):
    llm = ScriptedLLM([
        tool_use("get_kpi", name="arr_monthly", start="2017-12-31", end="2017-12-31"),
        text("ARR grew 812% over the year."),
        text("ARR grew 812% over the year, honestly."),
    ])
    a = agent.ask("How much did ARR grow?", llm, kpis)
    assert a.refused and a.unsupported_numbers == ["812%"]
    retry_prompt = llm.seen[-1][-1]["content"]
    assert "812%" in retry_prompt


def test_retry_can_fix_the_answer(kpis):
    arr = _dec_arr(kpis)
    llm = ScriptedLLM([
        tool_use("get_kpi", name="arr_monthly", start="2017-12-31", end="2017-12-31"),
        text("ARR doubled to about $20M."),
        text(f"ARR on 2017-12-31 was ${arr / 1e6:.1f}M. The KPI layer has no growth figure."),
    ])
    a = agent.ask("Did ARR double?", llm, kpis)
    assert not a.refused and "no growth figure" in a.answer


def test_kpi_errors_are_passed_back_not_raised(kpis):
    llm = ScriptedLLM([tool_use("get_kpi", name="churn_rate"), text("The KPI layer has no churn KPI.")])
    a = agent.ask("What is churn?", llm, kpis)
    assert not a.refused and a.kpi_calls[0]["error"]


def test_http_endpoint(kpis):
    llm = ScriptedLLM([text("The KPI layer has no figure for that.")])
    c = TestClient(create_question_app(llm=llm, kpis=kpis))
    r = c.post("/ask", json={"question": "What's the weather?"})
    assert r.status_code == 200 and r.json()["refused"] is False
    assert c.post("/ask", json={"question": ""}).status_code == 422


def test_missing_api_key_is_a_503(kpis, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    c = TestClient(create_question_app(kpis=kpis))
    r = c.post("/ask", json={"question": "What was ARR?"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
