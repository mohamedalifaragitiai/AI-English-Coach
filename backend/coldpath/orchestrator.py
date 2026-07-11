"""Cold path orchestrator for asynchronous evaluation.

Subscribes to UtteranceFinalized events from the hot path and runs
all registered evaluators. Stores results as assessments in the database.
"""

import asyncio
import wave
from pathlib import Path
from typing import Any

import numpy as np

from backend.coldpath.evaluators.base import (
    EvaluatorInput,
    EvaluatorResult,
    SkillType,
    get_registry,
)
from backend.core.event_bus import AssessmentReady, EventBus, UtteranceFinalized
from backend.core.logging import get_logger
from backend.core.resource_guard import ResourceGuard
from backend.persistence.repositories import AssessmentRepository

logger = get_logger(__name__)


class ColdPathOrchestrator:
    """Orchestrates cold path evaluation of utterances.

    Listens for UtteranceFinalized events and runs all registered
    evaluators concurrently. Aggregates results into an Assessment
    and stores in the database.
    """

    def __init__(
        self,
        event_bus: EventBus,
        guard: ResourceGuard,
        assessment_repo: AssessmentRepository,
    ) -> None:
        """Initialize orchestrator.

        Args:
            event_bus: Event bus for subscribing to events
            guard: Resource guard for admission control
            assessment_repo: Repository for storing assessments
        """
        self.event_bus = event_bus
        self.guard = guard
        self.assessment_repo = assessment_repo
        self._running = False
        self._processing_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start the orchestrator.

        Subscribes to UtteranceFinalized events.
        """
        if self._running:
            return

        self._running = True
        self.event_bus.subscribe("utterance_finalized", self._handle_utterance)
        logger.info("cold_path_orchestrator_started")

    async def stop(self) -> None:
        """Stop the orchestrator.

        Unsubscribes from events and waits for pending work.
        """
        self._running = False
        self.event_bus.unsubscribe("utterance_finalized", self._handle_utterance)

        # Wait for pending tasks
        if self._processing_tasks:
            logger.info("waiting_for_pending_evaluations", count=len(self._processing_tasks))
            await asyncio.gather(*self._processing_tasks, return_exceptions=True)

        logger.info("cold_path_orchestrator_stopped")

    async def _handle_utterance(self, event: UtteranceFinalized) -> None:
        """Handle an UtteranceFinalized event.

        Creates a task to process the utterance asynchronously.

        Args:
            event: The utterance event
        """
        if not self._running:
            return

        task = asyncio.create_task(self._process_utterance(event))
        self._processing_tasks.add(task)
        task.add_done_callback(self._processing_tasks.discard)

    async def _process_utterance(self, event: UtteranceFinalized) -> None:
        """Process an utterance through all evaluators.

        Args:
            event: The utterance event
        """
        logger.info(
            "processing_utterance",
            utterance_id=event.utterance_id,
            user_id=event.user_id,
            session_id=event.session_id,
        )

        try:
            # Load audio if path provided
            audio_array = None
            sample_rate = 16000
            if event.audio_path:
                audio_array, sample_rate = self._load_audio(event.audio_path)

            # Create evaluator input
            input_data = EvaluatorInput(
                utterance_id=event.utterance_id,
                session_id=event.session_id,
                user_id=event.user_id,
                transcript=event.transcript,
                audio_path=event.audio_path,
                audio_array=audio_array,
                sample_rate=sample_rate,
                stt_confidence=event.stt_confidence,
                context={
                    "correlation_id": event.correlation_id,
                },
            )

            # Run all evaluators concurrently
            registry = get_registry()
            evaluators = registry.get_all()

            if not evaluators:
                logger.warning("no_evaluators_registered")
                return

            results = await asyncio.gather(
                *[self._run_evaluator(e, input_data) for e in evaluators],
                return_exceptions=True,
            )

            # Collect successful results
            successful_results: list[EvaluatorResult] = []
            for result in results:
                if isinstance(result, EvaluatorResult):
                    successful_results.append(result)
                elif isinstance(result, Exception):
                    logger.error("evaluator_exception", error=str(result))

            if successful_results:
                # Store assessment
                assessment_id = await self._store_assessment(
                    event=event,
                    results=successful_results,
                )

                # Emit AssessmentReady event
                await self.event_bus.publish(
                    AssessmentReady(
                        user_id=event.user_id,
                        session_id=event.session_id,
                        assessment_id=assessment_id,
                        correlation_id=event.correlation_id,
                    )
                )

                logger.info(
                    "utterance_processed",
                    utterance_id=event.utterance_id,
                    assessment_id=assessment_id,
                    evaluator_count=len(successful_results),
                )

        except Exception as e:
            logger.exception(
                "utterance_processing_failed",
                utterance_id=event.utterance_id,
                error=str(e),
            )

    async def _run_evaluator(
        self,
        evaluator: Any,  # BaseEvaluator
        input_data: EvaluatorInput,
    ) -> EvaluatorResult:
        """Run a single evaluator.

        Args:
            evaluator: The evaluator to run
            input_data: Input data for evaluation

        Returns:
            EvaluatorResult
        """
        try:
            result = await evaluator.score(input_data)
            logger.debug(
                "evaluator_complete",
                evaluator=evaluator.name,
                skill=result.skill.value,
                score=result.score,
            )
            return result
        except Exception as e:
            logger.error(
                "evaluator_failed",
                evaluator=evaluator.name,
                error=str(e),
            )
            raise

    async def _store_assessment(
        self,
        event: UtteranceFinalized,
        results: list[EvaluatorResult],
    ) -> str:
        """Store evaluation results as an assessment.

        Args:
            event: Original utterance event
            results: List of evaluator results

        Returns:
            Assessment ID
        """
        # Build scores dictionary
        scores: dict[str, float] = {}
        details: dict[str, Any] = {}
        all_errors: list[dict] = []

        for result in results:
            skill_name = result.skill.value
            scores[skill_name] = result.score
            details[skill_name] = {
                "confidence": result.confidence,
                "details": result.details,
            }
            all_errors.extend(result.errors)

        # Compute overall score (weighted average)
        overall = self._compute_overall(scores)

        # Create assessment
        assessment = await self.assessment_repo.create(
            user_id=event.user_id,
            session_id=event.session_id,
            scores=scores,
            overall=overall,
            audio_path=event.audio_path,
            transcript=event.transcript,
        )

        return assessment.id

    def _compute_overall(self, scores: dict[str, float]) -> float:
        """Compute overall score from skill scores.

        Uses weighted average with standard weights.

        Args:
            scores: Dictionary of skill -> score

        Returns:
            Overall score 0.0 to 1.0
        """
        # Default weights (can be customized per user/level)
        weights = {
            SkillType.PRONUNCIATION.value: 0.25,
            SkillType.GRAMMAR.value: 0.25,
            SkillType.FLUENCY.value: 0.20,
            SkillType.VOCABULARY.value: 0.15,
            SkillType.COHERENCE.value: 0.10,
            SkillType.RELEVANCE.value: 0.05,
        }

        weighted_sum = 0.0
        total_weight = 0.0

        for skill, score in scores.items():
            weight = weights.get(skill, 0.1)
            weighted_sum += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight

    def _load_audio(self, audio_path: str) -> tuple[np.ndarray | None, int]:
        """Load audio from file.

        Args:
            audio_path: Path to WAV file

        Returns:
            Tuple of (audio array, sample rate)
        """
        path = Path(audio_path)
        if not path.exists():
            logger.warning("audio_file_not_found", path=audio_path)
            return None, 16000

        try:
            with wave.open(str(path), "rb") as wf:
                sample_rate = wf.getframerate()
                audio_bytes = wf.readframes(wf.getnframes())

            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return audio, sample_rate
        except Exception as e:
            logger.warning("audio_load_failed", path=audio_path, error=str(e))
            return None, 16000
