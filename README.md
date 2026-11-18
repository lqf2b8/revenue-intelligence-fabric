# Revenue Intelligence on Microsoft Fabric

> **Status: in progress.** Data foundation, synthetic usage, Salesforce loader, KQL KPIs, Spark transform,
> KPI API and question layer run as Docker services, tested in CI against a Kusto emulator.
> The Fabric build (Lakehouse, Eventhouse, semantic model, Power BI) is next.
> Target ship date: December 2, 2026.

This project builds a governed revenue analytics layer for a made-up B2B software company. It joins **CRM
pipeline data from Salesforce** with **product-usage events in a KQL database**. Every KPI is defined
**once**, as a measure in a Fabric semantic model. The Power BI report and a natural-language question
layer both read from those definitions, so they can't disagree.

```
Maven CRM CSVs ──► Salesforce (dev org) ──► Fabric Lakehouse ──┐
                                                               ├─► Spark: account-by-day table ─► Semantic model ─┬─► Power BI report
Usage generator ──► KQL database (Eventhouse) ─────────────────┘                                                  └─► Question layer
```

## What's real and what isn't

| Source | Real? | Details |
|---|---|---|
| CRM pipeline | **Real file, fictional company** | [Maven Analytics CRM Sales Opportunities](https://mavenanalytics.io/data-playground/crm-sales-opportunities): 85 accounts, 7 products, 35 sales agents, 8,800 opportunities (Oct 2016 – Dec 2017). See [`data/raw/README.md`](data/raw/README.md). |
| Product usage | **Synthetic** | Made by [`src/revintel/usage.py`](src/revintel/usage.py) with a fixed seed: ~567k rows of daily events per account, user and feature. |

**The CRM data is reframed as software.** The Maven company sells hardware. Here, each won opportunity
is a 12-month subscription starting on its close date, its close value is that subscription's annual
value, and each product is a plan tier. That's a modelling choice, not real SaaS data.

**The usage data contains a planted effect.** Before each account's expansion months, the generator
ramps usage up by as much as 35% over 28 days. So "rising usage precedes expansion" is true **by
construction**. It's there to test whether the pipeline and KPIs can recover a known relationship. It
is not a finding.

| Expansion-signal accounts at month-end (usage up >10% vs prior 28 days) | Expanded next month | Account-months |
|---|---|---|
| Planted effect on: signal | **67%** | 295 |
| Planted effect on: no signal | 29% | 340 |
| Control, effect off (`--lift 0`): signal | 46% | 260 |
| Control, effect off: no signal | 46% | 381 |

The control shows the signal carries nothing when nothing is planted, so the gap above comes from the
planted effect and not from how the KPI is built.

## KPIs

Each KPI has one definition. The same definition lives in four places, and each is checked against the
Python reference: [`src/revintel/kpis.py`](src/revintel/kpis.py) (reference numbers),
[`kql/02_kpi_functions.kql`](kql/02_kpi_functions.kql) (usage KPIs), the Spark notebooks, and the
semantic model.

| KPI | Definition | Source | Grain |
|---|---|---|---|
| Open pipeline | Sum of expected value of deals open on the date: engaged on or before it and not yet closed. Expected value is the product's list price, because the CRM records no value for open deals. Prospecting deals have no engage date, so they aren't counted. | Salesforce | Week, regional team, product |
| Win rate | Won ÷ (won + lost), deals closed in the quarter | Salesforce | Quarter, agent, product |
| Sales cycle | Median days from engage date to close date, won deals | Salesforce | Quarter, product |
| ARR | Annual value of subscriptions active on the date (start ≤ date < start + 365 days) | Salesforce | Month-end, plan tier |
| Active accounts | Accounts with at least one usage event in the trailing 28 days, including the day itself | KQL usage | Day |
| Consumption | Metered units, and units per account with usage that day | KQL usage (+ plan tier from Salesforce) | Day, plan tier |
| Adoption | Share of accounts active in the week (Monday start) that used **at least N = 4** of the 6 features that week | KQL usage | Week |
| Expansion signal | Account's units in the last 28 days vs the prior 28 days, at each month-end; signal when the rise is **more than X = 10%**. An account is scored only once it has 56 days of usage history. | KQL usage + Salesforce | Month-end, account |
| Expansion month (target) | After the account's first ("land") month, a month whose new ARR exceeds that account's median monthly new ARR | Salesforce | Month, account |

**Why N = 4 and X = 10%.** With N = 3, nearly every mature account counted as adopted (about 93%), so
the measure couldn't separate accounts. At N = 4, adoption builds from about 4% in the first weeks to
about 70% by December. X = 10% is about three times the typical change in the control data, where the
median 28-day change is about +3%. Even so, noise trips the signal on about a third of control
account-months, which is why the control comparison matters. Both values are set once, in `KpiParams`, the KQL function
defaults and the semantic model, and nowhere else.

**Plan tier for usage.** An account can hold several plan tiers at once. For tier breakdowns of usage,
each account-day takes the tier with the most active ARR (`primary_tier` on the account-by-day table).

## Services

Fabric is the system of record. Everything that runs outside it is a small, single-purpose service with its
own Docker image, sharing one library (`src/revintel`) and one KPI catalog
([`src/revintel/catalog.py`](src/revintel/catalog.py)).

| Service | Kind | What it does | Fabric counterpart |
|---|---|---|---|
| [`usage-generator`](services/usage-generator) | job | Synthetic usage + reference KPI tables; sinks: file, Kusto, or an Event Hub / Eventstream replay | Feeds the Eventhouse |
| [`kql-init`](services/kql-init) | job | Creates tables and KPI functions, ingests usage, checks every KQL KPI against the reference | Same scripts run on the Eventhouse |
| [`transform`](services/transform) | job | PySpark account-by-day table and consumption by tier, checked against the reference | Same functions run in a Fabric notebook |
| [`salesforce-loader`](services/salesforce-loader) | job | Deploys fields, upserts the CRM sample into a Salesforce org | Source for Fabric's Salesforce connector |
| [`kpi-api`](services/kpi-api) | API | Serves every catalog KPI. Backends: `reference`, `kusto`, `fabric` (DAX on the semantic model), `hybrid` | Reads the semantic model in Phase 1 |
| [`question-api`](services/question-api) | API | Claude with two tools (`list_kpis`, `get_kpi`) on the KPI API. Answers that contain a number no KPI returned are retried once, then refused. | Question layer |

The question layer never sees a database. It reaches data only through the KPI API, so switching
`KPI_BACKEND` to `fabric` moves every answer onto the semantic model's definitions without changing it.

```
usage-generator ─► shared volume ─► kusto (emulator) ◄─ kql-init: tables, functions, ingest, checks
                              └──► transform (Spark): account-by-day, checked
kpi-api: usage KPIs from kusto, CRM KPIs from reference ◄─ question-api (Claude, grounded answers)
```

```bash
docker compose up --build                       # whole stack, in dependency order
curl "localhost:8000/kpis/active_accounts?start=2017-12-31&end=2017-12-31"
ANTHROPIC_API_KEY=... docker compose up -d question-api
curl -X POST localhost:8001/ask -H 'content-type: application/json' \
     -d '{"question": "How did win rate for GTX Pro change across 2017?"}'
SF_AUTH_URL=... docker compose --profile salesforce run --rm salesforce-loader   # auth URL: see services/salesforce-loader/entrypoint.sh
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the unit tests, Spark included, then builds
every image. It runs the stack against Microsoft's Kusto emulator: every KQL KPI must match the reference,
the Spark output must match the reference, and the APIs are smoke-tested.

## Quickstart (without Docker)

```bash
pip install -e ".[all]"    # Spark tests also need Java 17+
python -m revintel all     # usage events + Salesforce CSVs + reference KPI tables
pytest                     # 58 tests, ~30 s
```

| Command | Output |
|---|---|
| `python -m revintel usage [--lift 0] [--seed N]` | `data/processed/usage_events.csv.gz`, `usage_config.json` |
| `python -m revintel salesforce-prep [--max-opportunities 2000]` | `salesforce/data/*.csv` (Bulk API upsert files) |
| `python -m revintel kpis` | `data/processed/reference_kpis/*.csv` (reference numbers) |
| `python -m revintel check <kpi> <export.csv>` | Pass/fail comparison of a KQL, Spark or Power BI export against the reference |

## Loading Salesforce

Developer Edition orgs have [5 MB of data storage](https://developer.salesforce.com/docs/atlas.en-us.264.0.api_rest.meta/api_rest/quickstart_dev_org.htm),
about 2,500 records at ~2 KB each. The full 8,800 opportunities don't fit. So Salesforce gets a sample
of **whole accounts** (default: about 2,000 opportunities, 21 accounts, about 4 MB), which keeps each
sampled account's history complete. The Lakehouse gets the full set from the CSVs. The report labels
which numbers come from which.

```bash
npm install -g @salesforce/cli
sf org login web --alias revintel
python -m revintel salesforce-prep
salesforce/scripts/load.sh revintel
```

`load.sh` deploys the custom fields and a permission set ([`salesforce/force-app`](salesforce/force-app)).
It then upserts Products, Accounts and Opportunities on external IDs, so it's safe to re-run, and prints
row counts and storage used. Relationships resolve by key (`Account.Maven_Account_Key__c`,
`Plan_Tier__r.Maven_Product_Key__c`), not by Salesforce record Id.

| Maven field | Salesforce |
|---|---|
| account, sector, employees, revenue ($M), office_location | Account: Name, Industry, NumberOfEmployees, AnnualRevenue ($), BillingCountry |
| product, series, sales_price | Product2: Name, Family, List_Price__c |
| deal_stage | StageName: Prospecting → Prospecting, Engaging → Qualification, Won → Closed Won, Lost → Closed Lost |
| close_date | CloseDate. Open deals have none, so they get 2018-03-31 with `Close_Date_Estimated__c = true` |
| close_value / list price | Amount: booked value if closed (0 if lost), list price if open |
| product | `Plan_Tier__c`, a lookup to Product2 |
| engage_date, sales_agent, manager, regional_office | Custom fields. Sales agents aren't Salesforce users, because Developer Edition has two licenses. |

## Loading KQL

1. Run [`kql/01_tables.kql`](kql/01_tables.kql) to create the table and mapping. In Phase 0 that's the free Azure Data Explorer cluster; in Phase 1, the Fabric Eventhouse.
2. Ingest `data/processed/usage_events.csv.gz` with the web UI's Get data > Local file.
3. Run [`kql/02_kpi_functions.kql`](kql/02_kpi_functions.kql), then the queries in [`kql/03_checks.kql`](kql/03_checks.kql). Export each result to CSV and run `python -m revintel check`.

## Repo layout

```
src/revintel/   shared library: maven.py (load + clean)  subscriptions.py (reframing)  usage.py (generator)
                kpis.py (reference KPIs)  catalog.py (KPI catalog)  kusto.py  salesforce_prep.py  cli.py
services/       one folder per service: code + Dockerfile
docker-compose.yml   local stack with the Kusto emulator
tests/          58 pytest checks: planted-effect recovery and a lift = 0 control, Spark vs reference,
                KPI API, grounding and the question agent (scripted model)
kql/            table setup, KPI functions, checks
salesforce/     custom fields, permission set, load script
fabric/         Fabric workspace items (Phase 1)
data/raw/       Maven CSVs (unchanged)    data/processed/  generated outputs
```

## Roadmap

- [x] Phase 0: data cleaning, reframing, usage generator, reference KPIs, Salesforce load, KQL drafts
- [x] Services and Docker stack: KQL checked on the Kusto emulator, Spark transform matched to the reference,
      KPI API, question layer with grounding check, CI
- [ ] Fabric: Lakehouse + Salesforce connector, Eventhouse, Git integration
- [ ] Spark notebook in Fabric calling `transform.jobs`
- [ ] Semantic model: one measure per KPI, matched to the reference; point `kpi-api` at it (`KPI_BACKEND=fabric`)
- [ ] Power BI report
- [ ] Scheduled refresh, screenshots, 5-minute walkthrough video (the Fabric trial ends after 60 days)
