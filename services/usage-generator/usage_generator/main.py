"""usage-generator: build the synthetic usage events and the reference KPIs, then deliver them.

SYNTHETIC DATA: see revintel/usage.py for how it's made and the planted effect.

Sinks (USAGE_SINKS, comma-separated, default "file"):
  file      always runs: usage_events.csv.gz + reference KPI tables in REVINTEL_PROCESSED_DIR
  kusto     loads the file into the UsageEvents table (settings: revintel.kusto). Use this
            for a cloud cluster; the local compose stack loads through kql-init instead.
  eventhub  replays the events, one day at a time, to an Event Hub or a Fabric
            Eventstream custom endpoint (EVENTHUB_CONNECTION_STRING, EVENTHUB_NAME)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pandas as pd

from revintel import cli
from revintel.paths import USAGE_FILE


def replay_to_eventhub(usage: pd.DataFrame, producer, days_per_second: float = 0.0, max_days: int | None = None) -> int:
    """Send events grouped by day as JSON, in Event Hub batches. Returns events sent.

    days_per_second > 0 paces the replay (e.g. 1 = one simulated day per second),
    so a Fabric real-time dashboard shows the data arriving.
    """
    sent = 0
    for i, (day, rows) in enumerate(usage.groupby("Day", sort=True)):
        if max_days is not None and i >= max_days:
            break
        batch = producer.create_batch()
        for rec in rows.assign(Day=day.strftime("%Y-%m-%d")).to_dict("records"):
            body = json.dumps(rec)
            try:
                batch.add(_event(body))
            except ValueError:  # batch full: send it and start another
                producer.send_batch(batch)
                batch = producer.create_batch()
                batch.add(_event(body))
            sent += 1
        producer.send_batch(batch)
        if days_per_second > 0:
            time.sleep(1 / days_per_second)
    return sent


def _event(body: str):
    try:
        from azure.eventhub import EventData
    except ImportError:  # tests use a fake producer that accepts plain strings
        return body
    return EventData(body)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="usage-generator")
    ap.add_argument("--sinks", default=os.environ.get("USAGE_SINKS", "file"))
    ap.add_argument("--lift", type=float, default=os.environ.get("USAGE_LIFT"))
    ap.add_argument("--seed", type=int, default=os.environ.get("USAGE_SEED"))
    ap.add_argument("--replay-days-per-second", type=float, default=float(os.environ.get("REPLAY_DAYS_PER_SECOND", "0")))
    ap.add_argument("--replay-max-days", type=int, default=os.environ.get("REPLAY_MAX_DAYS"))
    args = ap.parse_args(argv)
    sinks = {s.strip() for s in args.sinks.split(",") if s.strip()}
    unknown = sinks - {"file", "kusto", "eventhub"}
    if unknown:
        sys.exit(f"unknown sinks: {unknown}")

    cli.main(["usage"] + (["--lift", str(args.lift)] if args.lift is not None else [])
             + (["--seed", str(args.seed)] if args.seed is not None else []))
    cli.main(["kpis"])

    if "kusto" in sinks:
        from revintel import kusto

        s = kusto.KustoSettings.from_env()
        kusto.wait_until_ready(s)
        n = kusto.load_usage(s, USAGE_FILE)
        print(f"kusto: loaded {n:,} rows into UsageEvents")

    if "eventhub" in sinks:
        from azure.eventhub import EventHubProducerClient

        usage = pd.read_csv(USAGE_FILE, parse_dates=["Day"])
        producer = EventHubProducerClient.from_connection_string(
            os.environ["EVENTHUB_CONNECTION_STRING"], eventhub_name=os.environ.get("EVENTHUB_NAME"))
        with producer:
            n = replay_to_eventhub(usage, producer, args.replay_days_per_second,
                                   int(args.replay_max_days) if args.replay_max_days else None)
        print(f"eventhub: sent {n:,} events")


if __name__ == "__main__":
    main()
