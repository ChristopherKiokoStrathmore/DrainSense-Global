# Deploying NgaoDB from GitHub

Pushes to `main` update the live Autonomous Database **NgaoDB** (region `eu-frankfurt-1`, schema `WKSP_NGAO`) from this repository. The workflow is `.github/workflows/deploy-ngaodb.yml`.

On a push to `main` it:

1. Lints `db/deploy/manifest.txt` and refuses destructive SQL.
2. Stops immediately when the required secrets are missing. It does not decode a wallet and it does not open a connection.
3. Installs Temurin JDK 21 and Oracle SQLcl (the current `sqlcl-latest.zip`).
4. Connects with the Autonomous Database wallet.
5. Runs the scripts listed in the manifest, in order, as schema `WKSP_NGAO`.
6. Imports `frontend/apex/f100.sql` so APEX application **100** (`NGAO-MVUASMART`) is replaced in workspace **NGAO**.
7. Checks that app 100 is present, runs a count query against the dashboard views and climate tables, then requests the public home page and expects HTTP 200. The request keeps the `ORA_WWV_APP_100` cookie, because the friendly URL answers with one 302 that becomes 200 only when that cookie is sent back.

Pull requests run only step 1. They never receive the database secrets and they never connect.

A manual run can be a dry run: it connects, prints the script paths it would use, and stops before executing them or importing the app.

Deploys share the concurrency group `ngao-adb-deploy`. A second deploy waits until the one already running has finished.

## Secrets

Add repository secrets under **Settings → Secrets and variables → Actions → Repository secrets**. Do not commit the wallet, the base64 string, or the password. `.gitignore` already ignores `wallet*/` and `*.zip`.

### Required

| Secret | Value |
|---|---|
| `NGAO_DB_WALLET_B64` | Standard base64 of the instance wallet zip. See below. |
| `NGAO_DB_USER` | `WKSP_NGAO`, or `ADMIN` if that user can create objects in `WKSP_NGAO` and install APEX applications. |
| `NGAO_DB_PASSWORD` | Password for `NGAO_DB_USER`. One line. This is the database user password. |
| `NGAO_DB_SERVICE` | TNS alias from the wallet `tnsnames.ora`, for example `ngaodb_low` or `ngaodb_high`. Not a JDBC URL. |

If any required secret is missing or blank, the deploy job fails with the secret names and this document path. The pull-request job still passes, so this workflow can be merged before the secrets exist. After you add them, re-run the failed **Deploy NgaoDB** workflow from the Actions tab.

### Optional overrides

Leave these unset unless the live workspace differs from the export in `frontend/apex/f100.sql`.

| Secret | Default |
|---|---|
| `NGAO_APEX_WORKSPACE` | `NGAO` |
| `NGAO_APEX_SCHEMA` | `WKSP_NGAO` |
| `NGAO_APEX_APP_ID` | `100` |
| `NGAO_APEX_ALIAS` | `NGAO-MVUASMART` |
| `NGAO_APEX_WORKSPACE_ID` | unset. When set, the install also calls `apex_application_install.set_workspace_id`. The export was taken from workspace id `9382588374983398`. |
| `NGAO_SMOKE_URL` | `https://g93fcef1cec31ea-ngaodb.adb.eu-frankfurt-1.oraclecloudapps.com/ords/r/ngao/ngao-mvuasmart/home` |

Changing the alias changes the friendly URL. Update `NGAO_SMOKE_URL` in the same change.

### Build `NGAO_DB_WALLET_B64`

In the OCI console open **Oracle Database → Autonomous Database → NgaoDB → Database connection** and download the **instance wallet**. The download is a zip (often named `Wallet_ngaodb.zip`). Do not unzip it and do not commit it.

Linux:

```bash
base64 -w 0 Wallet_ngaodb.zip
```

macOS:

```bash
base64 -i Wallet_ngaodb.zip | tr -d '\n'
```

Paste the single line into the `NGAO_DB_WALLET_B64` secret. Open `tnsnames.ora` inside the zip only long enough to copy a net service name into `NGAO_DB_SERVICE`, then delete the zip from the laptop.

SQLcl connects with `set cloudconfig` pointed at that zip, then `connect USER/"password"@SERVICE`. The password is written into a mode `0600` file under the runner temp directory and the file is deleted when the step exits. It is not printed by these scripts. GitHub still masks the secret value if a failed `connect` echoes the line.

## What gets applied

`db/deploy/manifest.txt` is the list. Paths are repo-relative, one per line, and they run top to bottom. The committed list is:

