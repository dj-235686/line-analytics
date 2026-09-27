"""Tests for anomalies.py. They run on simulator output, so no database is needed."""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anomalies  # noqa: E402
import simulate  # noqa: E402

START_UTC = pd.Timestamp("2026-10-24 04:00", tz="UTC")
NO_OUTAGES = pd.DataFrame(columns=["station_id", "t_start", "t_end"])


def camera_and_truth(seed):
    rng = np.random.default_rng(seed)
    events, states = simulate.run_line(days=1.0, rng=rng)
    camera = simulate.make_camera_feed(events, NO_OUTAGES, START_UTC, rng)
    truth = states.assign(
        start_utc=START_UTC + pd.to_timedelta(states["t_start"], unit="s"),
        end_utc=START_UTC + pd.to_timedelta(states["t_end"], unit="s"),
    )
    return camera, truth


def test_one_huge_cycle_does_not_move_the_median():
    cycles = pd.DataFrame({
        "station_id": "S1",
        "part_id": [f"P{n:06d}" for n in range(1, 102)],
        "cycle_s": 50 + np.random.default_rng(0).normal(0, 2, 101),
    })
    cycles.loc[50, "cycle_s"] = 3000          # one breakdown in the middle
    flagged = anomalies.flag_anomalies(cycles)
    assert flagged["anomaly"].sum() == 1
    assert flagged.loc[50, "anomaly"]
    assert abs(flagged.loc[50, "median_s"] - 50) < 2   # a mean would have jumped by ~50 s


def test_every_long_breakdown_is_caught():
    camera, truth = camera_and_truth(seed=1)
    flagged = anomalies.flag_anomalies(anomalies.cycle_table(camera))
    breakdowns, precision = anomalies.grade_against_truth(flagged, truth)
    assert breakdowns.loc[breakdowns["duration_s"] >= 120, "caught"].all()
    assert precision >= 0.8


def test_line_without_breakdowns_is_almost_never_flagged(monkeypatch):
    no_breakdowns = [dataclasses.replace(s, mtbf_s=1e12) for s in simulate.STATIONS]
    monkeypatch.setattr(simulate, "STATIONS", no_breakdowns)
    camera, _ = camera_and_truth(seed=1)
    flagged = anomalies.flag_anomalies(anomalies.cycle_table(camera))
    assert flagged["anomaly"].mean() < 0.001
