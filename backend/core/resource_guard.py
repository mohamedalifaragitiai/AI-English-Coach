"""Resource Guard - The 90% ceiling enforcer.

This is the load-bearing feature for the resource-constrained host. It:
1. Samples GPU VRAM, GPU util, RAM, CPU, and disk on a background loop
2. Enforces a hard 90% ceiling per resource (configurable)
3. Implements a degradation ladder when pressure rises
4. Gates all heavy operations (STT, LLM, TTS, cold-path jobs)

The guard never runs work itself - it advises and records. Callers MUST
honor the admission decision.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

import psutil

from backend.core.logging import get_logger
from backend.core.metrics import (
    degradation_level as degradation_level_gauge,
    guard_sample_duration_seconds,
    jobs_deferred_total,
    resource_ceiling_hits_total,
    resource_usage_ratio,
    sessions_rejected_total,
)

logger = get_logger(__name__)

# Try to import pynvml for GPU monitoring
try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("pynvml_not_available", message="GPU monitoring disabled, running CPU-only mode")


class DegradationLevel(IntEnum):
    """Degradation levels - apply in order as pressure rises."""

    NORMAL = 0  # All systems go
    SOFT = 1  # >=80%: pause cold-path jobs, queue them
    MODERATE = 2  # >=85%: reduce LLM context/max_tokens, cap evaluators
    SEVERE = 3  # >=90% approached: switch to smaller models, lower quality
    CRITICAL = 4  # Would cross 90%: reject new sessions, protect current


@dataclass
class ResourceSnapshot:
    """Point-in-time snapshot of all resources."""

    gpu_vram_used: float | None = None  # bytes
    gpu_vram_total: float | None = None  # bytes
    gpu_vram_ratio: float | None = None  # 0-1
    gpu_util: float | None = None  # 0-1
    ram_used: float = 0.0  # bytes
    ram_total: float = 0.0  # bytes
    ram_ratio: float = 0.0  # 0-1
    cpu_percent: float = 0.0  # 0-100
    cpu_ratio: float = 0.0  # 0-1
    disk_used: float = 0.0  # bytes
    disk_total: float = 0.0  # bytes
    disk_ratio: float = 0.0  # 0-1
    timestamp: float = field(default_factory=time.time)

    def max_ratio(self) -> float:
        """Return the highest resource usage ratio."""
        ratios = [self.ram_ratio, self.cpu_ratio, self.disk_ratio]
        if self.gpu_vram_ratio is not None:
            ratios.append(self.gpu_vram_ratio)
        if self.gpu_util is not None:
            ratios.append(self.gpu_util)
        return max(ratios) if ratios else 0.0

    def to_dict(self) -> dict:
        """Convert to dict for logging/metrics."""
        return {
            "gpu_vram_ratio": self.gpu_vram_ratio,
            "gpu_util": self.gpu_util,
            "ram_ratio": self.ram_ratio,
            "cpu_ratio": self.cpu_ratio,
            "disk_ratio": self.disk_ratio,
        }


@dataclass
class ResourceEstimate:
    """Estimated resource requirements for an operation."""

    vram_bytes: float = 0.0
    ram_bytes: float = 0.0
    description: str = ""


@dataclass
class Admission:
    """Admission decision from the guard."""

    admitted: bool
    degraded: bool = False
    deferred: bool = False
    rejected: bool = False
    level: DegradationLevel = DegradationLevel.NORMAL
    params: dict = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def admit_full(cls) -> "Admission":
        """Full admission - proceed normally."""
        return cls(admitted=True)

    @classmethod
    def admit_degraded(cls, level: DegradationLevel, params: dict) -> "Admission":
        """Degraded admission - proceed with reduced params."""
        return cls(admitted=True, degraded=True, level=level, params=params)

    @classmethod
    def defer(cls, reason: str) -> "Admission":
        """Defer - queue and retry later (cold path only)."""
        return cls(admitted=False, deferred=True, reason=reason)

    @classmethod
    def reject(cls, reason: str) -> "Admission":
        """Reject - cannot proceed (new sessions only)."""
        return cls(admitted=False, rejected=True, reason=reason)


class ResourceGuard:
    """The 90% ceiling enforcer.

    Usage:
        guard = ResourceGuard(settings.resource)
        await guard.start()

        # Before heavy operation:
        admission = await guard.acquire(
            ResourceEstimate(vram_bytes=1.5e9, description="Load STT model"),
            path="hot"
        )
        if admission.admitted:
            # Proceed, respecting any degraded params
            ...
        elif admission.deferred:
            # Queue for later (cold path)
            ...
        elif admission.rejected:
            # Reject new session
            ...
    """

    def __init__(
        self,
        ceiling: float = 0.90,
        soft: float = 0.80,
        sample_interval: float = 1.0,
        rolling_window: int = 3,
        hysteresis_margin: float = 0.05,
    ) -> None:
        """Initialize the resource guard.

        Args:
            ceiling: Hard ceiling ratio (default 0.90)
            soft: Soft warning threshold (default 0.80)
            sample_interval: Seconds between samples (default 1.0)
            rolling_window: Samples for rolling average (default 3)
            hysteresis_margin: Margin for level recovery (default 0.05)
        """
        self.ceiling = ceiling
        self.soft = soft
        self.sample_interval = sample_interval
        self.rolling_window = rolling_window
        self.hysteresis_margin = hysteresis_margin

        # Thresholds for degradation levels (derived from ceiling)
        self.threshold_moderate = (self.ceiling + self.soft) / 2  # ~0.85
        self.threshold_severe = self.ceiling - 0.02  # ~0.88

        # State
        self._samples: deque[ResourceSnapshot] = deque(maxlen=rolling_window)
        self._current_level = DegradationLevel.NORMAL
        self._running = False
        self._sample_task: asyncio.Task | None = None
        self._gpu_initialized = False
        self._lock = asyncio.Lock()

        # Initialize GPU if available
        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._gpu_initialized = True
                device_count = pynvml.nvmlDeviceGetCount()
                logger.info("gpu_initialized", device_count=device_count)
            except Exception as e:
                logger.warning("gpu_init_failed", error=str(e))

    async def start(self) -> None:
        """Start the background sampling loop."""
        if self._running:
            return
        self._running = True
        self._sample_task = asyncio.create_task(self._sample_loop())
        logger.info(
            "resource_guard_started",
            ceiling=self.ceiling,
            soft=self.soft,
            interval=self.sample_interval,
        )

    async def stop(self) -> None:
        """Stop the background sampling loop."""
        self._running = False
        if self._sample_task:
            self._sample_task.cancel()
            try:
                await self._sample_task
            except asyncio.CancelledError:
                pass
        if self._gpu_initialized:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        logger.info("resource_guard_stopped")

    async def _sample_loop(self) -> None:
        """Background sampling loop - never busy-wait."""
        while self._running:
            try:
                start = time.perf_counter()
                snapshot = await self._take_snapshot()
                duration = time.perf_counter() - start

                # Record metrics
                guard_sample_duration_seconds.observe(duration)
                self._update_metrics(snapshot)

                # Update degradation level
                await self._update_level(snapshot)

                # Sleep until next sample
                await asyncio.sleep(self.sample_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("sample_loop_error", error=str(e))
                await asyncio.sleep(self.sample_interval)

    async def _take_snapshot(self) -> ResourceSnapshot:
        """Take a resource snapshot."""
        snapshot = ResourceSnapshot()

        # CPU (run in thread to avoid blocking)
        loop = asyncio.get_event_loop()
        cpu_percent = await loop.run_in_executor(None, lambda: psutil.cpu_percent(interval=0.1))
        snapshot.cpu_percent = cpu_percent
        snapshot.cpu_ratio = cpu_percent / 100.0

        # RAM
        mem = psutil.virtual_memory()
        snapshot.ram_used = mem.used
        snapshot.ram_total = mem.total
        snapshot.ram_ratio = mem.percent / 100.0

        # Disk (for the data directory)
        disk = psutil.disk_usage("/")
        snapshot.disk_used = disk.used
        snapshot.disk_total = disk.total
        snapshot.disk_ratio = disk.percent / 100.0

        # GPU (if available)
        if self._gpu_initialized:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                snapshot.gpu_vram_used = mem_info.used
                snapshot.gpu_vram_total = mem_info.total
                snapshot.gpu_vram_ratio = mem_info.used / mem_info.total

                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                snapshot.gpu_util = util.gpu / 100.0
            except Exception as e:
                logger.debug("gpu_sample_failed", error=str(e))

        async with self._lock:
            self._samples.append(snapshot)

        return snapshot

    def _update_metrics(self, snapshot: ResourceSnapshot) -> None:
        """Update Prometheus metrics from snapshot."""
        resource_usage_ratio.labels(resource="cpu").set(snapshot.cpu_ratio)
        resource_usage_ratio.labels(resource="ram").set(snapshot.ram_ratio)
        resource_usage_ratio.labels(resource="disk").set(snapshot.disk_ratio)
        if snapshot.gpu_vram_ratio is not None:
            resource_usage_ratio.labels(resource="gpu_vram").set(snapshot.gpu_vram_ratio)
        if snapshot.gpu_util is not None:
            resource_usage_ratio.labels(resource="gpu_util").set(snapshot.gpu_util)

    async def _update_level(self, snapshot: ResourceSnapshot) -> None:
        """Update degradation level based on current snapshot."""
        max_ratio = self._rolling_max_ratio()
        new_level = self._compute_level(max_ratio)

        if new_level != self._current_level:
            old_level = self._current_level
            self._current_level = new_level
            degradation_level_gauge.set(new_level)

            # Log level transition
            logger.warning(
                "degradation_level_changed",
                old_level=old_level.name,
                new_level=new_level.name,
                max_ratio=max_ratio,
                snapshot=snapshot.to_dict(),
            )

            # Track ceiling hits
            if new_level >= DegradationLevel.SEVERE:
                for resource, ratio in snapshot.to_dict().items():
                    if ratio is not None and ratio >= self.ceiling:
                        resource_ceiling_hits_total.labels(resource=resource).inc()

    def _rolling_max_ratio(self) -> float:
        """Get the max ratio across the rolling window."""
        if not self._samples:
            return 0.0
        return max(s.max_ratio() for s in self._samples)

    def _compute_level(self, max_ratio: float) -> DegradationLevel:
        """Compute degradation level from max ratio with hysteresis."""
        current = self._current_level

        # Going up is immediate
        if max_ratio >= self.ceiling:
            return DegradationLevel.CRITICAL
        elif max_ratio >= self.threshold_severe:
            return DegradationLevel.SEVERE
        elif max_ratio >= self.threshold_moderate:
            return DegradationLevel.MODERATE
        elif max_ratio >= self.soft:
            return DegradationLevel.SOFT

        # Going down requires hysteresis
        if current == DegradationLevel.CRITICAL:
            if max_ratio < self.threshold_severe - self.hysteresis_margin:
                return DegradationLevel.SEVERE
            return current
        elif current == DegradationLevel.SEVERE:
            if max_ratio < self.threshold_moderate - self.hysteresis_margin:
                return DegradationLevel.MODERATE
            return current
        elif current == DegradationLevel.MODERATE:
            if max_ratio < self.soft - self.hysteresis_margin:
                return DegradationLevel.SOFT
            return current
        elif current == DegradationLevel.SOFT:
            if max_ratio < self.soft - self.hysteresis_margin:
                return DegradationLevel.NORMAL
            return current

        return DegradationLevel.NORMAL

    def snapshot(self) -> ResourceSnapshot | None:
        """Get the latest snapshot."""
        return self._samples[-1] if self._samples else None

    def headroom(self, resource: str) -> float:
        """Get headroom (1 - usage) for a resource."""
        snap = self.snapshot()
        if not snap:
            return 1.0

        ratios = {
            "cpu": snap.cpu_ratio,
            "ram": snap.ram_ratio,
            "disk": snap.disk_ratio,
            "gpu_vram": snap.gpu_vram_ratio,
            "gpu_util": snap.gpu_util,
        }
        ratio = ratios.get(resource)
        if ratio is None:
            return 1.0
        return max(0.0, 1.0 - ratio)

    @property
    def degradation_level(self) -> DegradationLevel:
        """Current degradation level."""
        return self._current_level

    async def acquire(
        self,
        need: ResourceEstimate | None = None,
        path: Literal["hot", "cold"] = "hot",
    ) -> Admission:
        """Request admission for an operation.

        Args:
            need: Estimated resource requirements (optional)
            path: "hot" (live user waiting) or "cold" (deferrable)

        Returns:
            Admission decision - caller MUST honor it
        """
        level = self._current_level
        snap = self.snapshot()

        # Check if we'd exceed ceiling with this operation
        if need and snap:
            would_exceed = False
            if need.vram_bytes > 0 and snap.gpu_vram_total:
                projected = (snap.gpu_vram_used + need.vram_bytes) / snap.gpu_vram_total
                would_exceed = projected >= self.ceiling
            if need.ram_bytes > 0:
                projected = (snap.ram_used + need.ram_bytes) / snap.ram_total
                would_exceed = would_exceed or projected >= self.ceiling

            if would_exceed:
                level = DegradationLevel.CRITICAL

        # Hot path: must respond, may degrade but never fully block
        if path == "hot":
            if level == DegradationLevel.CRITICAL:
                # New sessions rejected, but in-flight turns still served
                if need and need.description.startswith("new_session"):
                    sessions_rejected_total.inc()
                    return Admission.reject(
                        "System at capacity. Please try again shortly."
                    )
                # In-flight: heavily degraded but still served
                return Admission.admit_degraded(
                    level,
                    {
                        "max_tokens": 50,
                        "use_smaller_model": True,
                        "skip_tts": True,
                    },
                )
            elif level >= DegradationLevel.SEVERE:
                return Admission.admit_degraded(
                    level,
                    {
                        "max_tokens": 100,
                        "use_smaller_model": True,
                        "lower_tts_quality": True,
                    },
                )
            elif level >= DegradationLevel.MODERATE:
                return Admission.admit_degraded(
                    level,
                    {
                        "max_tokens": 256,
                        "context_limit": 2000,
                    },
                )
            elif level >= DegradationLevel.SOFT:
                return Admission.admit_degraded(
                    level,
                    {
                        "max_tokens": 512,
                    },
                )
            else:
                return Admission.admit_full()

        # Cold path: always deferrable
        else:
            if level >= DegradationLevel.SOFT:
                jobs_deferred_total.inc()
                return Admission.defer(f"Resource pressure at level {level.name}")
            else:
                return Admission.admit_full()

    async def check_startup_budget(
        self,
        models: list[tuple[str, float]],
    ) -> tuple[bool, str]:
        """Check if models fit within budget at startup.

        Args:
            models: List of (name, vram_bytes) tuples

        Returns:
            (fits, message) - whether models fit and explanation
        """
        # Take a fresh snapshot
        snapshot = await self._take_snapshot()

        if snapshot.gpu_vram_total is None:
            # CPU-only mode
            return True, "Running in CPU-only mode"

        total_needed = sum(vram for _, vram in models)
        available = snapshot.gpu_vram_total * self.ceiling - snapshot.gpu_vram_used
        headroom = snapshot.gpu_vram_total * (1 - self.ceiling)

        if total_needed > available:
            model_list = ", ".join(f"{name} ({vram/1e9:.1f}GB)" for name, vram in models)
            return False, (
                f"Insufficient VRAM for minimum model set.\n"
                f"Models needed: {model_list}\n"
                f"Total needed: {total_needed/1e9:.1f}GB\n"
                f"Available under {self.ceiling*100:.0f}% ceiling: {available/1e9:.1f}GB\n"
                f"Consider: smaller LLM quant (Q3_K_S) or distil-large-v3 for STT"
            )

        return True, (
            f"Models fit within budget.\n"
            f"Total: {total_needed/1e9:.1f}GB, "
            f"Available: {available/1e9:.1f}GB, "
            f"Headroom: {headroom/1e9:.1f}GB"
        )

    @property
    def is_running(self) -> bool:
        """Whether the guard is running."""
        return self._running

    @property
    def has_gpu(self) -> bool:
        """Whether GPU monitoring is available."""
        return self._gpu_initialized
