-- 020_backend_bridge.sql  (Ngao / Team Daraja)  -- additive, re-runnable
-- Lets APEX (app 100) call the Django backend REST API (/ngao/v1, docs/API_CONTRACT.md §2.2) from
-- page processes, e.g. a "Send now" button:  ngao_backend.approve_alert(:P3_ALERT_ID);
--
-- Wiring (both done once; neither value is in GitHub):
--   * BASE_URL: written by the VM deploy (.github/workflows/deploy-vm.yml runs
--     `manage.py set_backend_config`), e.g. https://<vm-ip>.sslip.io/ngao/v1. HTTPS with a public
--     certificate is required: Autonomous Database only makes outbound HTTPS calls from APEX.
--   * Token: APEX > Workspace Utilities > Web Credentials > Create
--       Name / Static ID  NGAO_BACKEND
--       Authentication    HTTP Header
--       Credential Name   Authorization
--       Credential Secret Bearer <one of NGAO_API_TOKENS>
--       Valid for URLs    https://<vm-host>/
--
-- Without the backend, alert approvals still work: APEX sets ALERTS.status = 'APPROVED' and the
-- backend scheduler sends approved alerts within ~15 s. This bridge is for actions that need the
-- backend to run now (scoring, routes, the seasonal outlook) and for showing its answer in APEX.
SET DEFINE OFF

CREATE TABLE IF NOT EXISTS ngao_backend_config (
  config_key   VARCHAR2(40)   NOT NULL,
  config_value VARCHAR2(1000),
  updated_at   TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP,
  CONSTRAINT pk_ngao_backend_config PRIMARY KEY (config_key)
);

MERGE INTO ngao_backend_config c
USING (SELECT 'CREDENTIAL_STATIC_ID' AS config_key, 'NGAO_BACKEND' AS config_value FROM dual) s
ON (c.config_key = s.config_key)
WHEN NOT MATCHED THEN INSERT (config_key, config_value) VALUES (s.config_key, s.config_value);
COMMIT;

CREATE OR REPLACE PACKAGE ngao_backend AUTHID DEFINER AS
  -- https://<host>/ngao/v1 from NGAO_BACKEND_CONFIG (no trailing slash). Raises -20901 when unset.
  FUNCTION base_url RETURN VARCHAR2;

  -- One call to the backend. Returns the JSON body. Raises -20902 with the backend's error message
  -- for HTTP 4xx/5xx, so an APEX process shows it in the notification.
  FUNCTION request(p_method IN VARCHAR2, p_path IN VARCHAR2, p_body IN CLOB DEFAULT NULL) RETURN CLOB;

  -- POST /jobs/<job>. p_job: score | send-alerts | plan-routes | replay | reset-demo | dispatch |
  -- grade-outcomes | seasonal-outlook. Returns {"job_id", "job", "status", "summary"}.
  FUNCTION run_job(p_job IN VARCHAR2, p_body IN CLOB DEFAULT '{}') RETURN CLOB;

  -- POST /alerts/<id>/approve and /reject: the backend sends approved alerts immediately.
  PROCEDURE approve_alert(p_alert_id IN NUMBER, p_approved_by IN VARCHAR2 DEFAULT NULL);
  PROCEDURE reject_alert(p_alert_id IN NUMBER, p_rejected_by IN VARCHAR2 DEFAULT NULL);

  -- GET /health: {"status":"ok","database":...,"sms_backend":...}
  FUNCTION health RETURN CLOB;
END ngao_backend;
/

