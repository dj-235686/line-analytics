"""
load.py - load the simulator's CSV files into Postgres.

Recreates all tables from sql/schema.sql, then bulk-loads with COPY
(much faster than inserting row by row).

Connection settings come from environment variables, with defaults that
match docker-compose.yml:
    PGHOST=localhost PGPORT=5432 PGUSER=line PGPASSWORD=line PGDATABASE=line

Usage
    python load.py
    python load.py --data data
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).parent

TABLES = {
    # table name     : (csv file, columns in CSV order)
    "raw_events":    ("camera_events.csv",
                      ["camera_id", "station_id", "part_id", "event_type", "ts_local", "confidence"]),
    "truth_states":  ("truth_states.csv", ["station_id", "state", "start_utc", "end_utc"]),
    "truth_outages": ("truth_outages.csv", ["station_id", "start_utc", "end_utc"]),
}


def connection_string() -> str:
    return (
        f"host={os.getenv('PGHOST', 'localhost')} "
        f"port={os.getenv('PGPORT', '5432')} "
        f"user={os.getenv('PGUSER', 'line')} "
        f"password={os.getenv('PGPASSWORD', 'line')} "
        f"dbname={os.getenv('PGDATABASE', 'line')}"
    )


def copy_csv(cur: psycopg.Cursor, table: str, path: Path, columns: list[str]) -> None:
    sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true)"
    with path.open("rb") as f, cur.copy(sql) as copy:
        while chunk := f.read(1 << 16):
            copy.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load simulator output into Postgres")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()

    missing = [f for f, _ in TABLES.values() if not (args.data / f).exists()]
    if missing:
        raise SystemExit(f"Missing {missing} in {args.data}. Run `python simulate.py` first.")

    schema = (ROOT / "sql" / "schema.sql").read_text()

    with psycopg.connect(connection_string()) as conn, conn.cursor() as cur:
        cur.execute(schema)
        for table, (filename, columns) in TABLES.items():
            copy_csv(cur, table, args.data / filename, columns)
        cur.execute("ANALYZE")  # refresh planner statistics after a bulk load

        print("Loaded:")
        for table in ["stations", *TABLES]:
            cur.execute(f"SELECT count(*) FROM {table}")
            print(f"  {table:<14} {cur.fetchone()[0]:>8,} rows")


if __name__ == "__main__":
    main()
