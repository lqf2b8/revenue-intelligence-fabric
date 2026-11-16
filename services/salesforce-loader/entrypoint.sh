#!/bin/sh
# Log in to the org from an SFDX auth URL, build the upsert CSVs, run the load.
# Get the URL once on your machine after `sf org login web --alias revintel`:
#   sf org display --target-org revintel --verbose --json | jq -r .result.sfdxAuthUrl
# Treat it like a password: it grants API access to the org.
set -eu
: "${SF_AUTH_URL:?Set SF_AUTH_URL to the org's SFDX auth URL (see the comment in entrypoint.sh)}"
umask 077
printf '%s' "$SF_AUTH_URL" > /tmp/sf-auth-url
sf org login sfdx-url --sfdx-url-file /tmp/sf-auth-url --alias revintel --set-default >/dev/null
rm -f /tmp/sf-auth-url
python -m revintel salesforce-prep --max-opportunities "${SF_MAX_OPPORTUNITIES:-2000}" --out /app/salesforce/data
exec /app/salesforce/scripts/load.sh revintel
