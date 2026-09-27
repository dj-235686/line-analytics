-- 05_utilisation_truth.sql
-- REFERENCE ONLY: reads the simulator's truth_states, which a real plant does not have.
-- Question: What share of each day was each station really running, down,
--           starved or blocked?
-- Concepts: generate_series over days, AT TIME ZONE, interval-overlap join,
--           least/greatest, EXTRACT(EPOCH ...), sum(...) FILTER, ROLLUP.
-- An interval cannot be divided by an interval, so every duration is turned
-- into seconds first. Days are Berlin calendar days, so 25 Oct lasts 25 hours.

WITH bounds AS (
    SELECT date_trunc('day', min(start_utc) AT TIME ZONE 'Europe/Berlin') AS first_day,
           date_trunc('day', max(end_utc)   AT TIME ZONE 'Europe/Berlin') AS last_day
    FROM truth_states
),
days AS (
    -- Local midnights converted to UTC, so each day gets its true length
    SELECT d::date                                         AS day_local,
           d                      AT TIME ZONE 'Europe/Berlin' AS day_start_utc,
           (d + interval '1 day') AT TIME ZONE 'Europe/Berlin' AS day_end_utc
    FROM bounds,
         generate_series(first_day, last_day, interval '1 day') AS d
),
clipped AS (
    -- Cut every state interval at midnight (same overlap logic as 04b)
    SELECT t.station_id,
           dy.day_local,
           t.state,
           EXTRACT(EPOCH FROM least(t.end_utc, dy.day_end_utc)
                            - greatest(t.start_utc, dy.day_start_utc)) AS seconds
    FROM truth_states t
    JOIN days dy
      ON t.start_utc < dy.day_end_utc
     AND t.end_utc   > dy.day_start_utc
)
SELECT station_id,
       day_local,                                   -- empty row = whole run (ROLLUP)
       round(sum(seconds) / 3600, 1)                AS hours,
       round(100 * coalesce(sum(seconds) FILTER (WHERE state = 'running'), 0) / sum(seconds), 1) AS running_pct,
       round(100 * coalesce(sum(seconds) FILTER (WHERE state = 'down'),    0) / sum(seconds), 1) AS down_pct,
       round(100 * coalesce(sum(seconds) FILTER (WHERE state = 'starved'), 0) / sum(seconds), 1) AS starved_pct,
       round(100 * coalesce(sum(seconds) FILTER (WHERE state = 'blocked'), 0) / sum(seconds), 1) AS blocked_pct
FROM clipped
GROUP BY station_id, ROLLUP (day_local)
ORDER BY station_id, day_local NULLS LAST;