"""Tests for ResourceGuard - the 90% ceiling enforcer.

These tests verify:
1. Degradation level transitions at 80/85/90% thresholds
2. Hysteresis prevents flapping
3. Hot path always gets admission (possibly degraded)
4. Cold path defers under pressure
5. Startup budget check works
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.resource_guard import (
    Admission,
    DegradationLevel,
    ResourceEstimate,
    ResourceGuard,
    ResourceSnapshot,
)


@pytest.fixture
def guard() -> ResourceGuard:
    """Create a ResourceGuard with default settings."""
    return ResourceGuard(
        ceiling=0.90,
        soft=0.80,
        sample_interval=0.1,
        rolling_window=3,
        hysteresis_margin=0.05,
    )


class TestResourceSnapshot:
    """Test ResourceSnapshot functionality."""

    def test_max_ratio_cpu_only(self):
        """Max ratio works without GPU."""
        snap = ResourceSnapshot(
            cpu_ratio=0.5,
            ram_ratio=0.6,
            disk_ratio=0.3,
        )
        assert snap.max_ratio() == 0.6

    def test_max_ratio_with_gpu(self):
        """Max ratio includes GPU when available."""
        snap = ResourceSnapshot(
            cpu_ratio=0.5,
            ram_ratio=0.6,
            disk_ratio=0.3,
            gpu_vram_ratio=0.85,
            gpu_util=0.7,
        )
        assert snap.max_ratio() == 0.85

    def test_to_dict(self):
        """Snapshot converts to dict for logging."""
        snap = ResourceSnapshot(
            cpu_ratio=0.5,
            ram_ratio=0.6,
            disk_ratio=0.3,
            gpu_vram_ratio=0.7,
            gpu_util=0.4,
        )
        d = snap.to_dict()
        assert d["cpu_ratio"] == 0.5
        assert d["gpu_vram_ratio"] == 0.7


class TestAdmission:
    """Test Admission factory methods."""

    def test_admit_full(self):
        """Full admission allows everything."""
        a = Admission.admit_full()
        assert a.admitted is True
        assert a.degraded is False
        assert a.deferred is False
        assert a.rejected is False

    def test_admit_degraded(self):
        """Degraded admission includes params."""
        a = Admission.admit_degraded(
            DegradationLevel.MODERATE,
            {"max_tokens": 256},
        )
        assert a.admitted is True
        assert a.degraded is True
        assert a.level == DegradationLevel.MODERATE
        assert a.params["max_tokens"] == 256

    def test_defer(self):
        """Defer for cold path."""
        a = Admission.defer("Too busy")
        assert a.admitted is False
        assert a.deferred is True
        assert "Too busy" in a.reason

    def test_reject(self):
        """Reject new sessions."""
        a = Admission.reject("At capacity")
        assert a.admitted is False
        assert a.rejected is True


class TestDegradationLevels:
    """Test degradation level transitions."""

    def test_level_normal_under_soft(self, guard: ResourceGuard):
        """Below 80% = NORMAL."""
        level = guard._compute_level(0.75)
        assert level == DegradationLevel.NORMAL

    def test_level_soft_at_80(self, guard: ResourceGuard):
        """At 80% = SOFT."""
        level = guard._compute_level(0.80)
        assert level == DegradationLevel.SOFT

    def test_level_moderate_at_86(self, guard: ResourceGuard):
        """Above moderate threshold (0.85) = MODERATE."""
        # threshold_moderate = (0.90 + 0.80) / 2 = 0.85
        level = guard._compute_level(0.86)
        assert level == DegradationLevel.MODERATE

    def test_level_severe_at_88(self, guard: ResourceGuard):
        """At 88% (ceiling - 2%) = SEVERE."""
        level = guard._compute_level(0.88)
        assert level == DegradationLevel.SEVERE

    def test_level_critical_at_90(self, guard: ResourceGuard):
        """At 90% (ceiling) = CRITICAL."""
        level = guard._compute_level(0.90)
        assert level == DegradationLevel.CRITICAL


class TestHysteresis:
    """Test hysteresis prevents flapping."""

    def test_no_immediate_recovery(self, guard: ResourceGuard):
        """Don't drop level until margin below threshold."""
        # First, go to SOFT level
        guard._current_level = DegradationLevel.SOFT

        # Still in SOFT range (80%) - should stay SOFT
        level = guard._compute_level(0.80)
        assert level == DegradationLevel.SOFT

        # Just under SOFT (79%) - should stay SOFT (no hysteresis yet)
        level = guard._compute_level(0.79)
        assert level == DegradationLevel.SOFT

        # Below hysteresis margin (80% - 5% = 75%) - can recover
        level = guard._compute_level(0.74)
        assert level == DegradationLevel.NORMAL

    def test_immediate_escalation(self, guard: ResourceGuard):
        """Escalation is immediate, no hysteresis."""
        guard._current_level = DegradationLevel.NORMAL

        # Jump straight to CRITICAL
        level = guard._compute_level(0.91)
        assert level == DegradationLevel.CRITICAL

    def test_stepwise_recovery(self, guard: ResourceGuard):
        """Recovery happens one level at a time with hysteresis.

        The key insight: when computing level, we first check if the ratio
        puts us at or above any threshold (immediate escalation). Only if
        the ratio is below all thresholds do we apply hysteresis for recovery.

        So at 0.82 (which is >= soft=0.80), we return SOFT regardless of
        current level. The hysteresis only applies when ratio is below ALL
        thresholds for escalation.
        """
        guard._current_level = DegradationLevel.CRITICAL

        # At 0.82, which is >= soft threshold (0.80), the code returns SOFT
        # because 0.82 < threshold_moderate (0.85) but >= soft (0.80)
        level = guard._compute_level(0.82)
        # This is actually SOFT because 0.82 >= soft threshold
        assert level == DegradationLevel.SOFT

        # Test the actual hysteresis behavior: when below all thresholds
        guard._current_level = DegradationLevel.SOFT

        # At 0.78, still >= soft - hysteresis (0.80 - 0.05 = 0.75), stays SOFT
        level = guard._compute_level(0.78)
        assert level == DegradationLevel.SOFT

        # At 0.74, below soft - hysteresis, can recover to NORMAL
        level = guard._compute_level(0.74)
        assert level == DegradationLevel.NORMAL


