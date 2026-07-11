"""Tests for cold path evaluators and analysis."""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from backend.coldpath.evaluators.base import (
    BaseEvaluator,
    EvaluatorInput,
    EvaluatorResult,
    EvaluatorRegistry,
    SkillType,
    get_registry,
)
from backend.coldpath.evaluators.fluency import FluencyEvaluator
from backend.coldpath.evaluators.grammar import GrammarEvaluator
from backend.coldpath.evaluators.pronunciation import PronunciationEvaluator
from backend.analysis.gap_analyzer import GapAnalyzer, SkillGap
from backend.analysis.learning_plan import LearningPlanGenerator


class MockResourceGuard:
    """Mock resource guard for testing."""

    async def acquire(self, **kwargs):
        return MagicMock(allowed=True)


# --- Evaluator Base Tests ---

class TestEvaluatorRegistry:
    """Tests for EvaluatorRegistry."""

    def test_register_evaluator(self):
        """Test registering an evaluator."""
        registry = EvaluatorRegistry()
        guard = MockResourceGuard()
        evaluator = FluencyEvaluator(guard)

        registry.register(evaluator)

        assert SkillType.FLUENCY in registry.skills
        assert registry.get(SkillType.FLUENCY) == evaluator

    def test_get_all_evaluators(self):
        """Test getting all registered evaluators."""
        registry = EvaluatorRegistry()
        guard = MockResourceGuard()

        registry.register(FluencyEvaluator(guard))
        registry.register(PronunciationEvaluator(guard))

        all_evaluators = registry.get_all()
        assert len(all_evaluators) == 2

    def test_unregister_evaluator(self):
        """Test unregistering an evaluator."""
        registry = EvaluatorRegistry()
        guard = MockResourceGuard()
        evaluator = FluencyEvaluator(guard)

        registry.register(evaluator)
        registry.unregister(SkillType.FLUENCY)

        assert SkillType.FLUENCY not in registry.skills


# --- Fluency Evaluator Tests ---

class TestFluencyEvaluator:
    """Tests for FluencyEvaluator."""

    @pytest.fixture
    def evaluator(self):
        """Create fluency evaluator."""
        return FluencyEvaluator(MockResourceGuard())

    @pytest.fixture
    def sample_input(self):
        """Create sample evaluator input."""
        return EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="Hello, I am learning English. It is very interesting.",
            context={"learner_level": 3},
        )

    @pytest.mark.asyncio
    async def test_score_basic_transcript(self, evaluator, sample_input):
        """Test scoring a basic transcript."""
        result = await evaluator.score(sample_input)

        assert isinstance(result, EvaluatorResult)
        assert result.skill == SkillType.FLUENCY
        assert 0.0 <= result.score <= 1.0
        assert 0.0 <= result.confidence <= 1.0
        assert "speech_rate_wpm" in result.details

    @pytest.mark.asyncio
    async def test_score_empty_transcript(self, evaluator):
        """Test scoring an empty transcript."""
        input_data = EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="",
        )

        result = await evaluator.score(input_data)

        assert result.score == 0.0
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_score_with_fillers(self, evaluator):
        """Test that filler words reduce fluency score."""
        input_with_fillers = EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="Um, I like, you know, basically um want to um learn English.",
            context={"learner_level": 3},
        )

        input_without_fillers = EvaluatorInput(
            utterance_id="test-124",
            session_id="session-456",
            user_id="user-789",
            transcript="I want to learn English because it is useful.",
            context={"learner_level": 3},
        )

        result_with = await evaluator.score(input_with_fillers)
        result_without = await evaluator.score(input_without_fillers)

        # Fillers should reduce the score
        assert result_with.score < result_without.score

    @pytest.mark.asyncio
    async def test_score_with_audio(self, evaluator):
        """Test scoring with audio array."""
        # Generate simple audio
        sample_rate = 16000
        duration = 2.0
        audio = np.sin(2 * np.pi * 200 * np.linspace(0, duration, int(sample_rate * duration)))

        input_data = EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="Hello, this is a test.",
            audio_array=audio.astype(np.float32),
            sample_rate=sample_rate,
            context={"learner_level": 3},
        )

        result = await evaluator.score(input_data)

        assert result.confidence == 0.9  # Higher confidence with audio


