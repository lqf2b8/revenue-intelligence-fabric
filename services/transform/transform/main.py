"""transform: run the Spark jobs on local files and optionally check them against the reference.

Reads   REVINTEL_RAW_DIR/sales_pipeline.csv and REVINTEL_PROCESSED_DIR/usage_events.csv.gz
Writes  REVINTEL_PROCESSED_DIR/spark/{account_day,consumption_by_tier}.parquet

In Fabric the notebook calls the same functions in transform.jobs on Lakehouse tables.
"""

from __future__ import annotations

import argparse
import sys

from pyspark.sql import SparkSession

from revintel.cli import compare, read_reference
from revintel.paths import PROCESSED, RAW, USAGE_FILE

from . import jobs

OUT = PROCESSED / "spark"


def spark_session() -> SparkSession:
    return (SparkSession.builder.appName("revintel-transform").master("local[*]")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.shuffle.partitions", "8")
            .config("spark.ui.enabled", "false")
            .getOrCreate())


def run(spark: SparkSession) -> dict:
    pipeline = jobs.clean_pipeline(spark.read.csv(str(RAW / "sales_pipeline.csv"), header=True, inferSchema=False))
    usage = spark.read.csv(str(USAGE_FILE), header=True, inferSchema=True)
    ad = jobs.account_day(usage, pipeline).cache()
    out = {"account_day": ad, "consumption_by_tier": jobs.consumption_by_tier(ad)}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        df.toPandas().to_parquet(OUT / f"{name}.parquet", index=False)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="transform")
    ap.add_argument("--check", action="store_true", help="compare outputs with the reference tables")
    args = ap.parse_args(argv)
    spark = spark_session()
    try:
        out = run(spark)
        failures = 0
        for name, df in out.items():
            pdf = df.toPandas()
            print(f"{name}: {len(pdf):,} rows -> {OUT / (name + '.parquet')}")
            if args.check:
                problems = compare(name, read_reference(name), pdf)
                failures += bool(problems)
                print(("  FAIL\n    " + "\n    ".join(problems)) if problems else "  ok: matches the reference")
        if failures:
            sys.exit(f"{failures} table(s) disagree with the reference")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
