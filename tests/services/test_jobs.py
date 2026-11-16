"""usage-generator, kql-init and transform."""

from pathlib import Path

import pandas as pd
import pytest

from revintel import kusto

ROOT = Path(__file__).resolve().parents[2]


# ---------- usage-generator: Event Hub replay ----------

class FakeBatch:
    def __init__(self, cap):
        self.items, self.cap = [], cap

    def add(self, e):
        if len(self.items) >= self.cap:
            raise ValueError("full")
        self.items.append(e)


class FakeProducer:
    def __init__(self, cap=100):
        self.cap, self.sent = cap, []

    def create_batch(self):
        return FakeBatch(self.cap)

    def send_batch(self, b):
        self.sent.append(b.items)


def test_eventhub_replay_sends_every_event_in_day_order():
    from usage_generator.main import replay_to_eventhub

    usage = pd.read_csv(ROOT / "data/processed/usage_events.csv.gz", parse_dates=["Day"], nrows=5000)
    p = FakeProducer(cap=300)
    n = replay_to_eventhub(usage, p)
    assert n == len(usage) == sum(len(b) for b in p.sent)
    days = [str(e).split('"Day": "')[1][:10] for b in p.sent for e in b]
    assert days == sorted(days)


def test_eventhub_replay_max_days():
    from usage_generator.main import replay_to_eventhub

    usage = pd.read_csv(ROOT / "data/processed/usage_events.csv.gz", parse_dates=["Day"], nrows=5000)
    n = replay_to_eventhub(usage, FakeProducer(), max_days=1)
    assert n == (usage["Day"] == usage["Day"].min()).sum()


# ---------- kql-init: script parsing ----------

def test_split_commands_on_repo_scripts():
    tables = kusto.split_commands((ROOT / "kql/01_tables.kql").read_text())
    assert [c.split()[0] for c in tables] == [".create-merge", ".create-or-alter"]
    assert '"Ordinal": "5"' in tables[1] and tables[1].rstrip().endswith("```")
    funcs = kusto.split_commands((ROOT / "kql/02_kpi_functions.kql").read_text())
    assert len(funcs) == 4
    names = [c.splitlines()[1].split("(")[0] for c in funcs]
    assert names == ["ActiveAccounts", "ConsumptionDaily", "Adoption", "ExpansionSignal"]
    assert all("//" not in c.splitlines()[0] for c in funcs)


def test_catalog_kql_functions_exist_in_scripts():
    from revintel import catalog

    text = (ROOT / "kql/02_kpi_functions.kql").read_text()
    for k in catalog.CATALOG.values():
        if k.kql:
            assert f"\n{k.kql.split('(')[0]}(" in text


# ---------- transform: Spark matches the reference ----------

@pytest.mark.spark
def test_spark_account_day_matches_reference():
    pytest.importorskip("pyspark")
    from revintel.cli import compare, read_reference
    from transform import main as tmain

    spark = tmain.spark_session()
    try:
        out = tmain.run(spark)
        for name in ("account_day", "consumption_by_tier"):
            assert compare(name, read_reference(name), out[name].toPandas()) == []
    finally:
        spark.stop()
