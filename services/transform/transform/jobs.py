"""PySpark transforms: the account-by-day table and the KPIs that need both sources.

Plain functions over DataFrames, with no I/O, so the same code runs in this
container (local files) and in a Fabric notebook (Lakehouse tables). The output
must match the pandas reference in revintel.kpis exactly; `transform --check`
compares them.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

TERM_DAYS = 365
PRODUCT_FIXES = {"GTXPro": "GTX Pro"}


def clean_pipeline(pipeline: DataFrame) -> DataFrame:
    """Same fixes as revintel.maven: product name, account key, typed dates."""
    p = pipeline
    for bad, good in PRODUCT_FIXES.items():
        p = p.withColumn("product", F.when(F.col("product") == bad, good).otherwise(F.col("product")))
    key = F.regexp_replace(F.regexp_replace(F.lower(F.col("account")), r"[^a-z0-9]+", "-"), r"^-+|-+$", "")
    return (p.withColumn("account_key", key)
             .withColumn("engage_date", F.to_date("engage_date"))
             .withColumn("close_date", F.to_date("close_date"))
             .withColumn("close_value", F.col("close_value").cast("double")))


def subscriptions(pipeline: DataFrame) -> DataFrame:
    """Won deal = 12-month subscription from its close date; product = plan tier."""
    return (pipeline.where(F.col("deal_stage") == "Won")
            .select("account_key", F.col("product").alias("plan_tier"),
                    F.col("close_date").alias("start_date"),
                    F.date_add("close_date", TERM_DAYS).alias("end_date"),  # exclusive
                    F.col("close_value").alias("arr")))


def account_day(usage: DataFrame, pipeline: DataFrame) -> DataFrame:
    """One row per account per day, from its first subscription to the last usage day.

    Columns: account_key, day, active_arr, primary_tier, events, units, active_users, features_used.
    primary_tier = plan tier with the most active ARR that day (ties: tier name A-Z).
    """
    subs = subscriptions(pipeline)
    last_day = usage.agg(F.max(F.to_date("Day")).alias("d")).collect()[0]["d"]

    days = (subs.groupBy("account_key").agg(F.min("start_date").alias("first"))
            .select("account_key", F.explode(F.sequence("first", F.lit(last_day))).alias("day")))

    tier_arr = (days.join(subs, "account_key")
                .where((F.col("start_date") <= F.col("day")) & (F.col("end_date") > F.col("day")))
                .groupBy("account_key", "day", "plan_tier").agg(F.sum("arr").alias("arr")))
    w = Window.partitionBy("account_key", "day").orderBy(F.col("arr").desc(), F.col("plan_tier").asc())
    arr = (tier_arr.withColumn("rank", F.row_number().over(w))
           .groupBy("account_key", "day")
           .agg(F.round(F.sum("arr"), 2).alias("active_arr"),
                F.max(F.when(F.col("rank") == 1, F.col("plan_tier"))).alias("primary_tier")))

    daily = (usage.withColumn("day", F.to_date("Day"))
             .groupBy(F.col("AccountKey").alias("account_key"), "day")
             .agg(F.sum("Events").alias("events"), F.sum("Units").alias("units"),
                  F.countDistinct("UserId").alias("active_users"),
                  F.countDistinct("Feature").alias("features_used")))

    return (days.join(arr, ["account_key", "day"], "left")
            .join(daily, ["account_key", "day"], "left")
            .fillna(0, subset=["events", "units", "active_users", "features_used", "active_arr"])
            .select("account_key", "day", "active_arr", "primary_tier",
                    F.col("events").cast("long"), "units",
                    F.col("active_users").cast("long"), F.col("features_used").cast("long")))


def consumption_by_tier(ad: DataFrame) -> DataFrame:
    """Metered units per day and plan tier, and units per account with usage that day."""
    return (ad.where(F.col("events") > 0)
            .groupBy("day", "primary_tier")
            .agg(F.sum("units").alias("units"), F.countDistinct("account_key").alias("accounts"))
            .withColumn("units_per_active_account", F.col("units") / F.col("accounts")))
