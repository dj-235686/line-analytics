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

python simulate.py                   # writes CSVs to data/
python load.py                       # loads them into Postgres
pytest -q                            # run the tests
```

Open a SQL prompt:

```bash
docker exec -it line-analytics-db psql -U line -d line
```

## The line

```
S1 -> [buffer 6] -> S2 -> [buffer 4] -> S3 -> [buffer 6] -> S4 -> done
```

Each station has random cycle times and random breakdowns. Buffers are finite,
so a station can be **starved** (nothing to work on) or **blocked** (nowhere to
put a finished part). Because of that, the bottleneck is not fixed: S3 limits
the line most of the time, but in a typical 3-day run another station is the
busiest in between a third and a half of all hours.

The run starts on Saturday 24 October 2026 at 06:00 in Germany and crosses the
change from summer to winter time.

## Tables

| Table | What it holds |
|---|---|
| `stations` | Station order on the line and planned cycle times |
| `raw_events` | Exactly what the cameras uploaded. Never modified. |
| `truth_states` | True state of each station over time (UTC) |
| `truth_outages` | When each camera was really offline (UTC) |

The two `truth_` tables would not exist in a real plant. They are here so every
query and check in this project can be verified against the right answer.

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

## Project structure

```
line-analytics/
├── docker-compose.yml      Postgres 16
├── simulate.py             SimPy line simulation + messy camera feed
├── load.py                 CSV -> Postgres via COPY
├── sql/
│   └── schema.sql          tables, constraints, indexes
├── tests/
│   └── test_simulate.py    simulator correctness
└── .github/workflows/      CI: runs the tests on every push
```

## Limitations

- **The data is simulated.** Distributions and parameters are plausible, not
  measured. The value is in the pipeline and the checks, not in the numbers.
- **Part IDs are assumed readable** at every station (for example from a
  DataMatrix code). An overhead camera without that would need to infer part
  identity from sequence.
- **No shift patterns or planned breaks** yet; the line runs continuously.

## Roadmap

- [x] Simulator, schema, loader, tests
- [ ] SQL: cycle times, hourly output with gap filling, stoppages,
      utilisation, bottleneck by hour
- [ ] Data quality: duplicates, DST-safe timestamps, clock drift,
      camera outage vs real stoppage
- [ ] Anomaly detection on cycle times
- [ ] Short write-up of findings

## Note on tooling

AI assistance was used for scaffolding and review. The SQL analysis is written
by hand.
