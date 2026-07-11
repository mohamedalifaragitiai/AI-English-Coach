"""Grammar evaluator using LLM-based analysis.

Analyzes transcripts for grammatical correctness and identifies errors.
Uses Ollama for language model inference.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from backend.coldpath.evaluators.base import (
    BaseEvaluator,
    EvaluatorInput,
    EvaluatorResult,
    SkillType,
)
from backend.core.logging import get_logger
from backend.core.resource_guard import ResourceGuard

logger = get_logger(__name__)


GRAMMAR_ANALYSIS_PROMPT = """Analyze the following English sentence for grammar errors.
Return a JSON object with:
- "errors": list of grammar errors, each with "type", "text", "correction", "explanation"
- "score": grammar score from 0.0 (many errors) to 1.0 (perfect grammar)
- "corrected": the corrected sentence (if there are errors)

Be strict but fair. Consider:
- Subject-verb agreement
- Tense consistency
- Article usage (a/an/the)
- Preposition usage
- Word order
- Pronoun agreement
- Run-on sentences
- Fragments

Sentence: "{text}"

Return ONLY valid JSON, no markdown or explanation outside the JSON."""


@dataclass
class GrammarError:
    """A single grammar error."""

    error_type: str
    text: str
    correction: str
    explanation: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary."""
        return {
            "type": self.error_type,
            "text": self.text,
            "correction": self.correction,
            "explanation": self.explanation,
        }


