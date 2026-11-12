# Fabric workspace (Phase 1, from Nov 18)

This folder will hold the items Fabric's Git integration commits when the workspace
is connected to this repo:

- **Lakehouse**: Salesforce objects pulled through Fabric's Salesforce connector, plus
  the full Maven CSVs (Salesforce holds only a sample; see the root README).
- **Eventhouse / KQL database**: `UsageEvents`, set up with `kql/01_tables.kql` and
  `kql/02_kpi_functions.kql`.
- **Spark notebooks**: clean the CRM and usage tables, then build the account-by-day
  table. Target output: `data/processed/reference_kpis/account_day.csv.gz`.
- **Semantic model**: one measure per KPI in the README table.
- **Power BI report**: pipeline and revenue, usage and adoption, expansion signals.

Every layer is checked against the reference tables with `python -m revintel check`.
