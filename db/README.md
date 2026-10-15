# db/ (shared: Chris, Dennis, Trevor, Louisa; Esther — Slides (`slides-esther`) reviews)

Change `schema.sql` only by a small PR to `main`, then merge `main` into the feature branches. Chris applies it in Autonomous DB. Everyone reviews.
- `schema.sql`: DDL draft for schema `NGAO`: tables + core views (matches the ER diagram in docs/ARCHITECTURE.md §7).
- `migrations/`: `001_backend_alignment.sql` is historical and **must not be run** against the live NgaoDB (schema `WKSP_NGAO`). It would null out `ALERTS.SUBSCRIBER_ID` / `SCORE_ID`, widen `BODY`, add `MESSAGES.ALERT_ID` and create `JOB_RUNS`. The backend now writes the live columns. Check with `python manage.py check_schema` from `backend/`.
- Later: `views/*.sql`, `packages/pkg_alert_engine.sql`, `packages/pkg_demo.sql`, `seed/alert_types.sql`.
