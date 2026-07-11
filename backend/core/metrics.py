"""Prometheus metrics for the English Coach.

All components emit metrics through this module. The /metrics endpoint
exposes these for Prometheus scraping.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Resource Guard metrics
resource_usage_ratio = Gauge(
    "resource_usage_ratio",
    "Current resource usage as a ratio (0-1)",
    ["resource"],
)

resource_ceiling_hits_total = Counter(
    "resource_ceiling_hits_total",
    "Total number of times a resource hit the ceiling",
    ["resource"],
)

degradation_level = Gauge(
    "degradation_level",
    "Current degradation level (0=normal, higher=more degraded)",
)

jobs_deferred_total = Counter(
    "jobs_deferred_total",
    "Total number of cold-path jobs deferred due to resource pressure",
)

sessions_rejected_total = Counter(
    "sessions_rejected_total",
    "Total number of sessions rejected due to resource pressure",
)

guard_sample_duration_seconds = Histogram(
    "guard_sample_duration_seconds",
    "Time taken to sample all resources",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# Hot path metrics
hotpath_stage_duration_seconds = Histogram(
    "hotpath_stage_duration_seconds",
    "Duration of each hot path stage",
    ["stage"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

hotpath_total_duration_seconds = Histogram(
    "hotpath_total_duration_seconds",
    "Total duration of a hot path turn",
    buckets=[0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0],
)

# Cold path metrics
coldpath_queue_depth = Gauge(
    "coldpath_queue_depth",
    "Number of jobs waiting in the cold path queue",
)

coldpath_evaluator_duration_seconds = Histogram(
    "coldpath_evaluator_duration_seconds",
    "Duration of each evaluator",
    ["evaluator"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# Session metrics
active_sessions = Gauge(
    "active_sessions",
    "Number of currently active sessions",
)

utterances_processed_total = Counter(
    "utterances_processed_total",
    "Total number of utterances processed",
    ["role"],
)

assessments_completed_total = Counter(
    "assessments_completed_total",
    "Total number of assessments completed",
)


def get_metrics() -> bytes:
    """Generate metrics in Prometheus format."""
    return generate_latest()


def get_metrics_content_type() -> str:
    """Get the content type for Prometheus metrics."""
    return CONTENT_TYPE_LATEST
