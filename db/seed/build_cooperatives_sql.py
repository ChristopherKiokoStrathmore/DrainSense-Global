"""Regenerate cooperatives.sql from cooperatives.csv:  python3 db/seed/build_cooperatives_sql.py"""

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def q(v: str) -> str:
    return "NULL" if v == "" else "'" + v.replace("'", "''") + "'"


def main():
    with open(os.path.join(HERE, "cooperatives.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    out = ["-- Seed for COOPERATIVES. Generated from db/seed/cooperatives.csv; edit the CSV, not this file.",
           "-- ward_id is matched by county + ward name against WARDS; unmatched or blank wards stay NULL.",
           "-- Run after WARDS is loaded.", ""]
    for r in rows:
        ward = "NULL" if not r["ward_name"] else (
            f"(SELECT ward_id FROM wards WHERE UPPER(county) = UPPER({q(r['county'])}) "
            f"AND UPPER(ward_name) = UPPER({q(r['ward_name'])}) FETCH FIRST 1 ROWS ONLY)")
        out.append(
            "INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)\n"
            f"  VALUES ({r['coop_id']}, {q(r['coop_name'])}, {q(r['short_name'])}, {q(r['county'])}, {q(r['sub_county'])},\n"
            f"          {ward},\n          {q(r['location_basis'])}, {q(r['coop_type'])}, {q(r['source_url'])});")
    out.append("COMMIT;")
    with open(os.path.join(HERE, "cooperatives.sql"), "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
