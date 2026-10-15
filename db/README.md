# db/ (shared: Chris, Dennis, Trevor, Louisa; Esther — Slides (`slides-esther`) reviews)

Change `schema.sql` only by a small PR to `main`, then merge `main` into the feature branches. Chris applies it in Autonomous DB. Everyone reviews.
- `schema.sql`: DDL draft for schema `NGAO`: tables + core views (matches the ER diagram in docs/ARCHITECTURE.md §7).
- `seed/cooperatives.csv` → `seed/cooperatives.sql`: farmer cooperatives for USSD registration (run after `WARDS` is loaded). Edit the CSV, then `python3 db/seed/build_cooperatives_sql.py`. Every row has a source URL and a `location_basis` (STATED / INFERRED / CONFIRMED); set ward and `CONFIRMED` once the county cooperatives office confirms it.
- Later: `views/*.sql`, `packages/pkg_alert_engine.sql`, `packages/pkg_demo.sql`, `seed/alert_types.sql`.
