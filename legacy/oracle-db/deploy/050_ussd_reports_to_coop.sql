-- 050_ussd_reports_to_coop.sql  (Ngao)  -- additive, re-runnable
-- USSD menu-2 reports land in REPORTS (backend). The co-op pages (Live Disease Alerts, Vet Verification,
-- Command Centre KPIs, Outbreak Map) read COOP_DISEASE_REPORTS. This trigger copies each USSD report
-- into COOP_DISEASE_REPORTS as PENDING (vet check), so it flows through the existing co-op logic.
--   ward:  the reporter's registered ward (SUBSCRIBERS.ward_id), else REPORTS.ward_id; no ward -> skipped
--   coop:  the reporter's SUBSCRIBERS.coop_id, else the co-op in that ward (lowest id); none -> skipped
--   demo:  IS_DEMO = 'Y' for simulator numbers (+2547000...) or demo subscribers
--   types: harvested / bags_dried / rain are not alerts -> skipped
-- A failure here never blocks the farmer's report (logged to nothing, swallowed).
SET DEFINE OFF

DECLARE
  n NUMBER;
BEGIN
  SELECT COUNT(*) INTO n FROM all_tab_columns
   WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND table_name = 'COOP_DISEASE_REPORTS'
     AND column_name = 'SOURCE_REPORT_ID';
  IF n = 0 THEN
    EXECUTE IMMEDIATE 'ALTER TABLE coop_disease_reports ADD (source_report_id NUMBER)';
    EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX ux_cdr_source_report ON coop_disease_reports(source_report_id)';
  END IF;
END;
/

CREATE OR REPLACE PROCEDURE ngao_ussd_report_to_coop(
  p_report_id NUMBER, p_message_id NUMBER, p_ward_id NUMBER, p_type VARCHAR2,
  p_count NUMBER, p_payload VARCHAR2, p_created TIMESTAMP WITH TIME ZONE
) IS
  l_phone   VARCHAR2(30);
  l_sub     NUMBER;
  l_ward    NUMBER;
  l_coop    NUMBER;
  l_demo    CHAR(1) := 'N';
  l_cat     VARCHAR2(10);
  l_disease VARCHAR2(60);
  l_species VARCHAR2(30);
BEGIN
  CASE p_type
    WHEN 'wet_bags'        THEN l_cat := 'CROP';      l_disease := 'Wet grain (aflatoxin risk)';
    WHEN 'flooding'        THEN l_cat := 'CROP';      l_disease := 'Flooding (crop and field damage)';
    WHEN 'sick_animals'    THEN l_cat := 'LIVESTOCK'; l_disease := 'Sick animals';
    WHEN 'dead_animals'    THEN l_cat := 'LIVESTOCK'; l_disease := 'Dead animals';
    WHEN 'rvf_signs'       THEN l_cat := 'LIVESTOCK'; l_disease := 'Suspected Rift Valley fever';
    WHEN 'water_point_dry' THEN l_cat := 'LIVESTOCK'; l_disease := 'Water point dry';
    ELSE RETURN;
  END CASE;
  l_species := JSON_VALUE(p_payload, '$.species');
  IF l_species IS NOT NULL THEN l_disease := SUBSTR(l_disease || ' (' || l_species || ')', 1, 60); END IF;

  BEGIN
    SELECT m.phone, s.subscriber_id, s.ward_id, s.coop_id, NVL(s.is_demo, 'N')
      INTO l_phone, l_sub, l_ward, l_coop, l_demo
      FROM messages m LEFT JOIN subscribers s ON s.phone = m.phone
     WHERE m.message_id = p_message_id;
  EXCEPTION WHEN NO_DATA_FOUND THEN NULL;
  END;
  l_ward := NVL(l_ward, p_ward_id);
  IF l_ward IS NULL THEN RETURN; END IF;
  IF l_coop IS NULL THEN
    SELECT MIN(coop_id) INTO l_coop FROM cooperatives WHERE ward_id = l_ward;
  END IF;
  IF l_coop IS NULL THEN RETURN; END IF;
  IF l_phone LIKE '+2547000%' THEN l_demo := 'Y'; END IF;

  MERGE INTO coop_disease_reports t
  USING (SELECT p_report_id rid FROM dual) s ON (t.source_report_id = s.rid)
  WHEN NOT MATCHED THEN INSERT
    (coop_id, ward_id, subscriber_id, category, disease, animals_or_acres, symptoms, otp_verified,
     channel, reported_at, vet_status, is_demo, source_report_id)
  VALUES
    (l_coop, l_ward, l_sub, l_cat, l_disease, p_count,
     SUBSTR('USSD report ' || p_type || CASE WHEN p_count IS NOT NULL THEN ' x' || p_count END
            || CASE WHEN l_demo = 'Y' THEN ' (simulator/demo)' END, 1, 400),
     'N', 'USSD', NVL(p_created, SYSTIMESTAMP), 'PENDING', l_demo, p_report_id);
END;
/

CREATE OR REPLACE TRIGGER trg_reports_ussd_to_coop
AFTER INSERT ON reports FOR EACH ROW
WHEN (new.source = 'USSD')
BEGIN
  ngao_ussd_report_to_coop(:new.report_id, :new.message_id, :new.ward_id, :new.report_type,
                           :new.report_count, JSON_SERIALIZE(:new.payload RETURNING VARCHAR2(4000)),
                           :new.created_at);
EXCEPTION WHEN OTHERS THEN NULL;  -- never block the farmer's report
END;
/

-- Backfill USSD reports from before this trigger existed.
BEGIN
  FOR r IN (SELECT report_id, message_id, ward_id, report_type, report_count,
                   JSON_SERIALIZE(payload RETURNING VARCHAR2(4000)) payload, created_at
              FROM reports WHERE source = 'USSD') LOOP
    ngao_ussd_report_to_coop(r.report_id, r.message_id, r.ward_id, r.report_type, r.report_count,
                             r.payload, r.created_at);
  END LOOP;
  COMMIT;
END;
/
