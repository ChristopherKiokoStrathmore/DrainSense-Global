"""Django models for the live NgaoDB schema (WKSP_NGAO).

Table and column names match the live columns; Django's Oracle backend upper-cases them, so
db_table "risk_scores" is RISK_SCORES. In Oracle the tables already exist and `manage.py migrate`
is never run; the migrations in this app only build the SQLite schema used for development and
tests. `manage.py check_schema` compares these models with a connected database.

The live ALERTS row is one subscriber, not one ward: SUBSCRIBER_ID, SCORE_ID, WARD_ID and
ALERT_TYPE are NOT NULL, BODY is VARCHAR2(160), and the outbound SMS is ALERTS.MESSAGE_ID.
MESSAGES has no alert_id column. JOB_RUNS is not on the live database; the model stays so local
SQLite can record jobs, and the job runner skips it when the table is absent.

Live RISK_SCORES.DECISION is NOT NULL VARCHAR2(7) (ACT, WATCH, ABSTAIN — the values
V_WARD_RISK_LATEST colours on). IS_REPLAY is CHAR(1), nullable, and part of the unique key, so
this backend writes Y or N and never NULL. SUBSCRIBERS.PREFERRED_LANG is CHAR(2) NOT NULL and is
the language SMS and USSD read; LANGUAGE is a nullable copy kept in sync.

Ownership: the backend writes SUBSCRIBERS, FARMS, HERDS, MESSAGES, REPORTS, ALERTS, ROUTES,
ROUTE_STOPS, BOOKINGS, OUTCOMES, and JOB_RUNS when that table exists. Trevor writes WARDS,
WARD_FEATURES_DAILY, FORECASTS and RISK_SCORES (the backend's fallback scorer writes RISK_SCORES
too, but never over a row produced by another model_version). Louisa writes USSD_SESSIONS.
"""
from django.db import models
from django.utils import timezone

from ngao.fields import CharCodeField, JSONColumn, YesNoField

LANGS = [("sw", "Kiswahili"), ("en", "English")]
SERVICES = [("FARM", "MvuaSmart"), ("HERD", "Ngao Mifugo"), ("HEALTH", "Ngao Afya")]

# ----- Reference --------------------------------------------------------------------------


class Ward(models.Model):
    ward_id = models.IntegerField(primary_key=True)
    county = models.CharField(max_length=60)
    ward_name = models.CharField(max_length=100)
    # boundary SDO_GEOMETRY is read by APEX only; the backend uses the centroid.
    centroid_lat = models.FloatField(null=True, blank=True)
    centroid_lon = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "wards"
        ordering = ["county", "ward_name"]

    def __str__(self):
        return f"{self.ward_name} ({self.county})"


class AlertType(models.Model):
    alert_type = models.CharField(max_length=3, primary_key=True)  # A1..A5, B1..B4, C1..C3
    service = models.CharField(max_length=10, choices=SERVICES)
    name = models.CharField(max_length=60)
    act_threshold = models.FloatField(null=True, blank=True)
    watch_threshold = models.FloatField(null=True, blank=True)
    cooldown_hours = models.IntegerField(default=48, null=True)
    overrides_cap = YesNoField(default=False)  # floods/disease skip the weekly routine cap
    audience_roles = models.CharField(max_length=200, null=True, blank=True)
    template_sw = models.CharField(max_length=480, null=True, blank=True)
    template_en = models.CharField(max_length=480, null=True, blank=True)

    class Meta:
        db_table = "alert_types"
        ordering = ["alert_type"]

    def __str__(self):
        return self.alert_type


class AlertTemplate(models.Model):
    template_id = models.AutoField(primary_key=True)
    hazard = models.CharField(max_length=30)
    severity = models.CharField(max_length=16)
    lang = models.CharField(max_length=2, choices=LANGS)
    body = models.CharField(max_length=160)

    class Meta:
        db_table = "alert_template"
        constraints = [
            models.UniqueConstraint(fields=["hazard", "severity", "lang"], name="uq_alert_template")
        ]


