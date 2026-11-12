from revintel import salesforce_prep as sp


def test_sample_fits_developer_edition(data):
    files = sp.build(data, 2000)
    n = sum(len(v) for k, v in files.items() if k != "account_parents")
    assert n <= 2100  # ~4 MB at 2 KB per record, under the 5 MB limit


def test_sample_keeps_whole_accounts(data):
    files = sp.build(data, 2000)
    loaded = files["opportunities"]
    keys = set(loaded["Account.Maven_Account_Key__c"])
    full = data.pipeline[data.pipeline["account_key"].isin(keys)]
    assert len(full) == len(loaded)
    assert keys == set(files["accounts"]["Maven_Account_Key__c"])


def test_full_load_when_unlimited(data):
    files = sp.build(data, None)
    assert len(files["opportunities"]) + len(files["opportunities_no_account"]) == 8800


def test_columns_and_mappings(data):
    files = sp.build(data, 2000)
    o = files["opportunities"]
    assert o["StageName"].notna().all()
    assert o["Account.Maven_Account_Key__c"].notna().all()
    assert "Account.Maven_Account_Key__c" not in files["opportunities_no_account"].columns
    assert set(o["Plan_Tier__r.Maven_Product_Key__c"]) <= set(files["products"]["Maven_Product_Key__c"])
    open_ = o[o["Close_Date_Estimated__c"] == "true"]
    assert (open_["CloseDate"] == sp.OPEN_DEAL_CLOSE_DATE).all()
    assert o["Maven_Opportunity_Id__c"].is_unique
