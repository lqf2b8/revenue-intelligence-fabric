from dataclasses import replace
import pytest

from revintel import maven, usage
from revintel.paths import RAW



@pytest.fixture(scope="session")
def data():
    return maven.load(RAW)


@pytest.fixture(scope="session")
def usage_on(data):
    return usage.generate(data, usage.UsageConfig())


@pytest.fixture(scope="session")
def usage_off(data):
    return usage.generate(data, replace(usage.UsageConfig(), lift=0.0))
