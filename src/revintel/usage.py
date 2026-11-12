"""Synthetic product-usage generator.

SYNTHETIC DATA. Every row this module writes is made up. It is shaped to look like
daily telemetry from a B2B software product, keyed to the real (fictional-company)
accounts in the Maven CRM extract.

How usage is generated, per account and day:
- Usage starts on the account's first won deal (its subscription start).
- Seats scale with the account's employee count, clipped to [min_seats, max_seats].
- Each seat is active on a day with a probability that rises as the account
  onboards (1 - exp(-days/onboarding_tau)), drops at weekends and over the
  late-December holidays, and varies by a per-account health factor.
- Each account adopts each feature (or never does) after a random, feature-specific
  delay, so feature breadth (adoption) builds over time and differs by account.
  Once adopted, each active user touches the feature with a feature-specific probability.
- Events per user-feature are Poisson; metered units are events x a per-feature
  unit weight x gamma noise.

THE PLANTED EFFECT. Before each expansion month (see subscriptions.expansion_months)
usage ramps up linearly over `ramp_days` to (1 + lift) and then decays back over
`decay_days`. So rising usage precedes expansion by construction. The pipeline and
KPIs should recover this; with lift=0 they should find nothing. Any "finding" about
usage leading expansion in this project is this planted effect, not a discovery.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .maven import MavenData
from .subscriptions import expansion_months, monthly_bookings, subscriptions

# name -> (daily probability an active user touches it once the account has it,
#          units per event,
#          probability the account ever adopts it,
#          mean days from subscription start to adoption)
DEFAULT_FEATURES: dict[str, tuple[float, float, float, float]] = {
    "dashboards": (0.85, 1.0, 1.00, 0),
    "reports": (0.50, 2.0, 0.90, 20),
    "alerts": (0.35, 0.5, 0.70, 45),
    "api": (0.25, 5.0, 0.50, 60),
    "workflows": (0.20, 3.0, 0.45, 90),
    "ai_assist": (0.12, 8.0, 0.35, 120),
}

USAGE_COLUMNS = ["Day", "AccountKey", "UserId", "Feature", "Events", "Units"]


@dataclass(frozen=True)
class UsageConfig:
    seed: int = 20261113
    seat_rate: float = 0.01  # seats per employee
    min_seats: int = 5
    max_seats: int = 60
    base_active_p: float = 0.55
    weekend_factor: float = 0.25
    holiday_factor: float = 0.4  # Dec 24 - Dec 31
    onboarding_tau: float = 30.0  # days
    health_sigma: float = 0.20  # lognormal sd of per-account health
    events_mean: float = 3.0  # Poisson mean events per user-feature-day
    units_shape: float = 4.0  # gamma shape for unit noise (mean 1)
    lift: float = 0.35  # planted pre-expansion lift; 0 switches the effect off
    ramp_days: int = 28
    decay_days: int = 14
    features: dict[str, tuple[float, float, float, float]] = field(default_factory=lambda: dict(DEFAULT_FEATURES))

    def to_dict(self) -> dict:
        return asdict(self)


def seats_for(employees: pd.Series, cfg: UsageConfig) -> pd.Series:
    return (employees * cfg.seat_rate).round().clip(cfg.min_seats, cfg.max_seats).astype(int)


def expansion_multiplier(days: pd.DatetimeIndex, expansion_starts: list[pd.Timestamp], cfg: UsageConfig) -> np.ndarray:
    """Planted usage multiplier: linear ramp before each expansion month, decay after."""
    m = np.ones(len(days))
    if cfg.lift == 0:
        return m
    t = days.values.astype("datetime64[D]").astype(np.int64)
    for e in expansion_starts:
        e0 = np.datetime64(e.date(), "D").astype(np.int64)
        before = (t >= e0 - cfg.ramp_days) & (t < e0)
        ramp = 1 + cfg.lift * (t - (e0 - cfg.ramp_days) + 1) / cfg.ramp_days
        after = (t >= e0) & (t < e0 + cfg.decay_days)
        decay = 1 + cfg.lift * (1 - (t - e0) / cfg.decay_days)
        m = np.where(before, np.maximum(m, ramp), m)
        m = np.where(after, np.maximum(m, decay), m)
    return m


def generate(data: MavenData, cfg: UsageConfig | None = None) -> pd.DataFrame:
    cfg = cfg or UsageConfig()
    rng = np.random.default_rng(cfg.seed)
    subs = subscriptions(data.pipeline)
    exp = expansion_months(monthly_bookings(subs, data.data_end))
    first_won = subs.groupby("account_key")["start_date"].min()
    end = data.data_end.normalize()

    accts = data.accounts.set_index("account_key").loc[sorted(first_won.index)]
    seats = seats_for(accts["employees"], cfg)
    health = pd.Series(rng.lognormal(0.0, cfg.health_sigma, len(accts)), index=accts.index)

    feat_names = list(cfg.features)
    feat_p = np.array([cfg.features[f][0] for f in feat_names])
    feat_w = np.array([cfg.features[f][1] for f in feat_names])
    feat_adopt_p = np.array([cfg.features[f][2] for f in feat_names])
    feat_delay = np.array([cfg.features[f][3] for f in feat_names])

    frames = []
    for acct in accts.index:  # sorted order + one RNG stream => deterministic for a seed
        days = pd.date_range(first_won[acct], end, freq="D")
        n_days, n_seats = len(days), int(seats[acct])
        age = np.arange(n_days)
        onboard = 1 - np.exp(-(age + 1) / cfg.onboarding_tau)
        weekday = np.where(days.day_of_week >= 5, cfg.weekend_factor, 1.0)
        holiday = np.where((days.month == 12) & (days.day >= 24), cfg.holiday_factor, 1.0)
        e_starts = [p.to_timestamp() for p in exp.loc[(exp["account_key"] == acct) & exp["is_expansion"], "month"]]
        lift = expansion_multiplier(days, e_starts, cfg)

        # Split the lift between more active users and more units per event, so
        # both active-user and consumption measures carry it.
        p_active = np.clip(cfg.base_active_p * health[acct] * onboard * weekday * holiday * np.sqrt(lift), 0, 0.95)
        active = rng.random((n_days, n_seats)) < p_active[:, None]  # day x seat

        # Account-level feature adoption: adopted or not, and from which day.
        adopts = rng.random(len(feat_names)) < feat_adopt_p
        adopt_day = rng.exponential(np.maximum(feat_delay, 1e-9)) * (feat_delay > 0)
        has_feat = adopts[None, :] & (age[:, None] >= adopt_day[None, :])  # day x feature
        p_feat = feat_p[None, :] * has_feat
        uses = rng.random((n_days, n_seats, len(feat_names))) < p_feat[:, None, :]
        uses &= active[:, :, None]
        d_idx, s_idx, f_idx = np.nonzero(uses)
        if len(d_idx) == 0:
            continue
        events = rng.poisson(cfg.events_mean, len(d_idx)) + 1
        noise = rng.gamma(cfg.units_shape, 1 / cfg.units_shape, len(d_idx))
        units = events * feat_w[f_idx] * noise * np.sqrt(lift[d_idx])

        frames.append(
            pd.DataFrame(
                {
                    "Day": days[d_idx],
                    "AccountKey": acct,
                    "UserId": [f"{acct}-u{s:03d}" for s in s_idx],
                    "Feature": np.array(feat_names)[f_idx],
                    "Events": events.astype(np.int64),
                    "Units": np.round(units, 2),
                }
            )
        )
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["Day", "AccountKey", "UserId", "Feature"], ignore_index=True)[USAGE_COLUMNS]