# --- Pronunciation Evaluator Tests ---

class TestPronunciationEvaluator:
    """Tests for PronunciationEvaluator."""

    @pytest.fixture
    def evaluator(self):
        """Create pronunciation evaluator."""
        return PronunciationEvaluator(MockResourceGuard())

    @pytest.mark.asyncio
    async def test_score_basic(self, evaluator):
        """Test basic pronunciation scoring."""
        input_data = EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="Hello world",
            stt_confidence=0.9,
        )

        result = await evaluator.score(input_data)

        assert result.skill == SkillType.PRONUNCIATION
        assert 0.0 <= result.score <= 1.0

    @pytest.mark.asyncio
    async def test_high_stt_confidence_improves_score(self, evaluator):
        """Test that high STT confidence improves score."""
        high_conf = EvaluatorInput(
            utterance_id="test-1",
            session_id="s-1",
            user_id="u-1",
            transcript="Hello",
            stt_confidence=0.95,
        )

        low_conf = EvaluatorInput(
            utterance_id="test-2",
            session_id="s-1",
            user_id="u-1",
            transcript="Hello",
            stt_confidence=0.5,
        )

        result_high = await evaluator.score(high_conf)
        result_low = await evaluator.score(low_conf)

        assert result_high.score > result_low.score

    @pytest.mark.asyncio
    async def test_identifies_problematic_words(self, evaluator):
        """Test that problematic words are identified."""
        input_data = EvaluatorInput(
            utterance_id="test-123",
            session_id="session-456",
            user_id="user-789",
            transcript="I think this is three things",
            stt_confidence=0.8,
        )

        result = await evaluator.score(input_data)

        # Should identify words with 'th' sound
        assert result.details.get("problematic_word_count", 0) > 0


# --- Grammar Evaluator Tests ---

class TestGrammarEvaluator:
    """Tests for GrammarEvaluator."""

    @pytest.fixture
    def evaluator(self):
        """Create grammar evaluator."""
        return GrammarEvaluator(MockResourceGuard())

    @pytest.mark.asyncio
    async def test_rule_based_fallback(self, evaluator):
        """Test rule-based grammar analysis."""
        # Force rule-based by mocking HTTP error
        with patch.object(evaluator, "_analyze_with_llm", side_effect=Exception("No LLM")):
            input_data = EvaluatorInput(
                utterance_id="test-123",
                session_id="session-456",
                user_id="user-789",
                transcript="hello world",  # Missing capitalization
            )

            result = await evaluator.score(input_data)

            assert result.skill == SkillType.GRAMMAR
            assert result.details.get("method") == "rule_based"
            # Should detect capitalization issue
            assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_detects_contraction_errors(self, evaluator):
        """Test detection of missing apostrophes."""
        with patch.object(evaluator, "_analyze_with_llm", side_effect=Exception("No LLM")):
            input_data = EvaluatorInput(
                utterance_id="test-123",
                session_id="session-456",
                user_id="user-789",
                transcript="I dont think thats right.",
            )

            result = await evaluator.score(input_data)

            # Should detect missing apostrophes
            error_types = [e.get("type") for e in result.errors]
            assert "contraction" in error_types


# --- Gap Analyzer Tests ---

class TestGapAnalyzer:
    """Tests for GapAnalyzer."""

    @pytest.fixture
    def mock_repos(self):
        """Create mock repositories."""
        assessment_repo = AsyncMock()
        gap_repo = AsyncMock()

        # Mock skill trend data
        now = datetime.now(timezone.utc)
        assessment_repo.get_skill_trend = AsyncMock(return_value=[
            (now - timedelta(days=10), 0.5),
            (now - timedelta(days=7), 0.52),
            (now - timedelta(days=5), 0.55),
            (now - timedelta(days=2), 0.58),
            (now, 0.6),
        ])

        gap_repo.create = AsyncMock()

        return assessment_repo, gap_repo

    @pytest.mark.asyncio
    async def test_analyze_computes_gaps(self, mock_repos):
        """Test gap analysis computation."""
        assessment_repo, gap_repo = mock_repos
        analyzer = GapAnalyzer(assessment_repo, gap_repo)

        snapshot = await analyzer.analyze("user-123")

        assert snapshot.user_id == "user-123"
        assert len(snapshot.gaps) > 0
        assert len(snapshot.priority_skills) <= 3

    @pytest.mark.asyncio
    async def test_trends_computed_correctly(self, mock_repos):
        """Test that trends are computed from historical data."""
        assessment_repo, gap_repo = mock_repos
        analyzer = GapAnalyzer(assessment_repo, gap_repo)

        snapshot = await analyzer.analyze("user-123")

        # Since scores are increasing, trend should be positive
        for gap in snapshot.gaps:
            # With increasing scores, trend should be positive
            assert gap.trend >= 0