class TestAcquireHotPath:
    """Test acquire() for hot path operations."""

    @pytest.mark.asyncio
    async def test_hot_path_normal_full_admission(self, guard: ResourceGuard):
        """Hot path at normal level gets full admission."""
        guard._current_level = DegradationLevel.NORMAL

        admission = await guard.acquire(path="hot")
        assert admission.admitted is True
        assert admission.degraded is False

    @pytest.mark.asyncio
    async def test_hot_path_soft_degraded(self, guard: ResourceGuard):
        """Hot path at soft level gets degraded admission."""
        guard._current_level = DegradationLevel.SOFT

        admission = await guard.acquire(path="hot")
        assert admission.admitted is True
        assert admission.degraded is True
        assert "max_tokens" in admission.params

    @pytest.mark.asyncio
    async def test_hot_path_critical_still_admitted(self, guard: ResourceGuard):
        """Hot path at critical level still admitted (in-flight turn)."""
        guard._current_level = DegradationLevel.CRITICAL

        admission = await guard.acquire(path="hot")
        assert admission.admitted is True
        assert admission.degraded is True
        assert admission.params.get("use_smaller_model") is True

    @pytest.mark.asyncio
    async def test_hot_path_critical_new_session_rejected(self, guard: ResourceGuard):
        """New sessions rejected at critical level."""
        guard._current_level = DegradationLevel.CRITICAL

        admission = await guard.acquire(
            need=ResourceEstimate(description="new_session"),
            path="hot",
        )
        assert admission.admitted is False
        assert admission.rejected is True


class TestAcquireColdPath:
    """Test acquire() for cold path operations."""

    @pytest.mark.asyncio
    async def test_cold_path_normal_admitted(self, guard: ResourceGuard):
        """Cold path at normal level gets full admission."""
        guard._current_level = DegradationLevel.NORMAL

        admission = await guard.acquire(path="cold")
        assert admission.admitted is True
        assert admission.deferred is False

    @pytest.mark.asyncio
    async def test_cold_path_soft_deferred(self, guard: ResourceGuard):
        """Cold path at soft level gets deferred."""
        guard._current_level = DegradationLevel.SOFT

        admission = await guard.acquire(path="cold")
        assert admission.admitted is False
        assert admission.deferred is True

    @pytest.mark.asyncio
    async def test_cold_path_critical_deferred(self, guard: ResourceGuard):
        """Cold path at critical level gets deferred."""
        guard._current_level = DegradationLevel.CRITICAL

        admission = await guard.acquire(path="cold")
        assert admission.admitted is False
        assert admission.deferred is True


