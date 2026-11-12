"""Command line entry point: `python -m revintel <command>`.

Commands
  usage            Generate the synthetic usage events (CSV.gz) and record the config.
  salesforce-prep  Write Bulk API upsert CSVs for the Salesforce developer org.
  kpis             Write the reference ("golden") KPI tables every other layer is checked against.
  check            Compare a KPI exported from KQL / Spark / Power BI with the reference.
  all              usage + salesforce-prep + kpis.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from . import kpis, maven, salesforce_prep, usage

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
USAGE_FILE = PROCESSED / "usage_events.csv.gz"
REF_DIR = PROCESSED / "reference_kpis"
SF_DIR = ROOT / "salesforce" / "data"

# KPI name -> (key columns, value columns) for `check`.
CHECKS = {
    "active_accounts": (["day"], ["active_accounts"]),
    "consumption_daily": (["day"], ["units", "accounts", "units_per_active_account"]),
    "adoption": (["week"], ["active_accounts", "adopted", "adoption_rate"]),
    "expansion_signal": (["month_end", "account_key"], ["units_last28", "units_prior28", "signal"]),
    "arr_monthly_by_tier": (["date", "plan_tier"], ["arr"]),
    "win_rate_by_product": (["quarter", "product"], ["won", "closed", "win_rate"]),
    "sales_cycle_by_product": (["quarter", "product"], ["median_cycle_days"]),
    "open_pipeline_weekly": (["as_of", "regional_office", "product"], ["open_pipeline", "open_deals"]),
}


def cmd_usage(args) -> None:
    d = maven.load(args.raw)
    cfg = usage.UsageConfig()
    if args.lift is not None:
        cfg = replace(cfg, lift=args.lift)
    if args.seed is not None:
        cfg = replace(cfg, seed=args.seed)
    u = usage.generate(d, cfg)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    u.to_csv(USAGE_FILE, index=False, date_format="%Y-%m-%d", compression={"method": "gzip", "mtime": 0})
    (PROCESSED / "usage_config.json").write_text(json.dumps(cfg.to_dict(), indent=2))
    print(f"usage: {len(u):,} rows, {u['AccountKey'].nunique()} accounts, {u['Day'].min():%Y-%m-%d}..{u['Day'].max():%Y-%m-%d} -> {USAGE_FILE}")


def cmd_salesforce(args) -> None:
    d = maven.load(args.raw)
    limit = None if args.max_opportunities <= 0 else args.max_opportunities
    counts = salesforce_prep.write(salesforce_prep.build(d, limit), args.out)
    total = sum(counts.values()) - counts["account_parents"]
    print(f"salesforce: {counts} (~{total} records, ~{total * 2 / 1024:.1f} MB at 2 KB each) -> {args.out}")


def load_usage() -> pd.DataFrame:
    if not USAGE_FILE.exists():
        sys.exit(f"{USAGE_FILE} not found: run `python -m revintel usage` first")
    return pd.read_csv(USAGE_FILE, parse_dates=["Day"])


def reference_tables(d: maven.MavenData, u: pd.DataFrame, params: kpis.KpiParams) -> dict[str, pd.DataFrame]:
    p = d.pipeline
    ad = kpis.account_day(u, p)
    week_ends = pd.date_range(p["engage_date"].min(), d.data_end, freq="W-SUN")
    return {
        "account_day": ad,
        "active_accounts": kpis.active_accounts(u, params),
        "consumption_daily": kpis.consumption_daily(u),
        "consumption_by_tier": kpis.consumption(ad),
        "adoption": kpis.adoption(u, params),
        "expansion_signal": kpis.expansion_signal(u, params),
        "signal_vs_next_month_expansion": kpis.signal_vs_next_month_expansion(u, p, d.data_end, params),
        "arr_monthly": kpis.arr_by_month(p, d.data_end),
        "arr_monthly_by_tier": kpis.arr_by_month(p, d.data_end, by=["plan_tier"]),
        "win_rate_by_product": kpis.win_rate(p, ["product"]),
        "win_rate_by_agent": kpis.win_rate(p, ["sales_agent"]),
        "sales_cycle_by_product": kpis.sales_cycle(p, ["product"]),
        "open_pipeline_weekly": kpis.open_pipeline(p, week_ends, ["regional_office", "product"]),
    }


def cmd_kpis(args) -> None:
    d = maven.load(args.raw)
    u = load_usage()
    params = kpis.KpiParams()
    REF_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in reference_tables(d, u, params).items():
        path = REF_DIR / (f"{name}.csv.gz" if name == "account_day" else f"{name}.csv")
        df.to_csv(path, index=False, date_format="%Y-%m-%d",
                  compression={"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None)
    (REF_DIR / "params.json").write_text(json.dumps(params.__dict__, indent=2))
    print(f"kpis: reference tables -> {REF_DIR}")
    print(kpis.signal_vs_next_month_expansion(u, d.pipeline, d.data_end, params).to_string(index=False))


def compare(name: str, ref: pd.DataFrame, other: pd.DataFrame, rtol: float = 1e-6, atol: float = 0.01) -> list[str]:
    """Return a list of mismatch descriptions (empty means the KPI agrees)."""
    keys, values = CHECKS[name]
    ref, other = ref.copy(), other.copy()
    for df in (ref, other):
        df.columns = [c.lower() for c in df.columns]
        for k in keys:
            if any(t in k for t in ("day", "week", "date", "month_end", "as_of")):
                df[k] = pd.to_datetime(df[k]).dt.tz_localize(None).dt.normalize()
            else:
                df[k] = df[k].astype(str)
    m = ref.merge(other, on=keys, how="outer", suffixes=("_ref", "_other"), indicator=True)
    problems = []
    missing = m[m["_merge"] != "both"]
    if len(missing):
        problems.append(f"{len(missing)} key rows present on only one side, e.g. {missing[keys + ['_merge']].head(3).to_dict('records')}")
    both = m[m["_merge"] == "both"]
    for v in values:
        a, b = both[f"{v}_ref"], both[f"{v}_other"]
        if a.dtype == bool or b.dtype == bool or a.dtype == object:
            bad = a.astype(str).str.lower() != b.astype(str).str.lower()
        else:
            bad = ~pd.Series(abs(a.astype(float) - b.astype(float)) <= atol + rtol * abs(a.astype(float)), index=both.index)
        if bad.any():
            problems.append(f"{v}: {int(bad.sum())} mismatches, e.g. {both.loc[bad, keys + [f'{v}_ref', f'{v}_other']].head(3).to_dict('records')}")
    return problems


def cmd_check(args) -> None:
    ref = pd.read_csv(REF_DIR / f"{args.kpi}.csv")
    other = pd.read_csv(args.file)
    problems = compare(args.kpi, ref, other)
    if problems:
        print(f"{args.kpi}: MISMATCH\n  " + "\n  ".join(problems))
        sys.exit(1)
    print(f"{args.kpi}: matches reference ({len(ref)} rows)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="revintel")
    ap.add_argument("--raw", default=RAW, type=Path, help="folder with the Maven CSVs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("usage")
    u.add_argument("--lift", type=float, help="planted pre-expansion lift; 0 turns it off")
    u.add_argument("--seed", type=int)
    s = sub.add_parser("salesforce-prep")
    s.add_argument("--max-opportunities", type=int, default=2000, help="0 = load all (won't fit Developer Edition)")
    s.add_argument("--out", type=Path, default=SF_DIR)
    sub.add_parser("kpis")
    c = sub.add_parser("check")
    c.add_argument("kpi", choices=sorted(CHECKS))
    c.add_argument("file", type=Path, help="CSV exported from KQL, Spark or Power BI")
    sub.add_parser("all")
    args = ap.parse_args(argv)
    if args.cmd == "usage":
        cmd_usage(args)
    elif args.cmd == "salesforce-prep":
        cmd_salesforce(args)
    elif args.cmd == "kpis":
        cmd_kpis(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "all":
        args.lift = args.seed = None
        args.max_opportunities, args.out = 2000, SF_DIR
        cmd_usage(args)
        cmd_salesforce(args)
        cmd_kpis(args)


if __name__ == "__main__":
    main()