class AppUser(models.Model):
    user_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=100, unique=True)
    app_role = models.CharField(max_length=20)
    county = models.CharField(max_length=60, null=True, blank=True)

    class Meta:
        db_table = "app_users"


# ----- Time series (Trevor) ------------------------------------------------------------------


class WardFeatureDaily(models.Model):
    pk = models.CompositePrimaryKey("ward", "feature_date")
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    feature_date = models.DateField()
    available_date = models.DateField()  # leakage guard: replays read available_date <= t only
    rain_1d_mm = models.FloatField(null=True)
    rain_3d_mm = models.FloatField(null=True)
    rain_7d_mm = models.FloatField(null=True)
    rain_14d_mm = models.FloatField(null=True)
    rain_30d_mm = models.FloatField(null=True)
    rain_anom_30d = models.FloatField(null=True)
    gfs_rain_d1_mm = models.FloatField(null=True)
    gfs_rain_d2_mm = models.FloatField(null=True)
    gfs_rain_d3_mm = models.FloatField(null=True)
    gfs_rain_d5_mm = models.FloatField(null=True)
    gfs_rain_d7_mm = models.FloatField(null=True)
    ecmwf_agreement = models.FloatField(null=True)
    soil_moist_top = models.FloatField(null=True)
    soil_moist_root = models.FloatField(null=True)
    ndvi = models.FloatField(null=True)
    ndvi_anom = models.FloatField(null=True)
    vci3m = models.FloatField(null=True)
    lst_k = models.FloatField(null=True)
    flood_extent_pct = models.FloatField(null=True)
    glofas_p_5yr = models.FloatField(null=True)
    wet_bag_reports_3d = models.FloatField(null=True)
    sick_animal_reports_7d = models.FloatField(null=True)

    class Meta:
        db_table = "ward_features_daily"


class Forecast(models.Model):
    pk = models.CompositePrimaryKey("ward", "source", "issue_date", "lead_day")
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    source = models.CharField(max_length=10)  # GFS | ECMWF | GLOFAS
    issue_date = models.DateField()
    lead_day = models.IntegerField()
    available_date = models.DateField()
    rain_mm = models.FloatField(null=True)
    value_json = JSONColumn(null=True)

    class Meta:
        db_table = "forecasts"


# ----- Decisions ---------------------------------------------------------------------------------


