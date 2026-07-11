#!/usr/bin/env python3
"""One-time model download script.

Downloads and sets up all required models:
- Faster-Whisper (large-v3-turbo or distil-large-v3)
- Ollama LLM (pulls the model if not present)
- Piper TTS (downloads voice model)

All downloads are logged and disk headroom is checked before downloading.

Run with: uv run python scripts/setup_models.py
"""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import psutil
from faster_whisper import WhisperModel

from config.settings import get_settings

# ANSI colors for output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(msg: str) -> None:
    """Print a section header."""
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}{msg}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


def print_status(msg: str, status: str = "info") -> None:
    """Print a status message."""
    color = {"ok": GREEN, "warn": YELLOW, "error": RED}.get(status, "")
    print(f"{color}{msg}{RESET}")


def check_disk_space(required_gb: float = 10.0) -> bool:
    """Check if we have enough disk space."""
    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024**3)
    print_status(f"Disk space: {free_gb:.1f} GB free")

    if free_gb < required_gb:
        print_status(f"WARNING: Less than {required_gb} GB free!", "warn")
        return False
    return True


def setup_faster_whisper(model_name: str = "large-v3-turbo") -> bool:
    """Download Faster-Whisper model.

    The model will be cached in ~/.cache/huggingface/hub/
    """
    print_header(f"Setting up Faster-Whisper ({model_name})")

    try:
        print_status("Downloading model (this may take a few minutes)...")

        # This will download the model if not cached
        # Using CPU for download to avoid VRAM issues
        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=None,  # Use default cache
        )

        print_status(f"Faster-Whisper {model_name} ready!", "ok")

        # Clean up to free memory
        del model
        return True

    except Exception as e:
        print_status(f"Failed to setup Faster-Whisper: {e}", "error")
        return False


def check_ollama_installed() -> bool:
    """Check if Ollama is installed."""
    return shutil.which("ollama") is not None


async def setup_ollama_model(model_name: str = "qwen2.5:7b-instruct-q4_K_M") -> bool:
    """Pull Ollama model if not present."""
    print_header(f"Setting up Ollama LLM ({model_name})")

    if not check_ollama_installed():
        print_status("Ollama not installed!", "error")
        print_status("Install from: https://ollama.ai/download")
        return False

    settings = get_settings()

    try:
        # Check if Ollama server is running
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{settings.model.ollama_host}/api/tags", timeout=5.0)
                if response.status_code != 200:
                    print_status("Ollama server not responding. Start it with: ollama serve", "error")
                    return False

                # Check if model already exists
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]

                # Check for exact match or partial match
                model_base = model_name.split(":")[0]
                if any(model_name in m or model_base in m for m in models):
                    print_status(f"Model {model_name} already available!", "ok")
                    return True

            except httpx.ConnectError:
                print_status("Cannot connect to Ollama. Start it with: ollama serve", "error")
                return False

        # Pull the model
        print_status(f"Pulling model {model_name} (this may take several minutes)...")

        result = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=False,
            text=True,
        )

        if result.returncode == 0:
            print_status(f"Ollama model {model_name} ready!", "ok")
            return True
        else:
            print_status(f"Failed to pull model", "error")
            return False

    except Exception as e:
        print_status(f"Failed to setup Ollama: {e}", "error")
        return False


def setup_piper_tts(voice: str = "en_US-lessac-medium") -> bool:
    """Download Piper TTS voice model.

    Piper models are downloaded from:
    https://github.com/rhasspy/piper/releases
    """
    print_header(f"Setting up Piper TTS ({voice})")

    models_dir = Path("models/piper")
    models_dir.mkdir(parents=True, exist_ok=True)

    model_file = models_dir / f"{voice}.onnx"
    config_file = models_dir / f"{voice}.onnx.json"

    if model_file.exists() and config_file.exists():
        print_status(f"Piper voice {voice} already downloaded!", "ok")
        return True

    # Piper model URLs
    base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
    lang, name_quality = voice.split("-", 1) if "-" in voice else (voice, "")

    # Construct URL path
    # Format: en/en_US/lessac/medium/en_US-lessac-medium.onnx
    parts = voice.split("-")
    if len(parts) >= 3:
        lang_code = parts[0]  # en_US
        lang_short = lang_code.split("_")[0]  # en
        speaker = parts[1]  # lessac
        quality = parts[2]  # medium

        model_url = f"{base_url}/{lang_short}/{lang_code}/{speaker}/{quality}/{voice}.onnx"
        config_url = f"{base_url}/{lang_short}/{lang_code}/{speaker}/{quality}/{voice}.onnx.json"
    else:
        print_status(f"Unknown voice format: {voice}", "error")
        return False

    try:
        print_status(f"Downloading {voice} model...")

        # Download model file
        with httpx.Client(follow_redirects=True, timeout=300.0) as client:
            response = client.get(model_url)
            if response.status_code == 200:
                model_file.write_bytes(response.content)
                print_status(f"Downloaded {model_file.name} ({len(response.content) / 1024 / 1024:.1f} MB)")
            else:
                print_status(f"Failed to download model: HTTP {response.status_code}", "error")
                return False

            # Download config file
            response = client.get(config_url)
            if response.status_code == 200:
                config_file.write_bytes(response.content)
                print_status(f"Downloaded {config_file.name}")
            else:
                print_status(f"Failed to download config: HTTP {response.status_code}", "error")
                return False

        print_status(f"Piper voice {voice} ready!", "ok")
        return True

    except Exception as e:
        print_status(f"Failed to setup Piper: {e}", "error")
        return False


def print_vram_estimate() -> None:
    """Print estimated VRAM usage."""
    print_header("Estimated VRAM Usage")

    estimates = [
        ("Faster-Whisper large-v3-turbo", "~1.5 GB"),
        ("Qwen2.5-7B Q4_K_M (Ollama)", "~5.5 GB"),
        ("Piper TTS", "CPU only"),
        ("Total", "~7.0 GB"),
        ("90% ceiling on 8GB", "7.2 GB usable"),
    ]

    for name, usage in estimates:
        print(f"  {name}: {usage}")

    print_status("\nNote: Actual usage may vary. Run benchmark_models.py to measure.", "warn")


async def main() -> None:
    """Run all model setup."""
    print_header("English Coach Model Setup")

    settings = get_settings()

    # Check disk space
    if not check_disk_space(10.0):
        print_status("Continuing anyway...", "warn")

    results = {}

    # Setup Faster-Whisper
    results["whisper"] = setup_faster_whisper(settings.model.stt_model)

    # Setup Ollama
    results["ollama"] = await setup_ollama_model(settings.model.llm_model)

    # Setup Piper
    results["piper"] = setup_piper_tts(settings.model.tts_model)

    # Print summary
    print_header("Setup Summary")

    for name, success in results.items():
        status = "ok" if success else "error"
        symbol = "✓" if success else "✗"
        print_status(f"  {symbol} {name}", status)

    # Print VRAM estimates
    print_vram_estimate()

    if all(results.values()):
        print_status("\nAll models ready! You can now start the server.", "ok")
    else:
        print_status("\nSome models failed to setup. Check errors above.", "error")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
