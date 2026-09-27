"""
checks.py - data-quality checks on the camera events.

The SQL in sql/ shows the problems; these checks deal with them:
    duplicate_report / dedup   re-sent events (defect 2)
    to_utc                     naive local time -> UTC, incl. the DST hour (defect 4)
    camera_gap_vs_stoppage     offline camera vs stopped station (defect 1, the headline)

Every check works on a DataFrame with the raw_events columns, so the tests
can run on simulator output without a database.

Usage
    python checks.py        # run every check against Postgres and grade it
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

from load import connection_string

BERLIN = "Europe/Berlin"
KEY = ["station_id", "part_id", "event_type"]   # identifies one real event
EVENT_ORDER = {"start": 0, "end": 1}
MIN_GAP = pd.Timedelta("10min")                   # same threshold as sql/04


def fetch(sql: str) -> pd.DataFrame:
    """Run a query and return the result as a DataFrame."""
    with psycopg.connect(connection_string()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


def part_number(part_id: pd.Series) -> pd.Series:
    """'P001145' -> 1145."""
    return part_id.str[1:].astype(int)


# --- Duplicates (defect 2) --------------------------------------------------

def duplicate_report(events: pd.DataFrame) -> pd.DataFrame:
    """Every event sent more than once, and how far apart the copies are."""
    ts = events.groupby(KEY)["ts_local"]
    report = pd.DataFrame({
        "copies": ts.size(),
        "spread_s": (ts.max() - ts.min()).dt.total_seconds(),
    })
    return report[report["copies"] > 1].reset_index()


def dedup(events: pd.DataFrame) -> pd.DataFrame:
    """One row per event, keeping the earliest copy (same rule as sql/03)."""
    return events.sort_values([*KEY, "ts_local"]).drop_duplicates(KEY, keep="first")


# --- DST-safe timestamps (defect 4) -----------------------------------------

def to_utc(events: pd.DataFrame) -> pd.Series:
    """Naive Berlin wall-clock time -> UTC, resolving the repeated hour on 25 Oct.

    The clock cannot say which 02:30 is meant, so use something that can:
    order. Parts are numbered in the order S1 makes them and every station
    handles them first-in-first-out, so sorting a station's events by part
    number (start before end) gives their true order. In that order the wall
    clock goes backwards exactly once - at the fall-back. Ambiguous times
    before that step are summer time (CEST), after it winter time (CET).

    Limitation: if a station is idle for a whole hour across the change there
    is no backward step to see, and its ambiguous times cannot be placed.
    """
    if events.duplicated(KEY).any():
        raise ValueError("to_utc() needs one row per event - run dedup() first")

    ordered = events.assign(
        part_no=part_number(events["part_id"]),
        event_order=events["event_type"].map(EVENT_ORDER),
    ).sort_values(["station_id", "part_no", "event_order"])

    went_back = ordered.groupby("station_id")["ts_local"].diff() < pd.Timedelta(0)
    after_fallback = went_back.groupby(ordered["station_id"]).cumsum() > 0
    is_summer_time = ~after_fallback.reindex(events.index)

    return (events["ts_local"]
            .dt.tz_localize(BERLIN, ambiguous=is_summer_time.to_numpy())
            .dt.tz_convert("UTC"))


def cycle_times(events: pd.DataFrame, ts: str = "ts_local") -> pd.Series:
    """Seconds from start to end, one value per (station, part) with both events."""
    wide = events.groupby(KEY)[ts].min().unstack("event_type")
    return (wide["end"] - wide["start"]).dt.total_seconds().dropna()


# --- Camera outage vs real stoppage (defect 1, the headline) ----------------

def camera_gap_vs_stoppage(events: pd.DataFrame, min_gap: pd.Timedelta = MIN_GAP) -> pd.DataFrame:
    """Find every silent period at a station and say whether the camera or the line stopped.

    A silent period is more than `min_gap` between consecutive finished parts
    (as in sql/04). Parts cannot skip a station, and they pass every station
    in number order. So look at the part numbers either side of the silence:
      - next number (P1200 -> P1201): nothing went through; the station stopped.
      - numbers skipped (P1200 -> P1238): 37 parts went through that the camera
        never saw. The line kept running; the camera was offline.
    `seen_elsewhere` counts how many skipped parts the other cameras did
    record - the evidence that they really passed through.
    """
    events = dedup(events)
    events = events.assign(ts_utc=to_utc(events), part_no=part_number(events["part_id"]))

    ends = events[events["event_type"] == "end"].sort_values(["station_id", "part_no"])
    by_station = ends.groupby("station_id")
    ends = ends.assign(prev_ts=by_station["ts_utc"].shift(),
                       prev_no=by_station["part_no"].shift())
    gaps = ends[ends["ts_utc"] - ends["prev_ts"] > min_gap]

    seen = events.groupby("station_id")["part_id"].agg(set)
    seen_by_others = {s: set().union(*seen.drop(s)) for s in seen.index}

    rows = []
    for g in gaps.itertuples():
        skipped = [f"P{n:06d}" for n in range(int(g.prev_no) + 1, g.part_no)]
        rows.append({
            "station_id": g.station_id,
            "gap_start_utc": g.prev_ts,
            "gap_end_utc": g.ts_utc,
            "gap_min": round((g.ts_utc - g.prev_ts).total_seconds() / 60, 1),
            "skipped_parts": len(skipped),
            "seen_elsewhere": sum(p in seen_by_others[g.station_id] for p in skipped),
            "label": "camera outage" if skipped else "real stoppage",
        })
    columns = ["station_id", "gap_start_utc", "gap_end_utc", "gap_min",
               "skipped_parts", "seen_elsewhere", "label"]
    return pd.DataFrame(rows, columns=columns)


# --- Grading (the truth tables exist only in the simulator) ------------------

def _overlaps(table, station, start, end, start_col, end_col) -> bool:
    return bool(((table["station_id"] == station)
                 & (table[start_col] < end) & (table[end_col] > start)).any())


def grade_against_truth(gaps: pd.DataFrame, truth_outages: pd.DataFrame):
    """GRADING ONLY. Which true outages were caught, and which camera labels were wrong."""
    cameras = gaps[gaps["label"] == "camera outage"]
    found = truth_outages.assign(found=[
        _overlaps(cameras, o.station_id, o.start_utc, o.end_utc, "gap_start_utc", "gap_end_utc")
        for o in truth_outages.itertuples()
    ])
    false_alarm = np.array([
        not _overlaps(truth_outages, g.station_id, g.gap_start_utc, g.gap_end_utc,
                      "start_utc", "end_utc")
        for g in cameras.itertuples()
    ], dtype=bool)
    return found, cameras.loc[false_alarm]


def main() -> None:
    raw = fetch("SELECT station_id, part_id, event_type, ts_local FROM raw_events")
    raw["ts_local"] = pd.to_datetime(raw["ts_local"])
    truth = fetch("SELECT station_id, start_utc, end_utc FROM truth_outages")
    for col in ["start_utc", "end_utc"]:
        truth[col] = pd.to_datetime(truth[col], utc=True)

    dups = duplicate_report(raw)
    spreads = dups["spread_s"].round(1).value_counts().sort_index()
    print(f"Duplicates : {(dups['copies'] - 1).sum()} extra rows; copies apart by "
          + ", ".join(f"{s:g} s ({n}x)" for s, n in spreads.items()))

    events = dedup(raw)
    events = events.assign(ts_utc=to_utc(events))
    print(f"DST        : negative cycle times {(cycle_times(events, 'ts_local') < 0).sum()} "
          f"with naive local time, {(cycle_times(events, 'ts_utc') < 0).sum()} after to_utc()")

    gaps = camera_gap_vs_stoppage(raw)
    n_camera = (gaps["label"] == "camera outage").sum()
    print(f"Gaps       : {len(gaps)} silent periods > {MIN_GAP.seconds // 60} min - "
          f"{n_camera} camera outage, {len(gaps) - n_camera} real stoppage")

    print("\nCamera outages:")
    print(gaps[gaps["label"] == "camera outage"].to_string(index=False))

    # The repeated hour, 02:00-03:00 Berlin on 25 Oct 2026 = 00:00-02:00 UTC
    dst_start = pd.Timestamp("2026-10-25 00:00", tz="UTC")
    dst_end = pd.Timestamp("2026-10-25 02:00", tz="UTC")
    around_dst = (gaps["gap_start_utc"] < dst_end) & (gaps["gap_end_utc"] > dst_start)
    print("\nGaps touching the repeated DST hour (sql/04 got these wrong):")
    print(gaps[around_dst].to_string(index=False))

    found, false_alarms = grade_against_truth(gaps, truth)
    print(f"\nGRADING vs truth_outages: found {found['found'].sum()} of {len(found)}, "
          f"false alarms {len(false_alarms)}")


if __name__ == "__main__":
    main()