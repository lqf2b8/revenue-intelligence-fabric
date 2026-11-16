import pandas as pd
import pytest
from fastapi.testclient import TestClient

from kpi_api import backends
from kpi_api.main import create_app
from revintel import catalog


@pytest.fixture(scope="module")
def client(reference_tables):
    return TestClient(create_app(backends.ReferenceBackend(reference_tables)))


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "backend": "reference"}


def test_every_catalog_kpi_is_served_by_reference(client):
    kpis = client.get("/kpis").json()["kpis"]
    assert {k["name"] for k in kpis} == set(catalog.CATALOG)
    assert all(k["served_by"] == "reference" for k in kpis)


def test_time_and_key_filters(client):
    r = client.get("/kpis/arr_monthly_by_tier", params={"start": "2017-12-01", "end": "2017-12-31", "plan_tier": "GTX Pro"})
    body = r.json()
    assert r.status_code == 200
    assert body["row_count"] == 1
    assert body["rows"][0]["date"] == "2017-12-31" and body["rows"][0]["plan_tier"] == "GTX Pro"


def test_quarter_filter(client):
    body = client.get("/kpis/win_rate_by_product", params={"start": "2017-10-01", "end": "2017-12-31"}).json()
    assert {r["quarter"] for r in body["rows"]} == {"2017Q4"}


def test_unknown_kpi_and_filter(client):
    assert client.get("/kpis/nope").status_code == 404
    assert client.get("/kpis/adoption", params={"product": "x"}).status_code == 400


def test_limit_and_truncation(client):
    body = client.get("/kpis/active_accounts", params={"limit": 5}).json()
    assert len(body["rows"]) == 5 and body["truncated"] and body["row_count"] > 5


def test_nan_becomes_null(client):
    body = client.get("/kpis/expansion_signal", params={"limit": 5000}).json()
    growth = [r["growth"] for r in body["rows"]]
    assert all(g is None or isinstance(g, float) for g in growth)


class FakeKusto:
    name = "kusto"

    def serves(self, kpi):
        return kpi.kql is not None

    def fetch(self, kpi, q):
        return pd.DataFrame({"day": ["2017-12-31"], "active_accounts": [85]})


def test_hybrid_routes_usage_kpis_to_kusto(reference_tables):
    app = create_app(backends.HybridBackend(FakeKusto(), backends.ReferenceBackend(reference_tables)))
    c = TestClient(app)
    served = {k["name"]: k["served_by"] for k in c.get("/kpis").json()["kpis"]}
    assert served["active_accounts"] == "kusto"
    assert served["arr_monthly"] == "reference"
    assert c.get("/kpis/active_accounts").json()["rows"] == [{"day": "2017-12-31", "active_accounts": 85}]


def test_fabric_dax():
    fb = backends.FabricBackend(dataset_id="ds", column_map={
        "date": "'Calendar'[Date]", "plan_tier": "'Plan Tier'[Tier]"}, token_provider=lambda: "t")
    kpi = catalog.get("arr_monthly_by_tier")
    assert fb.serves(kpi)
    dax = fb.dax(kpi, backends.Query(start="2017-12-01", end="2017-12-31", filters={"plan_tier": 'GTX "Pro"'}))
    assert dax.startswith("EVALUATE\nSUMMARIZECOLUMNS(")
    assert "DATESBETWEEN('Calendar'[Date], DATE(2017,12,1), DATE(2017,12,31))" in dax
    assert 'TREATAS({"GTX ""Pro"""}, \'Plan Tier\'[Tier])' in dax
    assert '"arr", [ARR]' in dax
    assert not fb.serves(catalog.get("win_rate_by_agent"))  # no column mapping for its keys
