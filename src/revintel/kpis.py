"""Reference implementation of every KPI, in pandas.

These are the "golden numbers". The KQL queries, the Spark notebooks and the Fabric
semantic model are each checked against the output of this module, so a KPI that
disagrees across layers shows up as a failed check rather than a quiet mismatch.

Definitions here must match the README's KPI table word for word.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .subscriptions import arr_at, expansion_months, monthly_bookings, subscriptions


@dataclass(frozen=True)
class KpiParams:
    adoption_min_features: int = 4  # N: core features used in the week to count as "adopted"
    expansion_rise: float = 0.10  # X: 28-day consumption rise vs prior 28 days
    active_window_days: int = 28


# ---------- CRM KPIs (Salesforce) ----------

def open_pipeline(pipeline: pd.DataFrame, as_of: pd.DatetimeIndex, by: list[str]) -> pd.DataFrame:
    """Sum of expected value of opportunities open on each as-of date.

    Open on date d: engaged on or before d and not yet closed (close_date > d or none).
    Prospecting deals have no engage date, so they can't be placed in history and are
    excluded. Value is the product list price, because the extract records no value
    for open deals.
    """
    p = pipeline[pipeline["engage_date"].notna()]
    rows = []
    for d in as_of:
        o = p[(p["engage_date"] <= d) & (p["close_date"].isna() | (p["close_date"] > d))]
        if by:
            g = o.groupby(by, as_index=False).agg(open_pipeline=("sales_price", "sum"), open_deals=("opportunity_id", "count"))
        else:
            g = pd.DataFrame({"open_pipeline": [o["sales_price"].sum()], "open_deals": [len(o)]})
        g.insert(0, "as_of", d)
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def win_rate(pipeline: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Won / (won + lost) for deals closed in the period (quarter of close_date)."""
    c = pipeline[pipeline["deal_stage"].isin(["Won", "Lost"])].copy()
    c["quarter"] = c["close_date"].dt.to_period("Q").astype(str)
    g = c.groupby(["quarter", *by]).agg(won=("deal_stage", lambda s: (s == "Won").sum()), closed=("deal_stage", "size"))
    g["win_rate"] = g["won"] / g["closed"]
    return g.reset_index()


