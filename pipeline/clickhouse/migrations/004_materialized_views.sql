-- Migration 004: Materialized views for real-time aggregations

-- Hourly event counts
CREATE MATERIALIZED VIEW IF NOT EXISTS event_counts_hourly_mv
ENGINE = SummingMergeTree()
ORDER BY (project_id, event_name, event_hour)
AS SELECT
    project_id,
    event_name,
    toStartOfHour(timestamp) AS event_hour,
    count() AS event_count,
    uniq(user_id) AS unique_users
FROM events
GROUP BY project_id, event_name, event_hour;

-- Daily event counts
CREATE MATERIALIZED VIEW IF NOT EXISTS event_counts_daily_mv
ENGINE = SummingMergeTree()
ORDER BY (project_id, event_name, event_day)
AS SELECT
    project_id,
    event_name,
    toDate(timestamp) AS event_day,
    count() AS event_count,
    uniq(user_id) AS unique_users
FROM events
GROUP BY project_id, event_name, event_day;

-- Experiment metrics aggregation
CREATE MATERIALIZED VIEW IF NOT EXISTS experiment_metrics_mv
ENGINE = AggregatingMergeTree()
ORDER BY (project_id, experiment_id, variant, metric_name, metric_hour)
AS SELECT
    e.project_id AS project_id,
    e.experiment_id AS experiment_id,
    e.variant AS variant,
    ev.event_name AS metric_name,
    toStartOfHour(ev.timestamp) AS metric_hour,
    countState() AS event_count,
    uniqState(ev.user_id) AS unique_users,
    sumState(JSONExtractFloat(ev.properties, 'revenue')) AS revenue_sum
FROM events ev
INNER JOIN experiment_exposures e
    ON ev.project_id = e.project_id AND ev.user_id = e.user_id
WHERE ev.timestamp >= e.first_exposure
GROUP BY e.project_id, e.experiment_id, e.variant, ev.event_name, metric_hour;
