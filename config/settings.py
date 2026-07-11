"""Application settings using pydantic-settings.

All settings can be overridden via environment variables or .env file.
Resource ceiling defaults to 90% - this is the core constraint of the project.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResourceSettings(BaseSettings):
    """Resource governance settings - the 90% ceiling."""

    model_config = SettingsConfigDict(env_prefix="RESOURCE_")

    ceiling: float = Field(
        default=0.90,
        ge=0.5,
        le=0.99,
        description="Hard ceiling per resource (default 90%)",
    )
    soft: float = Field(
        default=0.80,
        ge=0.5,
        le=0.95,
        description="Soft warning threshold (default 80%)",
    )
    sample_interval: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Resource sampling interval in seconds",
    )
    rolling_window: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of samples for rolling average",
    )
    hysteresis_margin: float = Field(
        default=0.05,
        ge=0.01,
        le=0.15,
        description="Margin below threshold before recovering degradation level",
    )


class ModelSettings(BaseSettings):
    """Model configuration settings."""

    model_config = SettingsConfigDict(env_prefix="")

    stt_model: str = Field(
        default="large-v3-turbo",
        description="Faster-Whisper model name",
    )
    llm_model: str = Field(
        default="qwen2.5:7b-instruct-q4_K_M",
        description="Ollama LLM model name",
    )
    tts_model: str = Field(
        default="en_US-lessac-medium",
        description="Piper TTS model name",
    )
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL",
    )


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    path: Path = Field(
        default=Path("./data/english_coach.db"),
        description="SQLite database path",
    )


class AudioSettings(BaseSettings):
    """Audio storage configuration."""

    model_config = SettingsConfigDict(env_prefix="AUDIO_")

    storage_path: Path = Field(
        default=Path("./data/audio"),
        description="Path for storing audio files",
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = Field(default="127.0.0.1", description="Server host")
    port: int = Field(default=8000, ge=1, le=65535, description="Server port")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Logging level",
    )

    # Nested settings
    resource: ResourceSettings = Field(default_factory=ResourceSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)

    # Scoring model version - increment when weights/rubrics change
    scoring_model_version: str = Field(
        default="v1.0.0",
        description="Current scoring model version for assessment tracking",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
