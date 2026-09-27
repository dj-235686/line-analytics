-- 04_gaps.sql
-- Question: Where does a station go silent - more than 10 minutes between
--           consecutive finished parts?
-- Concepts: CTE, LAG() OVER (PARTITION BY ... ORDER BY ...), interval comparison.
-- Uses events_dedup (run 03 first). A gap means either the station stopped
-- or its camera stopped sending - this query alone cannot tell which.
-- Known limitation: ordering by naive local time interleaves the two passes
-- through 02:00-03:00 on 25 Oct, so a gap inside that hour could be hidden.

WITH ends AS (
    SELECT station_id,
           ts_local,
           LAG(ts_local) OVER (PARTITION BY station_id ORDER BY ts_local) AS prev_ts
    FROM events_dedup
    WHERE event_type = 'end'
)
SELECT station_id,
       prev_ts  AS gap_start_local,
       ts_local AS gap_end_local,
       round(EXTRACT(EPOCH FROM ts_local - prev_ts) / 60, 1) AS gap_min
FROM ends
WHERE ts_local - prev_ts > interval '10 minutes'
ORDER BY station_id, gap_start_local;