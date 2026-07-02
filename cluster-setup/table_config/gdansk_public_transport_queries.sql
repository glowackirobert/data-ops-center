-- Advanced Pinot queries for gdansk_public_transport (OFFLINE table)
-- Schema: routeShortName, headsign, vehicleCode, vehicleService (dims)
--         tripId, routeId, vehicleId, speed, direction, delay, lat, lon, gpsQuality (metrics)
--         generatedTransformed, scheduledTripStartTimeTransformed (timestamps, epoch ms)

-- ---------------------------------------------------------------------------
-- 1. ON-TIME PERFORMANCE PER ROUTE
--    Classifies each snapshot as on-time / slightly late / very late,
--    then shows the percentage breakdown per route (sorted by worst % very late).
--    run time 11 sec, when increase limit to 30 ten
-- ---------------------------------------------------------------------------
SELECT
    routeShortName,
    COUNT(*)                                                                 AS total_snapshots,
    COUNT(DISTINCT vehicleId)                                                AS unique_vehicles,
    ROUND(AVG(delay), 1)                                                     AS avg_delay_sec,
    MAX(delay)                                                               AS max_delay_sec,
    ROUND(
        SUM(CASE WHEN delay <= 60  THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                                        AS pct_on_time,
    ROUND(
        SUM(CASE WHEN delay > 60 AND delay <= 300 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                                        AS pct_slightly_late,
    ROUND(
        SUM(CASE WHEN delay > 300 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                                        AS pct_very_late
FROM gdansk_public_transport
GROUP BY routeShortName
ORDER BY pct_very_late DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 2. DELAY PERCENTILES PER ROUTE
--    P50 / P90 / P99 delay — reveals tail latency hidden by averages.
-- ---------------------------------------------------------------------------
SELECT
    routeShortName,
    COUNT(*)                         AS snapshots,
    ROUND(PERCENTILE(delay, 50), 0)  AS p50_delay_sec,
    ROUND(PERCENTILE(delay, 90), 0)  AS p90_delay_sec,
    ROUND(PERCENTILE(delay, 99), 0)  AS p99_delay_sec
FROM gdansk_public_transport
GROUP BY routeShortName
ORDER BY p90_delay_sec DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 3. HOURLY TRAFFIC VOLUME & AVERAGE SPEED
--    Aggregates by hour-of-day bucket to reveal rush-hour patterns.
--    DATETRUNC truncates the epoch-ms timestamp to the hour boundary.
-- ---------------------------------------------------------------------------
SELECT
    DATETRUNC('hour', generatedTransformed)  AS hour_bucket,
    COUNT(DISTINCT vehicleId)                AS active_vehicles,
    COUNT(*)                                 AS gps_snapshots,
    ROUND(AVG(speed), 2)                     AS avg_speed_kmh,
    ROUND(AVG(delay), 1)                     AS avg_delay_sec
FROM gdansk_public_transport
GROUP BY hour_bucket
ORDER BY hour_bucket;


-- ---------------------------------------------------------------------------
-- 4. VEHICLE SPEED DISTRIBUTION (BUCKETED)
--    Bins every GPS snapshot by speed range — useful for spotting stopped
--    or very slow vehicles vs. vehicles running at normal speed.
-- ---------------------------------------------------------------------------
SELECT
    CASE
        WHEN speed = 0              THEN '0 – stopped'
        WHEN speed < 10             THEN '1–9 – crawling'
        WHEN speed < 30             THEN '10–29 – slow'
        WHEN speed < 50             THEN '30–49 – normal'
        WHEN speed < 70             THEN '50–69 – fast'
        ELSE                             '70+ – very fast'
    END                            AS speed_bucket,
    COUNT(*)                       AS snapshots,
    COUNT(DISTINCT vehicleId)      AS distinct_vehicles,
    ROUND(AVG(delay), 1)           AS avg_delay_sec
FROM gdansk_public_transport
GROUP BY speed_bucket
ORDER BY MIN(speed);


-- ---------------------------------------------------------------------------
-- 5. GEOGRAPHIC DENSITY GRID (0.01° cells ≈ ~1 km)
--    Rounds lat/lon to a grid cell and counts how many GPS pings fell there.
--   eavily served areas.
-- ---------------------------------------------------------------------------
SELECT Helps identify congestion hot-spots or h
    ROUND(lat, 2)              AS lat_cell,
    ROUND(lon, 2)              AS lon_cell,
    COUNT(*)                   AS ping_count,
    COUNT(DISTINCT vehicleId)  AS vehicles,
    ROUND(AVG(speed), 1)       AS avg_speed_kmh,
    ROUND(AVG(delay), 1)       AS avg_delay_sec
FROM gdansk_public_transport
WHERE
    lat BETWEEN 54.27 AND 54.50   -- Gdansk bounding box
    AND lon BETWEEN 18.45 AND 18.80
GROUP BY lat_cell, lon_cell
ORDER BY ping_count DESC
LIMIT 30;


-- ---------------------------------------------------------------------------
-- 6. MOST DELAYED INDIVIDUAL VEHICLES (ALL TIME)
--    Surfaces chronic offenders — vehicles that are late most often.
-- ---------------------------------------------------------------------------
SELECT
    vehicleId,
    vehicleCode,
    routeShortName,
    COUNT(*)                         AS snapshots,
    ROUND(AVG(delay), 1)             AS avg_delay_sec,
    MAX(delay)                       AS max_delay_sec,
    ROUND(PERCENTILE(delay, 90), 0)  AS p90_delay_sec,
    ROUND(AVG(speed), 1)             AS avg_speed_kmh
FROM gdansk_public_transport
GROUP BY vehicleId, vehicleCode, routeShortName
HAVING AVG(delay) > 120
ORDER BY avg_delay_sec DESC
LIMIT 20;


-- ---------------------------------------------------------------------------
-- 7. ROUTE COVERAGE – UNIQUE HEADSIGNS & TRIP COUNT PER ROUTE
--    Useful for understanding how many distinct destinations each route serves.
-- ---------------------------------------------------------------------------
SELECT
    routeShortName,
    COUNT(DISTINCT headsign)    AS distinct_headsigns,
    COUNT(DISTINCT tripId)      AS distinct_trips,
    COUNT(DISTINCT vehicleId)   AS distinct_vehicles,
    COUNT(DISTINCT vehicleCode) AS distinct_vehicle_codes
FROM gdansk_public_transport
GROUP BY routeShortName
ORDER BY distinct_trips DESC
LIMIT 30;


-- ---------------------------------------------------------------------------
-- 8. DELAY TREND – DAILY AVERAGE (for offline batch ingested data)
--    Shows how average delay evolved day by day across all routes.
-- ---------------------------------------------------------------------------
SELECT
    DATETRUNC('day', generatedTransformed)  AS day,
    routeShortName,
    COUNT(*)                                AS snapshots,
    ROUND(AVG(delay), 1)                    AS avg_delay_sec,
    ROUND(AVG(speed), 1)                    AS avg_speed_kmh,
    COUNT(DISTINCT vehicleId)               AS vehicles
FROM gdansk_public_transport
GROUP BY day, routeShortName
ORDER BY day DESC, avg_delay_sec DESC
LIMIT 100;


-- ---------------------------------------------------------------------------
-- 9. GPS QUALITY BREAKDOWN
--    gpsQuality is an integer flag; understand data quality across routes.
-- ---------------------------------------------------------------------------
SELECT
    gpsQuality,
    COUNT(*)                    AS snapshots,
    COUNT(DISTINCT vehicleId)   AS vehicles,
    ROUND(AVG(speed), 1)        AS avg_speed_kmh,
    ROUND(AVG(delay), 1)        AS avg_delay_sec
FROM gdansk_public_transport
GROUP BY gpsQuality
ORDER BY snapshots DESC;


-- ---------------------------------------------------------------------------
-- 10. DIRECTION SPLIT PER ROUTE
--     direction (0 / 1) represents outbound vs. inbound leg.
--     Compare volume and delay between directions.
-- ---------------------------------------------------------------------------
SELECT
    routeShortName,
    direction,
    COUNT(*)                         AS snapshots,
    COUNT(DISTINCT vehicleId)        AS vehicles,
    ROUND(AVG(delay), 1)             AS avg_delay_sec,
    ROUND(PERCENTILE(delay, 90), 0)  AS p90_delay_sec,
    ROUND(AVG(speed), 1)             AS avg_speed_kmh
FROM gdansk_public_transport
GROUP BY routeShortName, direction
ORDER BY routeShortName, direction
LIMIT 60;