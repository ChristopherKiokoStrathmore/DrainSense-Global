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

MERGE INTO cooperatives c
USING (
  SELECT s.*,
         (SELECT MIN(w.ward_id) FROM wards w
           WHERE UPPER(w.county) = UPPER(s.county) AND UPPER(w.ward_name) = UPPER(s.ward_name)
             AND NVL(w.is_replay, 'N') = 'N') AS ward_id
    FROM (
      SELECT 'Ainabkoi Farmers Cooperative Society' AS coop_name, 'Ainabkoi FCS' AS short_name, 'Uasin Gishu' AS county, 'Ainabkoi' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'STATED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/index.php/department/ainabkoi-subcounty' AS source_url FROM dual
      UNION ALL
      SELECT 'Kuona Mbele Cooperative Society' AS coop_name, 'Kuona Mbele' AS short_name, 'Uasin Gishu' AS county, 'Ainabkoi' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'STATED' AS location_basis, 'grain' AS coop_type, 'https://uasingishu.go.ke/index.php/county-to-boost-cooperative-societies-through-strategic-partnerships' AS source_url FROM dual
      UNION ALL
      SELECT 'Moiben Farmers Cooperative Society' AS coop_name, 'Moiben FCS' AS short_name, 'Uasin Gishu' AS county, 'Moiben' AS sub_county, 'Moiben' AS ward_name, 'INFERRED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/?p=4229' AS source_url FROM dual
      UNION ALL
      SELECT 'Tuiyo Farmers Cooperative Society' AS coop_name, 'Tuiyo FCS' AS short_name, 'Uasin Gishu' AS county, 'Kapseret' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'STATED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/index.php/cooperative-societies-a-pillar-of-economic-stability-cecm-cheruto' AS source_url FROM dual
      UNION ALL
      SELECT 'Kesentany Marketing Cooperative Society' AS coop_name, 'Kesentany Marketing' AS short_name, 'Uasin Gishu' AS county, 'Soy' AS sub_county, 'Soy' AS ward_name, 'STATED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/?p=4893' AS source_url FROM dual
      UNION ALL
      SELECT 'Kapsaos Farmers'' Cooperative Society' AS coop_name, 'Kapsaos FCS' AS short_name, 'Uasin Gishu' AS county, 'Turbo' AS sub_county, 'Kapsaos' AS ward_name, 'INFERRED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/index.php/category/kapsaos-farmers-cooperative-society' AS source_url FROM dual
      UNION ALL
      SELECT 'Simat Farmers Cooperative Society' AS coop_name, 'Simat FCS' AS short_name, 'Uasin Gishu' AS county, 'Kapseret' AS sub_county, 'Simat/Kapseret' AS ward_name, 'INFERRED' AS location_basis, 'dairy' AS coop_type, 'https://uasingishu.go.ke/index.php/county-officials-meet-simat-fcs-leaders-over-revival-plan' AS source_url FROM dual
      UNION ALL
      SELECT 'Gumarey Honey Co-operative Society' AS coop_name, 'Gumarey Honey' AS short_name, 'Garissa' AS county, 'Ijara' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'STATED' AS location_basis, 'honey' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Safi Women Milk Co-operative Society' AS coop_name, 'Safi Women Milk' AS short_name, 'Garissa' AS county, 'Dadaab' AS sub_county, 'Dadaab' AS ward_name, 'INFERRED' AS location_basis, 'milk' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Bura East Fodder Co-operative Society' AS coop_name, 'Bura East Fodder' AS short_name, 'Garissa' AS county, 'Fafi' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'INFERRED' AS location_basis, 'fodder' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Fafi Farmers Marketing Co-operative Society' AS coop_name, 'Fafi Farmers Mktg' AS short_name, 'Garissa' AS county, 'Fafi' AS sub_county, 'Fafi' AS ward_name, 'INFERRED' AS location_basis, 'marketing' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Modogashe Women Milk Co-operative Society' AS coop_name, 'Modogashe Women Milk' AS short_name, 'Garissa' AS county, 'Lagdera' AS sub_county, 'Modogashe' AS ward_name, 'INFERRED' AS location_basis, 'milk' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Balambala Livestock Traders Co-operative Society' AS coop_name, 'Balambala Livestock' AS short_name, 'Garissa' AS county, 'Balambala' AS sub_county, 'Balambala' AS ward_name, 'INFERRED' AS location_basis, 'livestock' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
      UNION ALL
      SELECT 'Garissa Camel Milk Co-operative Society' AS coop_name, 'Garissa Camel Milk' AS short_name, 'Garissa' AS county, 'Garissa Township' AS sub_county, CAST(NULL AS VARCHAR2(100)) AS ward_name, 'STATED' AS location_basis, 'milk' AS coop_type, 'https://www.kenyanews.go.ke/?p=66528' AS source_url FROM dual
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
