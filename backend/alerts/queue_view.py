"""Join logic of the live V_ALERT_QUEUE view (APEX Alert Approvals, page 3).

The live view inner-joins ALERTS to WARDS, SUBSCRIBERS and RISK_SCORES. An alert row with a
missing subscriber, ward or score never appears on the dashboard. This module repeats those
joins so tests can prove the same visibility rule without a live database. It does not select
WARDS.IS_REPLAY: that column is on the live view but is not part of the join that hides a row.
"""

# Column list matches V_ALERT_QUEUE except W.IS_REPLAY, which the live SELECT projects and
# which this schema's WARDS table does not store. The FROM/JOIN clause is the visibility rule.
ALERT_QUEUE_SQL = """
SELECT a.alert_id, a.status, a.hazard, a.severity, a.alert_type,
       w.ward_id, w.ward_name, w.county,
       s.subscriber_id, s.phone, s.role, a.lang, a.body, LENGTH(a.body) AS body_len,
       r.probability AS risk_score, r.confidence_pct AS confidence, r.score_date AS as_of,
       a.created_at, a.approved_by, a.approved_at, a.sent_at, a.message_id
FROM {alerts} a
JOIN wards w ON w.ward_id = a.ward_id
JOIN subscribers s ON s.subscriber_id = a.subscriber_id
JOIN risk_scores r ON r.score_id = a.score_id
"""


def alert_queue_sql(alerts_table="alerts"):
    if not alerts_table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name {alerts_table!r}")
    return ALERT_QUEUE_SQL.format(alerts=alerts_table)


def alert_queue_rows(alerts_table="alerts"):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(alert_queue_sql(alerts_table))
        columns = [col[0].lower() for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
