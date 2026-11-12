"""Turn the cleaned Maven tables into Salesforce Bulk API 2.0 upsert files.

Every object is upserted on an external ID (Maven_*_Key__c / Maven_Opportunity_Id__c),
so the load can be re-run safely and relationships resolve by key instead of by
Salesforce record Id:
- Opportunity -> Account via the column "Account.Maven_Account_Key__c"
- Opportunity -> Product2 via "Plan_Tier__r.Maven_Product_Key__c"
- Account -> parent Account via "Parent.Maven_Account_Key__c" (second pass, after
  every account exists)

Developer Edition orgs have 5 MB of data storage (about 2,500 records at ~2 KB each),
so by default only a sample of opportunities is loaded. The sample keeps whole
accounts, so each sampled account's revenue history stays complete. The Lakehouse
gets the full set straight from the CSVs; the README says so.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .maven import MavenData

STAGE_MAP = {"Prospecting": "Prospecting", "Engaging": "Qualification", "Won": "Closed Won", "Lost": "Closed Lost"}
# Salesforce requires a CloseDate. Open deals have none in the extract, so they get
# the end of the quarter after the data ends, flagged with Close_Date_Estimated__c.
OPEN_DEAL_CLOSE_DATE = "2018-03-31"


def product_key(name: str) -> str:
    return name.lower().replace(" ", "-")


def sample_opportunities(d: MavenData, max_opportunities: int | None, seed: int = 7) -> pd.DataFrame:
    p = d.pipeline
    if max_opportunities is None or max_opportunities >= len(p):
        return p
    rng = np.random.default_rng(seed)
    with_acct = p[p["account_key"].notna()]
    no_acct = p[p["account_key"].isna()]
    frac_target = max_opportunities / len(p)
    keep, n = [], 0
    counts = with_acct.groupby("account_key").size()
    budget = max_opportunities - round(len(no_acct) * frac_target)
    for acct in rng.permutation(counts.index.to_numpy()):
        if n + counts[acct] > budget:
            continue
        keep.append(acct)
        n += counts[acct]
    sampled = with_acct[with_acct["account_key"].isin(keep)]
    room = max_opportunities - len(sampled)
    extra = no_acct.sample(n=min(room, len(no_acct)), random_state=seed) if room > 0 else no_acct.iloc[:0]
    return pd.concat([sampled, extra]).sort_values("opportunity_id")


def build(d: MavenData, max_opportunities: int | None = 2000) -> dict[str, pd.DataFrame]:
    opps = sample_opportunities(d, max_opportunities)
    acct_keys = set(opps["account_key"].dropna())
    a = d.accounts[d.accounts["account_key"].isin(acct_keys)]

    products = pd.DataFrame({
        "Maven_Product_Key__c": d.products["product"].map(product_key),
        "Name": d.products["product"],
        "ProductCode": d.products["product"].map(product_key).str.upper(),
        "Family": d.products["series"],
        "List_Price__c": d.products["sales_price"],
        "IsActive": "true",
    })

    accounts = pd.DataFrame({
        "Maven_Account_Key__c": a["account_key"],
        "Name": a["account"],
        "Industry": a["sector"].str.title(),
        "YearStarted": a["year_established"].astype(str),
        "AnnualRevenue": (a["revenue"] * 1_000_000).round(0).astype(np.int64),  # extract is in $M
        "NumberOfEmployees": a["employees"],
        "BillingCountry": a["office_location"],
    })

    # Parents only where the parent is also in the load.
    par = a[a["parent_key"].notna() & a["parent_key"].isin(acct_keys)]
    account_parents = pd.DataFrame({
        "Maven_Account_Key__c": par["account_key"],
        "Parent.Maven_Account_Key__c": par["parent_key"],
    })

    o = opps
    is_open = o["is_open"]
    opp = pd.DataFrame({
        "Maven_Opportunity_Id__c": o["opportunity_id"],
        "Name": o["product"] + " - " + o["account"].fillna("No account") + " - " + o["opportunity_id"],
        "StageName": o["deal_stage"].map(STAGE_MAP),
        "CloseDate": o["close_date"].dt.strftime("%Y-%m-%d").where(~is_open, OPEN_DEAL_CLOSE_DATE),
        "Close_Date_Estimated__c": np.where(is_open, "true", "false"),
        "Amount": o["amount"],
        "Engage_Date__c": o["engage_date"].dt.strftime("%Y-%m-%d").fillna(""),
        "Sales_Agent__c": o["sales_agent"],
        "Sales_Manager__c": o["manager"],
        "Regional_Office__c": o["regional_office"],
        "Plan_Tier__r.Maven_Product_Key__c": o["product"].map(product_key),
        "Account.Maven_Account_Key__c": o["account_key"],
    })
    # Bulk API resolves the account by external ID; rows with no account go in a
    # separate file without that column rather than relying on a blank key.
    has_acct = opp["Account.Maven_Account_Key__c"].notna()
    return {
        "products": products,
        "accounts": accounts,
        "account_parents": account_parents,
        "opportunities": opp[has_acct],
        "opportunities_no_account": opp[~has_acct].drop(columns=["Account.Maven_Account_Key__c"]),
    }


def write(files: dict[str, pd.DataFrame], out_dir: str | Path) -> dict[str, int]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, df in files.items():
        df.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
        counts[name] = len(df)
    return counts
