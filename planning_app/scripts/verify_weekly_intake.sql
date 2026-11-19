-- Independent SQLite check against saved observations, not the live order table.
WITH inputs AS (
    SELECT json_extract(data, '$.order_num') AS order_num,
           json_extract(data, '$.order_line') AS order_line,
           json_extract(data, '$.rel_num') AS rel_num,
           json_extract(data, '$.order_date') AS order_date,
           COALESCE(json_extract(data, '$.assembly_seq'), 0) = 0 AS main,
           CAST(ROUND(json_extract(data, '$.release_price_gbp') * 10000) AS INTEGER) AS price,
           CASE json_type(data, '$.void_line')
               WHEN 'true' THEN 1 WHEN 'false' THEN 0
           END AS void_flag
    FROM forecast_data_records
    WHERE extract_id = :extract_id
), releases AS (
    SELECT CASE WHEN SUM(main) > 0
                THEN MIN(CASE WHEN main THEN order_date END)
                ELSE MIN(order_date) END AS order_date,
           CASE WHEN SUM(main) > 0
                THEN COUNT(DISTINCT CASE WHEN main THEN order_date END) = 1
                     AND COUNT(CASE WHEN main THEN order_date END) = SUM(main)
                ELSE COUNT(DISTINCT order_date) = 1 AND COUNT(order_date) = COUNT(*)
           END AS has_date,
           SUM(main) > 0
               AND COUNT(DISTINCT CASE WHEN main THEN price END) = 1
               AND COUNT(CASE WHEN main THEN price END) = SUM(main)
               AND COUNT(DISTINCT CASE WHEN main THEN void_flag END) = 1
               AND COUNT(CASE WHEN main THEN void_flag END) = SUM(main) AS usable,
           MIN(CASE WHEN main THEN price END) AS price,
           MIN(CASE WHEN main THEN void_flag END) AS void_flag
    FROM inputs
    WHERE order_num IS NOT NULL AND order_line IS NOT NULL AND rel_num IS NOT NULL
    GROUP BY order_num, order_line, rel_num
)
SELECT 1 + CAST((julianday(order_date) - julianday(:first_week)) / 7 AS INTEGER) AS week,
       COUNT(*) AS releases,
       SUM(usable AND void_flag = 0) AS included,
       SUM(usable AND void_flag = 1) AS voided,
       SUM(NOT usable) AS exceptions,
       SUM(CASE WHEN usable THEN CASE WHEN void_flag = 0 THEN price ELSE 0 END END) AS net_units,
       SUM(CASE WHEN usable AND void_flag = 1 THEN price ELSE 0 END) AS void_units
FROM releases
WHERE has_date AND order_date >= :first_week AND order_date < :end
GROUP BY week
ORDER BY week;
