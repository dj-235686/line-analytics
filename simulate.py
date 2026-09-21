"""
simulate.py - simulate a 4-station serial production line and emit
camera-style events, the way an overhead vision system would report them.

The line
    S1 -> [buffer] -> S2 -> [buffer] -> S3 -> [buffer] -> S4 -> done

    * S1 always has raw material (never starved).
    * Every station has random processing times and random breakdowns.
    * Buffers are finite, so a station can be BLOCKED (buffer after it full)
      or STARVED (buffer before it empty). That is what makes the
      bottleneck move around over time.

Outputs in ./data
    camera_events.csv  what the cameras send. Messy on purpose (see below).
    truth_states.csv   true state of every station over time, in UTC.
    truth_outages.csv  when each camera was really offline, in UTC.

The two truth files do not exist in a real factory. They exist here so
you can prove that your SQL and data-quality checks give the right answer.

What is deliberately wrong in camera_events.csv
    1. Camera outages   - some windows where a camera sent nothing,
                          although the station kept working.
    2. Duplicates       - edge devices re-send some events after reconnecting.
    3. Clock drift      - camera on S2 has a clock that runs slowly fast.
    4. DST trap         - timestamps are local wall-clock time with no zone.
                          The run crosses 25 Oct 2026, when 02:00-03:00
                          happens twice in Germany.
    5. Upload order     - rows arrive shuffled, not in time order.

Usage
    python simulate.py                 # 3 days, seed 42
    python simulate.py --days 5 --seed 7
"""

from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import simpy

BERLIN = ZoneInfo("Europe/Berlin")

# Saturday 24 Oct 2026, 06:00 local. A 3-day run crosses the DST change.
START_LOCAL = datetime(2026, 10, 24, 6, 0, tzinfo=BERLIN)

S2_CLOCK_DRIFT_S_PER_HOUR = 1.5   # camera clock on S2 gains 1.5 s every hour
DUPLICATE_FRACTION = 0.005        # 0.5 % of events are sent twice


@dataclass(frozen=True)
class StationConfig:
    name: str
    mean_cycle_s: float   # average processing time per part
    cv: float             # variation: std / mean
    mtbf_s: float         # mean *busy* time between breakdowns
    mttr_s: float         # mean time to repair


STATIONS = [
    StationConfig("S1", mean_cycle_s=50, cv=0.10, mtbf_s=4.0 * 3600, mttr_s=10 * 60),
    StationConfig("S2", mean_cycle_s=54, cv=0.12, mtbf_s=3.0 * 3600, mttr_s=15 * 60),
    StationConfig("S3", mean_cycle_s=57, cv=0.15, mtbf_s=5.0 * 3600, mttr_s=20 * 60),
    StationConfig("S4", mean_cycle_s=52, cv=0.10, mtbf_s=2.5 * 3600, mttr_s=12 * 60),
]
BUFFER_CAPACITY = [6, 4, 6]  # between S1-S2, S2-S3, S3-S4


def lognormal_params(mean: float, cv: float) -> tuple[float, float]:
    """Return (mu, sigma) so a lognormal has the given mean and CV."""
    sigma2 = np.log(1 + cv**2)
    return np.log(mean) - sigma2 / 2, np.sqrt(sigma2)


class Station:
    """One workstation. Records every event and every state change."""

    def __init__(self, env, cfg, inbound, outbound, rng, events, states, part_ids):
        self.env, self.cfg, self.rng = env, cfg, rng
        self.inbound, self.outbound = inbound, outbound
        self.events, self.states = events, states
        self.part_ids = part_ids
        self.mu, self.sigma = lognormal_params(cfg.mean_cycle_s, cfg.cv)
        self.state, self.since = None, 0.0
        env.process(self.run())

    def set_state(self, new: str) -> None:
        if new == self.state:
            return
        if self.state is not None and self.env.now > self.since:
            self.states.append((self.cfg.name, self.state, self.since, self.env.now))
        self.state, self.since = new, self.env.now

    def flush(self) -> None:
        """Close the last open state when the simulation ends."""
        if self.state is not None and self.env.now > self.since:
            self.states.append((self.cfg.name, self.state, self.since, self.env.now))

    def run(self):
        busy_until_failure = self.rng.exponential(self.cfg.mtbf_s)
        while True:
            # 1. Get a part (wait here = starved)
            self.set_state("starved")
            if self.inbound is None:
                part_id = f"P{next(self.part_ids):06d}"
            else:
                part_id = yield self.inbound.get()

            # 2. Process it, possibly breaking down part-way through
            self.set_state("running")
            self.events.append((self.cfg.name, part_id, "start", self.env.now))
            remaining = self.rng.lognormal(self.mu, self.sigma)
            while remaining > 0:
                if busy_until_failure <= remaining:
                    yield self.env.timeout(busy_until_failure)
                    remaining -= busy_until_failure
                    self.set_state("down")
                    yield self.env.timeout(self.rng.exponential(self.cfg.mttr_s))
                    busy_until_failure = self.rng.exponential(self.cfg.mtbf_s)
                    self.set_state("running")
                else:
                    yield self.env.timeout(remaining)
                    busy_until_failure -= remaining
                    remaining = 0
            self.events.append((self.cfg.name, part_id, "end", self.env.now))

            # 3. Hand it on (wait here = blocked)
            if self.outbound is not None:
                self.set_state("blocked")
                yield self.outbound.put(part_id)