1. `db/deploy/000_live_objects.sql` — table `COOPERATIVES` (created only when it is missing) and `CREATE OR REPLACE VIEW` for `V_WARD_RISK_LATEST`, `V_ALERT_QUEUE`, `V_GARISSA_REPLAY`, and `V_RAIN_SERIES`. These views match the live dashboard. They assume the columns already present on NgaoDB (`risk_level`, alert body columns, `tana_discharge`, `rvf_timeline`, and the extra forecast and feature columns). A brand-new empty schema that has only `db/schema.sql` will fail here.
2. `db/deploy/010_climate_seasonal.sql` — `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `CREATE OR REPLACE VIEW` for the climate and seasonal objects. Re-running it leaves existing rows in place. It does not load data. Needs Oracle 23ai or newer for `IF NOT EXISTS`.

Then SQLcl runs `frontend/apex/f100.sql` (APEX 26.1 export of app 100).

### How the APEX import replaces app 100

`frontend/apex/f100.sql` is a SQL application export (`wwv_flow_imp.import_begin`), not an APEXlang project. SQLcl 26.3's `apex import` command loads APEXlang (a directory, a zip, or an `.apx` file) and does not install this file. The workflow runs the SQL script, which is the install path in the APEX 26.1 guide for a SQL export.



Oracle's install guide for APEX 26.1 says that when the target workspace, schema, application id, and alias match the export, running the export script overwrites that application id. The export calls `wwv_flow_imp.remove_flow` for the current application id and then creates it again. That removes APEX metadata for app 100. It does not drop schema tables. Supporting objects in this export are excluded, and the workflow does not call `set_auto_install_sup_obj`.

The runner still sets the install context explicitly, so a session connected as `ADMIN` cannot land in a different workspace or allocate a new application id:

```sql
begin
  apex_application_install.clear_all;
  apex_application_install.set_workspace('NGAO');
  apex_application_install.set_application_id(100);
  apex_application_install.set_schema('WKSP_NGAO');
  apex_application_install.set_application_alias('NGAO-MVUASMART');
end;
/
@frontend/apex/f100.sql
```

`generate_application_id` is not called. That would keep the existing app 100 and install a second copy. `generate_offset` is not called. The export's offset is 0, which keeps component ids stable while `remove_flow` replaces app 100. `set_offset` is not called either: Oracle requires that offset to be a positive integer, and the export default of 0 is the one we want.

Before the scripts, the session runs `ALTER SESSION SET CURRENT_SCHEMA = WKSP_NGAO` and checks that the schema actually changed.

## Add a script

1. Add a new file under `db/deploy/` whose name sorts after the scripts it depends on, for example `db/deploy/020_something.sql`.
2. Make it re-runnable: `CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE VIEW`, or a PL/SQL block that checks `user_tables` / `user_tab_columns` before altering. Do not assume the file runs only once.
3. Append the repo-relative path to `db/deploy/manifest.txt`.
4. Open a pull request. The validate job runs `db/deploy/self-test.sh`, which lints the manifest.

Every `db/deploy/*.sql` file must be listed. A file that is not listed fails the check, so a script cannot sit in the folder and look like it deploys when it does not.

The guard scans each listed file after stripping `--` and `/* */` comments. It fails the job when it finds `DROP TABLE`, `TRUNCATE`, or `DELETE FROM`, including inside dynamic SQL. It also fails if any listed path is named `001_backend_alignment.sql`.

## Dry run

From the Actions tab, choose **Deploy NgaoDB → Run workflow**, leave the branch on `main`, and set **dry run** on. The four required secrets must already exist, because a dry run connects. It prints the absolute paths it would run, selects the current user and schema, counts rows in `apex_applications` for application 100, and exits. It does not execute the manifest and it does not import `f100.sql`.

To lint without connecting, run this locally or open a pull request:

```bash
bash db/deploy/self-test.sh
```

## Roll back

Oracle commits DDL as it runs, and the APEX export ends with `COMMIT`. There is no transaction to roll back. A failed run may have applied the scripts that appear above the error in the log. Those scripts are safe to run again.

To put the dashboard back to a previous Git revision:

1. `git revert` the commit on `main` (open a pull request, merge it).
2. The following push imports the previous `frontend/apex/f100.sql`, which replaces app 100 with that export.
3. Objects created by an additive script stay in the database. This workflow will not drop them. Dropping a table is a manual step in SQL Workshop after you have decided the data can go.

Re-run a failed job from the Actions tab once the failure is fixed. Re-running applies the same commit again.

If NgaoDB is stopped (Always Free stops an idle instance), the SQLcl step fails on connect and nothing is imported. Start the database from the OCI console and re-run the workflow.

## Deliberately not automated

- `db/schema.sql`. It is the shared draft and uses plain `CREATE TABLE`, so a second run is not safe. Apply changes from that draft by writing a new idempotent script in `db/deploy/` and listing it.
- `db/migrations/001_backend_alignment.sql`. It is excluded by name. Do not add it to the manifest and do not copy it into `db/deploy/`.
- Any statement that drops a table, truncates, or deletes rows. The guard fails the job, and the scripts are not sent to SQLcl.
- Loading climate and seasonal rows. `010_climate_seasonal.sql` only creates empty tables and views. The Python loader stays outside this workflow.
- Creating the database user, the APEX workspace, ORDS modules, Select AI profiles, or grants that are not in the manifest scripts.
- Editing `frontend/apex/f100.sql` by hand. Export a fresh file from APEX and commit that.
- Waking a stopped Always Free database, rotating the wallet, or approving the deploy. Secrets live on the repository. A GitHub Environment approval gate is not configured.
- The APEX export's own page processes. The guard does not scan `f100.sql`. Importing it replaces application 100; it is the supported install path for that file.

## Local checks

`bash db/deploy/self-test.sh` runs the manifest self-test, wallet-decode self-test, SQL-render self-test, secret-missing check, bad-wallet check, and a local HTTP smoke check. It does not contact NgaoDB.

`db/deploy/install-sqlcl.sh` downloads SQLcl. Run it only after a JDK 17 or 21 is on `PATH`. The GitHub Actions job uses `actions/setup-java` with Temurin 21, then that script.
