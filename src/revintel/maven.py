"""Load and clean the Maven Analytics CRM Sales Opportunities CSVs.

The raw files are a public dataset for a fictional hardware company. This module
fixes the known quirks once, so every downstream step (Salesforce load, usage
generator, KPI reference numbers) sees the same cleaned tables.

Known quirks handled here:
- sales_pipeline.product uses "GTXPro" while products.product uses "GTX Pro".
- accounts.sector has the typo "technolgy".
- Dates are ISO in the original zip but M/D/YYYY if the file was re-saved in Excel.
- Open deals (Prospecting, Engaging) have no close_date and no close_value.
- 1,425 open deals have no account.
- Prospecting deals have no engage_date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PRODUCT_FIXES = {"GTXPro": "GTX Pro"}
SECTOR_FIXES = {"technolgy": "technology"}

OPEN_STAGES = ("Prospecting", "Engaging")
CLOSED_STAGES = ("Won", "Lost")
ALL_STAGES = OPEN_STAGES + CLOSED_STAGES


def account_key(name: str) -> str:
    """Stable, URL-safe key for an account name: 'Acme Corporation' -> 'acme-corporation'.

    Used as the Salesforce external ID and as AccountKey in the usage data, so the
    two sources join on the same value everywhere.
    """
    if pd.isna(name):
        return name
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


@dataclass(frozen=True)
class MavenData:
    accounts: pd.DataFrame
    products: pd.DataFrame
    sales_teams: pd.DataFrame
    pipeline: pd.DataFrame

    @property
    def data_end(self) -> pd.Timestamp:
        """Last date covered by the CRM extract (latest close or engage date)."""
        return max(self.pipeline["close_date"].max(), self.pipeline["engage_date"].max())


def _find(raw_dir: Path, *names: str) -> Path:
    for n in names:
        p = raw_dir / n
        if p.exists():
            return p
    raise FileNotFoundError(f"None of {names} found in {raw_dir}")


def _parse_dates(s: pd.Series) -> pd.Series:
    # format="mixed" accepts both 2017-03-01 and 3/1/2017.
    return pd.to_datetime(s, format="mixed", errors="raise")


def load(raw_dir: str | Path) -> MavenData:
    raw_dir = Path(raw_dir)
    accounts = pd.read_csv(_find(raw_dir, "accounts.csv"))
    products = pd.read_csv(_find(raw_dir, "products.csv"))
    sales_teams = pd.read_csv(_find(raw_dir, "sales_teams.csv", "sales_team.csv"))
    pipeline = pd.read_csv(_find(raw_dir, "sales_pipeline.csv"))

    accounts["sector"] = accounts["sector"].replace(SECTOR_FIXES)
    accounts["account_key"] = accounts["account"].map(account_key)
    accounts["parent_key"] = accounts["subsidiary_of"].map(account_key)

    pipeline["product"] = pipeline["product"].replace(PRODUCT_FIXES)
    pipeline["engage_date"] = _parse_dates(pipeline["engage_date"])
    pipeline["close_date"] = _parse_dates(pipeline["close_date"])
    pipeline["account_key"] = pipeline["account"].map(account_key)
    pipeline = pipeline.merge(products[["product", "series", "sales_price"]], on="product", how="left")
    pipeline = pipeline.merge(sales_teams, on="sales_agent", how="left")
    pipeline["is_open"] = pipeline["deal_stage"].isin(OPEN_STAGES)
    # Open deals carry no value in the extract. Their expected value is the product
    # list price; closed deals use the booked close_value (0 for lost deals).
    pipeline["amount"] = pipeline["close_value"].where(~pipeline["is_open"], pipeline["sales_price"])

    data = MavenData(accounts, products, sales_teams, pipeline)
    validate(data)
    return data


def validate(d: MavenData) -> None:
    p = d.pipeline
    problems = []
    if p["opportunity_id"].duplicated().any():
        problems.append("duplicate opportunity_id")
    if not p["deal_stage"].isin(ALL_STAGES).all():
        problems.append(f"unexpected stages {set(p['deal_stage']) - set(ALL_STAGES)}")
    if p["sales_price"].isna().any():
        problems.append(f"unmatched products {set(p.loc[p['sales_price'].isna(), 'product'])}")
    if p["manager"].isna().any():
        problems.append("sales agents missing from sales_teams")
    known = set(d.accounts["account_key"])
    unknown = set(p["account_key"].dropna()) - known
    if unknown:
        problems.append(f"pipeline accounts missing from accounts: {sorted(unknown)[:5]}")
    closed = p[~p["is_open"]]
    if closed["close_date"].isna().any() or closed["close_value"].isna().any():
        problems.append("closed deals without close_date/close_value")
    if closed["account_key"].isna().any():
        problems.append("closed deals without an account")
    if (closed["close_date"] < closed["engage_date"]).any():
        problems.append("close_date before engage_date")
    if d.accounts["account_key"].duplicated().any():
        problems.append("account names collide after keying")
    if problems:
        raise ValueError("Maven data failed validation: " + "; ".join(problems))
