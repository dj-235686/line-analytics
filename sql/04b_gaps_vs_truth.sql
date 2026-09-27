-- 04b_gaps_vs_truth.sql
-- GRADING ONLY: uses the simulator's truth tables, which a real plant does not have.
-- Question: Of the gaps found in 04, which were camera outages and which were
--           real stoppages - and what was the station actually doing?
-- Concepts: AT TIME ZONE, interval-overlap join, sum(...) FILTER, greatest/least,
--           EXISTS, CASE.
-- ts_local is naive Berlin time; AT TIME ZONE turns it into timestamptz so it
-- can be compared with the truth tables (stored in UTC).
-- Known bug: gaps touching 02:00-03:00 on 25 Oct are sized wrong. AT TIME ZONE
-- resolves the ambiguous hour to winter time (CET), adding ~60 min.

WITH ends AS (
    SELECT station_id,
           ts_local,
           LAG(ts_local) OVER (PARTITION BY station_id ORDER BY ts_local) AS prev_ts
    FROM events_dedup
    WHERE event_type = 'end'
),
gaps AS (
    SELECT station_id,
           prev_ts  AT TIME ZONE 'Europe/Berlin' AS gap_start_utc,
           ts_local AT TIME ZONE 'Europe/Berlin' AS gap_end_utc
    FROM ends
    WHERE ts_local - prev_ts > interval '10 minutes'
),
state_time AS (
    -- Two intervals overlap when each one starts before the other ends.
    -- The overlap runs from the later start to the earlier end.
    SELECT g.station_id,
           g.gap_start_utc,
           g.gap_end_utc,
           t.state,
           EXTRACT(EPOCH FROM least(t.end_utc, g.gap_end_utc)
                            - greatest(t.start_utc, g.gap_start_utc)) AS overlap_s
    FROM gaps g
    JOIN truth_states t
      ON t.station_id = g.station_id
     AND t.start_utc  < g.gap_end_utc
     AND t.end_utc    > g.gap_start_utc
)
SELECT st.station_id,
       st.gap_start_utc,
       round(EXTRACT(EPOCH FROM st.gap_end_utc - st.gap_start_utc) / 60, 1)     AS gap_min,
       round(coalesce(sum(overlap_s) FILTER (WHERE state = 'running'), 0) / 60, 1) AS run_min,
       round(coalesce(sum(overlap_s) FILTER (WHERE state = 'down'),    0) / 60, 1) AS down_min,
       round(coalesce(sum(overlap_s) FILTER (WHERE state = 'starved'), 0) / 60, 1) AS starved_min,
       round(coalesce(sum(overlap_s) FILTER (WHERE state = 'blocked'), 0) / 60, 1) AS blocked_min,
       CASE WHEN EXISTS (
                SELECT 1
                FROM truth_outages o
                WHERE o.station_id = st.station_id
                  AND o.start_utc  < st.gap_end_utc
                  AND o.end_utc    > st.gap_start_utc)
            THEN 'camera outage'
            ELSE 'real stoppage'
       END AS truth
FROM state_time st
GROUP BY st.station_id, st.gap_start_utc, st.gap_end_utc
ORDER BY truth, st.station_id, st.gap_start_utc;