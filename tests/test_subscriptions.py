import pandas as pd

from revintel import subscriptions as s


def test_one_subscription_per_won_deal(data):
    subs = s.subscriptions(data.pipeline)
    assert len(subs) == 4238
    assert ((subs["end_date"] - subs["start_date"]).dt.days == 365).all()


def test_arr_snapshot(data):
    subs = s.subscriptions(data.pipeline)
    d = pd.Timestamp("2017-12-31")
    expected = subs.loc[subs["start_date"] <= d, "arr"].sum()  # nothing has expired by then
    got = s.arr_at(subs, pd.DatetimeIndex([d]))["arr"].iloc[0]
    assert got == expected


def test_expansion_months(data):
    b = s.expansion_months(s.monthly_bookings(s.subscriptions(data.pipeline), data.data_end))
    assert not b.loc[b["is_land_month"], "is_expansion"].any()
    assert b.groupby("account_key")["is_land_month"].sum().eq(1).all()
    share = b.loc[~b["is_land_month"], "is_expansion"].mean()
    assert 0.35 < share < 0.55  # "above own median" -> a bit under half
