"""Tests for checks.py. They run on simulator output, so no database is needed."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checks  # noqa: E402
import simulate  # noqa: E402

# 06:00 Berlin on 24 Oct; a 1-day run crosses the DST change on 25 Oct
START_UTC = pd.Timestamp("2026-10-24 04:00", tz="UTC")


@pytest.fixture(scope="module")
def run():
    rng = np.random.default_rng(1)
    events, _ = simulate.run_line(days=1.0, rng=rng)
    outages = simulate.pick_outages(1.0, rng)
    camera = simulate.make_camera_feed(events, outages, START_UTC, rng)
    return events, outages, camera


def test_duplicate_report_counts_every_extra_row(run):
    *_, camera = run
    report = checks.duplicate_report(camera)
    assert (report["copies"] - 1).sum() == camera.duplicated(checks.KEY).sum() > 0
    assert report["spread_s"].between(0, 1.2).all()   # simulator jitter is 0 to 1.1 s


def test_dedup_leaves_one_row_per_event(run):
    *_, camera = run
    clean = checks.dedup(camera)
    assert not clean.duplicated(checks.KEY).any()
    assert len(clean) == len(camera) - camera.duplicated(checks.KEY).sum()


def test_to_utc_rejects_duplicates(run):
    *_, camera = run
    with pytest.raises(ValueError):
        checks.to_utc(camera)


def test_to_utc_recovers_true_time_through_dst(run):
    events, _, camera = run
    clean = checks.dedup(camera)
    clean = clean.assign(ts_utc=checks.to_utc(clean))
    truth = events.assign(
        true_utc=(START_UTC + pd.to_timedelta(events["t"], unit="s")).dt.round("ms"))
    merged = clean.merge(truth, on=checks.KEY)
    merged = merged[merged["station_id"] != "S2"]   # S2's clock drifts; separate problem

    repeated = merged["ts_local"].between("2026-10-25 02:00", "2026-10-25 02:59:59.999")
    assert merged.loc[repeated, "true_utc"].dt.hour.nunique() == 2   # both passes present
    assert (merged["ts_utc"] - merged["true_utc"]).abs().max() <= pd.Timedelta("1ms")


def test_to_utc_removes_negative_cycle_times(run):
    *_, camera = run
    clean = checks.dedup(camera)
    clean = clean.assign(ts_utc=checks.to_utc(clean))
    assert (checks.cycle_times(clean, "ts_local") < 0).any()   # the DST bug is there...
    assert (checks.cycle_times(clean, "ts_utc") > 0).all()     # ...and gone after to_utc


def test_camera_gap_vs_stoppage_finds_every_outage(run):
    _, outages, camera = run
    truth = pd.DataFrame({
        "station_id": outages["station_id"],
        "start_utc": START_UTC + pd.to_timedelta(outages["t_start"], unit="s"),
        "end_utc": START_UTC + pd.to_timedelta(outages["t_end"], unit="s"),
    })
    gaps = checks.camera_gap_vs_stoppage(camera)
    found, false_alarms = checks.grade_against_truth(gaps, truth)
    assert found["found"].all()
    assert false_alarms.empty


def test_no_outage_means_no_camera_label():
    rng = np.random.default_rng(2)
    events, _ = simulate.run_line(days=1.0, rng=rng)
    no_outages = pd.DataFrame(columns=["station_id", "t_start", "t_end"])
    camera = simulate.make_camera_feed(events, no_outages, START_UTC, rng)
    gaps = checks.camera_gap_vs_stoppage(camera)
    assert len(gaps) > 0                              # real stoppages happen...
    assert (gaps["label"] == "real stoppage").all()   # ...and none is blamed on the camera

def seed42_camera():
    """The feed simulate.py writes by default: seed 42, 3 days."""
    rng = np.random.default_rng(42)
    events, _ = simulate.run_line(days=3.0, rng=rng)
    outages = simulate.pick_outages(3.0, rng)
    return simulate.make_camera_feed(events, outages, START_UTC, rng)


def test_clock_drift_finds_the_fast_camera():
    pairs, cameras = checks.clock_drift(seed42_camera())
    impossible = pairs.set_index("pair")["impossible_handoffs"]
    assert impossible["S2->S3"] > 0                      # S2's clock says parts left late
    assert impossible[["S1->S2", "S3->S4"]].eq(0).all()
    assert cameras["CAM-S2"] == pytest.approx(simulate.S2_CLOCK_DRIFT_S_PER_HOUR, abs=0.3)
    assert cameras.drop("CAM-S2").abs().max() < 0.3


def test_no_drift_means_no_impossible_handoffs(monkeypatch):
    monkeypatch.setattr(simulate, "S2_CLOCK_DRIFT_S_PER_HOUR", 0.0)
    pairs, cameras = checks.clock_drift(seed42_camera())
    assert (pairs["impossible_handoffs"] == 0).all()
    assert cameras.abs().max() < 0.3
