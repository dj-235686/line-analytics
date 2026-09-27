-- 02_hourly_output.sql
-- Question: How many parts does the line finish per hour (S4 'end' events),
--           including hours in which nothing was finished?
-- Concepts: CTE, generate_series, LEFT JOIN, date_trunc, count(DISTINCT ...).
-- Buckets use the naive local time the cameras send, so the DST fall-back
-- hour (02:00 on 25 Oct) shows roughly double the output.

WITH bounds AS (
    SELECT date_trunc('hour', min(ts_local)) AS first_hour,
           date_trunc('hour', max(ts_local)) AS last_hour
    FROM raw_events
),
hours AS (
    -- Every hour of the run, whether or not anything happened in it
    SELECT generate_series(first_hour, last_hour, interval '1 hour') AS hour_local
    FROM bounds
),
finished AS (
    SELECT date_trunc('hour', ts_local) AS hour_local,
           part_id
    FROM raw_events
    WHERE station_id = 'S4'
      AND event_type = 'end'
)
SELECT h.hour_local,
       count(DISTINCT f.part_id) AS parts_finished  -- count(*) would return 1, not 0, for an empty hour
FROM hours h
LEFT JOIN finished f USING (hour_local)
GROUP BY h.hour_local
ORDER BY h.hour_local;