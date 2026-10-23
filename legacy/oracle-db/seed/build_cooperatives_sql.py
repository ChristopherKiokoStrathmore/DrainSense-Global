"""Regenerate db/deploy/040_cooperatives_registry.sql from db/seed/cooperatives.csv.

    python3 db/seed/build_cooperatives_sql.py

The output is additive and re-runnable (it is listed in db/deploy/manifest.txt):
it adds the registry columns to the live COOPERATIVES table and SUBSCRIBERS,
then merges the CSV rows by cooperative name with is_demo = 'N'. Database ids
are assigned by the table's identity; seed_id in the CSV is only for local runs.
Rows an officer has marked location_basis = 'CONFIRMED' are never overwritten.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "db" / "seed" / "cooperatives.csv"
OUT = ROOT / "db" / "deploy" / "040_cooperatives_registry.sql"

HEADER = """\
-- 040_cooperatives_registry.sql  (Ngao)  -- additive, re-runnable
-- GENERATED from db/seed/cooperatives.csv by db/seed/build_cooperatives_sql.py. Edit the CSV, not this file.
-- Real farmer cooperatives for USSD registration (menu 1 lists rows with is_demo = 'N').
-- Adds registry columns to the live COOPERATIVES table and SUBSCRIBERS.membership_no, then merges
-- rows by name. ward_id is matched by county + ward name on non-replay WARDS rows; blank or
-- unmatched wards stay NULL. Rows with location_basis = 'CONFIRMED' are left untouched.
SET DEFINE OFF

DECLARE
  PROCEDURE add_col(p_table VARCHAR2, p_col VARCHAR2, p_ddl VARCHAR2) IS
    n NUMBER;
  BEGIN
    -- all_tab_columns + CURRENT_SCHEMA: the deploy may log in as ADMIN and work in WKSP_NGAO.
    SELECT COUNT(*) INTO n FROM all_tab_columns
     WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND table_name = p_table AND column_name = p_col;
    IF n = 0 THEN
      EXECUTE IMMEDIATE 'ALTER TABLE ' || p_table || ' ADD (' || p_ddl || ')';
    END IF;
  END;
BEGIN
  add_col('COOPERATIVES', 'SHORT_NAME', 'short_name VARCHAR2(24)');
  add_col('COOPERATIVES', 'SUB_COUNTY', 'sub_county VARCHAR2(60)');
  add_col('COOPERATIVES', 'LOCATION_BASIS',
          'location_basis VARCHAR2(10) CHECK (location_basis IN (''STATED'',''INFERRED'',''CONFIRMED''))');
  add_col('COOPERATIVES', 'COOP_TYPE', 'coop_type VARCHAR2(20)');
  add_col('COOPERATIVES', 'SOURCE_URL', 'source_url VARCHAR2(400)');
  add_col('SUBSCRIBERS', 'COOP_ID', 'coop_id NUMBER REFERENCES cooperatives');
  add_col('SUBSCRIBERS', 'MEMBERSHIP_NO', 'membership_no VARCHAR2(20)');
END;
/
"""

MERGE = """
MERGE INTO cooperatives c
USING (
  SELECT s.*,
         (SELECT MIN(w.ward_id) FROM wards w
           WHERE UPPER(w.county) = UPPER(s.county) AND UPPER(w.ward_name) = UPPER(s.ward_name)
             AND NVL(w.is_replay, 'N') = 'N') AS ward_id
    FROM (
{rows}
    ) s
) s
ON (UPPER(c.coop_name) = UPPER(s.coop_name))
WHEN MATCHED THEN UPDATE SET
  c.short_name = s.short_name, c.county = s.county, c.sub_county = s.sub_county,
  c.ward_id = s.ward_id, c.location_basis = s.location_basis, c.coop_type = s.coop_type,
  c.source_url = s.source_url, c.is_demo = 'N'
  WHERE NVL(c.location_basis, 'X') <> 'CONFIRMED'
WHEN NOT MATCHED THEN INSERT
  (coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url, is_demo)
  VALUES (s.coop_name, s.short_name, s.county, s.sub_county, s.ward_id, s.location_basis, s.coop_type,
          s.source_url, 'N');

COMMIT;
"""

COLS = ["coop_name", "short_name", "county", "sub_county", "ward_name", "location_basis", "coop_type", "source_url"]


def q(v: str) -> str:
    return "CAST(NULL AS VARCHAR2(100))" if v == "" else "'" + v.replace("'", "''") + "'"


def main():
    with open(CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    selects = []
    for r in rows:
        cols = ", ".join(f"{q(r[c])} AS {c}" for c in COLS)
        selects.append(f"      SELECT {cols} FROM dual")
    OUT.write_text(HEADER + MERGE.format(rows="\n      UNION ALL\n".join(selects)), encoding="utf-8")


if __name__ == "__main__":
    main()
