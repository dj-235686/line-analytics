"""
anomalies.py - flag unusual cycle times with a rolling median and MAD.

A breakdown in the middle of a part makes that one cycle far longer than
normal. Mean and standard deviation get dragged around by exactly those
outliers, so this uses robust statistics instead:
    rolling median   the typical cycle time around this part
    MAD              median absolute deviation - a spread outliers cannot inflate
    robust z         0.6745 * (cycle - median) / MAD
Only slow cycles are flagged: a breakdown can only make a cycle longer.
The window counts parts, not minutes, and runs per station.

Threshold: the textbook cut-off is 3.5. Graded against the simulator's truth
(seed 42), only about half of those flags contained a breakdown - the rest were
slow but normal cycles. At 5.0, 96% contain a breakdown and every breakdown
longer than 2 minutes is still caught.

Usage
    python anomalies.py      # flag anomalies in the Postgres data and grade them
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from checks import dedup, fetch, part_number, to_utc

WINDOW = 61        # parts, about an hour; centred on the part being judged
THRESHOLD = 5.0    # robust z; see the module docstring for why not 3.5


def cycle_table(events: pd.DataFrame) -> pd.DataFrame:
    """One row per (station, part) with start/end in UTC and the cycle time."""
    events = dedup(events)
    events = events.assign(ts_utc=to_utc(events))
    wide = (events.pivot_table(index=["station_id", "part_id"], columns="event_type",
                               values="ts_utc", aggfunc="min")
                  .dropna()
                  .reset_index())
    return pd.DataFrame({
        "station_id": wide["station_id"],
        "part_id": wide["part_id"],
        "start_utc": wide["start"],
        "end_utc": wide["end"],
        "cycle_s": (wide["end"] - wide["start"]).dt.total_seconds(),
    })


def _mad(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x))))


def flag_anomalies(cycles: pd.DataFrame, window: int = WINDOW,
                   threshold: float = THRESHOLD) -> pd.DataFrame:
    """Add median_s, mad_s, robust_z and anomaly to a cycle table."""
    cycles = (cycles.assign(part_no=part_number(cycles["part_id"]))
                    .sort_values(["station_id", "part_no"]))
    rolling = cycles.groupby("station_id")["cycle_s"].rolling(
        window, center=True, min_periods=window // 2)
    median = rolling.median().droplevel(0)
    mad = rolling.apply(_mad, raw=True).droplevel(0)
    robust_z = 0.6745 * (cycles["cycle_s"] - median) / mad
    return cycles.assign(median_s=median, mad_s=mad, robust_z=robust_z,
                         anomaly=robust_z > threshold)


def grade_against_truth(flagged: pd.DataFrame, truth_states: pd.DataFrame):
    """GRADING ONLY. Was each true breakdown flagged, and did each flag contain one?

    Returns (breakdowns, precision). `breakdowns` has one row per true
    breakdown that a complete cycle overlapped (a breakdown during a camera
    outage has no cycle to judge), with its duration and whether it was caught.
    """
    downs = truth_states[truth_states["state"] == "down"]
    contains_breakdown = np.zeros(len(flagged), dtype=bool)
    rows = []
    for d in downs.itertuples():
        overlap = ((flagged["station_id"] == d.station_id)
                   & (flagged["start_utc"] < d.end_utc)
                   & (flagged["end_utc"] > d.start_utc)).to_numpy()
        if overlap.any():
            contains_breakdown |= overlap
            rows.append({
                "station_id": d.station_id,
                "start_utc": d.start_utc,
                "duration_s": (d.end_utc - d.start_utc).total_seconds(),
                "caught": bool(flagged["anomaly"].to_numpy()[overlap].any()),
            })
    is_flag = flagged["anomaly"].to_numpy()
    precision = (is_flag & contains_breakdown).sum() / max(is_flag.sum(), 1)
    return pd.DataFrame(rows), float(precision)


def main() -> None:
    raw = fetch("SELECT station_id, part_id, event_type, ts_local FROM raw_events")
    raw["ts_local"] = pd.to_datetime(raw["ts_local"])
    truth = fetch("SELECT station_id, state, start_utc, end_utc FROM truth_states")
    for col in ["start_utc", "end_utc"]:
        truth[col] = pd.to_datetime(truth[col], utc=True)

    flagged = flag_anomalies(cycle_table(raw))
    summary = flagged.groupby("station_id").agg(
        cycles=("cycle_s", "size"),
        anomalies=("anomaly", "sum"),
        median_s=("cycle_s", "median"),
        longest_s=("cycle_s", "max"),
    )
    print("Anomalous cycle times per station:")
    print(summary.round(1).to_string())

    print("\nFive most extreme:")
    cols = ["station_id", "part_id", "start_utc", "cycle_s", "median_s", "robust_z"]
    print(flagged.nlargest(5, "robust_z")[cols].round({"cycle_s": 1, "median_s": 1, "robust_z": 1})
          .to_string(index=False))

    breakdowns, precision = grade_against_truth(flagged, truth)
    long_ones = breakdowns[breakdowns["duration_s"] >= 120]
    missed = sorted(breakdowns.loc[~breakdowns["caught"], "duration_s"].round().astype(int))
    print(f"\nGRADING vs truth_states: {precision:.0%} of {int(flagged['anomaly'].sum())} flags "
          f"contained a breakdown; caught {breakdowns['caught'].sum()} of {len(breakdowns)} "
          f"breakdowns ({long_ones['caught'].sum()} of {len(long_ones)} longer than 2 min); "
          f"missed ones lasted {missed} s")


if __name__ == "__main__":
    main()
