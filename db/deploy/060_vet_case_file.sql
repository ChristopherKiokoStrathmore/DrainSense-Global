-- 060_vet_case_file.sql  (Ngao)  -- re-runnable
-- NGAO_VET_CASE_HTML(report_id): the "case file" a vet sees on APEX page 42 (Vet Verification).
-- Facts come only from database rows. The checklist is general reference guidance, labelled as such.
SET DEFINE OFF
CREATE OR REPLACE FUNCTION ngao_vet_case_html(p_report_id NUMBER) RETURN CLOB IS
  h        CLOB;
  r        coop_disease_reports%ROWTYPE;
  l_ward   VARCHAR2(200);
  l_county VARCHAR2(100);
  l_coop   VARCHAR2(200);
  l_phone  VARCHAR2(30);
  l_sdemo  CHAR(1);
  l_sward  VARCHAR2(200);
  l_role   VARCHAR2(20);
  n_past NUMBER; n_conf NUMBER; n_rej NUMBER;
  n_same NUMBER; n_same_c NUMBER; n_nb NUMBER; n_nb_c NUMBER;
  n_mem  NUMBER; n_demo NUMBER;
  l_dis  VARCHAR2(200);
  PROCEDURE a(s VARCHAR2) IS BEGIN IF s IS NOT NULL THEN dbms_lob.writeappend(h, LENGTH(s), s); END IF; END;
  FUNCTION e(s VARCHAR2) RETURN VARCHAR2 IS BEGIN RETURN apex_escape.html(s); END;
  FUNCTION mask(p VARCHAR2) RETURN VARCHAR2 IS
  BEGIN RETURN CASE WHEN p IS NULL THEN 'unknown' WHEN LENGTH(p) > 9 THEN SUBSTR(p,1,7)||'***'||SUBSTR(p,-2) ELSE '***' END; END;
  FUNCTION st(s VARCHAR2) RETURN VARCHAR2 IS
  BEGIN RETURN '<span class="vet-pill '||s||'">'||CASE s WHEN 'PENDING' THEN 'Pending' WHEN 'VET_CONFIRMED' THEN 'Confirmed' ELSE 'Rejected' END||'</span>'; END;
  FUNCTION ts(t TIMESTAMP WITH TIME ZONE) RETURN VARCHAR2 IS
  BEGIN RETURN TO_CHAR(t AT TIME ZONE 'Africa/Nairobi', 'DD Mon HH24:MI'); END;
