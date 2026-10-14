import datetime as dt
from types import SimpleNamespace

from django.test import SimpleTestCase

from ngao.models import Forecast, RiskScore
from ngao_core.timeutil import today_eat
from scoring import heuristics as h
from tests.base import NgaoTestCase


def inputs(forecasts=None, features=None, as_of=dt.date(2026, 10, 15)):
    return h.WardInputs(as_of=as_of, features=features or [], forecasts=forecasts or {})


class RuleTests(SimpleTestCase):
    def test_dry_window_agreeing_models_act(self):
        dry = {d: 0.0 for d in range(1, 8)}
        signal = h.score_a1(inputs({"GFS": dry, "ECMWF": dry}))
        self.assertIsNone(signal.abstain_reason)
        self.assertGreater(signal.probability, 0.9)
        self.assertEqual(h.decide(signal, 0.7, 0.5), "ACT")
        self.assertEqual(signal.hints["day_offset"], 1)

    def test_models_disagree_abstain(self):
        dry = {d: 0.0 for d in range(1, 8)}
        wet = {d: 10.0 for d in range(1, 8)}
        signal = h.score_a1(inputs({"GFS": dry, "ECMWF": wet}))
        self.assertIn("GFS/ECMWF disagree", signal.abstain_reason)
        self.assertEqual(h.decide(signal, 0.7, 0.5), "ABSTAIN")

    def test_no_forecast_is_no_data_not_abstain(self):
        self.assertTrue(h.score_a1(inputs()).no_data)
        self.assertTrue(h.score_a2(inputs()).no_data)

    def test_heavy_rain(self):
        signal = h.score_a2(inputs({"GFS": {1: 30.0, 2: 5.0}, "ECMWF": {1: 26.0, 2: 3.0}}))
        self.assertGreater(signal.probability, 0.8)
        calm = h.score_a2(inputs({"GFS": {1: 2.0, 2: 1.0}, "ECMWF": {1: 1.0, 2: 0.0}}))
        self.assertIsNone(h.decide(calm, 0.6, 0.4))

    def test_feature_row_fallback_for_gfs(self):
        row = SimpleNamespace(feature_date=dt.date(2026, 10, 15), gfs_rain_d1_mm=0.0, gfs_rain_d2_mm=0.0,
                              gfs_rain_d3_mm=0.0, gfs_rain_d5_mm=0.0, gfs_rain_d7_mm=0.0, ecmwf_agreement=None)
        series = h.rain_series(inputs(features=[row]))
        self.assertEqual(set(series["GFS"]), {1, 2, 3, 4, 5, 6, 7})

    def test_rvf_rule(self):
        def row(days_ago, ndvi, anomaly):
            return SimpleNamespace(feature_date=dt.date(2006, 12, 1) - dt.timedelta(days=days_ago), ndvi=ndvi,
                                   ndvi_anom=None, rain_anom_30d=anomaly)
        rows = [row(120, 0.24, 0.0), row(100, 0.25, 0.0), row(20, 0.55, 120.0), row(5, 0.58, 128.0)]
        signal = h.score_b3(inputs(features=rows, as_of=dt.date(2006, 12, 1)))
        self.assertGreater(signal.probability, 0.95)
        quiet = [row(120, 0.24, 0.0), row(5, 0.25, 0.0)]
        self.assertLess(h.score_b3(inputs(features=quiet, as_of=dt.date(2006, 12, 1))).probability, 0.1)


class ScoringServiceTests(NgaoTestCase):
    def decision(self, ward_id, code):
        row = RiskScore.objects.filter(ward_id=ward_id, alert_type_id=code, score_date=today_eat(), is_replay=False).first()
        return row and row.decision

    def test_demo_scenarios(self):
        summary = self.score_today()
        self.assertEqual(self.decision(12, "A1"), "ACT")        # Moiben dry window
        self.assertEqual(self.decision(13, "A2"), "ACT")        # Kapseret heavy rain
        self.assertEqual(self.decision(14, "A1"), "ABSTAIN")    # Ainabkoi: models disagree
        self.assertEqual(self.decision(16, "A2"), "WATCH")      # Tembelio borderline
        self.assertIsNone(self.decision(15, "A1"))              # Kimumu: nothing to say
        self.assertEqual(summary["act"], 2)
        abstain = RiskScore.objects.get(ward_id=14, alert_type_id="A1")
        self.assertIn("disagree", abstain.abstain_reason)

    def test_forecast_not_yet_available_is_ignored(self):
        Forecast.objects.filter(ward_id=12).update(available_date=today_eat() + dt.timedelta(days=1))
        self.score_today()
        # Falls back to the GFS columns on the feature row (available today), single-model.
        row = RiskScore.objects.get(ward_id=12, alert_type_id="A1", score_date=today_eat())
        self.assertLess(row.probability, 0.99)

    def test_never_overwrites_another_model(self):
        RiskScore.objects.create(ward_id=12, score_date=today_eat(), alert_type_id="A1", probability=0.2,
                                 confidence_pct=20, decision="WATCH", model_version="a1-xgb-2026-10-14-abc")
        summary = self.score_today()
        self.assertGreaterEqual(summary["kept_existing"], 1)
        self.assertEqual(RiskScore.objects.get(ward_id=12, alert_type_id="A1").model_version, "a1-xgb-2026-10-14-abc")
        self.job("score", {"overwrite": True})
        self.assertEqual(RiskScore.objects.get(ward_id=12, alert_type_id="A1").decision, "ACT")

    def test_rescoring_is_idempotent(self):
        self.score_today()
        count = RiskScore.objects.count()
        self.score_today()
        self.assertEqual(RiskScore.objects.count(), count)

    def test_dry_run_writes_nothing(self):
        self.job("score", {"dry_run": True})
        self.assertFalse(RiskScore.objects.exists())

    def test_unknown_alert_type(self):
        response = self.api.post("/ngao/v1/jobs/score", {"alert_types": ["Z9"]}, format="json")
        self.assertEqual(response.status_code, 400)


class ReplayTests(NgaoTestCase):
    def test_garissa_replay_warns_before_index_case(self):
        summary = self.job("replay", {"scenario": "garissa_rvf_2006", "from": "2006-09-01", "to": "2007-01-15"})["summary"]
        self.assertTrue(summary["first_act_date"] < "2006-11-30")
        self.assertGreater(summary["lead_days_before_index_case"], 0)
        self.assertFalse(RiskScore.objects.filter(is_replay=False, ward__county="Garissa").exists())
        self.assertFalse(RiskScore.objects.filter(is_replay=True, decision="ACT", score_date__lt="2006-10-01").exists())

    def test_unknown_scenario(self):
        response = self.api.post("/ngao/v1/jobs/replay", {"scenario": "atlantis"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_bad_dates(self):
        response = self.api.post("/ngao/v1/jobs/replay", {"from": "2007-01-01", "to": "2006-01-01"}, format="json")
        self.assertEqual(response.status_code, 400)
