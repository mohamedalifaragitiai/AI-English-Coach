#!/usr/bin/env python3
"""Profile the hot path pipeline for latency analysis.

Measures individual component timings and total end-to-end latency.
Target: <2 seconds from speech end to TTS audio start.

Usage:
    uv run python scripts/profile_hotpath.py
    uv run python scripts/profile_hotpath.py --iterations 10
    uv run python scripts/profile_hotpath.py --audio-file test.wav
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.logging import get_logger, setup_logging
from backend.core.model_manager import ModelManager
from backend.core.resource_guard import ResourceGuard
from backend.hotpath.dialogue import ConversationContext, DialogueService
from backend.hotpath.stt import STTService
from backend.hotpath.tts import TTSService
from backend.hotpath.vad import SileroVAD, VADConfig
from config.settings import get_settings

logger = get_logger(__name__)


def generate_test_audio(duration_seconds: float = 2.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate test audio with simulated speech pattern.

    Creates a sine wave with varying amplitude to simulate speech.
    """
    t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds))

    # Base frequency with harmonics
    audio = np.sin(2 * np.pi * 200 * t)  # Fundamental
    audio += 0.5 * np.sin(2 * np.pi * 400 * t)  # First harmonic
    audio += 0.3 * np.sin(2 * np.pi * 600 * t)  # Second harmonic

    # Amplitude envelope (simulate speech pattern)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)
    audio = audio * envelope

    # Normalize to [-1, 1]
    audio = audio / np.abs(audio).max()

    return audio.astype(np.float32)