BEGIN
  dbms_lob.createtemporary(h, TRUE);
  SELECT * INTO r FROM coop_disease_reports WHERE report_id = p_report_id;
  SELECT ward_name, county INTO l_ward, l_county FROM wards WHERE ward_id = r.ward_id;
  SELECT MAX(REPLACE(coop_name, 'SYNTHETIC ', '')) INTO l_coop FROM cooperatives WHERE coop_id = r.coop_id;
  BEGIN
    SELECT s.phone, s.is_demo, w.ward_name, s.role INTO l_phone, l_sdemo, l_sward, l_role
      FROM subscribers s LEFT JOIN wards w ON w.ward_id = s.ward_id WHERE s.subscriber_id = r.subscriber_id;
  EXCEPTION WHEN NO_DATA_FOUND THEN NULL;
  END;
  SELECT COUNT(*), COUNT(CASE WHEN vet_status = 'VET_CONFIRMED' THEN 1 END), COUNT(CASE WHEN vet_status = 'REJECTED' THEN 1 END)
    INTO n_past, n_conf, n_rej
    FROM coop_disease_reports
   WHERE report_id <> r.report_id AND r.subscriber_id IS NOT NULL AND subscriber_id = r.subscriber_id;
  l_dis := LOWER(r.disease);

  a('<div class="case">');
  a('<div class="case-h"><div><div class="vet-top">Report #'||r.report_id||' &middot; '||e(r.channel)
    ||CASE WHEN r.is_demo = 'Y' THEN ' <span class="vet-b d">DEMO</span>' END||'</div><div class="vet-title">'
    ||e(r.disease)||'</div></div>'||st(r.vet_status)||'</div>');

  -- Report
  a('<h4>Report</h4><dl class="case-dl">');
  a('<dt>Reported</dt><dd>'||e(r.category)||' &middot; '||e(r.disease)
    ||CASE WHEN r.animals_or_acres IS NOT NULL THEN ' &middot; count '||r.animals_or_acres
       ||CASE WHEN r.category = 'LIVESTOCK' THEN ' animals' WHEN l_dis LIKE 'wet grain%' THEN ' bags' ELSE ' (acres/bags)' END END||'</dd>');
  IF r.symptoms IS NOT NULL AND r.symptoms NOT LIKE 'USSD report%' THEN a('<dt>Symptoms</dt><dd>'||e(r.symptoms)||'</dd>'); END IF;
  a('<dt>Received</dt><dd>'||ts(r.reported_at)||' EAT &middot; ward '||e(l_ward)||' ('||e(l_county)||')</dd>');
  a('<dt>OTP verified</dt><dd>'||CASE r.otp_verified WHEN 'Y' THEN 'Yes' ELSE 'No' END||'</dd></dl>');

  -- Reporter
  a('<h4>Reporter</h4><dl class="case-dl">');
  a('<dt>Phone</dt><dd>'||mask(l_phone)||CASE WHEN l_sdemo = 'Y' OR r.is_demo = 'Y' THEN ' <span class="vet-b d">demo</span>' ELSE ' <span class="vet-b u">member</span>' END||'</dd>');
  a('<dt>Registered ward</dt><dd>'||NVL(e(l_sward), 'not registered')||CASE WHEN l_role IS NOT NULL THEN ' &middot; '||e(l_role) END||'</dd>');
  a('<dt>Co-op</dt><dd>'||NVL(e(l_coop), '-')||CASE WHEN r.member_no IS NOT NULL THEN ' &middot; member no. '||e(r.member_no) END||'</dd>');
  a('<dt>Track record</dt><dd>'||n_past||' earlier report(s): '||n_conf||' confirmed, '||n_rej||' rejected'
    ||CASE WHEN n_conf + n_rej > 0 THEN ' &middot; reliability '||ROUND(100*n_conf/(n_conf+n_rej))||'%' ELSE ' &middot; no reviewed history yet' END||'</dd></dl>');

  -- Corroboration
  SELECT COUNT(*), COUNT(CASE WHEN vet_status = 'VET_CONFIRMED' THEN 1 END) INTO n_same, n_same_c
    FROM coop_disease_reports
   WHERE ward_id = r.ward_id AND disease = r.disease AND report_id <> r.report_id
     AND reported_at > SYSTIMESTAMP - INTERVAL '14' DAY AND vet_status <> 'REJECTED';
  SELECT COUNT(*), COUNT(CASE WHEN x.vet_status = 'VET_CONFIRMED' THEN 1 END) INTO n_nb, n_nb_c
    FROM coop_disease_reports x JOIN ward_neighbours n ON n.neighbour_id = x.ward_id AND n.ward_id = r.ward_id
   WHERE x.disease = r.disease AND x.reported_at > SYSTIMESTAMP - INTERVAL '14' DAY AND x.vet_status <> 'REJECTED';
  a('<h4>Corroboration (same disease, last 14 days)</h4>');
  a('<div class="case-kpis"><div><b>'||n_same||'</b>in this ward ('||n_same_c||' confirmed)</div><div><b>'||n_nb
    ||'</b>in neighbouring wards ('||n_nb_c||' confirmed)</div></div><ul class="case-ul">');
  FOR x IN (SELECT x.report_id, w.ward_name, x.reported_at, x.vet_status, x.animals_or_acres
              FROM coop_disease_reports x JOIN wards w ON w.ward_id = x.ward_id
             WHERE x.disease = r.disease AND x.report_id <> r.report_id AND x.reported_at > SYSTIMESTAMP - INTERVAL '14' DAY
               AND (x.ward_id = r.ward_id OR x.ward_id IN (SELECT neighbour_id FROM ward_neighbours WHERE ward_id = r.ward_id))
             ORDER BY x.reported_at DESC FETCH FIRST 6 ROWS ONLY) LOOP
    a('<li>#'||x.report_id||' '||e(x.ward_name)||' &middot; '||ts(x.reported_at)
      ||CASE WHEN x.animals_or_acres IS NOT NULL THEN ' &middot; '||x.animals_or_acres END||' '||st(x.vet_status)||'</li>');
  END LOOP;
  IF n_same + n_nb = 0 THEN a('<li>No other reports nearby.</li>'); END IF;
  a('</ul>');

  -- Context
  a('<h4>Ward context</h4><dl class="case-dl">');
  DECLARE n NUMBER := 0;
  BEGIN
    FOR k IN (SELECT hazard, risk_level, ROUND(risk_score*100) p, as_of FROM v_ward_risk_latest
               WHERE ward_id = r.ward_id AND hazard IN ('rvf','field_flood','rain_coming','aflatoxin') ORDER BY hazard) LOOP
      n := n + 1;
      a('<dt>'||e(CASE k.hazard WHEN 'rvf' THEN 'RVF risk' WHEN 'field_flood' THEN 'Field flood' WHEN 'rain_coming' THEN 'Heavy rain (48h)' ELSE 'Aflatoxin' END)
        ||'</dt><dd>'||e(k.risk_level)||' ('||k.p||'%) &middot; '||TO_CHAR(k.as_of,'DD Mon')||'</dd>');
    END LOOP;
    IF n = 0 THEN a('<dt>Risk scores</dt><dd>none for this ward</dd>'); END IF;
  END;
  FOR k IN (SELECT MAX(feature_date) d,
                   ROUND(SUM(CASE WHEN feature_date > TRUNC(SYSDATE) - 7 THEN rain_1d_mm END)) r7,
                   ROUND(SUM(CASE WHEN feature_date > TRUNC(SYSDATE) - 30 THEN rain_1d_mm END)) r30,
                   COUNT(CASE WHEN feature_date > TRUNC(SYSDATE) - 30 THEN rain_1d_mm END) n30
              FROM v_rain_series WHERE ward_id = r.ward_id AND kind = 'OBSERVED' AND feature_date > TRUNC(SYSDATE) - 30) LOOP
    IF k.n30 > 0 THEN
      a('<dt>Rainfall (observed)</dt><dd>last 7 days '||NVL(TO_CHAR(k.r7),'0')||' mm &middot; last 30 days '||k.r30||' mm &middot; to '||TO_CHAR(k.d,'DD Mon')||'</dd>');
    ELSE
      a('<dt>Rainfall</dt><dd>no observed rainfall data for this ward</dd>');
    END IF;
  END LOOP;
  a('</dl>');

  -- Spread exposure
  SELECT COUNT(CASE WHEN is_demo = 'N' THEN 1 END), COUNT(CASE WHEN is_demo = 'Y' THEN 1 END) INTO n_mem, n_demo
    FROM subscribers WHERE ward_id = r.ward_id AND status = 'ACTIVE' AND consent_at IS NOT NULL;
  a('<h4>Who an alert would reach</h4><ul class="case-ul"><li>'||e(l_ward)||' (this ward): '||n_mem||' real member(s), '||n_demo||' demo</li>');
  FOR k IN (SELECT w.ward_name, ROUND(n.centroid_km,1) km,
                   (SELECT COUNT(*) FROM subscribers s WHERE s.ward_id = w.ward_id AND s.status = 'ACTIVE' AND s.consent_at IS NOT NULL AND s.is_demo = 'N') real_n,
                   (SELECT COUNT(*) FROM subscribers s WHERE s.ward_id = w.ward_id AND s.status = 'ACTIVE' AND s.consent_at IS NOT NULL AND s.is_demo = 'Y') demo_n
              FROM ward_neighbours n JOIN wards w ON w.ward_id = n.neighbour_id WHERE n.ward_id = r.ward_id ORDER BY n.centroid_km) LOOP
    a('<li>'||e(k.ward_name)||' ('||k.km||' km): '||k.real_n||' real, '||k.demo_n||' demo</li>');
  END LOOP;
  a('</ul>');

  -- Reference guidance (static, general)
  a('<h4>Checklist <span class="vet-b d">reference guidance</span></h4><ul class="case-ul">');
  IF l_dis LIKE '%rift valley%' THEN
    a('<li>Look for abortion storms in sheep/goats/cattle and deaths of young animals.</li><li>Ask about recent heavy rain and standing water (mosquito breeding).</li><li>Notifiable disease: inform the county veterinary officer; collect blood samples for laboratory confirmation (e.g. ELISA/PCR).</li><li>Advise protective handling of aborted material; RVF can infect people.</li>');
  ELSIF l_dis LIKE '%lumpy skin%' THEN
    a('<li>Look for firm skin nodules, fever, swollen lymph nodes, drop in milk.</li><li>Check for biting-insect exposure and recent cattle movements.</li><li>Field visit; skin biopsy or scab for laboratory PCR if needed.</li>');
  ELSIF l_dis LIKE '%peste des petits%' OR l_dis LIKE '%ppr%' THEN
    a('<li>Look for fever, eye and nasal discharge, mouth sores, diarrhoea in goats/sheep.</li><li>Check recent market purchases or mixing of flocks.</li><li>Field visit; swabs for laboratory confirmation; notify the county veterinary officer.</li>');
  ELSIF l_dis LIKE '%maize lethal%' OR l_dis LIKE '%mln%' THEN
    a('<li>Look for leaf yellowing from the margins, dead heart, poorly filled cobs.</li><li>Check for thrips/aphid vectors and continuous maize cropping.</li><li>Field visit; leaf samples for a laboratory/rapid virus test (agriculture extension).</li>');
  ELSIF l_dis LIKE '%armyworm%' THEN
    a('<li>Look for window-pane leaf damage, frass (sawdust-like droppings) in the funnel, larvae with an inverted Y on the head.</li><li>Field visit: scout a sample of plants across the field.</li><li>Agree control advice with agriculture extension.</li>');
  ELSIF l_dis LIKE 'wet grain%' THEN
    a('<li>Ask how long the grain has been wet and how it is stored.</li><li>Check moisture (meter) and for mould or heating.</li><li>Arrange drying; aflatoxin test kit if mould is seen.</li>');
  ELSIF l_dis LIKE 'flooding%' THEN
    a('<li>Confirm the extent of flooded fields and access to stores.</li><li>Check stored grain and animals at risk; plan drying and safe water.</li>');
  ELSE
    a('<li>Field visit: confirm the number of animals affected, signs and timing.</li><li>Collect samples for the laboratory if a notifiable disease is suspected; notify the county veterinary officer.</li>');
  END IF;
  a('<li class="case-note">General guidance only; follow county veterinary and agriculture protocols.</li></ul>');

  IF r.vet_status = 'PENDING' THEN
    a('<div class="case-act"><button type="button" class="vet-btn ok" onclick="vetOpen('||r.report_id||',''VET_CONFIRMED'')">Confirm</button>'
      ||'<button type="button" class="vet-btn no" onclick="vetOpen('||r.report_id||',''REJECTED'')">Reject</button></div>');
  ELSE
    a('<div class="case-act">Reviewed by '||NVL(e(r.vet_name),'-')||CASE WHEN r.vet_notes IS NOT NULL THEN ': '||e(r.vet_notes) END
      ||' <button type="button" class="vet-btn ed" onclick="vetOpen('||r.report_id||',''PENDING'')">Change</button></div>');
  END IF;
  a('</div>');
  RETURN h;
EXCEPTION WHEN NO_DATA_FOUND THEN
  RETURN '<p>Report not found.</p>';
END;
/