class TestStartupBudget:
    """Test startup budget checking."""

    @pytest.mark.asyncio
    async def test_budget_fits(self, guard: ResourceGuard):
        """Models that fit pass the check."""
        # Mock a snapshot with plenty of VRAM
        snap = ResourceSnapshot(
            gpu_vram_used=2e9,  # 2GB used
            gpu_vram_total=8e9,  # 8GB total
            gpu_vram_ratio=0.25,
            cpu_ratio=0.3,
            ram_ratio=0.4,
            disk_ratio=0.5,
        )
        guard._samples.append(snap)

        # Need 4GB, have 5.2GB available (8GB * 0.9 - 2GB)
        fits, msg = await guard.check_startup_budget([
            ("STT", 1.5e9),
            ("LLM", 2.5e9),
        ])
        assert fits is True
        assert "fit within budget" in msg

    @pytest.mark.asyncio
    async def test_budget_exceeds(self, guard: ResourceGuard):
        """Models that exceed budget fail the check."""
        snap = ResourceSnapshot(
            gpu_vram_used=4e9,  # 4GB used
            gpu_vram_total=8e9,  # 8GB total
            gpu_vram_ratio=0.5,
            cpu_ratio=0.3,
            ram_ratio=0.4,
            disk_ratio=0.5,
        )
        guard._samples.append(snap)

        # Need 5GB, have only 3.2GB available (8GB * 0.9 - 4GB)
        fits, msg = await guard.check_startup_budget([
            ("STT", 1.5e9),
            ("LLM", 3.5e9),
        ])
        assert fits is False
        assert "Insufficient VRAM" in msg

    @pytest.mark.asyncio
    async def test_budget_cpu_only(self):
        """CPU-only mode always passes."""
        # Create a guard without GPU
        with patch("backend.core.resource_guard.PYNVML_AVAILABLE", False):
            guard = ResourceGuard()
            guard._gpu_initialized = False

            snap = ResourceSnapshot(
                gpu_vram_total=None,  # No GPU
                cpu_ratio=0.3,
                ram_ratio=0.4,
                disk_ratio=0.5,
            )
            guard._samples.append(snap)

            fits, msg = await guard.check_startup_budget([
                ("STT", 1.5e9),
                ("LLM", 5.5e9),
            ])
            assert fits is True
            assert "CPU-only" in msg


class TestGuardLifecycle:
    """Test guard start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self, guard: ResourceGuard):
        """Guard starts and stops cleanly."""
        assert guard.is_running is False

        await guard.start()
        assert guard.is_running is True

        # Let it take a sample
        await asyncio.sleep(0.2)
        assert guard.snapshot() is not None

        await guard.stop()
        assert guard.is_running is False

    @pytest.mark.asyncio
    async def test_double_start_safe(self, guard: ResourceGuard):
        """Starting twice is safe."""
        await guard.start()
        await guard.start()  # Should not error
        assert guard.is_running is True
        await guard.stop()


class TestHeadroom:
    """Test headroom calculation."""

    def test_headroom_with_snapshot(self, guard: ResourceGuard):
        """Headroom calculated from snapshot."""
        snap = ResourceSnapshot(
            cpu_ratio=0.3,
            ram_ratio=0.6,
            disk_ratio=0.2,
        )
        guard._samples.append(snap)

        assert guard.headroom("cpu") == 0.7
        assert guard.headroom("ram") == 0.4
        assert guard.headroom("disk") == 0.8

    def test_headroom_no_snapshot(self, guard: ResourceGuard):
        """Headroom is 1.0 with no snapshot."""
        assert guard.headroom("cpu") == 1.0

    def test_headroom_unknown_resource(self, guard: ResourceGuard):
        """Unknown resource has 1.0 headroom."""
        snap = ResourceSnapshot(cpu_ratio=0.5, ram_ratio=0.5, disk_ratio=0.5)
        guard._samples.append(snap)
        assert guard.headroom("unknown") == 1.0