def run_line(days: float, rng: np.random.Generator):
    """Run the simulation. Returns (events, states) with times in sim seconds."""
    env = simpy.Environment()
    buffers = [simpy.Store(env, capacity=c) for c in BUFFER_CAPACITY]
    events, states = [], []
    part_ids = itertools.count(1)

    stations = []
    for i, cfg in enumerate(STATIONS):
        inbound = buffers[i - 1] if i > 0 else None
        outbound = buffers[i] if i < len(buffers) else None
        stations.append(Station(env, cfg, inbound, outbound, rng, events, states, part_ids))

    env.run(until=days * 24 * 3600)
    for s in stations:
        s.flush()

    events = pd.DataFrame(events, columns=["station_id", "part_id", "event_type", "t"])
    states = pd.DataFrame(states, columns=["station_id", "state", "t_start", "t_end"])
    return events, states


def pick_outages(days: float, rng: np.random.Generator) -> pd.DataFrame:
    """Choose a few camera outage windows (sim seconds)."""
    total = days * 24 * 3600
    rows = []
    for station in ["S3", "S3", "S2"]:
        duration = rng.uniform(20, 50) * 60
        start = rng.uniform(0.1 * total, 0.9 * total - duration)
        rows.append((station, start, start + duration))
    return pd.DataFrame(rows, columns=["station_id", "t_start", "t_end"])


def make_camera_feed(events, outages, start_utc, rng) -> pd.DataFrame:
    """Turn clean events into what a real camera system would upload."""
    df = events.copy()
    df["ts_utc"] = start_utc + pd.to_timedelta(df["t"], unit="s")

    # 1. Camera outages: drop events that happened while the camera was off
    lost = np.zeros(len(df), dtype=bool)
    for o in outages.itertuples():
        lost |= (df["station_id"] == o.station_id) & (df["t"] >= o.t_start) & (df["t"] < o.t_end)
    df = df[~lost].copy()

    # 2. Clock drift on S2's camera
    hours = df["t"] / 3600
    drift = np.where(df["station_id"] == "S2", hours * S2_CLOCK_DRIFT_S_PER_HOUR, 0.0)
    df["ts_device"] = df["ts_utc"] + pd.to_timedelta(drift, unit="s")

    # 3. Detection confidence: mostly high, occasionally low
    df["confidence"] = rng.beta(18, 1.5, len(df)).round(3)

    # 4. Duplicates: some events re-sent, same or slightly later timestamp
    dups = df.sample(frac=DUPLICATE_FRACTION, random_state=int(rng.integers(1e9))).copy()
    jitter = rng.choice([0.0, 0.0, 0.4, 1.1], size=len(dups))
    dups["ts_device"] += pd.to_timedelta(jitter, unit="s")
    df = pd.concat([df, dups], ignore_index=True)

    # 5. Device sends local wall-clock time with no timezone (DST trap)
    df["ts_local"] = (
        df["ts_device"].dt.tz_convert(BERLIN).dt.tz_localize(None).dt.round("ms")
    )
    df["camera_id"] = "CAM-" + df["station_id"]

    # 6. Rows arrive in upload order, not time order
    df = df.sample(frac=1, random_state=int(rng.integers(1e9)))

    cols = ["camera_id", "station_id", "part_id", "event_type", "ts_local", "confidence"]
    return df[cols]


def to_utc(start_utc, seconds: pd.Series) -> pd.Series:
    """Sim seconds -> uniform ISO strings with milliseconds and explicit +00:00."""
    ts = (start_utc + pd.to_timedelta(seconds, unit="s")).dt.round("ms")
    return ts.dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3] + "+00:00"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--days", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    start_utc = pd.Timestamp(START_LOCAL.astimezone(timezone.utc))

    events, states = run_line(args.days, rng)
    outages = pick_outages(args.days, rng)
    camera = make_camera_feed(events, outages, start_utc, rng)

    args.out.mkdir(parents=True, exist_ok=True)
    camera.to_csv(args.out / "camera_events.csv", index=False)

    truth_states = pd.DataFrame({
        "station_id": states["station_id"],
        "state": states["state"],
        "start_utc": to_utc(start_utc, states["t_start"]),
        "end_utc": to_utc(start_utc, states["t_end"]),
    })
    truth_states.to_csv(args.out / "truth_states.csv", index=False)

    truth_outages = pd.DataFrame({
        "station_id": outages["station_id"],
        "start_utc": to_utc(start_utc, outages["t_start"]),
        "end_utc": to_utc(start_utc, outages["t_end"]),
    })
    truth_outages.to_csv(args.out / "truth_outages.csv", index=False)

    finished = (events["station_id"] == "S4") & (events["event_type"] == "end")
    print(f"Simulated {args.days:g} days from {START_LOCAL:%Y-%m-%d %H:%M} Berlin time")
    print(f"  parts finished      : {finished.sum():,}")
    print(f"  camera events sent  : {len(camera):,}  (clean: {len(events):,})")
    print(f"  state intervals     : {len(truth_states):,}")
    print(f"  camera outages      : {len(truth_outages)}")
    print(f"Files written to {args.out.resolve()}")


if __name__ == "__main__":
    main()
