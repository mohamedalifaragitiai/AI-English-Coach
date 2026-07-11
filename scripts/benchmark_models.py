#!/usr/bin/env python3
"""Benchmark models for latency and VRAM usage.

Measures real performance on the actual hardware to enable
evidence-based model choices.

Run with: uv run python scripts/benchmark_models.py
"""

import asyncio
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

from config.settings import get_settings

# Try to import pynvml for GPU monitoring
try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except Exception:
    GPU_AVAILABLE = False


def get_gpu_memory() -> tuple[float, float] | None:
    """Get GPU memory (used, total) in GB."""
    if not GPU_AVAILABLE:
        return None
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return info.used / 1e9, info.total / 1e9
    except Exception:
        return None


def print_header(msg: str) -> None:
    """Print section header."""
    print(f"\n{'='*60}")
    print(f" {msg}")
    print(f"{'='*60}")


def print_metric(name: str, value: str) -> None:
    """Print a metric."""
    print(f"  {name:30} {value}")


def create_test_audio(duration_seconds: float = 5.0, sample_rate: int = 16000) -> str:
    """Create a test audio file with sine wave."""
    samples = int(duration_seconds * sample_rate)
    t = np.linspace(0, duration_seconds, samples, dtype=np.float32)
    # Generate a simple tone
    audio = 0.5 * np.sin(2 * np.pi * 440 * t)
    audio = (audio * 32767).astype(np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())
        return f.name


async def benchmark_stt(model_name: str = "large-v3-turbo") -> dict:
    """Benchmark STT model."""
    print_header(f"STT Benchmark: {model_name}")

    results = {
        "model": model_name,
        "success": False,
    }

    try:
        from faster_whisper import WhisperModel

        # Measure VRAM before loading
        vram_before = get_gpu_memory()

        # Load model
        print("  Loading model...")
        load_start = time.perf_counter()

        device = "cuda" if GPU_AVAILABLE else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        model = WhisperModel(model_name, device=device, compute_type=compute_type)

        load_time = time.perf_counter() - load_start
        print_metric("Load time:", f"{load_time:.2f}s")

        # Measure VRAM after loading
        vram_after = get_gpu_memory()
        if vram_before and vram_after:
            vram_used = vram_after[0] - vram_before[0]
            print_metric("VRAM used:", f"{vram_used:.2f} GB")
            results["vram_gb"] = vram_used

        # Create test audio
        test_audio = create_test_audio(5.0)

        # Benchmark transcription
        print("  Running transcription...")
        times = []
        for i in range(3):
            start = time.perf_counter()
            segments, info = model.transcribe(test_audio, language="en")
            # Consume the iterator
            for _ in segments:
                pass
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            print(f"    Run {i+1}: {elapsed:.2f}s (RTF: {elapsed/5.0:.2f})")

        avg_time = sum(times) / len(times)
        rtf = avg_time / 5.0

        print_metric("Average time (5s audio):", f"{avg_time:.2f}s")
        print_metric("Real-time factor:", f"{rtf:.2f}x")

        results["avg_time_5s"] = avg_time
        results["rtf"] = rtf
        results["device"] = device
        results["success"] = True

        # Cleanup
        Path(test_audio).unlink(missing_ok=True)
        del model

    except Exception as e:
        print(f"  ERROR: {e}")
        results["error"] = str(e)

    return results


