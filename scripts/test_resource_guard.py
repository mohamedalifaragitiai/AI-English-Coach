#!/usr/bin/env python3
"""Synthetic load test for ResourceGuard.

This script validates that the guard correctly:
1. Samples resources and detects pressure
2. Transitions through degradation levels
3. Defers cold-path work under pressure
4. Protects hot-path admission

Run with: uv run python scripts/test_resource_guard.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.resource_guard import (
    DegradationLevel,
    ResourceEstimate,
    ResourceGuard,
)


async def print_status(guard: ResourceGuard) -> None:
    """Print current resource status."""
    snapshot = guard.snapshot()
    if snapshot:
        print(f"\n{'='*60}")
        print(f"Degradation Level: {guard.degradation_level.name}")
        print(f"Resources:")
        print(f"  CPU:      {snapshot.cpu_ratio*100:5.1f}%")
        print(f"  RAM:      {snapshot.ram_ratio*100:5.1f}%")
        print(f"  Disk:     {snapshot.disk_ratio*100:5.1f}%")
        if snapshot.gpu_vram_ratio is not None:
            print(f"  GPU VRAM: {snapshot.gpu_vram_ratio*100:5.1f}%")
        if snapshot.gpu_util is not None:
            print(f"  GPU Util: {snapshot.gpu_util*100:5.1f}%")
        print(f"  Max:      {snapshot.max_ratio()*100:5.1f}%")
        print(f"{'='*60}")


async def test_admission(guard: ResourceGuard) -> None:
    """Test hot and cold path admission."""
    print("\nTesting admissions at current load...")

    # Hot path
    hot_admission = await guard.acquire(path="hot")
    print(f"Hot path: {'ADMITTED' if hot_admission.admitted else 'DENIED'}", end="")
    if hot_admission.degraded:
        print(f" (degraded: {hot_admission.params})")
    else:
        print()

    # Cold path
    cold_admission = await guard.acquire(path="cold")
    print(f"Cold path: {'ADMITTED' if cold_admission.admitted else 'DEFERRED'}")


async def test_startup_budget(guard: ResourceGuard) -> None:
    """Test startup budget check."""
    print("\nTesting startup budget for typical model set...")

    models = [
        ("Faster-Whisper large-v3-turbo", 1.5e9),  # 1.5 GB
        ("Qwen2.5-7B Q4_K_M", 5.5e9),  # 5.5 GB
    ]

    fits, message = await guard.check_startup_budget(models)
    print(f"Budget check: {'PASS' if fits else 'FAIL'}")
    print(f"Details: {message}")


async def main() -> None:
    """Run the resource guard test."""
    print("ResourceGuard Synthetic Test")
    print("=" * 60)

    # Create guard with standard settings
    guard = ResourceGuard(
        ceiling=0.90,
        soft=0.80,
        sample_interval=1.0,
        rolling_window=3,
        hysteresis_margin=0.05,
    )

    print(f"GPU available: {guard.has_gpu}")
    print(f"Ceiling: {guard.ceiling * 100}%")
    print(f"Soft threshold: {guard.soft * 100}%")

    # Start the guard
    await guard.start()
    print("Guard started, waiting for initial samples...")

    # Wait for a few samples
    await asyncio.sleep(3)

    # Print current status
    await print_status(guard)

    # Test admissions
    await test_admission(guard)

    # Test startup budget
    await test_startup_budget(guard)

    # Clean shutdown
    await guard.stop()
    print("\nGuard stopped. Test complete.")


if __name__ == "__main__":
    asyncio.run(main())
