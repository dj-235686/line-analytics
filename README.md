# line-analytics

Production-line analytics on camera-style event data, in Python and Postgres.

A simulated 4-station assembly line emits events the way an overhead vision
system would report them: a part started or finished at a station, with a
timestamp and a detection confidence. The events land in Postgres, and SQL and
Python turn them into cycle times, output, stoppages, utilisation and the
bottleneck, hour by hour.

The data is **deliberately messy**, because real shopfloor data is. The
interesting part of this project is not the dashboard; it is making sure the
numbers are right before anyone acts on them.

## Quick start

Requires Docker and Python 3.11+.

```bash
docker compose up -d                 # start Postgres
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python simulate.py                   # writes CSVs to data/ (seed 42, 3 days)
python load.py                       # loads them into Postgres
pytest -q                            # run the tests (no database needed)

python checks.py                     # data-quality checks, graded against truth
python anomalies.py                  # unusual cycle times, graded against truth
```

Run a query (create the `events_dedup` view with `03` first; `load.py` drops it):

```bash
docker exec -i line-analytics-db psql -U line -d line < sql/03_dedup.sql
docker exec -i line-analytics-db psql -U line -d line < sql/01_cycle_times.sql
```

On Windows PowerShell:
`Get-Content sql\01_cycle_times.sql | docker exec -i line-analytics-db psql -U line -d line`

## The line

```
S1 -> [buffer 6] -> S2 -> [buffer 4] -> S3 -> [buffer 6] -> S4 -> done
```

Each station has random cycle times and random breakdowns. Buffers are finite,
so a station can be **starved** (nothing to work on) or **blocked** (nowhere to
put a finished part). Because of that, the bottleneck is not fixed: S3 is the
designed bottleneck, but it is the busiest station in fewer than half of all
hours (see Findings).

The run starts on Saturday 24 October 2026 at 06:00 in Germany and crosses the
change from summer to winter time.

## Tables

| Table | What it holds |
|---|---|
| `stations` | Station order on the line and planned cycle times |
| `raw_events` | Exactly what the cameras uploaded. Never modified. |
| `events_dedup` (view) | `raw_events` with re-sent duplicates removed |
| `truth_states` | True state of each station over time (UTC) |
| `truth_outages` | When each camera was really offline (UTC) |

The two `truth_` tables would not exist in a real plant. They are here so every
query and check in this project can be verified against the right answer. They
are only ever used to grade a result, never to produce one.

## What is wrong with the data, on purpose

1. **Camera outages.** Some cameras go silent for 20 to 50 minutes while their
   station keeps working. In the data, an offline camera and a stopped station
   look identical.
2. **Duplicate events.** About 0.5% of events are sent twice, as edge devices
   do after reconnecting.
3. **Clock drift.** The camera on S2 gains about 1.5 seconds per hour.
4. **Daylight saving time.** Devices send local time with no time zone. On
   25 October 2026, 02:00 to 03:00 happens twice, so that hour appears to have
   double the output.
5. **Upload order.** Rows arrive shuffled, not in time order.

## Findings (seed 42, 3 days)

**An offline camera and a stopped line look identical - until you count parts.**
There are 123 silent periods longer than 10 minutes. Parts are numbered and
pass every station in order, so a station that really stopped resumes with the
next part number, while an offline camera resumes further on. `checks.py` finds
3 silences where 38, 25 and 23 parts went through unseen (and were recorded by
the other cameras). Those are exactly the 3 true camera outages; no real
stoppage was mislabelled.

**DST is not an edge case.** In naive local time the 02:00 hour on 25 October
shows 93 finished parts against a typical 52, four cycle times come out at
about -3,550 s, and 5 real stoppages disappear because the two 02:00 hours
interleave (`sql/04` finds 118 gaps, the UTC-corrected check finds 123).
`checks.to_utc()` resolves the ambiguous hour from part order, not from the
clock.

