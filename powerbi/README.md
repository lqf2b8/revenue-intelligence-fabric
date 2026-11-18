# Power BI wireframe

**Live report:** [Revenue Intelligence (Power BI, publish to web)](https://app.powerbi.com/view?r=eyJrIjoiZjUwYjkwMDctMTA3MS00YmZmLTkzNWItNDE0MDEzYmE4ZDJmIiwidCI6IjI2MWI2NDMxLTk1OTgtNDQzMi1hYmNiLTNhZGIwMThhNjdkZSJ9)

A first version of the Power BI report, published from a Fabric trial tenant. It reads the
reference KPI tables in [`data/processed/reference_kpis`](../data/processed/reference_kpis)
straight from this repo by URL, so it shows the same numbers the Python reference, the KQL
functions and the Spark transform are checked against.

| Page | Visuals |
|---|---|
| Revenue | ARR, open pipeline and win rate cards · ARR by plan tier · open pipeline by regional office · win rate by product, with a quarter slicer |
| Usage & adoption | Active accounts and adoption cards · active accounts · weekly adoption rate · consumption by plan tier |
| Expansion signal | Next-month expansion rate by signal · a note on the planted effect and its control · flagged accounts |

It is saved as a Power BI project (PBIR report + TMDL semantic model). To open it, install
Power BI Desktop and open `Revenue Intelligence.pbip`. The measures live in
`Revenue Intelligence.SemanticModel/definition/tables/*.tmdl`.

**Next:** the Phase 1 build replaces these URL-loaded tables with the Fabric semantic model
(Lakehouse + Eventhouse), and the report is republished over this one. The public link only
lasts as long as the Fabric trial (about mid-January 2027).
