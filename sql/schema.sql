-- schema.sql - tables for the line-analytics project.
-- load.py runs this file on every load, so it drops and recreates everything.

DROP TABLE IF EXISTS raw_events;
DROP TABLE IF EXISTS truth_states;
DROP TABLE IF EXISTS truth_outages;
DROP TABLE IF EXISTS stations;

-- Reference data: the order of stations on the line.
-- Needed to know which station is upstream / downstream of which.
CREATE TABLE stations (
    station_id      text PRIMARY KEY,
    position        int  NOT NULL UNIQUE,   -- 1 = first station on the line
    design_cycle_s  real NOT NULL           -- planned cycle time (what the ERP would say)
);

INSERT INTO stations (station_id, position, design_cycle_s) VALUES
    ('S1', 1, 50),
    ('S2', 2, 54),
    ('S3', 3, 57),
    ('S4', 4, 52);

-- Exactly what the cameras uploaded. Never edited; cleaning happens in views.
-- ts_local is deliberately `timestamp WITHOUT time zone`: that is what the
-- devices send, and it is ambiguous during the DST change on 25 Oct 2026.
CREATE TABLE raw_events (
    id          bigserial PRIMARY KEY,
    camera_id   text      NOT NULL,
    station_id  text      NOT NULL REFERENCES stations,
    part_id     text      NOT NULL,
    event_type  text      NOT NULL CHECK (event_type IN ('start', 'end')),
    ts_local    timestamp NOT NULL,
    confidence  real      CHECK (confidence BETWEEN 0 AND 1),
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX raw_events_station_ts ON raw_events (station_id, ts_local);
CREATE INDEX raw_events_part       ON raw_events (part_id);

-- Ground truth from the simulator. Does NOT exist in a real plant.
-- Use it only to check whether your queries and checks are right.
CREATE TABLE truth_states (
    station_id text        NOT NULL REFERENCES stations,
    state      text        NOT NULL CHECK (state IN ('running', 'starved', 'blocked', 'down')),
    start_utc  timestamptz NOT NULL,
    end_utc    timestamptz NOT NULL,
    CHECK (end_utc > start_utc)
);

CREATE INDEX truth_states_station_start ON truth_states (station_id, start_utc);

CREATE TABLE truth_outages (
    station_id text        NOT NULL REFERENCES stations,
    start_utc  timestamptz NOT NULL,
    end_utc    timestamptz NOT NULL
);
