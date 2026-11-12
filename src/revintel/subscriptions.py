"""Reframe the hardware CRM as a software business.

The Maven company sells hardware. This project treats:
- each won opportunity as a 12-month subscription that starts on its close date,
- its close_value as that subscription's annual value (ARR contribution),
- each product as a plan tier.

This is a modelling choice, stated in the README. It is not real SaaS data.
"""

from __future__ import annotations

import pandas as pd

TERM_DAYS = 365


def subscriptions(pipeline: pd.DataFrame) -> pd.DataFrame:
    won = pipeline[pipeline["deal_stage"] == "Won"]
    subs = pd.DataFrame(
        {
            "opportunity_id": won["opportunity_id"],
            "account_key": won["account_key"],
            "plan_tier": won["product"],
            "start_date": won["close_date"],
            "end_date": won["close_date"] + pd.Timedelta(days=TERM_DAYS),  # exclusive
            "arr": won["close_value"].astype(float),
        }
    )
    return subs.reset_index(drop=True)


def arr_at(subs: pd.DataFrame, dates: pd.DatetimeIndex, by: list[str] | None = None) -> pd.DataFrame:
    """ARR of subscriptions active on each date (start <= date < end)."""
    by = by or []
    rows = []
    for d in dates:
        active = subs[(subs["start_date"] <= d) & (subs["end_date"] > d)]
        if by:
            g = active.groupby(by, as_index=False)["arr"].sum()
        else:
            g = pd.DataFrame({"arr": [active["arr"].sum()]})
        g.insert(0, "date", d)
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def monthly_bookings(subs: pd.DataFrame, data_end: pd.Timestamp) -> pd.DataFrame:
    """New ARR booked per account per calendar month, with zero-filled months.

    Months run from each account's first won month to the month of data_end.
    """
    s = subs.assign(month=subs["start_date"].dt.to_period("M"))
    booked = s.groupby(["account_key", "month"])["arr"].sum()
    last = data_end.to_period("M")
    frames = []
    for acct, first in s.groupby("account_key")["month"].min().items():
        months = pd.period_range(first, last, freq="M")
        b = booked.loc[acct].reindex(months, fill_value=0.0)
        frames.append(pd.DataFrame({"account_key": acct, "month": months, "new_arr": b.values}))
    out = pd.concat(frames, ignore_index=True)
    out["is_land_month"] = out.groupby("account_key").cumcount() == 0
    return out


def expansion_months(bookings: pd.DataFrame) -> pd.DataFrame:
    """Flag expansion months: after the land month, new ARR above the account's own median.

    Judging each account against its own median keeps large and small accounts
    on the same footing; roughly half of each account's post-land months qualify.
    """
    b = bookings.copy()
    post = b[~b["is_land_month"]]
    med = post.groupby("account_key")["new_arr"].median().rename("median_new_arr")
    b = b.merge(med, on="account_key", how="left")
    b["is_expansion"] = (~b["is_land_month"]) & (b["new_arr"] > b["median_new_arr"])
    return b
