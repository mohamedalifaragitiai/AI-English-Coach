"""Dialogue service using Ollama LLM.

Provides conversation management with streaming responses.
Integrates with ResourceGuard for admission control.
"""

import json
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from backend.core.logging import get_logger
from backend.core.metrics import hotpath_stage_duration_seconds
from backend.core.resource_guard import ResourceEstimate, ResourceGuard

logger = get_logger(__name__)


# System prompt for the English coach
# /no_think disables qwen3's internal reasoning for faster responses
COACH_SYSTEM_PROMPT = """/no_think
You are a friendly English speaking coach. Keep responses SHORT (1-2 sentences max).

Level: {level} | Mode: {mode}

Rules:
- Be encouraging and natural
- Correct mistakes gently inline
- Ask one follow-up question
- NO long explanations
"""


@dataclass
class Message:
    """A conversation message."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class DialogueResponse:
    """Response from the dialogue service."""

    text: str
    tokens_used: int
    processing_time_seconds: float
    model: str


@dataclass
class ConversationContext:
    """Maintains conversation state."""

    messages: list[Message] = field(default_factory=list)
    learner_level: int = 0  # 0-5 scale
    session_mode: str = "free"
    max_history: int = 10  # Keep last N exchanges

    def add_system_prompt(self) -> None:
        """Add or update system prompt."""
        level_names = {
            0: "Beginner",
            1: "Intermediate",
            2: "Advanced",
            3: "Professional",
            4: "Fluent",
            5: "Native-like",
        }
        system_content = COACH_SYSTEM_PROMPT.format(
            level=level_names.get(self.learner_level, "Beginner"),
            mode=self.session_mode,
        )

        # Replace or add system message
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = Message(role="system", content=system_content)
        else:
            self.messages.insert(0, Message(role="system", content=system_content))

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self.messages.append(Message(role="user", content=content))
        self._trim_history()

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message."""
        self.messages.append(Message(role="assistant", content=content))
        self._trim_history()

    def _trim_history(self) -> None:
        """Keep only recent messages (preserve system prompt)."""
        if len(self.messages) > self.max_history + 1:
            # Keep system prompt and last N messages
            system = self.messages[0] if self.messages[0].role == "system" else None
            recent = self.messages[-(self.max_history):]
            self.messages = [system] + recent if system else recent

    def to_ollama_messages(self) -> list[dict]:
        """Convert to Ollama message format."""
        return [{"role": m.role, "content": m.content} for m in self.messages]


class DialogueService:
    """Dialogue service using Ollama.

    Usage:
        dialogue = DialogueService(llm_config, guard)

        # Create context for a session
        context = ConversationContext(learner_level=1, session_mode="free")
        context.add_system_prompt()

        # Get response
        response = await dialogue.chat(context, "Hello, how are you?")

        # Stream response
        async for chunk in dialogue.chat_stream(context, "Tell me about yourself"):
            print(chunk, end="")
    """

    def __init__(
        self,
        llm_config: dict,
        guard: ResourceGuard,
        default_max_tokens: int = 100,  # Shorter for faster responses
    ) -> None:
        """Initialize dialogue service.

        Args:
            llm_config: Dict with 'host' and 'model' keys
            guard: ResourceGuard for admission control
            default_max_tokens: Default max tokens for responses
        """
        self.host = llm_config["host"]
        self.model = llm_config["model"]
        self.guard = guard
        self.default_max_tokens = default_max_tokens

    async def chat(
        self,
        context: ConversationContext,
        user_message: str,
    ) -> DialogueResponse:
        """Get a complete response.

        Args:
            context: Conversation context with history
            user_message: User's message

        Returns:
            DialogueResponse with full text
        """
        start_time = time.perf_counter()

        # Add user message to context
        context.add_user_message(user_message)

        # Request admission
        admission = await self.guard.acquire(
            ResourceEstimate(vram_bytes=1.0e9, description="LLM chat"),
            path="hot",
        )

        # Apply degraded parameters
        max_tokens = self.default_max_tokens
        if admission.degraded:
            max_tokens = admission.params.get("max_tokens", 128)
            logger.info("dialogue_degraded", max_tokens=max_tokens)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.host}/api/chat",
                    json={
                        "model": self.model,
                        "messages": context.to_ollama_messages(),
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": 0.7,
                        },
                    },
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

            assistant_text = data.get("message", {}).get("content", "")
            context.add_assistant_message(assistant_text)

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="llm").observe(processing_time)

            tokens_used = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

            logger.info(
                "dialogue_complete",
                tokens=tokens_used,
                processing_time=processing_time,
            )

            return DialogueResponse(
                text=assistant_text,
                tokens_used=tokens_used,
                processing_time_seconds=processing_time,
                model=self.model,
            )

        except httpx.HTTPStatusError as e:
            logger.error("dialogue_http_error", status=e.response.status_code)
            raise
        except Exception as e:
            logger.error("dialogue_error", error=str(e))
            raise

    async def chat_stream(
        self,
        context: ConversationContext,
        user_message: str,
    ) -> AsyncIterator[str]:
        """Stream response tokens.

        Args:
            context: Conversation context with history
            user_message: User's message

        Yields:
            Text chunks as they're generated
        """
        start_time = time.perf_counter()

        context.add_user_message(user_message)

        admission = await self.guard.acquire(
            ResourceEstimate(vram_bytes=1.0e9, description="LLM stream"),
            path="hot",
        )

        max_tokens = self.default_max_tokens
        if admission.degraded:
            max_tokens = admission.params.get("max_tokens", 128)

        full_response = []

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.host}/api/chat",
                    json={
                        "model": self.model,
                        "messages": context.to_ollama_messages(),
                        "stream": True,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": 0.7,
                        },
                    },
                    timeout=60.0,
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                if "message" in data:
                                    chunk = data["message"].get("content", "")
                                    if chunk:
                                        full_response.append(chunk)
                                        yield chunk

                                if data.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue

            # Add complete response to context
            context.add_assistant_message("".join(full_response))

            processing_time = time.perf_counter() - start_time
            hotpath_stage_duration_seconds.labels(stage="llm").observe(processing_time)

            logger.info(
                "dialogue_stream_complete",
                chars=len("".join(full_response)),
                processing_time=processing_time,
            )

        except Exception as e:
            logger.error("dialogue_stream_error", error=str(e))
            raise

    async def health_check(self) -> bool:
        """Check if Ollama is available."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.host}/api/tags", timeout=5.0)
                return response.status_code == 200
        except Exception:
            return False