class RiskScore(models.Model):
    ACT, WATCH, ABSTAIN = "ACT", "WATCH", "ABSTAIN"
    DECISIONS = [(ACT, "Act"), (WATCH, "Watch"), (ABSTAIN, "Abstain")]
    # Live DECISION is VARCHAR2(7) NOT NULL. These three are the only values the views colour.
    DECISION_CODES = frozenset({ACT, WATCH, ABSTAIN})

    score_id = models.AutoField(primary_key=True)
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    score_date = models.DateField()
    alert_type = models.ForeignKey(AlertType, on_delete=models.DO_NOTHING, db_column="alert_type")
    raw_score = models.FloatField(null=True)
    probability = models.FloatField(null=True)  # calibrated 0..1
    confidence_pct = models.IntegerField(null=True)
    decision = models.CharField(max_length=7, choices=DECISIONS)
    abstain_reason = models.CharField(max_length=200, null=True, blank=True)
    model_version = models.CharField(max_length=60, null=True, blank=True)
    # Nullable on the live table, but part of uq_rs. NULL is not equal to 'N' in Oracle, so a
    # missing flag would hide the row from V_WARD_RISK_LATEST (is_replay='N') and allow a second
    # row with the same ward, date and alert type. Always store Y or N.
    is_replay = YesNoField(default=False, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "risk_scores"
        constraints = [
            models.UniqueConstraint(
                fields=["ward", "score_date", "alert_type", "is_replay"], name="uq_rs"
            )
        ]

    def save(self, *args, **kwargs):
        decision = normalize_decision(self.decision)
        extra = []
        if self.decision != decision:
            self.decision = decision
            extra.append("decision")
        if self.is_replay is None:
            self.is_replay = False
            extra.append("is_replay")
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and extra:
            kwargs["update_fields"] = list(dict.fromkeys([*update_fields, *extra]))
        super().save(*args, **kwargs)

    @classmethod
    def matching(cls, *, ward_id, score_date, alert_type_id, is_replay=False):
        """The row for this unique key. A NULL is_replay counts as non-replay ('N')."""
        base = cls.objects.filter(
            ward_id=ward_id, score_date=score_date, alert_type_id=alert_type_id,
        )
        if is_replay:
            return base.filter(is_replay=True).first()
        return base.filter(is_replay=False).first() or base.filter(is_replay__isnull=True).first()


def normalize_decision(value):
    """ACT, WATCH or ABSTAIN. Anything else cannot be stored in the live DECISION column."""
    code = "" if value is None else str(value).strip().upper()
    if code not in RiskScore.DECISION_CODES:
        raise ValueError(
            "RISK_SCORES.DECISION is NOT NULL VARCHAR2(7) and must be ACT, WATCH or ABSTAIN; "
            f"got {value!r}"
        )
    return code


# ----- People & conversations ----------------------------------------------------------------


class Subscriber(models.Model):
    ACTIVE, STOPPED = "ACTIVE", "STOPPED"
    ROLES = ["farmer", "herder", "both", "health_promoter", "operator", "committee"]

    subscriber_id = models.AutoField(primary_key=True)
    phone = models.CharField(max_length=16, unique=True)  # E.164
    ward = models.ForeignKey(
        Ward, on_delete=models.DO_NOTHING, db_column="ward_id", null=True, blank=True
    )
    role = models.CharField(max_length=16, null=True, blank=True)  # null until registration
    language = models.CharField(max_length=2, default="sw", null=True)  # legacy VARCHAR2, kept in sync
    # Live PREFERRED_LANG is CHAR(2) NOT NULL. This is the column SMS and USSD read.
    preferred_lang = CharCodeField(max_length=2, default="sw", choices=LANGS)
    consent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default=ACTIVE)
    is_demo = YesNoField(default=False)

    class Meta:
        db_table = "subscribers"

    def __str__(self):
        return self.phone

    @staticmethod
    def canonical_lang(value, default=None):
        """sw or en, the only values live PREFERRED_LANG accepts. Blank uses default."""
        code = "" if value is None else str(value).strip().lower()
        if not code:
            if default is None:
                raise ValueError("SUBSCRIBERS.PREFERRED_LANG is CHAR(2) NOT NULL")
            code = default
        if code not in ("sw", "en"):
            raise ValueError(f"SUBSCRIBERS.PREFERRED_LANG must be sw or en, not {value!r}")
        return code

    def spoken_lang(self):
        """Language for SMS and USSD. Reads PREFERRED_LANG, never the nullable LANGUAGE column."""
        try:
            return self.canonical_lang(self.preferred_lang)
        except ValueError:
            return "sw"

    def set_language(self, lang):
        self.preferred_lang = self.canonical_lang(lang)
        self.language = self.preferred_lang

    def save(self, *args, **kwargs):
        before = self.preferred_lang
        self.preferred_lang = self.canonical_lang(self.preferred_lang, default="sw")
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.preferred_lang != before:
            kwargs["update_fields"] = list(dict.fromkeys([*update_fields, "preferred_lang"]))
        super().save(*args, **kwargs)


class Farm(models.Model):
    farm_id = models.AutoField(primary_key=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id")
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    crop = models.CharField(max_length=30, default="maize", null=True)
    est_bags = models.FloatField(null=True)

    class Meta:
        db_table = "farms"


class Herd(models.Model):
    TLU_PER_HEAD = {"cattle": 0.7, "goats": 0.1, "sheep": 0.1, "camels": 1.0}

    herd_id = models.AutoField(primary_key=True)
    subscriber = models.ForeignKey(Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id")
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    species = models.CharField(max_length=10, null=True)
    head_count = models.FloatField(null=True)
    tlu = models.FloatField(null=True)

    class Meta:
        db_table = "herds"


class Message(models.Model):
    IN, OUT = "IN", "OUT"
    SMS, USSD = "SMS", "USSD"
    QUEUED, SENT, DELIVERED, FAILED, RECEIVED = "QUEUED", "SENT", "DELIVERED", "FAILED", "RECEIVED"

    message_id = models.AutoField(primary_key=True)
    direction = models.CharField(max_length=3)
    channel = models.CharField(max_length=4, default=SMS)
    phone = models.CharField(max_length=16)
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id", null=True, blank=True
    )
    body = models.CharField(max_length=1000, null=True, blank=True)
    provider_msg_id = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=12, default=QUEUED)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "messages"
        indexes = [models.Index(fields=["phone", "created_at"], name="ix_msg_phone")]