def sales_cycle(pipeline: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Median days from engage date to close date, won deals, by quarter of close."""
    w = pipeline[pipeline["deal_stage"] == "Won"].copy()
    w["quarter"] = w["close_date"].dt.to_period("Q").astype(str)
    w["cycle_days"] = (w["close_date"] - w["engage_date"]).dt.days
    return w.groupby(["quarter", *by], as_index=False)["cycle_days"].median().rename(columns={"cycle_days": "median_cycle_days"})


def arr_by_month(pipeline: pd.DataFrame, data_end: pd.Timestamp, by: list[str] | None = None) -> pd.DataFrame:
    """ARR on each month-end: annual value of subscriptions active that day."""
    subs = subscriptions(pipeline)
    month_ends = pd.date_range(subs["start_date"].min(), data_end, freq="ME")
    return arr_at(subs, month_ends, by=by)


# ---------- Account-by-day table (the cross-source join) ----------

def account_day(usage: pd.DataFrame, pipeline: pd.DataFrame) -> pd.DataFrame:
    """One row per account per day from its first subscription to the last usage day.

    Joins usage (KQL) with subscriptions (Salesforce). This is the table the Spark
    notebooks rebuild in the Lakehouse; every cross-source KPI reads from it.
    primary_tier is the plan tier with the most active ARR on that day.
    """
    daily = usage.groupby(["AccountKey", "Day"]).agg(
        events=("Events", "sum"),
        units=("Units", "sum"),
        active_users=("UserId", "nunique"),
        features_used=("Feature", "nunique"),
    )
    subs = subscriptions(pipeline)
    last_day = usage["Day"].max()
    frames = []
    for acct, s in subs.groupby("account_key"):
        days = pd.date_range(s["start_date"].min(), last_day, freq="D")
        # ARR by tier per day via start/end deltas.
        deltas = pd.concat(
            [s[["plan_tier", "start_date", "arr"]].rename(columns={"start_date": "d"}),
             s[["plan_tier", "end_date", "arr"]].rename(columns={"end_date": "d"}).assign(arr=lambda x: -x["arr"])]
        )
        tier_arr = (deltas.pivot_table(index="d", columns="plan_tier", values="arr", aggfunc="sum")
                    .reindex(days.union(deltas["d"].unique())).fillna(0).cumsum().reindex(days))
        f = pd.DataFrame({"account_key": acct, "day": days, "active_arr": tier_arr.sum(axis=1).round(2).values,
                          "primary_tier": tier_arr.idxmax(axis=1).values})
        frames.append(f)
    ad = pd.concat(frames, ignore_index=True)
    ad = ad.merge(daily, left_on=["account_key", "day"], right_index=True, how="left")
    for c in ["events", "units", "active_users", "features_used"]:
        ad[c] = ad[c].fillna(0)
    ad[["events", "active_users", "features_used"]] = ad[["events", "active_users", "features_used"]].astype(int)
    return ad


# ---------- Usage KPIs (KQL) ----------

def active_accounts(usage: pd.DataFrame, params: KpiParams = KpiParams()) -> pd.DataFrame:
    """Accounts with at least one usage event in the trailing 28 days (inclusive of the day)."""
    days = pd.date_range(usage["Day"].min(), usage["Day"].max(), freq="D")
    seen = usage[["AccountKey", "Day"]].drop_duplicates()
    mat = (seen.assign(v=1).pivot_table(index="Day", columns="AccountKey", values="v", aggfunc="max")
           .reindex(days).fillna(0))
    rolling = mat.rolling(params.active_window_days, min_periods=1).max()
    return pd.DataFrame({"day": days, "active_accounts": rolling.sum(axis=1).astype(int).values})


def consumption_daily(usage: pd.DataFrame) -> pd.DataFrame:
    """Metered units per day, and units per account with usage that day (KQL-only version)."""
    g = usage.groupby("Day").agg(units=("Units", "sum"), accounts=("AccountKey", "nunique")).reset_index()
    g["units_per_active_account"] = g["units"] / g["accounts"]
    return g.rename(columns={"Day": "day"})


def consumption(ad: pd.DataFrame) -> pd.DataFrame:
    """Metered units per day and plan tier, and units per account with usage that day.

    Plan tier comes from the CRM (primary_tier on the account-by-day table), so this
    version needs the cross-source join.
    """
    used = ad[ad["events"] > 0]
    g = used.groupby(["day", "primary_tier"], as_index=False).agg(units=("units", "sum"), accounts=("account_key", "nunique"))
    g["units_per_active_account"] = g["units"] / g["accounts"]
    return g


def adoption(usage: pd.DataFrame, params: KpiParams = KpiParams()) -> pd.DataFrame:
    """Share of accounts active in the week that used at least N distinct features that week.

    Weeks start Monday.
    """
    u = usage.assign(week=usage["Day"] - pd.to_timedelta(usage["Day"].dt.day_of_week, unit="D"))
    f = u.groupby(["week", "AccountKey"])["Feature"].nunique().reset_index(name="features")
    g = f.groupby("week").agg(active_accounts=("AccountKey", "nunique"),
                              adopted=("features", lambda s: (s >= params.adoption_min_features).sum()))
    g["adoption_rate"] = g["adopted"] / g["active_accounts"]
    return g.reset_index()


def expansion_signal(usage: pd.DataFrame, params: KpiParams = KpiParams()) -> pd.DataFrame:
    """At each month-end: an account's units in the last 28 days vs the prior 28 days.

    Windows are inclusive day ranges: last = [d-27, d], prior = [d-55, d-28].
    An account is scored at a month-end only once it has 56 days of history
    (first usage day <= d-55). signal = prior > 0 and (last / prior - 1) > X.
    """
    w = params.active_window_days
    daily = usage.groupby(["AccountKey", "Day"])["Units"].sum()
    first_seen = usage.groupby("AccountKey")["Day"].min()
    last_day = usage["Day"].max()
    month_ends = pd.date_range(usage["Day"].min(), last_day, freq="ME")
    rows = []
    for d in month_ends:
        for acct, first in first_seen.items():
            if first > d - pd.Timedelta(days=2 * w - 1):
                continue
            s = daily.loc[acct]
            last = s[(s.index > d - pd.Timedelta(days=w)) & (s.index <= d)].sum()
            prior = s[(s.index > d - pd.Timedelta(days=2 * w)) & (s.index <= d - pd.Timedelta(days=w))].sum()
            growth = last / prior - 1 if prior > 0 else np.nan
            rows.append((d, acct, round(last, 2), round(prior, 2), growth, bool(prior > 0 and growth > params.expansion_rise)))
    return pd.DataFrame(rows, columns=["month_end", "account_key", "units_last28", "units_prior28", "growth", "signal"])


def signal_vs_next_month_expansion(usage: pd.DataFrame, pipeline: pd.DataFrame, data_end: pd.Timestamp,
                                   params: KpiParams = KpiParams()) -> pd.DataFrame:
    """Does the expansion signal at month-end M precede an expansion month at M+1?

    Returns the expansion rate next month for signal vs no-signal account-months.
    With the planted effect on, the signal group should expand more often.
    """
    sig = expansion_signal(usage, params)
    exp = expansion_months(monthly_bookings(subscriptions(pipeline), data_end))
    sig["next_month"] = (sig["month_end"].dt.to_period("M") + 1)
    m = sig.merge(exp[["account_key", "month", "is_expansion"]], left_on=["account_key", "next_month"],
                  right_on=["account_key", "month"], how="inner")
    return m.groupby("signal").agg(account_months=("is_expansion", "size"), expansion_rate=("is_expansion", "mean")).reset_index()
