"""SYNTHETIC demo dataset for local development and tests (manage.py seed_demo).

Nothing here is real: phone numbers are fake (+2547000...), subscribers are is_demo = 'Y', dryers
are is_placeholder = 'Y', and the weather is shaped to exercise every decision path:
  Moiben          clear 3-day dry window          -> A1 ACT
  Kapseret        > 20 mm on days 1-2             -> A2 ACT
  Ainabkoi/Olare  GFS dry, ECMWF wet              -> A1 ABSTAIN ("GFS/ECMWF disagree")
  Tembelio        borderline heavy rain           -> A2 WATCH
  Garissa 2006    NDVI and rain anomaly rise from October -> B3/C2 replay ACT before 30 Nov
The real data (31 Uasin Gishu + 30 Garissa wards, CHIRPS/ERA5/MODIS/GloFAS) lives in the ADB.
"""
import datetime as dt

from django.db import transaction
from django.utils import timezone

from ngao.models import (CollectionPoint, Dryer, Forecast, Subscriber, Ward, WardFeatureDaily)

UG_WARDS = [
    # ward_id, name, lat, lon, gfs d1..d7, ecmwf d1..d7
    (12, "Moiben", 0.82, 35.38, [0.2, 0.0, 0.0, 0.4, 0.0, 1.0, 3.0], [0.0, 0.3, 0.0, 0.0, 0.8, 2.0, 4.0]),
    (13, "Kapseret", 0.47, 35.23, [28.0, 34.0, 9.0, 4.0, 2.0, 6.0, 1.0], [25.0, 31.0, 12.0, 3.0, 5.0, 2.0, 0.0]),
    (14, "Ainabkoi/Olare", 0.30, 35.47, [0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 1.0], [9.0, 14.0, 6.0, 8.0, 5.0, 7.0, 3.0]),
    (15, "Kimumu", 0.56, 35.29, [6.0, 5.0, 4.0, 8.0, 3.0, 9.0, 2.0], [7.0, 4.0, 6.0, 5.0, 6.0, 4.0, 5.0]),
    (16, "Tembelio", 0.61, 35.50, [14.0, 20.0, 3.0, 2.0, 1.0, 4.0, 6.0], [13.0, 19.0, 4.0, 3.0, 2.0, 2.0, 5.0]),
]
GARISSA_WARDS = [(41, "Township", -0.45, 39.65, 1.0), (42, "Iftin", -0.42, 39.62, 0.9),
                 (43, "Galbet", -0.47, 39.66, 0.8), (44, "Balambala", 0.05, 39.30, 0.55)]

POINTS = [(12, "Moiben market", 0.82, 35.38, "market"), (13, "Kapseret coop store", 0.47, 35.23, "coop_store"),
          (14, "Ainabkoi market", 0.30, 35.47, "market"), (16, "Tembelio depot", 0.61, 35.50, "depot")]
DRYERS = [("Mobile dryer 1 (placeholder)", "+254700000901", 2.0, 8.0),
          ("Mobile dryer 2 (placeholder)", "+254700000902", 1.5, 8.0)]

SUBSCRIBERS = [
    ("+254700000101", 12, "farmer", "sw"), ("+254700000102", 12, "farmer", "en"),
    ("+254700000103", 12, "both", "sw"), ("+254700000104", 13, "farmer", "sw"),
    ("+254700000105", 13, "farmer", "en"), ("+254700000106", 14, "farmer", "sw"),
    ("+254700000107", 16, "farmer", "sw"), ("+254700000108", 15, "farmer", "en"),
    ("+254700000201", 41, "herder", "sw"), ("+254700000202", 42, "herder", "sw"),
    ("+254700000203", 41, "health_promoter", "en"),
]


