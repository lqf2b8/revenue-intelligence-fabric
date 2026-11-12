"""KPI catalog: the one list of governed KPIs that every service reads.

Each entry says what the KPI means, which table holds it, its keys (grain) and
value columns, and where it lives in each backend:
- `reference`: CSV written by `python -m revintel kpis` (dev and tests)
- `kql`: stored function in the KQL database (usage KPIs only)
- `dax`: measure in the Fabric semantic model (Phase 1)

The definitions match the README's KPI table. Change a definition here and in
README together; tests check that every catalog KPI has a reference table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Kpi:
    name: str
    title: str
    definition: str
    source: str  # "salesforce" | "usage" | "salesforce+usage"
    keys: list[str]
    values: list[str]
    time_key: str
    kql: str | None = None
    dax_measures: dict[str, str] = field(default_factory=dict)  # value column -> semantic-model measure
    unit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


CATALOG: dict[str, Kpi] = {k.name: k for k in [
    Kpi(
        name="open_pipeline_weekly",
        title="Open pipeline",
        definition=("Sum of expected value of deals open on the date (engaged on or before it, not yet closed). "
                    "Expected value is the product list price; Prospecting deals have no engage date and are excluded."),
        source="salesforce", keys=["as_of", "regional_office", "product"], values=["open_pipeline", "open_deals"],
        time_key="as_of", unit="USD", dax_measures={"open_pipeline": "Open Pipeline", "open_deals": "Open Deals"},
    ),
    Kpi(
        name="win_rate_by_product",
        title="Win rate by product",
        definition="Won / (won + lost) for deals closed in the quarter.",
        source="salesforce", keys=["quarter", "product"], values=["won", "closed", "win_rate"],
        time_key="quarter", unit="ratio", dax_measures={"won": "Won Deals", "closed": "Closed Deals", "win_rate": "Win Rate"},
    ),
    Kpi(
        name="win_rate_by_agent",
        title="Win rate by sales agent",
        definition="Won / (won + lost) for deals closed in the quarter.",
        source="salesforce", keys=["quarter", "sales_agent"], values=["won", "closed", "win_rate"],
        time_key="quarter", unit="ratio", dax_measures={"won": "Won Deals", "closed": "Closed Deals", "win_rate": "Win Rate"},
    ),
    Kpi(
        name="sales_cycle_by_product",
        title="Sales cycle",
        definition="Median days from engage date to close date, won deals, by quarter of close.",
        source="salesforce", keys=["quarter", "product"], values=["median_cycle_days"],
        time_key="quarter", unit="days", dax_measures={"median_cycle_days": "Median Sales Cycle Days"},
    ),
    Kpi(
        name="arr_monthly",
        title="ARR",
        definition="Annual value of subscriptions active on the month-end (won deal = 12-month subscription from its close date).",
        source="salesforce", keys=["date"], values=["arr"], time_key="date", unit="USD", dax_measures={"arr": "ARR"},
    ),
    Kpi(
        name="arr_monthly_by_tier",
        title="ARR by plan tier",
        definition="ARR on each month-end, split by plan tier (product).",
        source="salesforce", keys=["date", "plan_tier"], values=["arr"], time_key="date", unit="USD", dax_measures={"arr": "ARR"},
    ),
    Kpi(
        name="active_accounts",
        title="Active accounts",
        definition="Accounts with at least one usage event in the trailing 28 days, including the day itself.",
        source="usage", keys=["day"], values=["active_accounts"], time_key="day", unit="accounts",
        kql="ActiveAccounts()", dax_measures={"active_accounts": "Active Accounts"},
    ),
    Kpi(
        name="consumption_daily",
        title="Consumption",
        definition="Metered units per day, and units per account with usage that day.",
        source="usage", keys=["day"], values=["units", "accounts", "units_per_active_account"], time_key="day",
        unit="units", kql="ConsumptionDaily()",
        dax_measures={"units": "Units", "accounts": "Accounts With Usage", "units_per_active_account": "Units per Active Account"},
    ),
    Kpi(
        name="consumption_by_tier",
        title="Consumption by plan tier",
        definition="Metered units per day and plan tier (the account's tier with the most active ARR that day).",
        source="salesforce+usage", keys=["day", "primary_tier"], values=["units", "accounts", "units_per_active_account"],
        time_key="day", unit="units",
        dax_measures={"units": "Units", "accounts": "Accounts With Usage", "units_per_active_account": "Units per Active Account"},
    ),
    Kpi(
        name="adoption",
        title="Adoption",
        definition="Share of accounts active in the week (Monday start) that used at least N = 4 of the 6 features that week.",
        source="usage", keys=["week"], values=["active_accounts", "adopted", "adoption_rate"], time_key="week",
        unit="ratio", kql="Adoption()",
        dax_measures={"active_accounts": "Weekly Active Accounts", "adopted": "Adopted Accounts", "adoption_rate": "Adoption Rate"},
    ),
    Kpi(
        name="expansion_signal",
        title="Expansion signal",
        definition=("Per account at each month-end: units in the last 28 days vs the prior 28 days; signal when the rise "
                    "is more than X = 10%. Scored once the account has 56 days of usage. Usage data is synthetic and the "
                    "link between this signal and expansion was planted on purpose."),
        source="usage", keys=["month_end", "account_key"], values=["units_last28", "units_prior28", "growth", "signal"],
        time_key="month_end", unit="units", kql="ExpansionSignal()",
        dax_measures={"units_last28": "Units Last 28d", "units_prior28": "Units Prior 28d", "growth": "Units Growth 28d",
                      "signal": "Expansion Signal"},
    ),
]}


def get(name: str) -> Kpi:
    try:
        return CATALOG[name]
    except KeyError:
        raise KeyError(f"unknown KPI {name!r}; known: {sorted(CATALOG)}") from None
