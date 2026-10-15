# db/ (shared: Chris, Dennis, Trevor, Louisa; Esther — Slides (`slides-esther`) reviews)

Change `schema.sql` only by a small PR to `main`, then merge `main` into the feature branches. Everyone reviews. What actually runs on NgaoDB is the ordered list in `deploy/manifest.txt`, applied by GitHub Actions. See [docs/DEPLOY.md](../docs/DEPLOY.md).

- `schema.sql`: DDL draft for the shared tables and core views (matches the ER diagram in docs/ARCHITECTURE.md §7). The deploy workflow leaves this file alone because plain `CREATE TABLE` is not re-runnable.
- `deploy/manifest.txt`: scripts that run on every push to `main`, in order. Seeded with `deploy/000_live_objects.sql` and `deploy/010_climate_seasonal.sql`.
- `deploy/000_live_objects.sql`: guarded `COOPERATIVES` table and `CREATE OR REPLACE` dashboard views from the live database.
- `deploy/010_climate_seasonal.sql`: re-runnable climate and seasonal tables and views.
- Later: `views/*.sql`, `packages/pkg_alert_engine.sql`, `packages/pkg_demo.sql`, `seed/alert_types.sql`. Promote a change into `deploy/` only when it is safe to run again, and append it to the manifest.
- `migrations/001_backend_alignment.sql` is excluded from the manifest. The guard rejects that file name.