CREATE OR REPLACE PACKAGE BODY ngao_backend AS
  c_jobs CONSTANT VARCHAR2(200) :=
    ',score,send-alerts,plan-routes,replay,reset-demo,dispatch,grade-outcomes,seasonal-outlook,';

  FUNCTION cfg(p_key IN VARCHAR2) RETURN VARCHAR2 IS
    l_value ngao_backend_config.config_value%TYPE;
  BEGIN
    SELECT config_value INTO l_value FROM ngao_backend_config WHERE config_key = p_key;
    RETURN l_value;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN NULL;
  END cfg;

  FUNCTION base_url RETURN VARCHAR2 IS
    l_url VARCHAR2(1000) := RTRIM(cfg('BASE_URL'), '/');
  BEGIN
    IF l_url IS NULL THEN
      raise_application_error(-20901,
        'NGAO_BACKEND_CONFIG.BASE_URL is not set. Deploy the backend VM or set it to https://<host>/ngao/v1');
    END IF;
    RETURN l_url;
  END base_url;

  FUNCTION who(p_user IN VARCHAR2) RETURN VARCHAR2 IS
  BEGIN
    RETURN apex_escape.json(SUBSTR(NVL(p_user, NVL(v('APP_USER'), USER)), 1, 100));
  END who;

  FUNCTION request(p_method IN VARCHAR2, p_path IN VARCHAR2, p_body IN CLOB DEFAULT NULL) RETURN CLOB IS
    l_response CLOB;
    l_status   PLS_INTEGER;
    l_message  VARCHAR2(4000);
  BEGIN
    apex_web_service.clear_request_headers;
    apex_web_service.set_request_headers(
      p_name_01 => 'Content-Type', p_value_01 => 'application/json',
      p_name_02 => 'Accept',       p_value_02 => 'application/json');
    l_response := apex_web_service.make_rest_request(
      p_url                  => base_url || p_path,
      p_http_method          => UPPER(p_method),
      p_body                 => p_body,
      p_credential_static_id => NVL(cfg('CREDENTIAL_STATIC_ID'), 'NGAO_BACKEND'),
      p_transfer_timeout     => 60);
    l_status := apex_web_service.g_status_code;
    IF l_status IS NULL OR l_status >= 400 THEN
      BEGIN
        SELECT JSON_VALUE(l_response, '$.error.message' RETURNING VARCHAR2(4000) NULL ON ERROR)
          INTO l_message FROM dual;
      EXCEPTION
        WHEN OTHERS THEN
          l_message := NULL;
      END;
      raise_application_error(-20902, 'Ngao backend ' || UPPER(p_method) || ' ' || p_path || ' returned HTTP '
        || NVL(TO_CHAR(l_status), '?') || ': ' || NVL(l_message, DBMS_LOB.SUBSTR(l_response, 500, 1)));
    END IF;
    RETURN l_response;
  END request;

  FUNCTION run_job(p_job IN VARCHAR2, p_body IN CLOB DEFAULT '{}') RETURN CLOB IS
  BEGIN
    IF p_job IS NULL OR INSTR(c_jobs, ',' || LOWER(p_job) || ',') = 0 THEN
      raise_application_error(-20903, 'Unknown Ngao job: ' || p_job);
    END IF;
    RETURN request('POST', '/jobs/' || LOWER(p_job), NVL(p_body, '{}'));
  END run_job;

  PROCEDURE approve_alert(p_alert_id IN NUMBER, p_approved_by IN VARCHAR2 DEFAULT NULL) IS
    l_response CLOB;
  BEGIN
    l_response := request('POST', '/alerts/' || TO_CHAR(p_alert_id) || '/approve',
                          '{"approved_by":"' || who(p_approved_by) || '"}');
  END approve_alert;

  PROCEDURE reject_alert(p_alert_id IN NUMBER, p_rejected_by IN VARCHAR2 DEFAULT NULL) IS
    l_response CLOB;
  BEGIN
    l_response := request('POST', '/alerts/' || TO_CHAR(p_alert_id) || '/reject',
                          '{"approved_by":"' || who(p_rejected_by) || '"}');
  END reject_alert;

  FUNCTION health RETURN CLOB IS
  BEGIN
    RETURN request('GET', '/health');
  END health;
END ngao_backend;
/
