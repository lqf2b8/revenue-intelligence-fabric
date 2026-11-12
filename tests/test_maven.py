import pandas as pd

from revintel import maven
from conftest import RAW


def test_shape_and_stages(data):
    p = data.pipeline
    assert len(p) == 8800
    assert p["deal_stage"].value_counts().to_dict() == {"Won": 4238, "Lost": 2473, "Engaging": 1589, "Prospecting": 500}
    assert len(data.accounts) == 85


def test_quirks_fixed(data):
    assert "GTXPro" not in set(data.pipeline["product"])
    assert data.pipeline["sales_price"].notna().all()
    assert "technolgy" not in set(data.accounts["sector"])


def test_open_deal_amount_is_list_price(data):
    p = data.pipeline
    open_ = p[p["is_open"]]
    assert (open_["amount"] == open_["sales_price"]).all()
    lost = p[p["deal_stage"] == "Lost"]
    assert (lost["amount"] == 0).all()


def test_only_open_deals_lack_account(data):
    p = data.pipeline
    assert set(p.loc[p["account_key"].isna(), "deal_stage"]) <= {"Prospecting", "Engaging"}


def test_account_key():
    assert maven.account_key("Acme Corporation") == "acme-corporation"
    assert maven.account_key("Green-Plus") == "green-plus"


def test_excel_resaved_dates_parse_the_same(tmp_path, data):
    """Files re-saved in Excel use M/D/YYYY; the loader must give identical results."""
    for f in ["accounts.csv", "products.csv", "sales_teams.csv"]:
        (tmp_path / f).write_text((RAW / f).read_text())
    p = pd.read_csv(RAW / "sales_pipeline.csv")
    for c in ["engage_date", "close_date"]:
        d = pd.to_datetime(p[c])
        p[c] = d.dt.month.astype("Int64").astype(str) + "/" + d.dt.day.astype("Int64").astype(str) + "/" + d.dt.year.astype("Int64").astype(str)
        p.loc[d.isna(), c] = None
    p.to_csv(tmp_path / "sales_pipeline.csv", index=False)
    other = maven.load(tmp_path)
    pd.testing.assert_frame_equal(other.pipeline, data.pipeline)


def test_data_end(data):
    assert data.data_end == pd.Timestamp("2017-12-31")
