-- 01_cycle_times.sql
-- Question: How long does each station take per part, and how does that
--           compare with the design cycle time?
-- Concepts: CTE, min(...) FILTER (WHERE ...), EXTRACT(EPOCH FROM ...),
--           percentile_cont(0.5) WITHIN GROUP (ORDER BY ...), JOIN.
-- Reads raw_events directly: min() absorbs duplicate events, and parts
-- with a missing start or end (camera outage, end of run) are dropped.

WITH part_times AS (
    -- One row per (station, part): pivot the start/end rows into columns
    SELECT station_id,
           part_id,
           min(ts_local) FILTER (WHERE event_type = 'start') AS start_ts,
           min(ts_local) FILTER (WHERE event_type = 'end')   AS end_ts
    FROM raw_events
    GROUP BY station_id, part_id
),
cycle_times AS (
    -- Interval -> seconds, keep only complete start/end pairs
    SELECT station_id,
           part_id,
           EXTRACT(EPOCH FROM end_ts - start_ts) AS cycle_s
    FROM part_times
    WHERE start_ts IS NOT NULL
      AND end_ts   IS NOT NULL
)
SELECT c.station_id,
       s.design_cycle_s,
       count(*)                                         AS n_parts,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.cycle_s)::numeric, 1)
                                                        AS median_s,
       round(avg(c.cycle_s), 1)                         AS mean_s,
       round(min(c.cycle_s), 1)                         AS min_s,
       round(max(c.cycle_s), 1)                         AS max_s,
       count(*) FILTER (WHERE c.cycle_s < 0)            AS n_negative
FROM cycle_times c
JOIN stations s USING (station_id)
GROUP BY c.station_id, s.design_cycle_s
ORDER BY c.station_id;