**The bottleneck moves.** S3 is the designed bottleneck, but it is the busiest
station in only 48% of hours; S2 in 27%, S4 in 20%, S1 in 6% (`sql/06`). On
26 October S2 was down 15% of the day, and S3 - the "bottleneck" - was starved
for 15%. A single averaged number hides this.

**Report the median; treat the tail as its own finding.** Median cycle times
sit on the design values (S1 49.8 / S2 53.7 / S3 56.2 / S4 51.5 s against
50 / 54 / 57 / 52). Means are 1.4-4.2 s higher at every station because a breakdown
in the middle of a part makes that one cycle very long. `anomalies.py` flags 73
such cycles; 96% of them really contained a breakdown, and every breakdown
longer than 2 minutes was caught.

**A drifting clock shows up as impossible handoffs.** 429 parts appear to start
at S3 before they finished at S2. The minimum handoff time between those two
cameras falls by 1.48 s per hour: CAM-S2 runs fast (the simulator uses 1.5).
The same drift stretches the time range past the end of the run, which is the
only "zero-output hour" in `sql/02`.

**Duplicates:** 150 re-sent events, 0, 0.4 or 1.1 s apart, removed in the
`events_dedup` view. `raw_events` is never changed, so what the cleaning did
can always be shown.

## Project structure

```
line-analytics/
├── docker-compose.yml          Postgres 16
├── simulate.py                 SimPy line simulation + messy camera feed
├── load.py                     CSV -> Postgres via COPY
├── checks.py                   duplicates, DST-safe UTC, camera outage vs
│                               stoppage, clock drift
├── anomalies.py                rolling median + MAD on cycle times
├── sql/
│   ├── schema.sql              tables, constraints, indexes
│   ├── 01_cycle_times.sql      cycle time per station: median, mean, vs design
│   ├── 02_hourly_output.sql    parts finished per hour, empty hours included
│   ├── 03_dedup.sql            view events_dedup
│   ├── 04_gaps.sql             silences > 10 min per station
│   ├── 04b_gaps_vs_truth.sql   grading: which gaps were camera outages
│   ├── 05_utilisation_truth.sql  reference: true time share per state, per day
│   └── 06_bottleneck_by_hour.sql busiest station per hour
├── tests/                      19 tests; run on simulator output, no database
└── .github/workflows/          CI: runs the tests on every push
```

## Limitations

- **The data is simulated.** Distributions and parameters are plausible, not
  measured. The value is in the pipeline and the checks, not in the numbers.
- **Part IDs are assumed readable** at every station (for example from a
  DataMatrix code), and assumed to be **sequential with first-in-first-out
  flow**. The outage check and the DST fix both rely on that. With random
  serial numbers, the order would have to come from the first station's
  sequence or the MES.
- **DST fix:** if a station is idle for a whole hour across the clock change,
  its ambiguous timestamps cannot be placed from its own data.
- **Clock drift:** detecting it (impossible handoffs) is reliable; the size
  estimate is rough - within 0.3 s/hour in 9 of 10 simulated seeds.
- **Anomaly threshold** (robust z > 5, not the textbook 3.5) was chosen by
  grading against the truth for this one run. On real data it would need
  re-checking against maintenance logs.
- **`sql/06` counts a breakdown in the middle of a part as busy time**, and a
  camera outage makes a station look idle.
- **No shift patterns or planned breaks** yet; the line runs continuously.

## Roadmap

- [x] Simulator, schema, loader, tests
- [x] SQL: cycle times, hourly output with gap filling, stoppages,
      utilisation, bottleneck by hour
- [x] Data quality: duplicates, DST-safe timestamps, clock drift,
      camera outage vs real stoppage
- [x] Anomaly detection on cycle times
- [x] Short write-up of findings
- [ ] Shift patterns and planned breaks
- [ ] Fuse with MES/PLC data (planned vs actual output)

## Note on tooling

AI assistance (Claude) was used for drafting the SQL. Every result was run locally and checked
against the simulator's truth tables; the numbers above come from those runs.
