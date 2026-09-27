-- 06_bottleneck_by_hour.sql
-- Question: Which station is the bottleneck, hour by hour - and does it move?
-- Concepts: CTE, date_trunc, ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC),
--           count(*) OVER (PARTITION BY ...), aggregate inside a window: sum(count(*)) OVER ().
-- "Busy" = time between a part's start and end at the station (working, or
-- broken down mid-part). The station busy for the largest share of an hour
-- is that hour's bottleneck. Each cycle counts in the hour it ends.
-- Limitations: a camera outage makes a station look idle; the DST hour
-- (02:00 on 25 Oct) holds two real hours, which affects all stations equally.

WITH part_times AS (
    SELECT station_id,
           part_id,
           min(ts_local) FILTER (WHERE event_type = 'start') AS start_ts,
           min(ts_local) FILTER (WHERE event_type = 'end')   AS end_ts
    FROM events_dedup
    GROUP BY station_id, part_id
),
busy AS (
    SELECT station_id,
           date_trunc('hour', end_ts)                 AS hour_local,
           sum(EXTRACT(EPOCH FROM end_ts - start_ts)) AS busy_s
    FROM part_times
    WHERE end_ts > start_ts   -- drops incomplete pairs (NULLs) and the 4 DST negatives
    GROUP BY station_id, date_trunc('hour', end_ts)
),
ranked AS (
    SELECT hour_local,
           station_id,
           ROW_NUMBER() OVER (PARTITION BY hour_local ORDER BY busy_s DESC) AS rank_in_hour,
           count(*)     OVER (PARTITION BY hour_local)                      AS stations_in_hour
    FROM busy
)
SELECT station_id,
       count(*)                                           AS hours_as_bottleneck,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_hours
FROM ranked
WHERE rank_in_hour = 1
  AND stations_in_hour = 4    -- skip hours where not every station reported (e.g. the drift hour 05:00 on 27 Oct)
GROUP BY station_id
ORDER BY hours_as_bottleneck DESC;