"""WebSocket session manager for live conversation.

Handles the real-time audio loop:
mic → VAD → STT → LLM → TTS → speaker

Target latency: <2 seconds end-to-end.
"""

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from backend.core.event_bus import EventBus, UtteranceFinalized
from backend.core.logging import get_logger, set_correlation_id
from backend.core.metrics import (
    active_sessions,
    hotpath_total_duration_seconds,
    utterances_processed_total,
)
from backend.core.model_manager import ModelManager
from backend.core.resource_guard import ResourceGuard
from backend.hotpath.dialogue import ConversationContext, DialogueService
from backend.hotpath.stt import STTService
from backend.hotpath.tts import TTSService
from backend.hotpath.vad import SileroVAD, VADConfig, VADState

logger = get_logger(__name__)


class SessionState(str, Enum):
    """WebSocket session states."""

    CONNECTING = "connecting"
    READY = "ready"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    CLOSED = "closed"


@dataclass
class SessionConfig:
    """Configuration for a conversation session."""

    user_id: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    mode: str = "free"
    learner_level: int = 0
    sample_rate: int = 16000
    save_audio: bool = True
    audio_dir: Path = field(default_factory=lambda: Path("data/audio"))


@dataclass
class TurnMetrics:
    """Metrics for a single conversation turn."""

    turn_id: str
    start_time: float
    vad_time: float = 0.0
    stt_time: float = 0.0
    llm_time: float = 0.0
    tts_time: float = 0.0
    total_time: float = 0.0