def _garissa_curve(day, strength):
    """NDVI 0.24 -> 0.58 and 30-day rain anomaly 0 -> +128 mm between mid Sep and late Nov 2006."""
    start, peak = dt.date(2006, 9, 15), dt.date(2006, 11, 20)
    f = 0.0 if day <= start else min(1.0, (day - start).days / (peak - start).days)
    if day > dt.date(2007, 1, 10):
        f = max(0.0, f - (day - dt.date(2007, 1, 10)).days / 40)
    return 0.24 + 0.34 * f * strength, 128.0 * f * strength - 5.0


@transaction.atomic
def build(today=None):
    today = today or timezone.localdate()
    counts = {}
    for ward_id, name, lat, lon, *_ in UG_WARDS:
        Ward.objects.update_or_create(ward_id=ward_id, defaults=dict(county="Uasin Gishu", ward_name=name,
                                                                     centroid_lat=lat, centroid_lon=lon))
    for ward_id, name, lat, lon, _ in GARISSA_WARDS:
        Ward.objects.update_or_create(ward_id=ward_id, defaults=dict(county="Garissa", ward_name=name,
                                                                     centroid_lat=lat, centroid_lon=lon))
    counts["wards"] = len(UG_WARDS) + len(GARISSA_WARDS)

    features = 0
    for ward_id, _name, _lat, _lon, gfs, ecmwf in UG_WARDS:
        for back in range(30, -1, -1):
            day = today - dt.timedelta(days=back)
            values = dict(available_date=day, rain_1d_mm=3.0, rain_7d_mm=25.0, rain_14d_mm=60.0, rain_30d_mm=110.0,
                          rain_anom_30d=10.0, soil_moist_top=0.28, ndvi=0.55, wet_bag_reports_3d=0.0)
            if back == 0:
                values.update(gfs_rain_d1_mm=gfs[0], gfs_rain_d2_mm=gfs[1], gfs_rain_d3_mm=gfs[2],
                              gfs_rain_d5_mm=gfs[4], gfs_rain_d7_mm=gfs[6])
            WardFeatureDaily.objects.update_or_create(ward_id=ward_id, feature_date=day, defaults=values)
            features += 1
        for source, series in (("GFS", gfs), ("ECMWF", ecmwf)):
            for lead, mm in enumerate(series, start=1):
                Forecast.objects.update_or_create(ward_id=ward_id, source=source, issue_date=today, lead_day=lead,
                                                  defaults=dict(available_date=today, rain_mm=mm))
    day = dt.date(2006, 6, 1)
    while day <= dt.date(2007, 1, 31):
        for ward_id, _name, _lat, _lon, strength in GARISSA_WARDS:
            ndvi, anomaly = _garissa_curve(day, strength)
            WardFeatureDaily.objects.update_or_create(
                ward_id=ward_id, feature_date=day,
                defaults=dict(available_date=day + dt.timedelta(days=5), ndvi=round(ndvi, 3),
                              rain_anom_30d=round(anomaly, 1), rain_30d_mm=round(30 + anomaly, 1)))
            features += 1
        day += dt.timedelta(days=4)
    counts["ward_features_daily"] = features

    for ward_id, name, lat, lon, kind in POINTS:
        CollectionPoint.objects.get_or_create(ward_id=ward_id, point_name=name,
                                              defaults=dict(lat=lat, lon=lon, kind=kind))
    for name, phone, tph, hours in DRYERS:
        Dryer.objects.get_or_create(name=name, defaults=dict(operator_phone=phone, tonnes_per_hour=tph,
                                                             hours_per_day=hours, is_placeholder=True))
    for phone, ward_id, role, lang in SUBSCRIBERS:
        Subscriber.objects.update_or_create(phone=phone, defaults=dict(
            ward_id=ward_id, role=role, preferred_lang=lang, language=lang, status=Subscriber.ACTIVE,
            consent_at=timezone.now(), is_demo=True))
    counts.update(collection_points=len(POINTS), dryers=len(DRYERS), subscribers=len(SUBSCRIBERS))
    return counts
