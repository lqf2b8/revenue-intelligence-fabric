"""kpi-api: governed KPIs over HTTP.  Run: uvicorn kpi_api.main:create_app --factory

GET /health
GET /kpis                      the catalog: definitions, grain, which backend serves each
GET /kpis/{name}?start=&end=&limit=&<key>=<value>
                               rows for one KPI, filtered by time and by any key column
"""

from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
from fastapi import FastAPI, HTTPException, Request

from revintel import catalog

from . import backends

MAX_LIMIT = 5000


def _json_value(v):
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):  # numpy scalar
        return _json_value(v.item())
    return v


def create_app(backend=None) -> FastAPI:
    app = FastAPI(title="Revenue Intelligence KPI API", version="0.1.0")
    app.state.backend = backend or backends.from_env()

    def served_by(kpi) -> str | None:
        b = app.state.backend
        if not b.serves(kpi):
            return None
        return b.source_for(kpi).name if isinstance(b, backends.HybridBackend) else b.name

    @app.get("/health")
    def health():
        return {"status": "ok", "backend": app.state.backend.name}

    @app.get("/kpis")
    def list_kpis():
        return {"kpis": [{**k.to_dict(), "served_by": served_by(k)} for k in catalog.CATALOG.values()]}

    @app.get("/kpis/{name}")
    def get_kpi(name: str, request: Request, start: str | None = None, end: str | None = None, limit: int = 1000):
        try:
            kpi = catalog.get(name)
        except KeyError as e:
            raise HTTPException(404, str(e)) from None
        reserved = {"start", "end", "limit"}
        filters = {k: v for k, v in request.query_params.items() if k not in reserved}
        unknown = set(filters) - set(kpi.keys)
        if unknown:
            raise HTTPException(400, f"{name} can be filtered by {kpi.keys}; unknown: {sorted(unknown)}")
        limit = max(1, min(limit, MAX_LIMIT))
        q = backends.Query(start=start, end=end, filters=filters, limit=limit)
        try:
            df = app.state.backend.fetch(kpi, q)
        except backends.NotServed as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        cols = [c for c in kpi.keys + kpi.values if c in df.columns]
        rows = [{c: _json_value(v) for c, v in r.items()} for r in df[cols].head(limit).to_dict("records")]
        return {
            "kpi": kpi.to_dict(),
            "served_by": served_by(kpi),
            "query": {"start": start, "end": end, "filters": filters, "limit": limit},
            "row_count": len(df),
            "truncated": len(df) > limit,
            "rows": rows,
        }

    return app