def load_audio_file(filepath: str, target_sr: int = 16000) -> np.ndarray:
    """Load audio file and resample if needed."""
    import wave

    with wave.open(filepath, "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError("Audio must be mono")

        sample_rate = wf.getframerate()
        audio_bytes = wf.readframes(wf.getnframes())

    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # Simple resampling if needed
    if sample_rate != target_sr:
        ratio = target_sr / sample_rate
        new_length = int(len(audio) * ratio)
        audio = np.interp(np.linspace(0, len(audio), new_length), np.arange(len(audio)), audio)

    return audio


class HotPathProfiler:
    """Profile the hot path pipeline."""

    def __init__(self, guard: ResourceGuard, model_manager: ModelManager) -> None:
        self.guard = guard
        self.model_manager = model_manager

        # Initialize components
        self.vad = SileroVAD(VADConfig())
        self.stt: STTService | None = None
        self.dialogue: DialogueService | None = None
        self.tts: TTSService | None = None

        # Results storage
        self.results: list[dict] = []

    async def initialize(self) -> None:
        """Initialize hot path components."""
        stt_model = self.model_manager.get_stt()
        llm_config = self.model_manager.get_llm_config()
        tts_config = self.model_manager.get_tts_config()

        if stt_model:
            self.stt = STTService(stt_model, self.guard)
            logger.info("stt_initialized")

        if llm_config:
            self.dialogue = DialogueService(llm_config, self.guard)
            logger.info("dialogue_initialized")

        if tts_config:
            self.tts = TTSService(tts_config, self.guard)
            logger.info("tts_initialized")

    async def profile_iteration(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> dict:
        """Run one profiling iteration through the full pipeline.

        Returns timing breakdown for each component.
        """
        timings = {
            "vad_ms": 0.0,
            "stt_ms": 0.0,
            "llm_ms": 0.0,
            "llm_ttft_ms": 0.0,  # Time to first token
            "tts_ms": 0.0,
            "total_ms": 0.0,
        }

        total_start = time.perf_counter()

        # VAD (process the audio to simulate real usage)
        vad_start = time.perf_counter()
        self.vad.reset()
        for i in range(0, len(audio), 512):
            frame = audio[i : i + 512]
            if len(frame) == 512:
                self.vad.process(frame)
        timings["vad_ms"] = (time.perf_counter() - vad_start) * 1000

        # STT
        transcript = ""
        if self.stt:
            stt_start = time.perf_counter()
            result = await self.stt.transcribe_array(audio, sample_rate)
            timings["stt_ms"] = (time.perf_counter() - stt_start) * 1000
            transcript = result.text if result.text else "Hello, how are you today?"
        else:
            transcript = "Hello, how are you today?"

        # LLM
        response_text = ""
        if self.dialogue and transcript:
            context = ConversationContext(learner_level=3, session_mode="free")
            context.add_system_prompt()

            llm_start = time.perf_counter()
            first_token_time = None

            chunks = []
            async for chunk in self.dialogue.chat_stream(context, transcript):
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                    timings["llm_ttft_ms"] = (first_token_time - llm_start) * 1000
                chunks.append(chunk)

            response_text = "".join(chunks)
            timings["llm_ms"] = (time.perf_counter() - llm_start) * 1000
        else:
            response_text = "I'm doing well, thank you for asking!"

        # TTS
        if self.tts and response_text:
            tts_start = time.perf_counter()
            tts_result = await self.tts.synthesize(response_text)
            timings["tts_ms"] = (time.perf_counter() - tts_start) * 1000

        timings["total_ms"] = (time.perf_counter() - total_start) * 1000
        timings["transcript"] = transcript[:50]
        timings["response"] = response_text[:50]

        return timings

    async def run_profile(
        self,
        audio: np.ndarray,
        iterations: int = 5,
        warmup: int = 1,
        sample_rate: int = 16000,
    ) -> dict:
        """Run multiple profiling iterations and compute statistics."""
        logger.info("profiling_start", iterations=iterations, warmup=warmup)

        # Warmup runs
        for i in range(warmup):
            logger.info("warmup_iteration", iteration=i + 1)
            await self.profile_iteration(audio, sample_rate)

        # Profile runs
        self.results = []
        for i in range(iterations):
            logger.info("profile_iteration", iteration=i + 1)
            timings = await self.profile_iteration(audio, sample_rate)
            self.results.append(timings)
            logger.info(
                "iteration_complete",
                total_ms=f"{timings['total_ms']:.1f}",
                stt_ms=f"{timings['stt_ms']:.1f}",
                llm_ms=f"{timings['llm_ms']:.1f}",
                tts_ms=f"{timings['tts_ms']:.1f}",
            )

        # Compute statistics
        stats = self._compute_stats()
        return stats

    def _compute_stats(self) -> dict:
        """Compute statistics from results."""
        if not self.results:
            return {}

        metrics = ["vad_ms", "stt_ms", "llm_ms", "llm_ttft_ms", "tts_ms", "total_ms"]
        stats = {}

        for metric in metrics:
            values = [r[metric] for r in self.results if metric in r]
            if values:
                stats[metric] = {
                    "mean": statistics.mean(values),
                    "median": statistics.median(values),
                    "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                    "min": min(values),
                    "max": max(values),
                }

        return stats

    def print_report(self, stats: dict) -> None:
        """Print formatted profiling report."""
        print("\n" + "=" * 70)
        print("HOT PATH PROFILING REPORT")
        print("=" * 70)

        components = [
            ("VAD", "vad_ms"),
            ("STT", "stt_ms"),
            ("LLM", "llm_ms"),
            ("LLM TTFT", "llm_ttft_ms"),
            ("TTS", "tts_ms"),
            ("TOTAL", "total_ms"),
        ]

        print(f"\n{'Component':<12} {'Mean':>10} {'Median':>10} {'Stdev':>10} {'Min':>10} {'Max':>10}")
        print("-" * 70)

        for name, key in components:
            if key in stats:
                s = stats[key]
                print(
                    f"{name:<12} {s['mean']:>9.1f}ms {s['median']:>9.1f}ms "
                    f"{s['stdev']:>9.1f}ms {s['min']:>9.1f}ms {s['max']:>9.1f}ms"
                )

        # Budget analysis
        print("\n" + "-" * 70)
        total_mean = stats.get("total_ms", {}).get("mean", 0)
        budget_ms = 2000
        within_budget = total_mean < budget_ms

        print(f"\nTarget Budget: {budget_ms}ms")
        print(f"Actual Mean:   {total_mean:.1f}ms")
        print(f"Status:        {'WITHIN BUDGET' if within_budget else 'OVER BUDGET'}")

        if not within_budget:
            overage = total_mean - budget_ms
            print(f"Overage:       {overage:.1f}ms ({overage/budget_ms*100:.1f}%)")

        # Component breakdown
        print("\nComponent Breakdown (% of total):")
        for name, key in components[:-1]:  # Exclude TOTAL
            if key in stats and "total_ms" in stats:
                pct = stats[key]["mean"] / stats["total_ms"]["mean"] * 100
                bar = "#" * int(pct / 2)
                print(f"  {name:<12} {pct:>5.1f}% {bar}")

        print("\n" + "=" * 70)


async def main() -> None:
    """Main profiling entry point."""
    parser = argparse.ArgumentParser(description="Profile hot path latency")
    parser.add_argument("--iterations", type=int, default=5, help="Number of profile iterations")
    parser.add_argument("--warmup", type=int, default=1, help="Number of warmup iterations")
    parser.add_argument("--audio-file", type=str, help="Path to audio file (WAV, 16kHz mono)")
    parser.add_argument("--audio-duration", type=float, default=2.0, help="Generated audio duration (seconds)")
    args = parser.parse_args()

    setup_logging("INFO")
    settings = get_settings()

    print("Initializing hot path profiler...")

    # Initialize resource guard
    guard = ResourceGuard(
        ceiling=settings.resource.ceiling,
        soft=settings.resource.soft,
    )
    await guard.start()

    try:
        # Initialize model manager
        model_manager = ModelManager(
            guard=guard,
            stt_model=settings.model.stt_model,
            llm_model=settings.model.llm_model,
            tts_model=settings.model.tts_model,
            ollama_host=settings.model.ollama_host,
        )

        print("Loading models...")
        await model_manager.initialize()

        if not model_manager.is_initialized:
            print("ERROR: Models failed to load. Run scripts/setup_models.py first.")
            return

        # Create profiler
        profiler = HotPathProfiler(guard, model_manager)
        await profiler.initialize()

        # Load or generate audio
        if args.audio_file:
            print(f"Loading audio from: {args.audio_file}")
            audio = load_audio_file(args.audio_file)
        else:
            print(f"Generating {args.audio_duration}s test audio...")
            audio = generate_test_audio(args.audio_duration)

        print(f"Audio duration: {len(audio) / 16000:.2f}s ({len(audio)} samples)")

        # Run profiling
        stats = await profiler.run_profile(
            audio,
            iterations=args.iterations,
            warmup=args.warmup,
        )

        # Print report
        profiler.print_report(stats)

        # Cleanup
        await model_manager.shutdown()

    finally:
        await guard.stop()


if __name__ == "__main__":
    asyncio.run(main())
