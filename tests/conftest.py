from dataclasses import replace
from pathlib import Path

import pytest

from revintel import maven, usage

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="session")
def data():
    return maven.load(RAW)


@pytest.fixture(scope="session")
def usage_on(data):
    return usage.generate(data, usage.UsageConfig())


@pytest.fixture(scope="session")
def usage_off(data):
    return usage.generate(data, replace(usage.UsageConfig(), lift=0.0))