class GrammarEvaluator(BaseEvaluator):
    """Grammar evaluator using LLM analysis.

    Uses Ollama to analyze grammar and identify errors.
    Falls back to rule-based analysis if LLM unavailable.
    """

    def __init__(
        self,
        guard: ResourceGuard,
        ollama_host: str = "http://localhost:11434",
        model: str = "qwen2.5:1.5b",
        timeout: float = 30.0,
    ) -> None:
        """Initialize grammar evaluator.

        Args:
            guard: Resource guard for admission control
            ollama_host: Ollama server URL
            model: Model name for grammar analysis
            timeout: Request timeout in seconds
        """
        super().__init__(guard)
        self.ollama_host = ollama_host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def skill_type(self) -> SkillType:
        """Grammar skill type."""
        return SkillType.GRAMMAR

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def score(self, input_data: EvaluatorInput) -> EvaluatorResult:
        """Score grammar in the transcript.

        Args:
            input_data: Input data with transcript

        Returns:
            EvaluatorResult with grammar score and errors
        """
        transcript = input_data.transcript.strip()

        if not transcript:
            return EvaluatorResult(
                skill=SkillType.GRAMMAR,
                score=0.0,
                confidence=0.0,
                details={"reason": "empty_transcript"},
            )

        # Check resources
        if not await self._check_resources():
            logger.warning("grammar_evaluation_deferred", utterance_id=input_data.utterance_id)
            return self._create_error_result("Resources unavailable, evaluation deferred")

        try:
            # Try LLM analysis first
            result = await self._analyze_with_llm(transcript)
            return result
        except Exception as e:
            logger.warning("llm_grammar_analysis_failed", error=str(e))
            # Fall back to rule-based analysis
            return self._analyze_with_rules(transcript)

    async def _analyze_with_llm(self, text: str) -> EvaluatorResult:
        """Analyze grammar using LLM.

        Args:
            text: Text to analyze

        Returns:
            EvaluatorResult with grammar analysis
        """
        client = await self._get_client()
        prompt = GRAMMAR_ANALYSIS_PROMPT.format(text=text)

        response = await client.post(
            f"{self.ollama_host}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 500,
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        response_text = data.get("response", "")

        # Parse JSON from response
        analysis = self._parse_llm_response(response_text)

        errors = [
            GrammarError(
                error_type=e.get("type", "unknown"),
                text=e.get("text", ""),
                correction=e.get("correction", ""),
                explanation=e.get("explanation", ""),
            )
            for e in analysis.get("errors", [])
        ]

        score = float(analysis.get("score", 0.5))
        # Clamp to valid range
        score = max(0.0, min(1.0, score))

        return EvaluatorResult(
            skill=SkillType.GRAMMAR,
            score=score,
            confidence=0.85,  # LLM confidence
            details={
                "method": "llm",
                "model": self.model,
                "corrected": analysis.get("corrected", ""),
                "error_count": len(errors),
            },
            errors=[e.to_dict() for e in errors],
        )

    def _parse_llm_response(self, response_text: str) -> dict[str, Any]:
        """Parse JSON from LLM response.

        Args:
            response_text: Raw LLM response

        Returns:
            Parsed JSON dictionary
        """
        # Try to extract JSON from response
        # Handle cases where LLM wraps in markdown code blocks
        text = response_text.strip()

        # Remove markdown code blocks if present
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Try to find JSON object
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            text = json_match.group()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("llm_json_parse_failed", response=text[:200])
            return {"score": 0.5, "errors": [], "corrected": ""}

    def _analyze_with_rules(self, text: str) -> EvaluatorResult:
        """Analyze grammar using rule-based heuristics.

        Simple fallback when LLM is unavailable.

        Args:
            text: Text to analyze

        Returns:
            EvaluatorResult with basic grammar analysis
        """
        errors: list[dict[str, str]] = []
        score = 1.0

        # Check for basic issues
        words = text.split()

        # Check capitalization
        if text and not text[0].isupper():
            errors.append({
                "type": "capitalization",
                "text": text[:20],
                "correction": text[0].upper() + text[1:],
                "explanation": "Sentences should start with a capital letter",
            })
            score -= 0.1

        # Check ending punctuation
        if text and text[-1] not in ".!?":
            errors.append({
                "type": "punctuation",
                "text": text[-20:],
                "correction": text + ".",
                "explanation": "Sentences should end with punctuation",
            })
            score -= 0.1

        # Check for common contractions without apostrophe
        contraction_errors = {
            "dont": "don't",
            "doesnt": "doesn't",
            "didnt": "didn't",
            "cant": "can't",
            "wont": "won't",
            "wouldnt": "wouldn't",
            "shouldnt": "shouldn't",
            "couldnt": "couldn't",
            "isnt": "isn't",
            "arent": "aren't",
            "wasnt": "wasn't",
            "werent": "weren't",
            "hasnt": "hasn't",
            "havent": "haven't",
            "hadnt": "hadn't",
            "im": "I'm",
            "ive": "I've",
            "youre": "you're",
            "youve": "you've",
            "theyre": "they're",
            "theyve": "they've",
            "weve": "we've",
            "hes": "he's",
            "shes": "she's",
            "its": "it's",  # Note: context-dependent
            "thats": "that's",
            "whats": "what's",
            "whos": "who's",
        }

        text_lower = text.lower()
        for error, correction in contraction_errors.items():
            if error in text_lower.split():
                errors.append({
                    "type": "contraction",
                    "text": error,
                    "correction": correction,
                    "explanation": f"Missing apostrophe: '{error}' should be '{correction}'",
                })
                score -= 0.15

        # Check for double spaces
        if "  " in text:
            errors.append({
                "type": "spacing",
                "text": "double space",
                "correction": "single space",
                "explanation": "Use single spaces between words",
            })
            score -= 0.05

        # Subject-verb agreement (basic)
        sv_patterns = [
            (r"\bi\s+(is|was|has)\b", "I am/was/have"),
            (r"\b(he|she|it)\s+(are|were|have)\b", "he/she/it is/was/has"),
            (r"\b(they|we|you)\s+(is|was|has)\b", "they/we/you are/were/have"),
        ]

        for pattern, correction_hint in sv_patterns:
            if re.search(pattern, text_lower):
                errors.append({
                    "type": "subject_verb_agreement",
                    "text": pattern,
                    "correction": correction_hint,
                    "explanation": "Subject and verb do not agree",
                })
                score -= 0.2

        # Clamp score
        score = max(0.0, min(1.0, score))

        return EvaluatorResult(
            skill=SkillType.GRAMMAR,
            score=score,
            confidence=0.5,  # Lower confidence for rule-based
            details={
                "method": "rule_based",
                "error_count": len(errors),
            },
            errors=errors,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
