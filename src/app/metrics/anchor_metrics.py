"""
ARED Edge IoT Platform - IOTA Anchor Metrics

Prometheus metrics for the IOTA Anchoring Service.
Per IOTA Anchor README and P6.1.4.

Metrics Categories:
- Anchor posting operations
- Failure tracking
- Confirmation latency
- Merkle tree building
- Event aggregation
"""

from prometheus_client import Counter, Gauge, Histogram, Info

import structlog

logger = structlog.get_logger(__name__)


class AnchorMetrics:
    """
    Centralized metrics for the IOTA Anchoring Service.

    Provides visibility into:
    - Anchor posting success/failure
    - Tangle confirmation times
    - Merkle tree operations
    - Event aggregation stats
    """

    def __init__(self) -> None:
        """Initialize all anchor metrics."""
        self._init_posting_metrics()
        self._init_confirmation_metrics()
        self._init_merkle_metrics()
        self._init_aggregation_metrics()
        self._init_reconciliation_metrics()
        self._init_info_metrics()

    def _init_posting_metrics(self) -> None:
        """Initialize anchor posting metrics."""
        self.anchors_posted = Counter(
            "ared_anchor_posted_total",
            "Total anchors successfully posted to IOTA",
        )

        self.anchors_failed = Counter(
            "ared_anchor_failed_total",
            "Total anchor posting failures",
            ["reason"],
        )

        self.posting_duration = Histogram(
            "ared_anchor_posting_duration_seconds",
            "Time to post anchor to IOTA Tangle",
            buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
        )

        self.posting_retries = Counter(
            "ared_anchor_posting_retries_total",
            "Number of posting retries",
        )

        self.posting_in_progress = Gauge(
            "ared_anchor_posting_in_progress",
            "Anchor posts currently in progress",
        )

    def _init_confirmation_metrics(self) -> None:
        """Initialize confirmation metrics."""
        self.confirmation_latency = Histogram(
            "ared_anchor_confirmation_latency_seconds",
            "Time from posting to confirmation on Tangle",
            buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
        )

        self.confirmations_total = Counter(
            "ared_anchor_confirmations_total",
            "Total anchor confirmations received",
            ["status"],
        )

        self.pending_confirmations = Gauge(
            "ared_anchor_pending_confirmations",
            "Anchors awaiting confirmation",
        )

        self.confirmation_timeout = Counter(
            "ared_anchor_confirmation_timeout_total",
            "Anchors that timed out waiting for confirmation",
        )

    def _init_merkle_metrics(self) -> None:
        """Initialize Merkle tree metrics."""
        self.merkle_build_duration = Histogram(
            "ared_anchor_merkle_build_duration_seconds",
            "Merkle tree build time",
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
        )

        self.merkle_tree_size = Histogram(
            "ared_anchor_merkle_tree_size",
            "Number of leaves in Merkle tree",
            buckets=[10, 50, 100, 500, 1000, 5000, 10000, 50000],
        )

        self.merkle_proof_generation = Histogram(
            "ared_anchor_merkle_proof_duration_seconds",
            "Merkle proof generation time",
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01],
        )

        self.merkle_verifications = Counter(
            "ared_anchor_merkle_verifications_total",
            "Merkle proof verifications",
            ["result"],
        )

    def _init_aggregation_metrics(self) -> None:
        """Initialize event aggregation metrics."""
        self.events_aggregated = Counter(
            "ared_anchor_events_aggregated_total",
            "Total events aggregated for anchoring",
        )

        self.aggregation_duration = Histogram(
            "ared_anchor_aggregation_duration_seconds",
            "Event aggregation duration",
            buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0],
        )

        self.aggregation_window_size = Gauge(
            "ared_anchor_aggregation_window_seconds",
            "Current aggregation window size",
        )

        self.events_per_anchor = Histogram(
            "ared_anchor_events_per_anchor",
            "Number of events per anchor",
            buckets=[1, 10, 50, 100, 500, 1000, 5000, 10000],
        )

        self.last_aggregation_timestamp = Gauge(
            "ared_anchor_last_aggregation_timestamp",
            "Timestamp of last aggregation (Unix epoch)",
        )

    def _init_reconciliation_metrics(self) -> None:
        """Initialize reconciliation metrics."""
        self.reconciliation_runs = Counter(
            "ared_anchor_reconciliation_runs_total",
            "Total reconciliation runs",
            ["result"],
        )

        self.reconciliation_duration = Histogram(
            "ared_anchor_reconciliation_duration_seconds",
            "Reconciliation process duration",
            buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0],
        )

        self.failed_anchors_recovered = Counter(
            "ared_anchor_failed_recovered_total",
            "Failed anchors successfully recovered",
        )

        self.anchors_marked_review = Counter(
            "ared_anchor_marked_review_total",
            "Anchors marked for manual review",
        )

        self.retry_queue_size = Gauge(
            "ared_anchor_retry_queue_size",
            "Number of anchors in retry queue",
        )

    def _init_info_metrics(self) -> None:
        """Initialize info metrics."""
        self.service_info = Info(
            "ared_anchor_service",
            "IOTA Anchor service information",
        )

        self.iota_node_info = Info(
            "ared_anchor_iota_node",
            "IOTA node connection information",
        )

        self.last_anchor_digest = Gauge(
            "ared_anchor_last_digest_timestamp",
            "Timestamp of last successful anchor",
        )

    # Convenience methods

    def record_anchor_posted(self, duration: float, events_count: int) -> None:
        """Record successful anchor posting."""
        self.anchors_posted.inc()
        self.posting_duration.observe(duration)
        self.events_per_anchor.observe(events_count)

    def record_anchor_failed(self, reason: str) -> None:
        """Record failed anchor posting."""
        self.anchors_failed.labels(reason=reason).inc()

    def record_posting_retry(self) -> None:
        """Record posting retry."""
        self.posting_retries.inc()

    def record_confirmation(
        self,
        success: bool,
        latency: float | None = None,
    ) -> None:
        """Record anchor confirmation."""
        status = "confirmed" if success else "failed"
        self.confirmations_total.labels(status=status).inc()
        if success and latency:
            self.confirmation_latency.observe(latency)

    def record_merkle_build(
        self,
        duration: float,
        tree_size: int,
    ) -> None:
        """Record Merkle tree build."""
        self.merkle_build_duration.observe(duration)
        self.merkle_tree_size.observe(tree_size)

    def record_merkle_verification(self, valid: bool) -> None:
        """Record Merkle proof verification."""
        result = "valid" if valid else "invalid"
        self.merkle_verifications.labels(result=result).inc()

    def record_aggregation(
        self,
        events_count: int,
        duration: float,
    ) -> None:
        """Record event aggregation."""
        self.events_aggregated.inc(events_count)
        self.aggregation_duration.observe(duration)
        import time
        self.last_aggregation_timestamp.set(time.time())

    def record_reconciliation(
        self,
        success: bool,
        duration: float,
        recovered: int = 0,
        marked_review: int = 0,
    ) -> None:
        """Record reconciliation run."""
        result = "success" if success else "failed"
        self.reconciliation_runs.labels(result=result).inc()
        self.reconciliation_duration.observe(duration)
        if recovered > 0:
            self.failed_anchors_recovered.inc(recovered)
        if marked_review > 0:
            self.anchors_marked_review.inc(marked_review)

    def update_pending_confirmations(self, count: int) -> None:
        """Update pending confirmations gauge."""
        self.pending_confirmations.set(count)

    def update_retry_queue(self, size: int) -> None:
        """Update retry queue size."""
        self.retry_queue_size.set(size)

    def set_posting_in_progress(self, count: int) -> None:
        """Set posting in progress count."""
        self.posting_in_progress.set(count)

    def set_service_info(
        self,
        version: str,
        environment: str,
        schedule: str = "daily",
    ) -> None:
        """Set service info labels."""
        self.service_info.info({
            "version": version,
            "environment": environment,
            "schedule": schedule,
        })

    def set_iota_node_info(
        self,
        node_url: str,
        network: str = "mainnet",
    ) -> None:
        """Set IOTA node info labels."""
        self.iota_node_info.info({
            "node_url": node_url,
            "network": network,
        })


# Singleton instance
_anchor_metrics: AnchorMetrics | None = None


def get_anchor_metrics() -> AnchorMetrics:
    """Get global anchor metrics instance."""
    global _anchor_metrics
    if _anchor_metrics is None:
        _anchor_metrics = AnchorMetrics()
    return _anchor_metrics
