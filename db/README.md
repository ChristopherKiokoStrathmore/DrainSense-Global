# db/ (shared: Chris, Dennis, Trevor, Louisa; Esther — Slides (`slides-esther`) reviews)

Change `schema.sql` only by a small PR to `main`, then merge `main` into the feature branches. Chris applies it in Autonomous DB. Everyone reviews.
- `schema.sql`: DDL draft for schema `NGAO`: tables + core views (matches the ER diagram in docs/ARCHITECTURE.md §7).
- `migrations/`: re-runnable ALTER scripts for a database created from an earlier `schema.sql` (`001_backend_alignment.sql`: alert approval columns, `MESSAGES.alert_id`, `JOB_RUNS`). Check with `python manage.py check_schema` from `backend/`.
- Later: `views/*.sql`, `packages/pkg_alert_engine.sql`, `packages/pkg_demo.sql`, `seed/alert_types.sql`.
