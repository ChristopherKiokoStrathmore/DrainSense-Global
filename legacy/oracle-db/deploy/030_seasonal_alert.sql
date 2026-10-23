-- 030_seasonal_alert.sql  (Ngao / Team Daraja)  -- additive, re-runnable
-- Alert type S1 "Seasonal outlook: very wet short rains" and its SMS copy (Kiswahili / English).
-- The backend's seasonal outlook (POST /jobs/seasonal-outlook, the scheduler in October) writes S1 rows
-- to RISK_SCORES for wards at WATCH/ACT; send-alerts drafts ACT alerts; officers approve on APEX page 3.
-- Inserts only when missing: officers' edits on the Alert Templates page (11) are never overwritten.
-- The Kiswahili copy must be checked by a native speaker before real use (API_CONTRACT §4).
SET DEFINE OFF

DECLARE
  c_sw CONSTANT VARCHAR2(160) := 'MvuaSmart: Mvua za Okt-Des kata ya {ward} huenda zikazidi kawaida sana (uwezekano {confidence}%). Chimba mitaro, panda kwenye matuta, hifadhi nafaka pakavu.';
  c_en CONSTANT VARCHAR2(160) := 'MvuaSmart: Oct-Dec rains in {ward} may be far above normal ({confidence}% chance). Dig drainage, plant on raised beds, keep stored grain dry.';
  l_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_count FROM alert_types WHERE alert_type = 'S1';
  IF l_count = 0 THEN
    -- The live ALERT_TYPES has a HAZARD column (V_WARD_RISK_LATEST reads it); db/schema.sql does not.
    -- all_tab_columns + CURRENT_SCHEMA: the deploy may log in as ADMIN and work in WKSP_NGAO.
    SELECT COUNT(*) INTO l_count FROM all_tab_columns
     WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AND table_name = 'ALERT_TYPES' AND column_name = 'HAZARD';
    IF l_count > 0 THEN
      EXECUTE IMMEDIATE
        'INSERT INTO alert_types (alert_type, service, name, act_threshold, watch_threshold, cooldown_hours, '
        || 'overrides_cap, audience_roles, template_sw, template_en, hazard) '
        || 'VALUES (''S1'', ''FARM'', ''Seasonal outlook: very wet short rains'', 0.5, NULL, 1440, ''N'', '
        || '''farmer,both'', :sw, :en, ''seasonal_wet'')'
        USING c_sw, c_en;
    ELSE
      EXECUTE IMMEDIATE
        'INSERT INTO alert_types (alert_type, service, name, act_threshold, watch_threshold, cooldown_hours, '
        || 'overrides_cap, audience_roles, template_sw, template_en) '
        || 'VALUES (''S1'', ''FARM'', ''Seasonal outlook: very wet short rains'', 0.5, NULL, 1440, ''N'', '
        || '''farmer,both'', :sw, :en)'
        USING c_sw, c_en;
    END IF;
  END IF;

  MERGE INTO alert_template t
  USING (SELECT 'seasonal_wet' AS hazard, 'warning' AS severity, 'sw' AS lang, c_sw AS body FROM dual
         UNION ALL
         SELECT 'seasonal_wet', 'warning', 'en', c_en FROM dual) s
  ON (t.hazard = s.hazard AND t.severity = s.severity AND t.lang = s.lang)
  WHEN NOT MATCHED THEN INSERT (hazard, severity, lang, body) VALUES (s.hazard, s.severity, s.lang, s.body);
  COMMIT;
END;
/
