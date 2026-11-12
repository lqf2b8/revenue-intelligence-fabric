#!/usr/bin/env bash
# Load the Maven CRM sample into a Salesforce Developer Edition org.
#
# Prerequisites
#   1. Salesforce CLI:            npm install -g @salesforce/cli
#   2. Log in once with an alias: sf org login web --alias revintel
#   3. Build the CSVs:            python -m revintel salesforce-prep   (from the repo root)
#
# Usage:  salesforce/scripts/load.sh [org-alias]       (default alias: revintel)
#
# Every step is an upsert on an external ID, so re-running is safe.
set -euo pipefail

ORG="${1:-revintel}"
cd "$(dirname "$0")/.."   # -> salesforce/
DATA=data

for f in products accounts account_parents opportunities opportunities_no_account; do
  [[ -f "$DATA/$f.csv" ]] || { echo "Missing $DATA/$f.csv - run: python -m revintel salesforce-prep"; exit 1; }
done

echo "== Storage before load"
sf org list limits --target-org "$ORG" --json | python3 -c '
import json, sys
for l in json.load(sys.stdin)["result"]:
    if l["name"] in ("DataStorageMB", "DailyBulkV2QueryJobs", "DailyApiRequests"):
        print("  %s: %s used of %s" % (l["name"], l["max"] - l["remaining"], l["max"]))'

echo "== Deploy custom fields and permission set"
sf project deploy start --source-dir force-app --target-org "$ORG" --wait 10
sf org assign permset --name Revenue_Intel_Loader --target-org "$ORG" \
  || echo "  (permission set already assigned)"

upsert () {  # sobject file external-id
  echo "== Upsert $1 from $2"
  sf data upsert bulk --sobject "$1" --file "$DATA/$2.csv" --external-id "$3" \
    --target-org "$ORG" --wait 15 --line-ending LF
}

upsert Product2    products                  Maven_Product_Key__c
upsert Account     accounts                  Maven_Account_Key__c
# Parents only after every account exists.
if [[ $(wc -l < "$DATA/account_parents.csv") -gt 1 ]]; then
  upsert Account   account_parents           Maven_Account_Key__c
fi
upsert Opportunity opportunities             Maven_Opportunity_Id__c
upsert Opportunity opportunities_no_account  Maven_Opportunity_Id__c

echo "== Row counts in the org (compare with the CSV row counts)"
for q in \
  "SELECT COUNT() FROM Product2 WHERE Maven_Product_Key__c != null" \
  "SELECT COUNT() FROM Account WHERE Maven_Account_Key__c != null" \
  "SELECT COUNT() FROM Opportunity WHERE Maven_Opportunity_Id__c != null"; do
  printf '  %-75s ' "$q"
  sf data query --query "$q" --target-org "$ORG" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["totalSize"])'
done
for f in products accounts opportunities opportunities_no_account; do
  printf '  %-30s %s rows\n' "$f.csv" "$(($(wc -l < "$DATA/$f.csv") - 1))"
done

echo "== Storage after load (Setup > Storage Usage shows the per-object breakdown)"
sf org list limits --target-org "$ORG" --json | python3 -c '
import json, sys
for l in json.load(sys.stdin)["result"]:
    if l["name"] == "DataStorageMB":
        print("  DataStorageMB: %s used of %s" % (l["max"] - l["remaining"], l["max"]))'