class ConversationSession:
    """Manages a single WebSocket conversation session.

    Usage:
        session = ConversationSession(
            websocket=ws,
            config=config,
            model_manager=mm,
            guard=guard,
            event_bus=bus,
        )
        await session.run()
    """

    def __init__(
        self,
        websocket: WebSocket,
        config: SessionConfig,
        model_manager: ModelManager,
        guard: ResourceGuard,
        event_bus: EventBus,
    ) -> None:
        """Initialize conversation session.

        Args:
            websocket: FastAPI WebSocket connection
            config: Session configuration
            model_manager: Model manager instance
            guard: Resource guard instance
            event_bus: Event bus for UtteranceFinalized events
        """
        self.websocket = websocket
        self.config = config
        self.model_manager = model_manager
        self.guard = guard
        self.event_bus = event_bus

        self.state = SessionState.CONNECTING
        self.correlation_id = set_correlation_id()

        # Initialize components
        self.vad = SileroVAD(VADConfig(sample_rate=config.sample_rate))
        self.stt: STTService | None = None
        self.dialogue: DialogueService | None = None
        self.tts: TTSService | None = None

        # Conversation context
        self.context = ConversationContext(
            learner_level=config.learner_level,
            session_mode=config.mode,
        )
        self.context.add_system_prompt()

        # Audio buffer for current turn
        self._audio_buffer: list[np.ndarray] = []
        self._turn_count = 0

        # Ensure audio directory exists
        if config.save_audio:
            config.audio_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> None:
        """Run the conversation session."""
        try:
            await self._initialize()
            await self._send_state("ready")
            self.state = SessionState.READY

            active_sessions.inc()
            logger.info(
                "session_started",
                session_id=self.config.session_id,
                user_id=self.config.user_id,
            )

            # Main message loop
            await self._message_loop()

        except WebSocketDisconnect:
            logger.info("session_disconnected", session_id=self.config.session_id)
        except Exception as e:
            logger.exception("session_error", error=str(e))
            await self._send_error(str(e))
        finally:
            await self._cleanup()

    async def _initialize(self) -> None:
        """Initialize session components."""
        await self.websocket.accept()
        self.state = SessionState.CONNECTING

        # Get model instances
        stt_model = self.model_manager.get_stt()
        llm_config = self.model_manager.get_llm_config()
        tts_config = self.model_manager.get_tts_config()

        if stt_model:
            self.stt = STTService(stt_model, self.guard)

        if llm_config:
            self.dialogue = DialogueService(llm_config, self.guard)

        if tts_config:
            self.tts = TTSService(tts_config, self.guard)

        logger.info(
            "session_initialized",
            stt_available=self.stt is not None,
            llm_available=self.dialogue is not None,
            tts_available=self.tts is not None,
        )

    async def _cleanup(self) -> None:
        """Cleanup session resources."""
        self.state = SessionState.CLOSED
        active_sessions.dec()
        self.vad.reset()
        logger.info("session_closed", session_id=self.config.session_id)

    async def _message_loop(self) -> None:
        """Main WebSocket message loop."""
        while True:
            try:
                message = await self.websocket.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if "bytes" in message:
                    # Binary audio data
                    await self._handle_audio(message["bytes"])
                elif "text" in message:
                    # JSON control message
                    await self._handle_control(json.loads(message["text"]))

            except WebSocketDisconnect:
                break

    async def _handle_audio(self, audio_bytes: bytes) -> None:
        """Handle incoming audio data.

        Args:
            audio_bytes: Raw audio bytes (16-bit PCM, 16kHz, mono)
        """
        # Convert bytes to numpy array
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # If audio is longer than 0.5 seconds, treat as push-to-talk (process immediately)
        # This bypasses VAD for better UX with push-to-talk UI
        if len(audio) > self.config.sample_rate * 0.5:  # > 0.5 seconds
            logger.info("push_to_talk_audio", samples=len(audio), duration_s=len(audio)/self.config.sample_rate)
            await self._process_turn(audio)
            return

        # For smaller chunks, use VAD (streaming mode)
        results = self.vad.process_chunk(audio)
        for result in results:
            if result.state == VADState.SPEAKING:
                self.state = SessionState.LISTENING

            if result.speech_end_time and result.audio_buffer is not None:
                await self._process_turn(result.audio_buffer)
                break

    async def _handle_control(self, message: dict) -> None:
        """Handle control messages.

        Args:
            message: JSON control message
        """
        msg_type = message.get("type")

        if msg_type == "ping":
            await self._send_json({"type": "pong"})

        elif msg_type == "reset":
            self.context = ConversationContext(
                learner_level=self.config.learner_level,
                session_mode=self.config.mode,
            )
            self.context.add_system_prompt()
            self.vad.reset()
            self._audio_buffer.clear()
            await self._send_state("ready")

        elif msg_type == "text":
            # Direct text input (bypass STT)
            text = message.get("text", "")
            if text:
                await self._process_text_turn(text)

    async def _process_turn(self, audio: np.ndarray) -> None:
        """Process a complete speech turn.

        Args:
            audio: Complete audio buffer for the turn
        """
        turn_start = time.perf_counter()
        turn_id = f"turn_{self._turn_count}"
        self._turn_count += 1

        self.state = SessionState.PROCESSING
        await self._send_state("processing")

        metrics = TurnMetrics(turn_id=turn_id, start_time=turn_start)

        try:
            # Save audio if configured
            audio_path = None
            if self.config.save_audio:
                audio_path = await self._save_audio(audio, turn_id)

            # STT
            transcript = ""
            stt_confidence = 0.0

            if self.stt:
                stt_start = time.perf_counter()
                result = await self.stt.transcribe_array(audio, self.config.sample_rate)
                metrics.stt_time = time.perf_counter() - stt_start
                transcript = result.text
                stt_confidence = result.confidence

                await self._send_json({
                    "type": "transcript",
                    "text": transcript,
                    "confidence": stt_confidence,
                })
                logger.info("turn_stt", transcript=transcript[:100], stt_time=metrics.stt_time)

            utterances_processed_total.labels(role="learner").inc()

            # LLM
            response_text = ""
            if self.dialogue and transcript:
                llm_start = time.perf_counter()

                # Stream response
                self.state = SessionState.SPEAKING
                await self._send_state("speaking")

                response_parts = []
                async for chunk in self.dialogue.chat_stream(self.context, transcript):
                    response_parts.append(chunk)
                    await self._send_json({"type": "response_chunk", "text": chunk})

                response_text = "".join(response_parts)
                metrics.llm_time = time.perf_counter() - llm_start

                await self._send_json({
                    "type": "response",
                    "text": response_text,
                })
                logger.info("turn_llm", response=response_text[:100], llm_time=metrics.llm_time)

            utterances_processed_total.labels(role="coach").inc()

            # TTS
            if self.tts and response_text:
                tts_start = time.perf_counter()
                tts_result = await self.tts.synthesize(response_text)
                metrics.tts_time = time.perf_counter() - tts_start

                if tts_result:
                    # Send audio as base64
                    audio_b64 = base64.b64encode(tts_result.audio_bytes).decode("utf-8")
                    await self._send_json({
                        "type": "audio",
                        "data": audio_b64,
                        "sample_rate": tts_result.sample_rate,
                    })
                    logger.info("turn_tts", tts_time=metrics.tts_time)

            # Calculate total time
            metrics.total_time = time.perf_counter() - turn_start
            hotpath_total_duration_seconds.observe(metrics.total_time)

            # Emit UtteranceFinalized event for cold path
            await self._emit_utterance_event(
                audio_path=audio_path,
                transcript=transcript,
                stt_confidence=stt_confidence,
                audio_duration_ms=int(len(audio) / self.config.sample_rate * 1000),
            )

            # Log turn metrics
            logger.info(
                "turn_complete",
                turn_id=turn_id,
                total_time=metrics.total_time,
                stt_time=metrics.stt_time,
                llm_time=metrics.llm_time,
                tts_time=metrics.tts_time,
                under_budget=metrics.total_time < 2.0,
            )

            # Send metrics to client
            await self._send_json({
                "type": "metrics",
                "total_time": metrics.total_time,
                "stt_time": metrics.stt_time,
                "llm_time": metrics.llm_time,
                "tts_time": metrics.tts_time,
            })

        except Exception as e:
            logger.exception("turn_error", error=str(e))
            await self._send_error(f"Turn processing failed: {str(e)}")

        finally:
            self.state = SessionState.READY
            await self._send_state("ready")
            self._audio_buffer.clear()

    async def _process_text_turn(self, text: str) -> None:
        """Process a text-only turn (no audio).

        Args:
            text: User's text input
        """
        turn_start = time.perf_counter()

        self.state = SessionState.PROCESSING
        await self._send_state("processing")

        try:
            utterances_processed_total.labels(role="learner").inc()

            if self.dialogue:
                self.state = SessionState.SPEAKING
                await self._send_state("speaking")

                response_parts = []
                async for chunk in self.dialogue.chat_stream(self.context, text):
                    response_parts.append(chunk)
                    await self._send_json({"type": "response_chunk", "text": chunk})

                response_text = "".join(response_parts)
                await self._send_json({"type": "response", "text": response_text})

                utterances_processed_total.labels(role="coach").inc()

                # TTS
                if self.tts and response_text:
                    tts_result = await self.tts.synthesize(response_text)
                    if tts_result:
                        audio_b64 = base64.b64encode(tts_result.audio_bytes).decode("utf-8")
                        await self._send_json({
                            "type": "audio",
                            "data": audio_b64,
                            "sample_rate": tts_result.sample_rate,
                        })

            total_time = time.perf_counter() - turn_start
            hotpath_total_duration_seconds.observe(total_time)

        except Exception as e:
            logger.exception("text_turn_error", error=str(e))
            await self._send_error(str(e))

        finally:
            self.state = SessionState.READY
            await self._send_state("ready")

    async def _save_audio(self, audio: np.ndarray, turn_id: str) -> str:
        """Save audio to file.

        Args:
            audio: Audio samples
            turn_id: Turn identifier

        Returns:
            Path to saved file
        """
        import wave

        filename = f"{self.config.session_id}_{turn_id}.wav"
        filepath = self.config.audio_dir / filename

        # Convert to 16-bit PCM
        audio_int16 = (audio * 32767).astype(np.int16)

        with wave.open(str(filepath), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        return str(filepath)

    async def _emit_utterance_event(
        self,
        audio_path: str | None,
        transcript: str,
        stt_confidence: float,
        audio_duration_ms: int,
    ) -> None:
        """Emit UtteranceFinalized event for cold path processing."""
        event = UtteranceFinalized(
            utterance_id=str(uuid.uuid4()),
            session_id=self.config.session_id,
            user_id=self.config.user_id,
            audio_path=audio_path,
            transcript=transcript,
            stt_confidence=stt_confidence,
            start_ms=0,
            end_ms=audio_duration_ms,
            correlation_id=self.correlation_id,
        )
        await self.event_bus.publish(event)

    async def _send_state(self, state: str) -> None:
        """Send state update to client."""
        await self._send_json({"type": "state", "state": state})

    async def _send_error(self, error: str) -> None:
        """Send error message to client."""
        await self._send_json({"type": "error", "error": error})

    async def _send_json(self, data: dict) -> None:
        """Send JSON message to client."""
        try:
            await self.websocket.send_json(data)
        except Exception as e:
            logger.warning("send_failed", error=str(e))
