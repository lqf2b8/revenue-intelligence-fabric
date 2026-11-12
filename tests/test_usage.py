import hashlib
from dataclasses import replace

import numpy as np
import pandas as pd

from revintel import kpis, usage
from revintel.subscriptions import subscriptions


def _digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def test_schema(usage_on):
    assert list(usage_on.columns) == usage.USAGE_COLUMNS
    assert usage_on["Events"].min() >= 1
    assert (usage_on["Units"] > 0).all()
    assert set(usage_on["Feature"]) == set(usage.DEFAULT_FEATURES)


def test_fixed_seed_is_reproducible(data, usage_on):
    assert _digest(usage.generate(data)) == _digest(usage_on)


def test_different_seed_differs(data, usage_on):
    other = usage.generate(data, replace(usage.UsageConfig(), seed=1))
    assert _digest(other) != _digest(usage_on)


def test_no_usage_before_subscription_start(data, usage_on):
    first_won = subscriptions(data.pipeline).groupby("account_key")["start_date"].min()
    first_use = usage_on.groupby("AccountKey")["Day"].min()
    assert (first_use >= first_won.reindex(first_use.index)).all()
    assert set(first_use.index) == set(first_won.index)


def test_seats_within_bounds(data, usage_on):
    cfg = usage.UsageConfig()
    users = usage_on.groupby("AccountKey")["UserId"].nunique()
    assert users.max() <= cfg.max_seats
    seats = usage.seats_for(data.accounts.set_index("account_key")["employees"], cfg)
    assert (users <= seats.reindex(users.index)).all()


def test_weekends_are_quieter(usage_on):
    daily = usage_on.groupby("Day")["Events"].sum()
    weekend = daily[daily.index.day_of_week >= 5].mean()
    weekday = daily[daily.index.day_of_week < 5].mean()
    assert weekend < 0.5 * weekday


def test_expansion_multiplier_shape():
    cfg = usage.UsageConfig()
    days = pd.date_range("2017-05-01", "2017-07-31")
    m = usage.expansion_multiplier(days, [pd.Timestamp("2017-07-01")], cfg)
    s = pd.Series(m, index=days)
    assert s["2017-06-02"] == 1.0  # before the ramp
    assert s["2017-06-03"] > 1.0  # ramp covers the 28 days before the month
    assert np.isclose(s["2017-06-30"], 1 + cfg.lift)  # peak on the eve of the expansion month
    assert s["2017-07-14"] > 1.0 and s["2017-07-15"] == 1.0  # decays over 14 days
    off = usage.expansion_multiplier(days, [pd.Timestamp("2017-07-01")], replace(cfg, lift=0.0))
    assert (off == 1).all()


def test_planted_effect_is_recovered(data, usage_on):
    r = kpis.signal_vs_next_month_expansion(usage_on, data.pipeline, data.data_end).set_index("signal")
    lift = r.loc[True, "expansion_rate"] - r.loc[False, "expansion_rate"]
    assert lift > 0.25, r


def test_no_effect_when_lift_is_zero(data, usage_off):
    """Control: with the planted effect off, the signal says nothing about next month."""
    r = kpis.signal_vs_next_month_expansion(usage_off, data.pipeline, data.data_end).set_index("signal")
    lift = r.loc[True, "expansion_rate"] - r.loc[False, "expansion_rate"]
    assert abs(lift) < 0.10, r
