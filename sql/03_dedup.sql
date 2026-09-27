-- 03_dedup.sql
-- Question: What do the camera events look like with re-sent duplicates removed?
-- Concepts: CREATE VIEW, CTE, ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...).
-- raw_events is never modified; this view is the cleaned layer on top of it.
-- A duplicate is a second event for the same (station, part, event_type).
-- The copy with the earliest timestamp is kept (same choice as min() in 01).

DROP VIEW IF EXISTS events_dedup;

CREATE VIEW events_dedup AS
WITH ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY station_id, part_id, event_type
               ORDER BY ts_local, id
           ) AS rn
    FROM raw_events
)
SELECT id, camera_id, station_id, part_id, event_type,
       ts_local, confidence, ingested_at
FROM ranked
WHERE rn = 1;