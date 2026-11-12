import pytest

from revintel import cli
from revintel.paths import REF_DIR, USAGE_FILE


@pytest.fixture(scope="session", autouse=True)
def reference_tables():
    """Service tests read the generated usage file and reference KPI tables; build them if missing."""
    if not USAGE_FILE.exists():
        cli.main(["usage"])
    if not (REF_DIR / "account_day.csv.gz").exists():
        cli.main(["kpis"])
    return REF_DIR
