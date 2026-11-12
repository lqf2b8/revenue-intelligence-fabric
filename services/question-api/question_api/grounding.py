"""Grounding check: every number in an answer must come from a KPI the API returned.

The question layer may round a figure or show a ratio as a percentage, but it may
not produce a number the governed KPIs didn't (no sums, averages or differences
computed by the model). This module finds the numbers in an answer and tests each
against the pool of numbers seen in tool results, the question and the KPI
definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 1,234.5   $10.0M   67%   -0.3   2.4k
_NUM = re.compile(r"(?<![\w.])(-?)\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|[kKmMbB](?![A-Za-z]))?")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


@dataclass(frozen=True)
class Figure:
    text: str
    value: float  # with any k/M/B multiplier applied
    tolerance: float  # half a unit of the last shown digit, times the multiplier
    percent: bool


def figures(text: str) -> list[Figure]:
    out = []
    for m in _NUM.finditer(text):
        sign, whole, frac, suffix = m.groups()
        number = float(sign + whole.replace(",", "") + (frac or ""))
        decimals = len(frac) - 1 if frac else 0
        mult = _SUFFIX.get((suffix or "").lower(), 1.0)
        out.append(Figure(m.group(0).strip(), number * mult, 0.5 * 10 ** -decimals * mult, suffix == "%"))
    return out


def pool_from(obj, pool: set[float] | None = None) -> set[float]:
    """Every number inside a JSON-like object, including digits inside strings (dates, quarters)."""
    pool = set() if pool is None else pool
    if isinstance(obj, bool) or obj is None:
        return pool
    if isinstance(obj, (int, float)):
        pool.add(float(obj))
    elif isinstance(obj, str):
        pool.update(float(x) for x in re.findall(r"\d+(?:\.\d+)?", obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            pool_from(v, pool)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            pool_from(v, pool)
    return pool


def supported(f: Figure, pool: set[float]) -> bool:
    scales = (1.0, 100.0) if f.percent else (1.0,)
    return any(abs(f.value - v * s) <= f.tolerance + 1e-9 * abs(v * s) for v in pool for s in scales)


def unsupported(answer: str, pool: set[float]) -> list[str]:
    return [f.text for f in figures(answer) if not supported(f, pool)]
