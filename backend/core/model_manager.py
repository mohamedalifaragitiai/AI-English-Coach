"""Model Manager - Load and manage AI models through ResourceGuard.

Handles loading STT, LLM, and TTS models within the VRAM budget.
Validates startup budget and refuses to start if models won't fit.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from backend.core.logging import get_logger
from backend.core.resource_guard import ResourceGuard

logger = get_logger(__name__)


class ModelType(str, Enum):
    """Types of models we manage."""

    STT = "stt"
    LLM = "llm"
    TTS = "tts"


@dataclass
class ModelInfo:
    """Information about a loaded model."""

    model_type: ModelType
    name: str
    loaded: bool = False
    vram_bytes: float = 0.0
    device: str = "cpu"
    instance: Any = None


# Estimated VRAM usage for models (conservative estimates)
MODEL_VRAM_ESTIMATES = {
    # Faster-Whisper models
    "large-v3-turbo": 1.5e9,  # ~1.5 GB
    "large-v3": 3.0e9,  # ~3.0 GB
    "distil-large-v3": 1.2e9,  # ~1.2 GB
    "medium": 1.0e9,  # ~1.0 GB
    "small": 0.5e9,  # ~0.5 GB
    "base": 0.2e9,  # ~0.2 GB
    "tiny": 0.1e9,  # ~0.1 GB
    # Ollama LLM models (approximate)
    "qwen3:4b": 2.5e9,  # ~2.5 GB (Q4_K_M quantized)
    "qwen3:8b": 5.0e9,  # ~5.0 GB
    "qwen3:1.7b": 1.2e9,  # ~1.2 GB
    "qwen2.5:7b-instruct-q4_K_M": 5.5e9,  # ~5.5 GB
    "qwen2.5:7b": 5.5e9,
    "qwen2.5:1.5b": 1.0e9,  # ~1.0 GB
    "llama3.1:8b-instruct-q4_K_M": 5.5e9,
    "llama3.1:8b": 5.5e9,
    "mistral:7b-instruct-q4_K_M": 5.0e9,
    "phi3:mini": 2.5e9,
    # TTS (CPU-based, no VRAM)
    "piper": 0.0,
}


class ModelManager:
    """Manages AI model lifecycle with ResourceGuard integration.

    Usage:
        manager = ModelManager(guard, settings)
        await manager.initialize()

        # Access models
        stt = manager.get_stt()
        llm = manager.get_llm()
        tts = manager.get_tts()

        # Cleanup
        await manager.shutdown()
    """

    def __init__(
        self,
        guard: ResourceGuard,
        stt_model: str = "large-v3-turbo",
        llm_model: str = "qwen2.5:7b-instruct-q4_K_M",
        tts_model: str = "en_US-lessac-medium",
        ollama_host: str = "http://localhost:11434",
    ) -> None:
        """Initialize the model manager.

        Args:
            guard: ResourceGuard instance for budget checking
            stt_model: Faster-Whisper model name
            llm_model: Ollama model name
            tts_model: Piper voice name
            ollama_host: Ollama server URL
        """
        self.guard = guard
        self.stt_model_name = stt_model
        self.llm_model_name = llm_model
        self.tts_model_name = tts_model
        self.ollama_host = ollama_host

        self._models: dict[ModelType, ModelInfo] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize and load models.

        Returns:
            True if all models loaded successfully

        Raises:
            RuntimeError: If models don't fit within VRAM budget
        """
        logger.info(
            "model_manager_init",
            stt=self.stt_model_name,
            llm=self.llm_model_name,
            tts=self.tts_model_name,
        )

        # Check startup budget
        models_to_load = [
            (f"STT ({self.stt_model_name})", self._get_vram_estimate(self.stt_model_name)),
            (f"LLM ({self.llm_model_name})", self._get_vram_estimate(self.llm_model_name)),
        ]

        fits, message = await self.guard.check_startup_budget(models_to_load)
        if not fits:
            logger.error("model_budget_exceeded", message=message)
            raise RuntimeError(f"Models don't fit in VRAM budget:\n{message}")

        logger.info("model_budget_ok", message=message)

        # Load models
        await self._load_stt()
        await self._load_llm()
        await self._load_tts()

        self._initialized = True
        logger.info("model_manager_ready")
        return True

    async def shutdown(self) -> None:
        """Unload all models and cleanup."""
        logger.info("model_manager_shutdown")

        for model_type, info in self._models.items():
            if info.loaded and info.instance is not None:
                try:
                    # Cleanup model-specific resources
                    if model_type == ModelType.STT:
                        del info.instance
                    info.loaded = False
                    info.instance = None
                    logger.info("model_unloaded", model_type=model_type.value)
                except Exception as e:
                    logger.warning("model_unload_error", model_type=model_type.value, error=str(e))

        self._models.clear()
        self._initialized = False

    def _get_vram_estimate(self, model_name: str) -> float:
        """Get VRAM estimate for a model."""
        # Try exact match first
        if model_name in MODEL_VRAM_ESTIMATES:
            return MODEL_VRAM_ESTIMATES[model_name]

        # Try partial match
        for key, value in MODEL_VRAM_ESTIMATES.items():
            if key in model_name or model_name in key:
                return value

        # Default estimate for unknown models
        logger.warning("unknown_model_vram", model=model_name, default="5GB")
        return 5.0e9

    async def _load_stt(self) -> None:
        """Load Faster-Whisper STT model."""
        from faster_whisper import WhisperModel

        logger.info("loading_stt", model=self.stt_model_name)

        # Determine device and compute type based on guard
        device = "cuda" if self.guard.has_gpu else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        # Check if we should use CPU fallback
        if self.guard.degradation_level.value >= 3:
            device = "cpu"
            compute_type = "int8"
            logger.warning("stt_cpu_fallback", reason="high_resource_pressure")

        try:
            model = WhisperModel(
                self.stt_model_name,
                device=device,
                compute_type=compute_type,
            )

            self._models[ModelType.STT] = ModelInfo(
                model_type=ModelType.STT,
                name=self.stt_model_name,
                loaded=True,
                vram_bytes=self._get_vram_estimate(self.stt_model_name) if device == "cuda" else 0,
                device=device,
                instance=model,
            )

            logger.info("stt_loaded", model=self.stt_model_name, device=device, compute_type=compute_type)

        except Exception as e:
            logger.error("stt_load_failed", error=str(e))
            raise

    async def _load_llm(self) -> None:
        """Initialize Ollama LLM client."""
        import httpx

        logger.info("loading_llm", model=self.llm_model_name)

        try:
            # Verify Ollama is available and model exists
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.ollama_host}/api/tags", timeout=10.0)
                if response.status_code != 200:
                    raise RuntimeError("Ollama server not responding")

                data = response.json()
                models = [m["name"] for m in data.get("models", [])]

                model_base = self.llm_model_name.split(":")[0]
                if not any(self.llm_model_name in m or model_base in m for m in models):
                    raise RuntimeError(
                        f"Model {self.llm_model_name} not found. Run: ollama pull {self.llm_model_name}"
                    )

            # Store LLM config (actual client created per-request)
            self._models[ModelType.LLM] = ModelInfo(
                model_type=ModelType.LLM,
                name=self.llm_model_name,
                loaded=True,
                vram_bytes=self._get_vram_estimate(self.llm_model_name),
                device="cuda" if self.guard.has_gpu else "cpu",
                instance={"host": self.ollama_host, "model": self.llm_model_name},
            )

            logger.info("llm_loaded", model=self.llm_model_name, host=self.ollama_host)

        except httpx.ConnectError:
            logger.error("ollama_not_running")
            raise RuntimeError("Ollama server not running. Start with: ollama serve")
        except Exception as e:
            logger.error("llm_load_failed", error=str(e))
            raise

    async def _load_tts(self) -> None:
        """Initialize Piper TTS."""
        logger.info("loading_tts", model=self.tts_model_name)

        models_dir = Path("models/piper")
        model_file = models_dir / f"{self.tts_model_name}.onnx"
        config_file = models_dir / f"{self.tts_model_name}.onnx.json"

        if not model_file.exists() or not config_file.exists():
            logger.warning(
                "tts_model_missing",
                model=self.tts_model_name,
                path=str(models_dir),
            )
            # TTS is optional - continue without it
            self._models[ModelType.TTS] = ModelInfo(
                model_type=ModelType.TTS,
                name=self.tts_model_name,
                loaded=False,
                vram_bytes=0,
                device="cpu",
                instance=None,
            )
            return

        # Store TTS config (actual synthesis handled by tts.py)
        self._models[ModelType.TTS] = ModelInfo(
            model_type=ModelType.TTS,
            name=self.tts_model_name,
            loaded=True,
            vram_bytes=0,  # CPU-based
            device="cpu",
            instance={
                "model_path": str(model_file),
                "config_path": str(config_file),
            },
        )

        logger.info("tts_loaded", model=self.tts_model_name)

    def get_stt(self) -> Any:
        """Get the STT model instance."""
        info = self._models.get(ModelType.STT)
        if info and info.loaded:
            return info.instance
        return None

    def get_llm_config(self) -> dict | None:
        """Get the LLM configuration."""
        info = self._models.get(ModelType.LLM)
        if info and info.loaded:
            return info.instance
        return None

    def get_tts_config(self) -> dict | None:
        """Get the TTS configuration."""
        info = self._models.get(ModelType.TTS)
        if info and info.loaded:
            return info.instance
        return None

    @property
    def is_initialized(self) -> bool:
        """Check if manager is initialized."""
        return self._initialized

    @property
    def models_status(self) -> dict:
        """Get status of all models."""
        return {
            model_type.value: {
                "name": info.name,
                "loaded": info.loaded,
                "device": info.device,
                "vram_gb": info.vram_bytes / 1e9 if info.vram_bytes else 0,
            }
            for model_type, info in self._models.items()
        }
