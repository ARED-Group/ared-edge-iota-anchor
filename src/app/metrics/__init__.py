"""
ARED Edge IoT Platform - IOTA Anchor Metrics Module

Prometheus metrics for the IOTA Anchoring Service.
Per IOTA Anchor README and P6.1.4.

Exports:
- Anchor posting metrics
- Failure counters
- Confirmation latency
- Merkle tree build times
"""

from app.metrics.anchor_metrics import (
    AnchorMetrics,
    get_anchor_metrics,
)

__all__ = [
    "AnchorMetrics",
    "get_anchor_metrics",
]
