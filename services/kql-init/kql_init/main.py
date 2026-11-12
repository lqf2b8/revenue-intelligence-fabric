"""kql-init: set up and verify the KQL database.

Works against the local emulator, the free Azure Data Explorer cluster, or a
Fabric Eventhouse; settings come from environment variables (see revintel.kusto).

  apply   run the .kql scripts (table, mapping, KPI functions)
  ingest  replace UsageEvents with the generated usage file and verify the row count
  check   run every catalog KPI that has a KQL function and compare it with the
          reference table; exit 1 on any mismatch
  all     wait for the cluster, apply, ingest, check
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from revintel import catalog, kusto
from revintel.cli import compare, read_reference
from revintel.paths import USAGE_FILE

def _scripts_dir() -> Path:
    if "KQL_SCRIPTS_DIR" in os.environ:  # set in the container image
        return Path(os.environ["KQL_SCRIPTS_DIR"])
    return Path(__file__).resolve().parents[3] / "kql"  # repo checkout


SCRIPTS = _scripts_dir()
DEFAULT_SCRIPTS = ["01_tables.kql", "02_kpi_functions.kql"]


def apply(s: kusto.KustoSettings, names: list[str]) -> None:
    for name in names:
        for first_line in kusto.apply_script(s, SCRIPTS / name):
            print(f"applied [{name}] {first_line}")


def check(s: kusto.KustoSettings) -> int:
    failures = 0
    for kpi in catalog.CATALOG.values():
        if not kpi.kql:
            continue
        got = kusto.query_df(s, kpi.kql)
        problems = compare(kpi.name, read_reference(kpi.name), got)
        if problems:
            failures += 1
            print(f"FAIL {kpi.name} ({kpi.kql}):\n  " + "\n  ".join(problems))
        else:
            print(f"ok   {kpi.name} ({kpi.kql}): {len(got)} rows match the reference")
    return failures


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kql-init")
    ap.add_argument("command", choices=["apply", "ingest", "check", "all"])
    ap.add_argument("--scripts", nargs="*", default=DEFAULT_SCRIPTS)
    args = ap.parse_args(argv)
    s = kusto.KustoSettings.from_env()
    if args.command in ("apply", "all"):
        kusto.wait_until_ready(s)
        apply(s, args.scripts)
    if args.command in ("ingest", "all"):
        kusto.wait_until_ready(s)
        print(f"ingest: UsageEvents has {kusto.load_usage(s, USAGE_FILE):,} rows")
    if args.command in ("check", "all"):
        failures = check(s)
        if failures:
            sys.exit(f"{failures} KPI(s) disagree with the reference")


if __name__ == "__main__":
    main()
