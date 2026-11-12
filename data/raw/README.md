# Raw data: Maven Analytics CRM Sales Opportunities

**Source:** [Maven Analytics Data Playground – CRM Sales Opportunities](https://mavenanalytics.io/data-playground/crm-sales-opportunities).
B2B sales pipeline data for a **fictional** company that sells computer hardware.
Before the repo goes public, check the license on that page and confirm it allows redistribution.

These are the files from the original download zip, unchanged. The rows are identical to the official
download; byte hashes may differ if a file has been re-saved, so the tests check content, not hashes.

| File | Rows |
|---|---|
| accounts.csv | 85 |
| products.csv | 7 |
| sales_teams.csv | 35 |
| sales_pipeline.csv | 8,800 |
| data_dictionary.csv | – |

SHA-256 of the files in this folder:

```
e5242324768a563fc632cddfed49a29acbbf2892b8a3c6453cc9650de9ae0358  accounts.csv
22b34e498d07e3d7f322afdbf81d70a5dc0a389792944e50ca2af86a3597f0af  data_dictionary.csv
7c1c8cbbdb6d4c286902e1985eeb529a36366d6a43f43cd4a93c4b1da2a6eb84  products.csv
825ce8f6c32d4009548b468df3173d55a46fd73f2531f532c5459371dc52adf2  sales_pipeline.csv
aeff1272ebe196f5a27e3fc0578aa27abf48ed9ae461aa344fb95990e5ad8bd1  sales_teams.csv
```

Coverage: deals engaged 2016-10-20 to 2017-12-27, closed 2017-03-01 to 2017-12-31.

Quirks, all handled in `src/revintel/maven.py`:
- `sales_pipeline.product` says `GTXPro`; `products.product` says `GTX Pro`.
- `accounts.sector` contains the typo `technolgy`.
- Open deals (Prospecting, Engaging) have no close date and no close value.
- 1,425 open deals have no account. Every closed deal has one.
- Prospecting deals have no engage date.
