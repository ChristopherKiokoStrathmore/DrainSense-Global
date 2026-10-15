"""Seasonal outlook: the v3 artifact, live sources, the job, S1 alerts and the read endpoints."""
import datetime as dt
import io
import tempfile
from unittest import mock

import h5py
import numpy as np
from django.test import SimpleTestCase, override_settings

from ngao.models import Alert, RiskScore, SeasonalBacktest, SeasonalModelRun, SeasonalOutlook, Subscriber
from ngao_core.timeutil import today_eat
from seasonal import sources
from seasonal.indices import BOXES, box_mean, derive
from seasonal.model import RegionLocator, load_artifact, predict
from seasonal.service import category, default_year
from tests.base import NgaoTestCase

# August-September 2026 box means from NOAA ERSSTv5 (strong El Nino, warm western Indian Ocean).
SST_2026 = {"wio": 28.24, "eio": 28.673, "nino34": 29.483}
# A neutral year (2021-like): La Nina, cool Nino 3.4.
SST_NEUTRAL = {"wio": 27.3, "eio": 28.6, "nino34": 25.9}


class ArtifactTests(SimpleTestCase):
    def test_reproduces_the_one_pager(self):
        """Rebuilt from open data, the recommended configuration matches ngao_seasonal_model_onepager.pdf."""
        m = load_artifact()["variants"]["lit6"]["targets"]["y25"]["metrics"]
        self.assertAlmostEqual(m["auc"], 0.775, delta=0.01)
        self.assertAlmostEqual(m["f1"], 0.662, delta=0.01)
        self.assertAlmostEqual(m["brier"], 0.122, delta=0.005)
        yter = load_artifact()["variants"]["lit6"]["targets"]["yter"]["metrics"]
        self.assertAlmostEqual(yter["auc"], 0.736, delta=0.01)

    def test_prediction_matches_training_export(self):
        """predict() on the stored 2023 indices gives the backtest's high 2023 probability."""
        artifact = load_artifact()
        spec = artifact["variants"]["sst4"]["targets"]["y25"]
        features = artifact["indices"]["2023"]
        probs = [predict(spec, features, r, 6) for r in range(6)]
        self.assertTrue(all(0.5 < p < 1 for p in probs), probs)

    def test_region_locator_and_categories(self):
        locate = RegionLocator(load_artifact()["regions"])
        self.assertIn(locate(0.52, 35.27), range(6))  # Eldoret
        self.assertEqual(category(0.62, 0.33), "HIGH")
        self.assertEqual(category(0.40, 0.33), "MEDIUM")
        self.assertEqual(category(0.10, 0.33), "LOW")

    def test_default_year(self):
        self.assertEqual(default_year(dt.date(2026, 10, 15)), 2026)
        self.assertEqual(default_year(dt.date(2026, 7, 1)), 2025)


def _nc(path_or_buffer, variables, coords):
    with h5py.File(path_or_buffer, "w") as handle:
        for name, values in coords.items():
            handle[name] = values
        for name, (values, attrs) in variables.items():
            ds = handle.create_dataset(name, data=values)
            for key, value in attrs.items():
                ds.attrs[key] = value


class SourceTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(NGAO_SEASONAL_CACHE_DIR=self.tmp.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_box_mean_weights_and_skips_land(self):
        lats, lons = np.array([-10.0, 0.0, 10.0]), np.array([50.0, 60.0, 70.0])
        field = np.array([[1.0, 1.0, np.nan], [3.0, 3.0, 3.0], [1.0, 1.0, 1.0]])
        value = box_mean(field, lats, lons, BOXES["wio"])
        w = np.cos(np.deg2rad(10))
        self.assertAlmostEqual(value, (3 * 3 + 5 * w) / (3 + 5 * w))  # five 1.0 cells at +-10 deg, NaN skipped
        self.assertEqual(derive({"wio": 28, "eio": 27, "slp_wio": 1010, "slp_eio": 1012})["slp_grad"], 2)

    def test_ersst_month_reads_ncei_file(self):
        lats, lons = np.arange(-88, 89, 2.0), np.arange(0, 360, 2.0)
        sst = np.full((1, 1, 89, 180), 25.0, dtype="float32")
        sst[0, 0, 44, 30] = -999.0  # land/missing cell
        buffer = io.BytesIO()
        _nc(buffer, {"sst": (sst, {"_FillValue": np.float32(-999.0), "scale_factor": np.float32(1.0),
                                   "add_offset": np.float32(0.0)})}, {"lat": lats, "lon": lons})
        response = mock.Mock(status_code=200, content=buffer.getvalue())
        response.raise_for_status = lambda: None
        session = mock.Mock(get=mock.Mock(return_value=response))
        field, got_lats, got_lons = sources.ersst_month(2026, 9, session=session)
        self.assertTrue(np.isnan(field[44, 30]))
        self.assertEqual(field[0, 0], 25.0)
        session.get.assert_called_once()
        sources.ersst_month(2026, 9, session=session)  # cached: no second download
        session.get.assert_called_once()

    def test_unpublished_month_is_unavailable(self):
        session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=404)))
        with self.assertRaises(sources.SourceUnavailable):
            sources.ersst_month(2031, 9, session=session)

    def test_era5_series_and_reference_scale(self):
        lats, lons = np.arange(10, -10.5, -1.0), np.arange(50, 110.5, 1.0)
        times = np.array([np.datetime64(f"2026-{m:02d}-01", "s").astype("int64") for m in (6, 7, 8, 9)])
        msl = np.stack([np.full((len(lats), len(lons)), 101000.0 + 100 * i) for i in range(4)])
        path = f"{self.tmp.name}/msl.nc"
        _nc(path, {"msl": (msl, {})}, {"latitude": lats, "longitude": lons, "valid_time": times})
        series = sources.era5_box_series(path, "msl", ("slp_wio", "slp_eio"))
        self.assertAlmostEqual(series[2026]["slp_wio"], 101200.0)  # Jul-Sep only
        reference = [100990.0, 101010.0] * 15
        mapped = sources.to_reference_scale(101010.0, reference, {"mean": 1010.0, "std": 0.5})
        self.assertAlmostEqual(mapped, 1010.0 + 0.5 * (10 / np.std(reference, ddof=1)), places=6)

    @override_settings(NGAO_CDSAPI_KEY=None)
    def test_era5_needs_a_key(self):
        self.assertFalse(sources.era5_configured())
        with self.assertRaises(sources.SourceUnavailable):
            sources.atmos_box_means(2026, {})


