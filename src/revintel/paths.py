"""Data locations. Defaults suit a repo checkout; containers override them with env vars.

REVINTEL_RAW_DIR        Maven CSVs                      (default: <repo>/data/raw)
REVINTEL_PROCESSED_DIR  generated usage + reference KPIs (default: <repo>/data/processed)
REVINTEL_SF_DIR         Salesforce upsert CSVs           (default: <repo>/salesforce/data)
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

RAW = Path(os.environ.get("REVINTEL_RAW_DIR", _REPO / "data" / "raw"))
PROCESSED = Path(os.environ.get("REVINTEL_PROCESSED_DIR", _REPO / "data" / "processed"))
SF_DIR = Path(os.environ.get("REVINTEL_SF_DIR", _REPO / "salesforce" / "data"))
USAGE_FILE = PROCESSED / "usage_events.csv.gz"
REF_DIR = PROCESSED / "reference_kpis"
