"""Hot path: VAD, STT, dialogue, TTS - synchronous, target <2s."""

from backend.hotpath.dialogue import ConversationContext, DialogueService
from backend.hotpath.stt import STTService, TranscriptionResult
from backend.hotpath.tts import TTSService, TTSResult

__all__ = [
    "STTService",
    "TranscriptionResult",
    "DialogueService",
    "ConversationContext",
    "TTSService",
    "TTSResult",
]