@mock.patch("seasonal.service.sst_box_means", return_value=SST_2026)
class OutlookJobTests(NgaoTestCase):
    def issue(self, body=None, expect=202):
        return self.job("seasonal-outlook", body or {"year": 2026}, expect=expect)["summary"]

    def test_issue_writes_every_ward_and_the_model_card(self, _sst):
        summary = self.issue()
        self.assertEqual((summary["status"], summary["variant"], summary["season"]), ("ISSUED", "sst4", "OND 2026"))
        self.assertEqual(summary["model_version"], "ngao-seasonal-v3-sst4")
        self.assertEqual(SeasonalOutlook.objects.filter(season="OND 2026").count(), summary["wards"])
        self.assertEqual(summary["high"] + summary["medium"] + summary["low"], summary["wards"])
        self.assertGreater(summary["high"], 0)  # strong El Nino year
        self.assertEqual(summary["features"]["dmi"], round(SST_2026["wio"] - SST_2026["eio"], 4))
        # Model card: 2 variants x 2 targets, 10 backtest years each.
        self.assertEqual(SeasonalModelRun.objects.count(), 4)
        self.assertEqual(SeasonalBacktest.objects.count(), 40)
        self.issue()  # re-issuing updates in place
        self.assertEqual(SeasonalOutlook.objects.filter(season="OND 2026").count(), summary["wards"])
        self.assertEqual(SeasonalModelRun.objects.count(), 4)

    def test_high_wards_flow_into_s1_alerts_for_approval(self, _sst):
        with mock.patch("seasonal.service.default_year", return_value=2026):
            summary = self.issue()
        self.assertEqual(summary["s1_alert_type"], "present")
        act = RiskScore.objects.filter(alert_type_id="S1", decision="ACT", score_date=today_eat())
        self.assertEqual(act.count(), summary["s1_act"])
        self.assertGreater(act.count(), 0)
        drafted = self.job("send-alerts")["summary"]
        self.assertGreater(drafted["pending_approval"], 0)
        alert = Alert.objects.filter(alert_type_id="S1").first()
        self.assertEqual(alert.status, "PENDING_APPROVAL")
        self.assertIn("Okt-Des" if alert.subscriber.preferred_lang == "sw" else "Oct-Dec", alert.body)

    def test_past_season_never_alerts(self, _sst):
        with mock.patch("seasonal.service.default_year", return_value=2026):
            self.issue()
            current = RiskScore.objects.filter(alert_type_id="S1").count()
            self.assertGreater(current, 0)
            summary = self.issue({"year": 2023})
        self.assertIn("not the current season", summary["s1_alert_type"])
        self.assertEqual(summary["s1_act"], 0)
        self.assertEqual(RiskScore.objects.filter(alert_type_id="S1").count(), current)  # 2026 rows kept

    def test_neutral_year_is_low(self, sst):
        sst.return_value = SST_NEUTRAL
        summary = self.issue({"year": 2021})
        self.assertEqual(summary["high"], 0)
        self.assertEqual(summary["s1_act"], 0)

    def test_dry_run_writes_nothing(self, _sst):
        summary = self.issue({"year": 2026, "dry_run": True})
        self.assertEqual(summary["status"], "DRY_RUN")
        self.assertEqual(SeasonalOutlook.objects.count(), 0)
        self.assertEqual(SeasonalModelRun.objects.count(), 0)

    def test_waiting_for_data(self, sst):
        sst.side_effect = sources.SourceUnavailable("ERSSTv5 2027-09 is not published yet")
        summary = self.issue({"year": 2027})
        self.assertEqual(summary["status"], "waiting_for_data")
        self.assertEqual(SeasonalOutlook.objects.count(), 0)

    def test_era5_without_key_is_a_validation_error(self, _sst):
        response = self.api.post("/ngao/v1/jobs/seasonal-outlook", {"year": 2026, "source": "era5"}, format="json")
        self.assertEqual(response.status_code, 400)

    @override_settings(NGAO_CDSAPI_KEY="test-key")
    def test_auto_falls_back_to_sst_when_era5_fails(self, _sst):
        with mock.patch("seasonal.service.era5_configured", return_value=True), \
                mock.patch("seasonal.service.atmos_box_means",
                           side_effect=sources.SourceUnavailable("ERA5 July-September 2026 is not complete yet")):
            summary = self.issue()
        self.assertEqual(summary["variant"], "sst4")
        self.assertIn("SST-only variant used", summary["notes"][0])

    def test_full_model_with_era5(self, _sst):
        atmos = {"u850_ceio": -1.2, "slp_wio": 1011.6, "slp_eio": 1009.9}
        with mock.patch("seasonal.service.era5_configured", return_value=True), \
                mock.patch("seasonal.service.atmos_box_means", return_value=atmos):
            summary = self.issue()
        self.assertEqual((summary["variant"], summary["model_version"]), ("lit6", "ngao-seasonal-v3-lit6"))
        self.assertEqual(set(summary["features"]), {"wio", "eio", "dmi", "nino34", "u850_ceio", "slp_grad"})

    def test_read_endpoints(self, _sst):
        self.issue()
        items = self.api.get("/ngao/v1/seasonal/outlook").json()["items"]
        self.assertEqual(len(items), SeasonalOutlook.objects.count())
        self.assertEqual(items[0]["season"], "OND 2026")
        self.assertGreaterEqual(items[0]["probability"], items[-1]["probability"])
        one = self.api.get("/ngao/v1/seasonal/outlook", {"ward_id": 13}).json()["items"]
        self.assertEqual([i["ward_id"] for i in one], [13])
        self.assertEqual(self.anon.get("/ngao/v1/seasonal/outlook").status_code, 401)

        card = self.anon.get("/ngao/v1/public/seasonal-model-card").json()
        self.assertEqual(len(card["items"]), 4)
        self.assertIn("KMD", card["caveats"])

    def test_ussd_line(self, _sst):
        self.issue()
        Subscriber.objects.filter(phone="+254700000104").update(preferred_lang="en", language="en")
        line = self.ussd.get("/ngao/v1/ussd/seasonal-outlook", {"phone": "+254700000104"}).json()
        self.assertEqual(line["season"], "OND 2026")
        self.assertRegex(line["text"], r"^Kapseret: \d+% chance Oct-Dec 2026 rain is far above normal\.$")
        unknown = self.ussd.get("/ngao/v1/ussd/seasonal-outlook", {"phone": "+254799999999"}).json()
        self.assertEqual((unknown["registered"], unknown["probability"]), (False, None))

    def test_scheduler_issues_once_in_october(self, _sst):
        from seasonal.service import issue_if_due

        self.assertIsNone(issue_if_due(dt.date(2026, 9, 30)))
        self.assertEqual(issue_if_due(dt.date(2026, 10, 15))["status"], "ISSUED")
        self.assertIsNone(issue_if_due(dt.date(2026, 10, 16)))