async def benchmark_llm(model_name: str = "qwen2.5:7b-instruct-q4_K_M") -> dict:
    """Benchmark LLM via Ollama."""
    print_header(f"LLM Benchmark: {model_name}")

    results = {
        "model": model_name,
        "success": False,
    }

    try:
        import httpx

        settings = get_settings()
        host = settings.model.ollama_host

        # Check if Ollama is running
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{host}/api/tags", timeout=5.0)
                if response.status_code != 200:
                    print("  ERROR: Ollama not responding")
                    return results
            except httpx.ConnectError:
                print("  ERROR: Ollama not running. Start with: ollama serve")
                return results

        # Measure VRAM before
        vram_before = get_gpu_memory()

        # Run inference to load model into VRAM
        print("  Loading model (first inference)...")

        test_prompt = "Say hello in one sentence."
        messages = [{"role": "user", "content": test_prompt}]

        async with httpx.AsyncClient() as client:
            # First call loads the model
            start = time.perf_counter()
            response = await client.post(
                f"{host}/api/chat",
                json={"model": model_name, "messages": messages, "stream": False},
                timeout=120.0,
            )
            first_time = time.perf_counter() - start

            if response.status_code != 200:
                print(f"  ERROR: {response.text}")
                return results

            print_metric("First inference (cold):", f"{first_time:.2f}s")

            # Measure VRAM after loading
            vram_after = get_gpu_memory()
            if vram_before and vram_after:
                vram_used = vram_after[0] - vram_before[0]
                print_metric("VRAM used:", f"{vram_used:.2f} GB")
                results["vram_gb"] = vram_used

            # Benchmark warm inference
            print("  Running warm inferences...")
            times = []
            tokens = []

            for i in range(3):
                start = time.perf_counter()
                response = await client.post(
                    f"{host}/api/chat",
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": "Count from 1 to 10."}],
                        "stream": False,
                        "options": {"num_predict": 50},
                    },
                    timeout=60.0,
                )
                elapsed = time.perf_counter() - start
                data = response.json()
                token_count = data.get("eval_count", 0)

                times.append(elapsed)
                tokens.append(token_count)
                tps = token_count / elapsed if elapsed > 0 else 0
                print(f"    Run {i+1}: {elapsed:.2f}s, {token_count} tokens, {tps:.1f} tok/s")

            avg_time = sum(times) / len(times)
            avg_tokens = sum(tokens) / len(tokens)
            avg_tps = avg_tokens / avg_time if avg_time > 0 else 0

            print_metric("Average time:", f"{avg_time:.2f}s")
            print_metric("Average tokens/sec:", f"{avg_tps:.1f}")

            results["avg_time"] = avg_time
            results["tokens_per_sec"] = avg_tps
            results["success"] = True

    except Exception as e:
        print(f"  ERROR: {e}")
        results["error"] = str(e)

    return results


async def benchmark_tts() -> dict:
    """Benchmark TTS."""
    print_header("TTS Benchmark: Piper")

    results = {
        "model": "piper",
        "success": False,
    }

    try:
        import subprocess

        # Check if piper is available
        result = subprocess.run(["piper", "--help"], capture_output=True)
        if result.returncode != 0:
            print("  Piper not installed. Skipping TTS benchmark.")
            return results

        settings = get_settings()
        model_path = Path("models/piper") / f"{settings.model.tts_model}.onnx"

        if not model_path.exists():
            print(f"  TTS model not found: {model_path}")
            return results

        # Benchmark synthesis
        test_texts = [
            "Hello, how are you today?",
            "The quick brown fox jumps over the lazy dog.",
            "Learning English is a wonderful journey that opens many doors.",
        ]

        times = []
        for text in test_texts:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                output_file = f.name

            start = time.perf_counter()
            result = subprocess.run(
                f'echo "{text}" | piper --model {model_path} --output_file {output_file}',
                shell=True,
                capture_output=True,
                timeout=30,
            )
            elapsed = time.perf_counter() - start

            if result.returncode == 0:
                times.append(elapsed)
                print(f"    '{text[:30]}...': {elapsed:.2f}s")
                Path(output_file).unlink(missing_ok=True)

        if times:
            avg_time = sum(times) / len(times)
            print_metric("Average time:", f"{avg_time:.2f}s")
            print_metric("Device:", "CPU")
            results["avg_time"] = avg_time
            results["device"] = "cpu"
            results["success"] = True

    except FileNotFoundError:
        print("  Piper not installed")
    except Exception as e:
        print(f"  ERROR: {e}")
        results["error"] = str(e)

    return results


async def main() -> None:
    """Run all benchmarks."""
    print_header("English Coach Model Benchmark")

    # System info
    print_header("System Information")
    print_metric("CPU:", f"{psutil.cpu_count()} cores")
    print_metric("RAM:", f"{psutil.virtual_memory().total / 1e9:.1f} GB")

    if GPU_AVAILABLE:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        print_metric("GPU:", name)
        print_metric("VRAM:", f"{mem.total / 1e9:.1f} GB")
    else:
        print_metric("GPU:", "Not available")

    settings = get_settings()

    # Run benchmarks
    results = {}

    results["stt"] = await benchmark_stt(settings.model.stt_model)
    results["llm"] = await benchmark_llm(settings.model.llm_model)
    results["tts"] = await benchmark_tts()

    # Summary
    print_header("Summary")

    total_vram = 0
    for name, data in results.items():
        status = "✓" if data.get("success") else "✗"
        vram = data.get("vram_gb", 0)
        total_vram += vram
        print(f"  {status} {name.upper():5} {data['model']:30} VRAM: {vram:.1f} GB")

    print()
    print_metric("Total VRAM:", f"{total_vram:.1f} GB")
    print_metric("90% of 8GB:", "7.2 GB")

    if total_vram > 7.2:
        print("\n  WARNING: Models may exceed VRAM budget!")
    else:
        print("\n  Models fit within VRAM budget.")


if __name__ == "__main__":
    asyncio.run(main())
