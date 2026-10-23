-- DO NOT RUN against the live NgaoDB (ADB, schema WKSP_NGAO).
--
-- This script was an attempt to change the live tables so an earlier backend could insert
-- ward-level alerts (nullable subscriber_id / score_id, BODY 480, MESSAGES.ALERT_ID, JOB_RUNS).
-- The live ALERTS row is the opposite, and V_ALERT_QUEUE inner-joins SUBSCRIBERS. The backend
-- now writes that live shape. Running this file would change the live database; do not.
--
-- The statements below are kept for history and are not executed.
/*
-- Ngao / MvuaSmart: align an existing ADB schema with the backend (Dennis, backend-dennis).
-- Safe to re-run: every step skips itself when the change is already there.
-- Run as the schema owner (NGAO, or WKSP_NGAO for the APEX workspace) in SQL Worksheet / SQLcl.
-- Then check from the backend VM:  python manage.py check_schema
--
-- What and why
--  1. ALERTS become one row per (risk score, language) drafted by the alert engine and approved by
--     an officer on the APEX Alert Approvals page (PENDING_APPROVAL -> APPROVED / REJECTED -> SENT).
--     That is the flow the APEX app already uses; schema.sql v1-draft still had per-subscriber rows.
--     New columns: hazard, severity, lang, body, approved_by, approved_at, sent_at.
--     subscriber_id / score_id become nullable; status widens to VARCHAR2(16) for PENDING_APPROVAL.
--  2. MESSAGES.alert_id: which alert an OUT SMS delivered (fan-out, cooldowns, weekly cap).
--  3. JOB_RUNS: one row per /jobs/* call; job_id in the 202 response (API_CONTRACT §2.2).
--  Fixes: REPORTS.source widened to VARCHAR2(12) ('SMS_KEYWORD' did not fit); timestamps stored
--     with a time-zone region are re-stored with a UTC offset (python-oracledb thin mode, step 4).

SET SERVEROUTPUT ON

DECLARE
  l_len NUMBER;

  PROCEDURE run(p_sql VARCHAR2, p_ignore PLS_INTEGER DEFAULT NULL, p_ignore2 PLS_INTEGER DEFAULT NULL) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
    DBMS_OUTPUT.PUT_LINE('ok    ' || SUBSTR(p_sql, 1, 100));
  EXCEPTION WHEN OTHERS THEN
    IF SQLCODE IN (NVL(p_ignore, 1), NVL(p_ignore2, 1)) THEN
      DBMS_OUTPUT.PUT_LINE('skip  ' || SUBSTR(p_sql, 1, 100) || '  (' || SQLERRM || ')');
    ELSE
      RAISE;
    END IF;
  END;

  PROCEDURE add_column(p_table VARCHAR2, p_column_ddl VARCHAR2) IS
  BEGIN
    run('ALTER TABLE ' || p_table || ' ADD (' || p_column_ddl || ')', -1430);  -- ORA-01430 column exists
  END;
BEGIN
  -- 1. ALERTS --------------------------------------------------------------------------------
  add_column('alerts', 'hazard VARCHAR2(30)');
  add_column('alerts', 'severity VARCHAR2(16)');
  add_column('alerts', 'lang CHAR(2)');
  add_column('alerts', 'body VARCHAR2(480)');
  add_column('alerts', 'approved_by VARCHAR2(100)');
  add_column('alerts', 'approved_at TIMESTAMP WITH TIME ZONE');
  add_column('alerts', 'sent_at TIMESTAMP WITH TIME ZONE');
  run('ALTER TABLE alerts MODIFY (subscriber_id NULL)', -1451);  -- ORA-01451 already nullable
  run('ALTER TABLE alerts MODIFY (score_id NULL)', -1451);

  SELECT data_length INTO l_len FROM user_tab_columns WHERE table_name = 'ALERTS' AND column_name = 'STATUS';
  IF l_len < 16 THEN
    run('ALTER TABLE alerts MODIFY (status VARCHAR2(16))');
  END IF;

  -- Replace the status CHECK (system-named in v1-draft) with the approval workflow's values.
  -- NOVALIDATE keeps any existing rows that use other values.
  FOR c IN (SELECT constraint_name FROM user_constraints
             WHERE table_name = 'ALERTS' AND constraint_type = 'C'
               AND UPPER(search_condition_vc) LIKE '%STATUS%IN%'
               AND constraint_name <> 'CK_ALERTS_STATUS') LOOP
    run('ALTER TABLE alerts DROP CONSTRAINT ' || c.constraint_name);
  END LOOP;
  run(q'[ALTER TABLE alerts ADD CONSTRAINT ck_alerts_status CHECK (status IN
        ('PENDING_APPROVAL','APPROVED','REJECTED','SENT','SUPPRESSED','CREATED','QUEUED')) ENABLE NOVALIDATE]',
      -2264);  -- ORA-02264 name already used

  -- REPORTS.source was VARCHAR2(10) but its CHECK allows 'SMS_KEYWORD' (11 chars).
  SELECT data_length INTO l_len FROM user_tab_columns WHERE table_name = 'REPORTS' AND column_name = 'SOURCE';
  IF l_len < 12 THEN
    run('ALTER TABLE reports MODIFY (source VARCHAR2(12))');
  END IF;

  -- 2. MESSAGES.alert_id ------------------------------------------------------------------------
  add_column('messages', 'alert_id NUMBER');
  run('ALTER TABLE messages ADD CONSTRAINT fk_msg_alert FOREIGN KEY (alert_id) REFERENCES alerts (alert_id)',
      -2275, -2264);  -- ORA-02275 FK exists / ORA-02264 name used
  run('CREATE INDEX ix_msg_alert ON messages (alert_id)', -955, -1408);
  run('CREATE INDEX ix_alerts_score ON alerts (score_id, lang)', -955, -1408);

  -- 3. JOB_RUNS -------------------------------------------------------------------------------------
  run(q'[CREATE TABLE job_runs (
           job_id      NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
           job_name    VARCHAR2(30) NOT NULL,
           params      JSON,
           status      VARCHAR2(10) DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','DONE','FAILED')),
           summary     JSON,
           error_text  VARCHAR2(1000),
           started_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP,
           finished_at TIMESTAMP WITH TIME ZONE)]', -955);  -- ORA-00955 name already used

  -- 4. Timestamps stored with a time-zone REGION (e.g. written from an APEX session whose time
  --    zone is 'Africa/Nairobi') cannot be read by python-oracledb thin mode (DPY-3022).
  --    Re-store them as the same instant with a UTC offset. Re-runnable; changes no instants.
  FOR c IN (SELECT table_name, column_name FROM user_tab_columns
             WHERE data_type LIKE 'TIMESTAMP%WITH TIME ZONE'
               AND table_name IN ('SUBSCRIBERS','MESSAGES','ALERTS','REPORTS','ROUTE_STOPS','RISK_SCORES',
                                  'USSD_SESSIONS','JOB_RUNS')) LOOP
    EXECUTE IMMEDIATE 'UPDATE ' || c.table_name || ' SET ' || c.column_name ||
      ' = FROM_TZ(CAST(SYS_EXTRACT_UTC(' || c.column_name || ') AS TIMESTAMP), ''+00:00'')' ||
      ' WHERE EXTRACT(TIMEZONE_REGION FROM ' || c.column_name || ') <> ''UNKNOWN''';
    IF SQL%ROWCOUNT > 0 THEN
      DBMS_OUTPUT.PUT_LINE('tz    ' || c.table_name || '.' || c.column_name || ': ' || SQL%ROWCOUNT || ' rows to UTC offset');
    END IF;
  END LOOP;
  COMMIT;
END;
/
*/