# --- Learning Plan Generator Tests ---

class TestLearningPlanGenerator:
    """Tests for LearningPlanGenerator."""

    @pytest.fixture
    def generator(self):
        """Create learning plan generator."""
        return LearningPlanGenerator()

    @pytest.fixture
    def mock_gap_snapshot(self):
        """Create mock gap snapshot."""
        from backend.analysis.gap_analyzer import GapSnapshot

        return GapSnapshot(
            user_id="user-123",
            timestamp=datetime.now(timezone.utc),
            overall_score=0.5,
            overall_level=2,
            gaps=[
                SkillGap(
                    skill=SkillType.GRAMMAR,
                    current_score=0.45,
                    target_score=0.55,
                    gap_size=0.10,
                    trend=0.05,
                    priority=2.0,
                    assessment_count=10,
                ),
                SkillGap(
                    skill=SkillType.PRONUNCIATION,
                    current_score=0.60,
                    target_score=0.70,
                    gap_size=0.10,
                    trend=-0.02,
                    priority=1.8,
                    assessment_count=10,
                ),
            ],
            priority_skills=[SkillType.GRAMMAR, SkillType.PRONUNCIATION],
        )

    def test_generate_plan(self, generator, mock_gap_snapshot):
        """Test plan generation."""
        plan = generator.generate(mock_gap_snapshot)

        assert plan.user_id == "user-123"
        assert len(plan.items) > 0
        assert len(plan.milestones) > 0
        assert plan.current_level == 2
        assert plan.target_level == 3

    def test_plan_items_target_focus_skills(self, generator, mock_gap_snapshot):
        """Test that plan items target focus skills."""
        plan = generator.generate(mock_gap_snapshot)

        item_skills = [item.skill for item in plan.items]

        # Should include items for focus skills
        assert SkillType.GRAMMAR in item_skills

    def test_plan_includes_milestones(self, generator, mock_gap_snapshot):
        """Test that plan includes milestones."""
        plan = generator.generate(mock_gap_snapshot)

        assert len(plan.milestones) >= 2
        assert all("week" in m for m in plan.milestones)
        assert all("goal" in m for m in plan.milestones)


# --- Rate Limiter Tests ---

class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_sliding_window_allows_requests(self):
        """Test that requests within limit are allowed."""
        from backend.core.rate_limiter import SlidingWindowCounter

        counter = SlidingWindowCounter(60, 10)

        for _ in range(5):
            allowed, remaining = counter.is_allowed("test-key")
            assert allowed

    def test_sliding_window_blocks_excess(self):
        """Test that requests over limit are blocked."""
        from backend.core.rate_limiter import SlidingWindowCounter

        counter = SlidingWindowCounter(60, 5)

        for _ in range(5):
            counter.is_allowed("test-key")

        allowed, remaining = counter.is_allowed("test-key")
        assert not allowed
        assert remaining == 0

    def test_rate_limiter_websocket_limit(self):
        """Test WebSocket connection limiting."""
        from backend.core.rate_limiter import RateLimiter, RateLimitConfig

        limiter = RateLimiter(RateLimitConfig(websocket_connections_per_user=2))

        assert limiter.check_websocket("user-1")
        assert limiter.check_websocket("user-1")
        assert not limiter.check_websocket("user-1")  # Third connection blocked

        limiter.release_websocket("user-1")
        assert limiter.check_websocket("user-1")  # Now allowed
