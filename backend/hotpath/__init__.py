"""Hot path: VAD, STT, dialogue, TTS - synchronous, target <2s."""

from backend.hotpath.dialogue import ConversationContext, DialogueService
from backend.hotpath.stt import STTService, TranscriptionResult
from backend.hotpath.tts import TTSService, TTSResult
from backend.hotpath.vad import SileroVAD, VADConfig, VADProcessor, VADResult, VADState
from backend.hotpath.ws_session import ConversationSession, SessionConfig, SessionState

__all__ = [
    "STTService",
    "TranscriptionResult",
    "DialogueService",
    "ConversationContext",
    "TTSService",
    "TTSResult",
    "SileroVAD",
    "VADConfig",
    "VADProcessor",
    "VADResult",
    "VADState",
    "ConversationSession",
    "SessionConfig",
    "SessionState",
]
