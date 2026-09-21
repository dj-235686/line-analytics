"""Tests for simulate.py. Run with: pytest -q"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simulate  # noqa: E402

START_UTC = pd.Timestamp("2026-10-24 04:00", tz="UTC")


@pytest.fixture(scope="module")
def run():
    rng = np.random.default_rng(1)
    events, states = simulate.run_line(days=1.0, rng=rng)
    outages = simulate.pick_outages(1.0, rng)
    camera = simulate.make_camera_feed(events, outages, START_UTC, rng)
    return events, states, outages, camera


def test_same_seed_gives_same_result():
    a, _ = simulate.run_line(0.2, np.random.default_rng(5))
    b, _ = simulate.run_line(0.2, np.random.default_rng(5))
    pd.testing.assert_frame_equal(a, b)


def test_every_part_ends_after_it_starts(run):
    events, *_ = run
    wide = events.pivot_table(index=["station_id", "part_id"], columns="event_type", values="t")
    wide = wide.dropna()
    assert (wide["end"] > wide["start"]).all()


def test_parts_visit_stations_in_order(run):
    events, *_ = run
    ends = events[events.event_type == "end"].pivot(index="part_id", columns="station_id", values="t")
    done = ends.dropna()
    assert (done["S1"] < done["S2"]).all()
    assert (done["S2"] < done["S3"]).all()
    assert (done["S3"] < done["S4"]).all()


def test_state_intervals_do_not_overlap(run):
    _, states, *_ = run
    for _, g in states.sort_values("t_start").groupby("station_id"):
        assert (g["t_start"].iloc[1:].values >= g["t_end"].iloc[:-1].values - 1e-9).all()


def test_no_camera_events_during_outages(run):
    events, _, outages, camera = run
    # Rebuild approximate true time for each camera row via the clean events
    clean = events.assign(key=events.station_id + events.part_id + events.event_type)
    cam_keys = set(camera.station_id + camera.part_id + camera.event_type)
    for o in outages.itertuples():
        inside = clean[(clean.station_id == o.station_id)
                       & (clean.t >= o.t_start) & (clean.t < o.t_end)]
        assert not set(inside.key) & cam_keys


def test_feed_contains_duplicates(run):
    *_, camera = run
    assert camera.duplicated(["station_id", "part_id", "event_type"]).sum() > 0


def test_feed_timestamps_are_naive_local(run):
    *_, camera = run
    assert camera["ts_local"].dt.tz is None
