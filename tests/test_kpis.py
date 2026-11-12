import numpy as np
import pandas as pd
import pytest

from revintel import kpis
from revintel.cli import compare


@pytest.fixture(scope="module")
def ad(usage_on, data):
    return kpis.account_day(usage_on, data.pipeline)


def test_account_day_preserves_usage_totals(ad, usage_on):
    assert np.isclose(ad["units"].sum(), usage_on["Units"].sum())
    assert ad["events"].sum() == usage_on["Events"].sum()
    assert not ad.duplicated(["account_key", "day"]).any()


def test_account_day_arr_matches_subscriptions(ad, data):
    arr = kpis.arr_by_month(data.pipeline, data.data_end).set_index("date")["arr"]
    dec = ad[ad["day"] == "2017-12-31"]["active_arr"].sum()
    assert np.isclose(dec, arr.loc["2017-12-31"], atol=1)


def test_active_accounts(usage_on):
    a = kpis.active_accounts(usage_on)
    assert a["active_accounts"].max() == 85
    assert a["active_accounts"].is_monotonic_increasing  # no churn inside the 12-month terms


def test_adoption_builds_over_time(usage_on):
    a = kpis.adoption(usage_on)
    assert a["adoption_rate"].between(0, 1).all()
    assert a["adoption_rate"].iloc[:4].mean() < 0.2 < 0.5 < a["adoption_rate"].iloc[-6:].mean()


def test_win_rate_total(data):
    w = kpis.win_rate(data.pipeline, [])
    assert w["won"].sum() == 4238 and w["closed"].sum() == 4238 + 2473


def test_open_pipeline_excludes_unengaged_deals(data):
    d = pd.DatetimeIndex([data.data_end])
    o = kpis.open_pipeline(data.pipeline, d, [])
    assert o["open_deals"].iloc[0] == 1589  # every Engaging deal, no Prospecting


def test_compare_passes_on_identical_and_catches_a_change(usage_on):
    ref = kpis.adoption(usage_on)
    assert compare("adoption", ref, ref.copy()) == []
    bad = ref.copy()
    bad.loc[5, "adopted"] += 1
    assert compare("adoption", ref, bad)


def test_compare_accepts_kql_style_export(usage_on):
    ref = kpis.expansion_signal(usage_on)
    kql = ref.rename(columns=str.title).assign(
        Month_End=ref["month_end"].dt.strftime("%Y-%m-%dT00:00:00Z"),
        Signal=ref["signal"].map({True: "true", False: "false"}),
    )
    assert compare("expansion_signal", ref, kql) == []
