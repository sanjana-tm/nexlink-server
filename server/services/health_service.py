"""
NexLink Server — Health Scoring Service
========================================
Computes composite health scores from device metrics.

Score weights:
  CPU:     25%
  Memory:  25%
  Storage: 20%
  Network: 15%
  Uptime:  15%
"""
from __future__ import annotations


class HealthService:
    """Compute device health scores from metrics."""

    @staticmethod
    def compute_health_score(
        cpu_percent: float | None = None,
        memory_percent: float | None = None,
        storage_percent: float | None = None,
        wifi_signal_dbm: int | None = None,
        heartbeat_on_time: bool = True,
    ) -> int:
        """
        Compute a health score from 0 to 100.

        Each metric maps to a 0-100 sub-score, then weighted:
          CPU     25%  -- good below 50%, degrades linearly to 0 at 100%
          Memory  25%  -- good below 60%, degrades linearly to 0 at 100%
          Storage 20%  -- good below 70%, degrades linearly to 0 at 95%
          Network 15%  -- good above -50 dBm, poor below -80 dBm
          Uptime  15%  -- 100 if heartbeat on time, 30 otherwise
        """

        def cpu_score(val: float | None) -> float:
            if val is None:
                return 80.0
            if val < 50:
                return 100.0
            if val >= 100:
                return 0.0
            return 100.0 - (val - 50) * 2

        def memory_score(val: float | None) -> float:
            if val is None:
                return 80.0
            if val < 60:
                return 100.0
            if val >= 100:
                return 0.0
            return 100.0 - (val - 60) * 2.5

        def storage_score(val: float | None) -> float:
            if val is None:
                return 80.0
            if val < 70:
                return 100.0
            if val >= 95:
                return 0.0
            return 100.0 - (val - 70) * 4

        def network_score(dbm: int | None) -> float:
            if dbm is None:
                return 80.0
            if dbm > -55:
                return 100.0
            if dbm < -85:
                return 30.0
            return 100.0 - (abs(dbm) - 55) * (70.0 / 30.0)

        uptime_val = 100.0 if heartbeat_on_time else 30.0

        score = (
            cpu_score(cpu_percent) * 0.25
            + memory_score(memory_percent) * 0.25
            + storage_score(storage_percent) * 0.20
            + network_score(wifi_signal_dbm) * 0.15
            + uptime_val * 0.15
        )
        return max(0, min(100, int(round(score))))

    @staticmethod
    def status_from_score(score: int) -> str:
        """Map a health score to a human-readable status string."""
        if score >= 80:
            return "healthy"
        if score >= 50:
            return "warning"
        return "critical"
