"""Where KPI rows come from. Every backend returns rows for a catalog KPI.

reference  CSV tables from `python -m revintel kpis` (dev, tests, CI)
kusto      KQL functions in the KQL database (usage KPIs only)
fabric     DAX against the Fabric semantic model through the Power BI REST API
hybrid     usage KPIs from kusto, everything else from reference (the local compose stack)

Selected with KPI_BACKEND. The question layer only ever talks to this API, so
pointing KPI_BACKEND at fabric moves every answer onto the semantic model's
definitions without touching the question layer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from revintel.catalog import Kpi

DATE_KEYS = {"day", "week", "date", "month_end", "as_of"}


class NotServed(Exception):
    """This backend has no source for the KPI."""


@dataclass
class Query:
    start: str | None = None  # ISO date, inclusive
    end: str | None = None  # ISO date, inclusive
    filters: dict[str, str] | None = None  # key column -> exact value
    limit: int = 1000


def _quarter(d: str) -> str:
    return str(pd.Timestamp(d).to_period("Q"))


def apply_query(kpi: Kpi, df: pd.DataFrame, q: Query) -> pd.DataFrame:
    t = kpi.time_key
    if t in DATE_KEYS:
        df[t] = pd.to_datetime(df[t]).dt.tz_localize(None)
        if q.start:
            df = df[df[t] >= pd.Timestamp(q.start)]
        if q.end:
            df = df[df[t] <= pd.Timestamp(q.end)]
    elif t == "quarter":
        if q.start:
            df = df[df[t].astype(str) >= _quarter(q.start)]
        if q.end:
            df = df[df[t].astype(str) <= _quarter(q.end)]
    for col, val in (q.filters or {}).items():
        if col not in kpi.keys:
            raise ValueError(f"{kpi.name} can be filtered by {kpi.keys}, not {col!r}")
        df = df[df[col].astype(str).str.lower() == str(val).lower()]
    return df.sort_values(kpi.keys).reset_index(drop=True)


class ReferenceBackend:
    name = "reference"

    def __init__(self, ref_dir: Path):
        self.ref_dir = Path(ref_dir)
        self._cache: dict[str, pd.DataFrame] = {}

    def serves(self, kpi: Kpi) -> bool:
        return (self.ref_dir / f"{kpi.name}.csv").exists() or (self.ref_dir / f"{kpi.name}.csv.gz").exists()

    def fetch(self, kpi: Kpi, q: Query) -> pd.DataFrame:
        if not self.serves(kpi):
            raise NotServed(f"no reference table for {kpi.name} in {self.ref_dir}")
        if kpi.name not in self._cache:
            p = self.ref_dir / f"{kpi.name}.csv"
            self._cache[kpi.name] = pd.read_csv(p if p.exists() else p.with_suffix(".csv.gz"))
        return apply_query(kpi, self._cache[kpi.name].copy(), q)


class KustoBackend:
    name = "kusto"

    def __init__(self, settings=None):
        from revintel import kusto

        self._kusto = kusto
        self.settings = settings or kusto.KustoSettings.from_env()

    def serves(self, kpi: Kpi) -> bool:
        return kpi.kql is not None

    def fetch(self, kpi: Kpi, q: Query) -> pd.DataFrame:
        if not kpi.kql:
            raise NotServed(f"{kpi.name} is not a KQL KPI")
        # Filtering is done in pandas after the function runs: the functions are
        # small, and this keeps user input out of the query text entirely.
        return apply_query(kpi, self._kusto.query_df(self.settings, kpi.kql), q)


class FabricBackend:
    """DAX over the Fabric semantic model via the Power BI executeQueries REST API.

    FABRIC_DATASET_ID       semantic model id
    FABRIC_COLUMN_MAP       JSON file mapping catalog key columns to 'Table'[Column]
    FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET   service principal
    Measures come from Kpi.dax_measures. Phase 1: the model doesn't exist yet, so
    this backend is built and unit-tested on its DAX, not yet run for real.
    """

    name = "fabric"
    URL = "https://api.powerbi.com/v1.0/myorg/datasets/{id}/executeQueries"

    def __init__(self, dataset_id: str | None = None, column_map: dict[str, str] | None = None, token_provider=None):
        self.dataset_id = dataset_id or os.environ.get("FABRIC_DATASET_ID")
        if column_map is None:
            path = os.environ.get("FABRIC_COLUMN_MAP")
            column_map = json.loads(Path(path).read_text()) if path else {}
        self.column_map = column_map
        self._token_provider = token_provider

    def serves(self, kpi: Kpi) -> bool:
        return bool(kpi.dax_measures) and all(k in self.column_map for k in kpi.keys)

    def dax(self, kpi: Kpi, q: Query) -> str:
        cols = [self.column_map[k] for k in kpi.keys]
        args = list(cols)
        t = kpi.time_key
        if t in self.column_map and (q.start or q.end) and t in DATE_KEYS:
            lo = f"DATE({pd.Timestamp(q.start).year},{pd.Timestamp(q.start).month},{pd.Timestamp(q.start).day})" if q.start else "DATE(1900,1,1)"
            hi = f"DATE({pd.Timestamp(q.end).year},{pd.Timestamp(q.end).month},{pd.Timestamp(q.end).day})" if q.end else "DATE(2999,12,31)"
            args.append(f"DATESBETWEEN({self.column_map[t]}, {lo}, {hi})")
        for col, val in (q.filters or {}).items():
            if col not in kpi.keys:
                raise ValueError(f"{kpi.name} can be filtered by {kpi.keys}, not {col!r}")
            safe = str(val).replace('"', '""')
            args.append(f'TREATAS({{"{safe}"}}, {self.column_map[col]})')
        args += [f'"{col}", [{measure}]' for col, measure in kpi.dax_measures.items()]
        return "EVALUATE\nSUMMARIZECOLUMNS(\n    " + ",\n    ".join(args) + "\n)"

    def _token(self) -> str:
        if self._token_provider:
            return self._token_provider()
        from azure.identity import ClientSecretCredential

        cred = ClientSecretCredential(os.environ["FABRIC_TENANT_ID"], os.environ["FABRIC_CLIENT_ID"],
                                      os.environ["FABRIC_CLIENT_SECRET"])
        return cred.get_token("https://analysis.windows.net/powerbi/api/.default").token

    def fetch(self, kpi: Kpi, q: Query) -> pd.DataFrame:
        import httpx

        if not self.serves(kpi):
            raise NotServed(f"{kpi.name}: no measures or column mapping in the semantic model")
        body = {"queries": [{"query": self.dax(kpi, q)}], "serializerSettings": {"includeNulls": True}}
        r = httpx.post(self.URL.format(id=self.dataset_id), json=body,
                       headers={"Authorization": f"Bearer {self._token()}"}, timeout=60)
        r.raise_for_status()
        rows = r.json()["results"][0]["tables"][0]["rows"]
        # Columns come back as 'Table'[Column] or [name]; map them to catalog names.
        reverse = {v: k for k, v in self.column_map.items()}
        df = pd.DataFrame([{reverse.get(k, k.strip("[]")): v for k, v in row.items()} for row in rows])
        return apply_query(kpi, df, Query(limit=q.limit)) if len(df) else df


class HybridBackend:
    name = "hybrid"

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback

    def serves(self, kpi: Kpi) -> bool:
        return self.primary.serves(kpi) or self.fallback.serves(kpi)

    def source_for(self, kpi: Kpi):
        return self.primary if self.primary.serves(kpi) else self.fallback

    def fetch(self, kpi: Kpi, q: Query) -> pd.DataFrame:
        return self.source_for(kpi).fetch(kpi, q)


def from_env():
    from revintel.paths import REF_DIR

    kind = os.environ.get("KPI_BACKEND", "reference")
    if kind == "reference":
        return ReferenceBackend(REF_DIR)
    if kind == "kusto":
        return KustoBackend()
    if kind == "fabric":
        return FabricBackend()
    if kind == "hybrid":
        return HybridBackend(KustoBackend(), ReferenceBackend(REF_DIR))
    raise ValueError(f"KPI_BACKEND must be reference, kusto, fabric or hybrid, not {kind!r}")