class Alert(models.Model):
    """One live ALERTS row per subscriber.

    Drafted for each eligible subscriber of an ACT score, approved on the APEX Alert Approvals
    page (V_ALERT_QUEUE), then sent as one SMS. V_ALERT_QUEUE inner-joins subscribers, wards and
    risk_scores, so those foreign keys are required. The SMS link is message_id on this row.
    Status values the approvals page already filters and writes: PENDING_APPROVAL, APPROVED,
    REJECTED, SENT.
    """

    PENDING_APPROVAL, APPROVED, REJECTED, SENT = "PENDING_APPROVAL", "APPROVED", "REJECTED", "SENT"

    alert_id = models.AutoField(primary_key=True)
    score = models.ForeignKey(RiskScore, on_delete=models.DO_NOTHING, db_column="score_id")
    subscriber = models.ForeignKey(Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id")
    alert_type = models.ForeignKey(AlertType, on_delete=models.DO_NOTHING, db_column="alert_type")
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    message = models.ForeignKey(  # live ALERTS.MESSAGE_ID -> MESSAGES; null until the SMS is queued
        Message, on_delete=models.DO_NOTHING, db_column="message_id", null=True, blank=True,
        related_name="+",
    )
    hazard = models.CharField(max_length=30, null=True, blank=True)
    severity = models.CharField(max_length=16, null=True, blank=True)
    lang = models.CharField(max_length=2, null=True, blank=True, choices=LANGS)
    body = models.CharField(max_length=160, null=True, blank=True)  # live VARCHAR2(160)
    status = models.CharField(max_length=16, default=PENDING_APPROVAL)
    created_at = models.DateTimeField(default=timezone.now)
    approved_by = models.CharField(max_length=100, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "alerts"


class UssdSession(models.Model):
    """Written by Louisa's ussd/ service on every hop; the backend only reads it."""

    session_id = models.CharField(max_length=100, primary_key=True)
    subscriber = models.ForeignKey(
        Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id", null=True, blank=True
    )
    phone = models.CharField(max_length=16)
    last_text = models.CharField(max_length=200, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ussd_sessions"


class Report(models.Model):
    AUTO_ACCEPTED, NEEDS_REVIEW = "AUTO_ACCEPTED", "NEEDS_REVIEW"
    ACCEPTED, CORRECTED, REJECTED = "ACCEPTED", "CORRECTED", "REJECTED"
    USABLE = (AUTO_ACCEPTED, ACCEPTED, CORRECTED)
    SMS_AI, SMS_KEYWORD, SOURCE_USSD = "SMS_AI", "SMS_KEYWORD", "USSD"

    report_id = models.AutoField(primary_key=True)
    message = models.ForeignKey(
        Message, on_delete=models.DO_NOTHING, db_column="message_id", null=True, blank=True
    )
    ward = models.ForeignKey(
        Ward, on_delete=models.DO_NOTHING, db_column="ward_id", null=True, blank=True
    )
    report_type = models.CharField(max_length=20)
    report_count = models.FloatField(null=True, blank=True)
    unit = models.CharField(max_length=10, null=True, blank=True)
    payload = JSONColumn(null=True, blank=True)
    ai_confidence = models.FloatField(null=True, blank=True)
    source = models.CharField(max_length=12, default=SMS_AI)
    review_status = models.CharField(max_length=14, default=NEEDS_REVIEW)
    reviewed_by = models.ForeignKey(
        AppUser, on_delete=models.DO_NOTHING, db_column="reviewed_by", null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "reports"
        indexes = [models.Index(fields=["ward", "created_at"], name="ix_rep_ward")]


# ----- Dryers ------------------------------------------------------------------------------------


class CollectionPoint(models.Model):
    point_id = models.AutoField(primary_key=True)
    ward = models.ForeignKey(Ward, on_delete=models.DO_NOTHING, db_column="ward_id")
    point_name = models.CharField(max_length=100)
    lat = models.FloatField(null=True)
    lon = models.FloatField(null=True)
    kind = models.CharField(max_length=20, null=True)  # market | coop_store | school | depot

    class Meta:
        db_table = "collection_points"


class Dryer(models.Model):
    dryer_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=60, null=True)
    operator_phone = models.CharField(max_length=16, null=True, blank=True)
    tonnes_per_hour = models.FloatField(null=True)
    hours_per_day = models.FloatField(null=True)
    is_placeholder = YesNoField(default=True)  # demo dryers are labelled honestly

    class Meta:
        db_table = "dryers"


class Route(models.Model):
    PLANNED, NOTIFIED, DONE = "PLANNED", "NOTIFIED", "DONE"

    route_id = models.AutoField(primary_key=True)
    dryer = models.ForeignKey(Dryer, on_delete=models.DO_NOTHING, db_column="dryer_id")
    route_date = models.DateField()
    status = models.CharField(max_length=10, default=PLANNED)

    class Meta:
        db_table = "routes"


class RouteStop(models.Model):
    stop_id = models.AutoField(primary_key=True)
    route = models.ForeignKey(Route, on_delete=models.DO_NOTHING, db_column="route_id", related_name="stops")
    point = models.ForeignKey(CollectionPoint, on_delete=models.DO_NOTHING, db_column="point_id")
    stop_order = models.IntegerField()
    eta = models.DateTimeField(null=True)
    urgency = models.FloatField(null=True)
    planned_bags = models.FloatField(null=True)

    class Meta:
        db_table = "route_stops"


class Booking(models.Model):
    OFFERED, CONFIRMED, DRIED, NO_SHOW = "OFFERED", "CONFIRMED", "DRIED", "NO_SHOW"

    booking_id = models.AutoField(primary_key=True)
    stop = models.ForeignKey(RouteStop, on_delete=models.DO_NOTHING, db_column="stop_id", related_name="bookings")
    subscriber = models.ForeignKey(Subscriber, on_delete=models.DO_NOTHING, db_column="subscriber_id")
    bags = models.FloatField(null=True)
    fee_kes = models.FloatField(null=True)
    status = models.CharField(max_length=10, default=OFFERED)

    class Meta:
        db_table = "bookings"


# ----- Truth & logs ------------------------------------------------------------------------------


class Outcome(models.Model):
    outcome_id = models.AutoField(primary_key=True)
    alert = models.ForeignKey(Alert, on_delete=models.DO_NOTHING, db_column="alert_id", null=True, blank=True)
    score = models.ForeignKey(RiskScore, on_delete=models.DO_NOTHING, db_column="score_id", null=True, blank=True)
    outcome_type = models.CharField(max_length=30, null=True)
    hit = YesNoField(null=True)
    lead_days = models.FloatField(null=True)
    tonnes_dried = models.FloatField(null=True)
    observed_at = models.DateField(null=True)

    class Meta:
        db_table = "outcomes"


class JobRun(models.Model):
    """One row per §2.2 action call when the table exists.

    The live NgaoDB has no JOB_RUNS table. Local SQLite (and any database that has the table)
    still records runs. api.jobs.run_job keeps the HTTP contract without this table.
    """

    RUNNING, DONE, FAILED = "RUNNING", "DONE", "FAILED"

    job_id = models.AutoField(primary_key=True)
    job_name = models.CharField(max_length=30)
    params = JSONColumn(null=True, blank=True)
    status = models.CharField(max_length=10, default=RUNNING)
    summary = JSONColumn(null=True, blank=True)
    error_text = models.CharField(max_length=1000, null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "job_runs"
