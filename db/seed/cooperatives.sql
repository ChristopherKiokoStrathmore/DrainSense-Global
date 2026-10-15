-- Seed for COOPERATIVES. Generated from db/seed/cooperatives.csv; edit the CSV, not this file.
-- ward_id is matched by county + ward name against WARDS; unmatched or blank wards stay NULL.
-- Run after WARDS is loaded.

INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (1, 'Ainabkoi Farmers Cooperative Society', 'Ainabkoi FCS', 'Uasin Gishu', 'Ainabkoi',
          NULL,
          'STATED', 'dairy', 'https://uasingishu.go.ke/index.php/department/ainabkoi-subcounty');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (2, 'Kuona Mbele Cooperative Society', 'Kuona Mbele', 'Uasin Gishu', 'Ainabkoi',
          NULL,
          'STATED', 'grain', 'https://uasingishu.go.ke/index.php/county-to-boost-cooperative-societies-through-strategic-partnerships');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (3, 'Moiben Farmers Cooperative Society', 'Moiben FCS', 'Uasin Gishu', 'Moiben',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Uasin Gishu') AND UPPER(ward_name) = UPPER('Moiben') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'dairy', 'https://uasingishu.go.ke/?p=4229');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (4, 'Tuiyo Farmers Cooperative Society', 'Tuiyo FCS', 'Uasin Gishu', 'Kapseret',
          NULL,
          'STATED', 'dairy', 'https://uasingishu.go.ke/index.php/cooperative-societies-a-pillar-of-economic-stability-cecm-cheruto');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (5, 'Kesentany Marketing Cooperative Society', 'Kesentany Marketing', 'Uasin Gishu', 'Soy',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Uasin Gishu') AND UPPER(ward_name) = UPPER('Soy') FETCH FIRST 1 ROWS ONLY),
          'STATED', 'dairy', 'https://uasingishu.go.ke/?p=4893');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (6, 'Kapsaos Farmers'' Cooperative Society', 'Kapsaos FCS', 'Uasin Gishu', 'Turbo',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Uasin Gishu') AND UPPER(ward_name) = UPPER('Kapsaos') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'dairy', 'https://uasingishu.go.ke/index.php/category/kapsaos-farmers-cooperative-society');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (7, 'Simat Farmers Cooperative Society', 'Simat FCS', 'Uasin Gishu', 'Kapseret',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Uasin Gishu') AND UPPER(ward_name) = UPPER('Simat/Kapseret') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'dairy', 'https://uasingishu.go.ke/index.php/county-officials-meet-simat-fcs-leaders-over-revival-plan');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (8, 'Gumarey Honey Co-operative Society', 'Gumarey Honey', 'Garissa', 'Ijara',
          NULL,
          'STATED', 'honey', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (9, 'Safi Women Milk Co-operative Society', 'Safi Women Milk', 'Garissa', 'Dadaab',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Garissa') AND UPPER(ward_name) = UPPER('Dadaab') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'milk', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (10, 'Bura East Fodder Co-operative Society', 'Bura East Fodder', 'Garissa', 'Fafi',
          NULL,
          'INFERRED', 'fodder', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (11, 'Fafi Farmers Marketing Co-operative Society', 'Fafi Farmers Mktg', 'Garissa', 'Fafi',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Garissa') AND UPPER(ward_name) = UPPER('Fafi') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'marketing', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (12, 'Modogashe Women Milk Co-operative Society', 'Modogashe Women Milk', 'Garissa', 'Lagdera',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Garissa') AND UPPER(ward_name) = UPPER('Modogashe') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'milk', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (13, 'Balambala Livestock Traders Co-operative Society', 'Balambala Livestock', 'Garissa', 'Balambala',
          (SELECT ward_id FROM wards WHERE UPPER(county) = UPPER('Garissa') AND UPPER(ward_name) = UPPER('Balambala') FETCH FIRST 1 ROWS ONLY),
          'INFERRED', 'livestock', 'https://www.kenyanews.go.ke/?p=66528');
INSERT INTO cooperatives (coop_id, coop_name, short_name, county, sub_county, ward_id, location_basis, coop_type, source_url)
  VALUES (14, 'Garissa Camel Milk Co-operative Society', 'Garissa Camel Milk', 'Garissa', 'Garissa Township',
          NULL,
          'STATED', 'milk', 'https://www.kenyanews.go.ke/?p=66528');
COMMIT;
